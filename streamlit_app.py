import streamlit as st
import pandas as pd
import plotly.express as px
import time
from typing import Optional, Tuple, List, Dict

# -------------------- Page Config (must be first) --------------------
st.set_page_config(
    page_title="Stock Comparison Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# -------------------- Session State Defaults --------------------
ss = st.session_state
ss.setdefault("compare_data_clicked", False)
ss.setdefault("warehouse_csv", None)
ss.setdefault("ecommerce_csv", None)

# -------------------- Helpers --------------------
def format_bytes(size_bytes: int) -> str:
    if size_bytes is None:
        return "—"
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024**2:
        return f"{size_bytes/1024:.2f} KB"
    elif size_bytes < 1024**3:
        return f"{size_bytes/1024**2:.2f} MB"
    return f"{size_bytes/1024**3:.2f} GB"


def _try_read_csv(uploaded_file, engine: Optional[str], encoding: Optional[str], bad_lines: bool):
    """
    Internal: attempts a read_csv with the given parameters.
    Some engine/param combos (e.g., pyarrow + on_bad_lines) are invalid — catch gracefully.
    """
    uploaded_file.seek(0)
    kwargs = dict(encoding=encoding)
    if engine:
        kwargs["engine"] = engine
    if bad_lines:
        # on_bad_lines is not supported by the pyarrow engine
        kwargs["on_bad_lines"] = "skip"
    return pd.read_csv(uploaded_file, **kwargs)


@st.cache_data(show_spinner="Loading data...")
def load_csv(uploaded_file) -> pd.DataFrame:
    """
    Robust CSV loader with multiple fallbacks:
    - Tries pyarrow engine for speed if available (no on_bad_lines)
    - Falls back to C/Python engines with on_bad_lines='skip'
    - Tries a few encodings commonly seen in the wild
    """
    if uploaded_file is None:
        return pd.DataFrame()

    engines = ["pyarrow", "c", "python"]
    encodings = ["utf-8", "utf-8-sig", "cp1252", "latin-1"]

    # 1) Try pyarrow quickly without on_bad_lines
    for enc in encodings:
        try:
            return _try_read_csv(uploaded_file, engine="pyarrow", encoding=enc, bad_lines=False)
        except Exception:
            pass

    # 2) Fall back to C then Python engine with on_bad_lines=skip
    for eng in ("c", "python"):
        for enc in encodings:
            try:
                return _try_read_csv(uploaded_file, engine=eng, encoding=enc, bad_lines=True)
            except Exception:
                continue

    # 3) Last resort — let pandas guess everything
    uploaded_file.seek(0)
    return pd.read_csv(uploaded_file)


def _norm_token(s: str) -> str:
    return (s or "").strip().lower().replace(" ", "").replace("-", "").replace("_", "")


def guess_column(columns: List[str], role: str) -> Optional[str]:
    """
    Heuristic guesser for SKU / Account / Quantity columns.
    Case-insensitive, whitespace/dash/underscore-insensitive.
    """
    role_syns: Dict[str, List[str]] = {
        "sku": ["sku", "productid", "productcode", "item", "itemcode", "barcode", "upc", "ean", "supplier_sku", "suppliersku"],
        "account": ["account", "accountnumber", "supplier", "vendor", "store", "channel", "partner", "account_id"],
        "qty": ["quantity", "qty", "freestock", "stock", "onhand", "available", "inventory", "soh"],
    }
    syns = role_syns.get(role, [])
    if not columns:
        return None

    norm_map = {_norm_token(c): c for c in columns}
    # Exact match first
    for s in syns:
        if s in norm_map:
            return norm_map[s]
    # Substring match (e.g., "supplier_sku" contains "sku")
    for c in columns:
        n = _norm_token(c)
        if any(s in n for s in syns):
            return c
    # Fallback to first column
    return columns[0]


def normalize_keys(series: pd.Series, upper: bool, strip: bool) -> pd.Series:
    s = series.astype(str)
    if strip:
        s = s.str.strip()
    if upper:
        s = s.str.upper()
    return s


def coerce_quantity(s: pd.Series, clamp_negative: bool) -> pd.Series:
    q = pd.to_numeric(s, errors="coerce")
    if clamp_negative:
        q = q.mask(q < 0, 0)
    # Prefer integers when safe; keep floats otherwise
    if (q.dropna() % 1 == 0).all():
        try:
            q = q.astype("Int64")
        except Exception:
            q = q.astype("float64")
    return q


def safe_rerun():
    try:
        st.rerun()
    except Exception:
        st.experimental_rerun()


# -------------------- UI --------------------
st.title("📊 Stock Comparison Dashboard")

# Sidebar — Uploads
st.sidebar.title("App Navigation & Tools")
with st.sidebar.expander("📁 Upload Your Files", expanded=True):
    st.write("Upload the two CSVs to compare.")
    ss.warehouse_csv = st.file_uploader("Upload Warehouse CSV", type="csv", key="warehouse_file_uploader")
    ss.ecommerce_csv = st.file_uploader("Upload E-Commerce CSV", type="csv", key="ecommerce_file_uploader")

# Sidebar — Settings
with st.sidebar.expander("⚙️ Settings", expanded=False):
    preview_rows = st.slider("Preview rows", min_value=5, max_value=100, value=20, step=5)
    agg_choice = st.selectbox(
        "Duplicate key aggregator (when multiple rows share the same SKU+Account)",
        ["sum", "max", "min", "first", "last"],
        help="Applies separately in each file before joining.",
    )
    upper_keys = st.checkbox("Case-insensitive key match (uppercase keys)", True)
    strip_keys = st.checkbox("Trim whitespace in keys", True)
    clamp_negative = st.checkbox("Clamp negative quantities to 0", False)

# Sidebar — Reset
st.sidebar.markdown("---")
if st.sidebar.button("🔄 Reset App"):
    ss.clear()
    safe_rerun()

# Main flow
if ss.warehouse_csv and ss.ecommerce_csv:
    df_a = load_csv(ss.warehouse_csv)
    df_b = load_csv(ss.ecommerce_csv)

    st.subheader("Step 1: Preview Your Data & File Info")
    st.write("Review columns and a quick sample of each file.")

    col1, col2 = st.columns(2)
    with col1:
        with st.expander(f"Warehouse: **{ss.warehouse_csv.name}**", expanded=True):
            st.metric("File Size", format_bytes(getattr(ss.warehouse_csv, "size", None)))
            st.write("Columns:", df_a.columns.tolist())
            st.dataframe(df_a.head(preview_rows), use_container_width=True)
    with col2:
        with st.expander(f"E-Commerce: **{ss.ecommerce_csv.name}**", expanded=True):
            st.metric("File Size", format_bytes(getattr(ss.ecommerce_csv, "size", None)))
            st.write("Columns:", df_b.columns.tolist())
            st.dataframe(df_b.head(preview_rows), use_container_width=True)

    st.divider()

    # Step 2: Mapping (with smart defaults)
    st.subheader("Step 2: Map Your Columns")

    # Smart guesses (don’t crash on empty frames)
    sku_a_guess = guess_column(df_a.columns.tolist(), "sku") if not df_a.empty else None
    acc_a_guess = guess_column(df_a.columns.tolist(), "account") if not df_a.empty else None
    qty_a_guess = guess_column(df_a.columns.tolist(), "qty") if not df_a.empty else None

    sku_b_guess = guess_column(df_b.columns.tolist(), "sku") if not df_b.empty else None
    acc_b_guess = guess_column(df_b.columns.tolist(), "account") if not df_b.empty else None
    qty_b_guess = guess_column(df_b.columns.tolist(), "qty") if not df_b.empty else None

    m1, m2 = st.columns(2)
    with m1:
        st.info("Warehouse Mapping 🏢")
        col_sku_a = st.selectbox("Warehouse SKU", df_a.columns, index=(df_a.columns.get_loc(sku_a_guess) if sku_a_guess in df_a.columns else 0), key="sku_a")
        col_acc_a = st.selectbox("Warehouse Account", df_a.columns, index=(df_a.columns.get_loc(acc_a_guess) if acc_a_guess in df_a.columns else 0), key="acc_a")
        col_qty_a = st.selectbox("Warehouse Quantity", df_a.columns, index=(df_a.columns.get_loc(qty_a_guess) if qty_a_guess in df_a.columns else 0), key="qty_a")

    with m2:
        st.info("E-Commerce Mapping 🛒")
        col_sku_b = st.selectbox("E-Commerce SKU", df_b.columns, index=(df_b.columns.get_loc(sku_b_guess) if sku_b_guess in df_b.columns else 0), key="sku_b")
        col_acc_b = st.selectbox("E-Commerce Account", df_b.columns, index=(df_b.columns.get_loc(acc_b_guess) if acc_b_guess in df_b.columns else 0), key="acc_b")
        col_qty_b = st.selectbox("E-Commerce Quantity", df_b.columns, index=(df_b.columns.get_loc(qty_b_guess) if qty_b_guess in df_b.columns else 0), key="qty_b")

    st.divider()

    # Step 3: Compare
    if st.button("🚀 Compare Data", type="primary"):
        ss.compare_data_clicked = True

    if ss.compare_data_clicked:
        with st.spinner("Crunching numbers…"):
            t0 = time.time()
            try:
                # ---- Normalize & clean ----
                a = df_a[[col_sku_a, col_acc_a, col_qty_a]].copy()
                b = df_b[[col_sku_b, col_acc_b, col_qty_b]].copy()

                a.rename(columns={col_sku_a: "sku_raw", col_acc_a: "account_raw", col_qty_a: "qty_wh"}, inplace=True)
                b.rename(columns={col_sku_b: "sku_raw", col_acc_b: "account_raw", col_qty_b: "qty_ecom"}, inplace=True)

                a["sku_key"] = normalize_keys(a["sku_raw"], upper=upper_keys, strip=strip_keys)
                a["account_key"] = normalize_keys(a["account_raw"], upper=upper_keys, strip=strip_keys)
                b["sku_key"] = normalize_keys(b["sku_raw"], upper=upper_keys, strip=strip_keys)
                b["account_key"] = normalize_keys(b["account_raw"], upper=upper_keys, strip=strip_keys)

                a["qty_wh"] = coerce_quantity(a["qty_wh"], clamp_negative=clamp_negative)
                b["qty_ecom"] = coerce_quantity(b["qty_ecom"], clamp_negative=clamp_negative)

                # Drop rows missing essential keys
                a = a.dropna(subset=["sku_key", "account_key"])
                b = b.dropna(subset=["sku_key", "account_key"])

                # ---- Aggregate duplicates by key (SKU+Account) ----
                agg_map = {
                    "sum": "sum",
                    "max": "max",
                    "min": "min",
                    "first": "first",
                    "last": "last",
                }
                aggfunc = agg_map.get(agg_choice, "sum")

                a_grp = (
                    a.groupby(["sku_key", "account_key"], as_index=False)
                    .agg(qty_wh=("qty_wh", aggfunc), sku_raw=("sku_raw", "first"), account_raw=("account_raw", "first"))
                )
                b_grp = (
                    b.groupby(["sku_key", "account_key"], as_index=False)
                    .agg(qty_ecom=("qty_ecom", aggfunc), sku_raw=("sku_raw", "first"), account_raw=("account_raw", "first"))
                )

                # ---- Merge (outer) ----
                merged = pd.merge(
                    a_grp[["sku_key", "account_key", "qty_wh"]],
                    b_grp[["sku_key", "account_key", "qty_ecom"]],
                    on=["sku_key", "account_key"],
                    how="outer",
                    indicator=True,
                )

                # Presence before fill
                merged["present_wh"] = merged["qty_wh"].notna()
                merged["present_ecom"] = merged["qty_ecom"].notna()

                # Fill NaNs to 0 for arithmetic, retain presence flags
                merged["qty_wh"] = merged["qty_wh"].fillna(0)
                merged["qty_ecom"] = merged["qty_ecom"].fillna(0)

                # Difference & status
                merged["qty_diff"] = merged["qty_wh"] - merged["qty_ecom"]
                merged["status"] = merged["qty_diff"].apply(lambda x: "Match" if x == 0 else "Mismatch")

                # Source coverage
                def _coverage(r):
                    if r["present_wh"] and r["present_ecom"]:
                        return "Both"
                    if r["present_wh"] and not r["present_ecom"]:
                        return "Warehouse Only"
                    if not r["present_wh"] and r["present_ecom"]:
                        return "E-Commerce Only"
                    return "—"

                merged["source_status"] = merged.apply(_coverage, axis=1)

                # In-stock / OOS flags (0 is OOS)
                merged["in_stock_wh"] = merged["qty_wh"] > 0
                merged["in_stock_ecom"] = merged["qty_ecom"] > 0

                # KPI counts
                total_records = len(merged)
                match_count = int((merged["status"] == "Match").sum())
                mismatch_count = int((merged["status"] == "Mismatch").sum())
                wh_only_count = int((merged["source_status"] == "Warehouse Only").sum())
                ecom_only_count = int((merged["source_status"] == "E-Commerce Only").sum())
                both_sources_count = int((merged["source_status"] == "Both").sum())

                # Extras: in-stock vs OOS cross
                wh_stock_ecom_oos = int((merged["in_stock_wh"] & ~merged["in_stock_ecom"]).sum())
                ecom_stock_wh_oos = int((merged["in_stock_ecom"] & ~merged["in_stock_wh"]).sum())

                total_qty_a = float(merged["qty_wh"].sum())
                total_qty_b = float(merged["qty_ecom"].sum())
                total_abs_diff = float((merged["qty_diff"].abs()).sum())

                t1 = time.time()

                # -------------------- Dashboard --------------------
                st.header("📊 Comparison Dashboard")

                k1, k2, k3, k4 = st.columns(4)
                k1.metric("Total Unique Items (SKU+Account)", f"{total_records:,}")
                k2.metric("✅ Matched Quantities", f"{match_count:,}")
                k3.metric("❌ Mismatched Quantities", f"{mismatch_count:,}")
                k4.metric("⏱️ Processing Time", f"{(t1 - t0):.2f} sec")

                st.divider()

                sub1, sub2, sub3 = st.columns([1.1, 1, 1])
                with sub1:
                    st.subheader("📈 Status Breakdown")
                    if total_records > 0:
                        fig_status = px.pie(
                            merged,
                            names="status",
                            title="Comparison Status",
                            color="status",
                            color_discrete_map={"Match": "lightgreen", "Mismatch": "lightcoral"},
                        )
                        st.plotly_chart(fig_status, use_container_width=True)
                with sub2:
                    st.subheader("📦 Presence Across Sources")
                    if total_records > 0:
                        fig_presence = px.pie(
                            merged,
                            names="source_status",
                            title="Item Presence",
                            color="source_status",
                            color_discrete_map={
                                "Both": "lightblue",
                                "Warehouse Only": "orange",
                                "E-Commerce Only": "purple",
                            },
                        )
                        st.plotly_chart(fig_presence, use_container_width=True)
                with sub3:
                    st.subheader("🔍 Quantity Aggregates")
                    st.metric("Total Warehouse Quantity", f"{int(total_qty_a):,}")
                    st.metric("Total E-Commerce Quantity", f"{int(total_qty_b):,}")
                    st.metric("Total Absolute Discrepancy", f"{int(total_abs_diff):,}")

                st.divider()

                # Extra KPIs
                ek1, ek2, ek3 = st.columns(3)
                ek1.metric("SKUs Present in Both", f"{both_sources_count:,}")
                ek2.metric("In Stock in WH & OOS in E-Com", f"{wh_stock_ecom_oos:,}")
                ek3.metric("In Stock in E-Com & OOS in WH", f"{ecom_stock_wh_oos:,}")

                st.divider()

                # -------------------- Detailed Table + Filters --------------------
                st.subheader("📋 Detailed Results")

                f1, f2, f3 = st.columns(3)
                with f1:
                    status_filter = st.multiselect(
                        "Filter by Status",
                        options=sorted(merged["status"].unique().tolist()),
                        default=sorted(merged["status"].unique().tolist()),
                    )
                with f2:
                    source_filter = st.multiselect(
                        "Filter by Presence",
                        options=sorted(merged["source_status"].unique().tolist()),
                        default=sorted(merged["source_status"].unique().tolist()),
                    )
                with f3:
                    show_only_nonzero = st.checkbox("Only rows where at least one qty > 0", False)

                filtered = merged[
                    merged["status"].isin(status_filter)
                    & merged["source_status"].isin(source_filter)
                ].copy()

                if show_only_nonzero:
                    filtered = filtered[(filtered["qty_wh"] > 0) | (filtered["qty_ecom"] > 0)]

                # Order columns nicely
                cols_order = [
                    "sku_key",
                    "account_key",
                    "qty_wh",
                    "qty_ecom",
                    "qty_diff",
                    "status",
                    "source_status",
                    "in_stock_wh",
                    "in_stock_ecom",
                    "present_wh",
                    "present_ecom",
                ]
                filtered = filtered.reindex(columns=cols_order)

                if not filtered.empty:
                    st.dataframe(filtered, use_container_width=True)
                else:
                    st.info("No records match the selected filters.")

                st.markdown("---")

                # -------------------- Downloads --------------------
                d1, d2, d3 = st.columns(3)
                with d1:
                    csv_full = merged.to_csv(index=False).encode("utf-8")
                    st.download_button(
                        "📥 Download Full Results CSV",
                        data=csv_full,
                        file_name="stock_comparison_full_results.csv",
                        mime="text/csv",
                    )
                with d2:
                    mm = merged[merged["status"] == "Mismatch"]
                    if not mm.empty:
                        csv_mm = mm.to_csv(index=False).encode("utf-8")
                        st.download_button(
                            "⬇️ Download Mismatches CSV",
                            data=csv_mm,
                            file_name="stock_comparison_mismatches.csv",
                            mime="text/csv",
                        )
                    else:
                        st.info("No mismatches to download.")
                with d3:
                    # Two cross in-stock/OOS exports
                    set1 = merged[merged["in_stock_wh"] & ~merged["in_stock_ecom"]]
                    set2 = merged[merged["in_stock_ecom"] & ~merged["in_stock_wh"]]
                    if not set1.empty:
                        st.download_button(
                            "⬇️ Download: In Stock WH & OOS E-Com",
                            data=set1.to_csv(index=False).encode("utf-8"),
                            file_name="wh_instock_ecom_oos.csv",
                            mime="text/csv",
                        )
                    if not set2.empty:
                        st.download_button(
                            "⬇️ Download: In Stock E-Com & OOS WH",
                            data=set2.to_csv(index=False).encode("utf-8"),
                            file_name="ecom_instock_wh_oos.csv",
                            mime="text/csv",
                        )

            except KeyError as e:
                st.error(f"❌ Column Mapping Error: {e}. Check your selections.")
                ss.compare_data_clicked = False
            except Exception as e:
                st.error(f"Unexpected error during comparison: {e}")
                ss.compare_data_clicked = False

else:
    st.info("👈 Upload both Warehouse and E-Commerce CSV files in the sidebar to begin.")
    ss.compare_data_clicked = False
