import io
import time
from typing import Optional, List, Dict, Tuple

import streamlit as st
import pandas as pd
import plotly.express as px

# -------- Optional Polars (fast for 5M+ rows) --------
try:
    import polars as pl
    HAVE_POLARS = True
except Exception:
    HAVE_POLARS = False

# -------- Page Config (must be first) --------
st.set_page_config(
    page_title="Stock Comparison Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# [theme]
# base="dark"
# backgroundColor="#0d121c"
# secondaryBackgroundColor="#131529"

ss = st.session_state
ss.setdefault("compare_data_clicked", False)
ss.setdefault("warehouse_file", None)
ss.setdefault("ecommerce_file", None)

def format_bytes(size_bytes: Optional[int]) -> str:
    if not size_bytes:
        return "—"
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024**2:
        return f"{size_bytes/1024:.2f} KB"
    if size_bytes < 1024**3:
        return f"{size_bytes/1024**2:.2f} MB"
    return f"{size_bytes/1024**3:.2f} GB"

def ext_of(name: str) -> str:
    return (name.rsplit(".", 1)[-1] if "." in name else "").lower()

def _norm_token(s: str) -> str:
    return (s or "").strip().lower().replace(" ", "").replace("-", "").replace("_", "")

def guess_column(columns: List[str], role: str) -> Optional[str]:
    role_syns: Dict[str, List[str]] = {
        "sku": ["sku", "productid", "productcode", "item", "itemcode", "barcode", "upc", "ean", "supplier_sku", "suppliersku"],
        "account": ["account", "accountnumber", "supplier", "vendor", "store", "channel", "partner", "account_id"],
        "qty": ["quantity", "qty", "freestock", "stock", "onhand", "available", "inventory", "soh"],
    }
    syns = role_syns.get(role, [])
    if not columns:
        return None
    norm_map = {_norm_token(c): c for c in columns}
    for s in syns:
        if s in norm_map:
            return norm_map[s]
    for c in columns:
        n = _norm_token(c)
        if any(s in n for s in syns):
            return c
    return columns[0]

def safe_rerun():
    try:
        st.rerun()
    except Exception:
        st.experimental_rerun()

# New Streamlit API removed use_container_width — wrap for compatibility.
def show_df(df: pd.DataFrame):
    try:
        st.dataframe(df, width="stretch")
    except TypeError:
        st.dataframe(df, use_container_width=True)

def show_chart(fig):
    try:
        st.plotly_chart(fig, width="stretch")
    except TypeError:
        st.plotly_chart(fig, use_container_width=True)

@st.cache_data(show_spinner="Reading CSV (pandas)…")
def load_csv_pandas(data: bytes) -> pd.DataFrame:
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        for eng in ("pyarrow", "c", "python"):
            try:
                bio = io.BytesIO(data)
                kwargs = dict(encoding=enc)
                if eng != "pyarrow":
                    kwargs["on_bad_lines"] = "skip"
                return pd.read_csv(bio, engine=(None if eng == "pyarrow" else eng), **kwargs)
            except Exception:
                continue
    return pd.read_csv(io.BytesIO(data))

@st.cache_data(show_spinner="Reading CSV (Polars)…")
def load_csv_polars(data: bytes) -> "pl.DataFrame":
    return pl.read_csv(io.BytesIO(data))

@st.cache_data(show_spinner="Inspecting Excel…")
def excel_sheet_names(data: bytes) -> List[str]:
    x = pd.ExcelFile(io.BytesIO(data))
    return x.sheet_names

@st.cache_data(show_spinner="Reading Excel sheet…")
def load_excel_sheet_pandas(data: bytes, sheet_name: str | int) -> pd.DataFrame:
    return pd.read_excel(io.BytesIO(data), sheet_name=sheet_name)

def normalize_pandas(df: pd.DataFrame,
                     col_sku: str, col_acc: str, col_qty: str,
                     upper_keys: bool, strip_keys: bool,
                     clamp_negative: bool) -> pd.DataFrame:
    out = df[[col_sku, col_acc, col_qty]].copy()
    out.columns = ["sku_raw", "account_raw", "qty"]
    out["sku_key"] = out["sku_raw"].astype(str)
    out["account_key"] = out["account_raw"].astype(str)
    if strip_keys:
        out["sku_key"] = out["sku_key"].str.strip()
        out["account_key"] = out["account_key"].str.strip()
    if upper_keys:
        out["sku_key"] = out["sku_key"].str.upper()
        out["account_key"] = out["account_key"].str.upper()
    out["qty"] = pd.to_numeric(out["qty"], errors="coerce")
    if clamp_negative:
        out.loc[out["qty"] < 0, "qty"] = 0
    return out.dropna(subset=["sku_key", "account_key"])

def aggregate_dupes_pandas(df: pd.DataFrame, aggfunc: str, qty_col: str) -> pd.DataFrame:
    agg_map = {"sum": "sum", "max": "max", "min": "min", "first": "first", "last": "last"}
    f = agg_map.get(aggfunc, "sum")
    grp = (
        df.groupby(["sku_key", "account_key"], as_index=False)
          .agg(**{qty_col: (qty_col, f),
                  "sku_raw": ("sku_raw", "first"),
                  "account_raw": ("account_raw", "first")})
    )
    return grp

def compare_pandas(a_df: pd.DataFrame, b_df: pd.DataFrame, aggfunc: str) -> pd.DataFrame:
    a = aggregate_dupes_pandas(a_df, aggfunc, "qty").rename(columns={"qty": "qty_wh"})
    b = aggregate_dupes_pandas(b_df, aggfunc, "qty").rename(columns={"qty": "qty_ecom"})
    merged = pd.merge(
        a[["sku_key", "account_key", "qty_wh"]],
        b[["sku_key", "account_key", "qty_ecom"]],
        on=["sku_key", "account_key"],
        how="outer",
        indicator=True,
    )
    merged["present_wh"] = merged["qty_wh"].notna()
    merged["present_ecom"] = merged["qty_ecom"].notna()
    merged["qty_wh"] = merged["qty_wh"].fillna(0)
    merged["qty_ecom"] = merged["qty_ecom"].fillna(0)
    merged["qty_diff"] = merged["qty_wh"] - merged["qty_ecom"]
    merged["status"] = merged["qty_diff"].apply(lambda x: "Match" if x == 0 else "Mismatch")
    def _coverage(r):
        if r["present_wh"] and r["present_ecom"]:
            return "Both"
        if r["present_wh"] and not r["present_ecom"]:
            return "Warehouse Only"
        if not r["present_wh"] and r["present_ecom"]:
            return "E-Commerce Only"
        return "—"
    merged["source_status"] = merged.apply(_coverage, axis=1)
    merged["in_stock_wh"] = merged["qty_wh"] > 0
    merged["in_stock_ecom"] = merged["qty_ecom"] > 0
    return merged

def rollup_by_account_pandas(merged: pd.DataFrame) -> pd.DataFrame:
    g = merged.groupby("account_key", as_index=False).agg(
        total_items=("sku_key", "nunique"),
        matched=("status", lambda s: int((s == "Match").sum())),
        mismatched=("status", lambda s: int((s == "Mismatch").sum())),
        wh_only=("source_status", lambda s: int((s == "Warehouse Only").sum())),
        ecom_only=("source_status", lambda s: int((s == "E-Commerce Only").sum())),
        qty_wh_total=("qty_wh", "sum"),
        qty_ecom_total=("qty_ecom", "sum"),
        abs_diff_total=("qty_diff", lambda s: s.abs().sum()),
    )
    cross = merged.groupby("account_key").apply(
        lambda x: pd.Series({
            "instock_wh_oos_ecom": int((x["in_stock_wh"] & ~x["in_stock_ecom"]).sum()),
            "instock_ecom_oos_wh": int((x["in_stock_ecom"] & ~x["in_stock_wh"]).sum())
        })
    ).reset_index()
    g = g.merge(cross, on="account_key", how="left").fillna(0)
    return g

def normalize_polars(df: "pl.DataFrame",
                     col_sku: str, col_acc: str, col_qty: str,
                     upper_keys: bool, strip_keys: bool,
                     clamp_negative: bool) -> "pl.DataFrame":
    out = df.select([
        pl.col(col_sku).alias("sku_raw"),
        pl.col(col_acc).alias("account_raw"),
        pl.col(col_qty).alias("qty"),
    ])
    s = pl.col("sku_raw").cast(pl.Utf8)
    a = pl.col("account_raw").cast(pl.Utf8)
    if strip_keys:
        s = s.str.strip_chars()
        a = a.str.strip_chars()
    if upper_keys:
        s = s.str.to_uppercase()
        a = a.str.to_uppercase()
    q = pl.col("qty").cast(pl.Float64, strict=False)
    if clamp_negative:
        q = pl.when(q < 0).then(0).otherwise(q)
    return out.with_columns([s.alias("sku_key"), a.alias("account_key"), q.alias("qty")]) \
              .drop_nulls(["sku_key", "account_key"])

def aggregate_dupes_polars(df: "pl.DataFrame", aggfunc: str, qty_col: str) -> "pl.DataFrame":
    f_map = {"sum": pl.sum, "max": pl.max, "min": pl.min, "first": pl.first, "last": pl.last}
    f = f_map.get(aggfunc, pl.sum)
    return (df.group_by(["sku_key", "account_key"])
              .agg([f(pl.col(qty_col)).alias(qty_col),
                    pl.col("sku_raw").first().alias("sku_raw"),
                    pl.col("account_raw").first().alias("account_raw")]))

def compare_polars(a_df: "pl.DataFrame", b_df: "pl.DataFrame", aggfunc: str) -> "pl.DataFrame":
    a = aggregate_dupes_polars(a_df, aggfunc, "qty").rename({"qty": "qty_wh"})
    b = aggregate_dupes_polars(b_df, aggfunc, "qty").rename({"qty": "qty_ecom"})
    merged = a.join(b, on=["sku_key", "account_key"], how="outer")
    merged = merged.with_columns([
        pl.col("qty_wh").is_not_null().alias("present_wh"),
        pl.col("qty_ecom").is_not_null().alias("present_ecom"),
        pl.col("qty_wh").fill_null(0),
        pl.col("qty_ecom").fill_null(0),
    ])
    merged = merged.with_columns([
        (pl.col("qty_wh") - pl.col("qty_ecom")).alias("qty_diff"),
        pl.when(pl.col("qty_wh") - pl.col("qty_ecom") == 0)
          .then(pl.lit("Match")).otherwise(pl.lit("Mismatch")).alias("status"),
        pl.when(pl.col("present_wh") & pl.col("present_ecom")).then("Both")
         .when(pl.col("present_wh") & ~pl.col("present_ecom")).then("Warehouse Only")
         .when(~pl.col("present_wh") & pl.col("present_ecom")).then("E-Commerce Only")
         .otherwise("—").alias("source_status"),
        (pl.col("qty_wh") > 0).alias("in_stock_wh"),
        (pl.col("qty_ecom") > 0).alias("in_stock_ecom"),
    ])
    return merged

def rollup_by_account_polars(merged: "pl.DataFrame") -> "pl.DataFrame":
    return (merged.group_by("account_key")
        .agg([
            pl.n_unique("sku_key").alias("total_items"),
            (pl.col("status") == "Match").sum().alias("matched"),
            (pl.col("status") == "Mismatch").sum().alias("mismatched"),
            (pl.col("source_status") == "Warehouse Only").sum().alias("wh_only"),
            (pl.col("source_status") == "E-Commerce Only").sum().alias("ecom_only"),
            pl.col("qty_wh").sum().alias("qty_wh_total"),
            pl.col("qty_ecom").sum().alias("qty_ecom_total"),
            pl.col("qty_diff").abs().sum().alias("abs_diff_total"),
            (pl.col("in_stock_wh") & ~pl.col("in_stock_ecom")).sum().alias("instock_wh_oos_ecom"),
            (pl.col("in_stock_ecom") & ~pl.col("in_stock_wh")).sum().alias("instock_ecom_oos_wh"),
        ]))

SENTINEL = "— Select —"

def _normalize_preview_key(s: pd.Series, upper: bool, strip: bool) -> pd.Series:
    s = s.astype(str)
    if strip:
        s = s.str.strip()
    if upper:
        s = s.str.upper()
    return s

def mapping_widget(df: pd.DataFrame, label: str,
                   sku_guess: str | None, acc_guess: str | None, qty_guess: str | None):
    options = [SENTINEL] + df.columns.tolist()
    def _idx(guess):
        if guess in df.columns:
            return options.index(guess)
        return 0
    st.info(f"{label} Mapping")
    sku = st.selectbox(f"{label} SKU", options, index=_idx(sku_guess), key=f"{label}_sku")
    acc = st.selectbox(f"{label} Account", options, index=_idx(acc_guess), key=f"{label}_acc")
    qty = st.selectbox(f"{label} Quantity", options, index=_idx(qty_guess), key=f"{label}_qty")
    sku = None if sku == SENTINEL else sku
    acc = None if acc == SENTINEL else acc
    qty = None if qty == SENTINEL else qty
    return sku, acc, qty

def validate_mapping_side(df: pd.DataFrame, side_name: str,
                          sku: str | None, acc: str | None, qty: str | None,
                          *, upper_keys: bool, strip_keys: bool,
                          clamp_negative: bool, agg_choice: str) -> tuple[bool, List[str], List[str]]:
    errors, warnings = [], []
    missing = [n for n, v in (("SKU", sku), ("Account", acc), ("Quantity", qty)) if not v]
    if missing:
        errors.append(f"{side_name}: select {', '.join(missing)}.")
        return False, errors, warnings
    picks = [sku, acc, qty]
    if len(set(picks)) != len(picks):
        errors.append(f"{side_name}: SKU/Account/Quantity must be different columns.")
    for col in picks:
        if col not in df.columns:
            errors.append(f"{side_name}: column '{col}' not found.")
            return False, errors, warnings
    key_sku = _normalize_preview_key(df[sku], upper=upper_keys, strip=strip_keys)
    key_acc = _normalize_preview_key(df[acc], upper=upper_keys, strip=strip_keys)
    null_sku = int(key_sku.isna().sum() + (key_sku == "nan").sum())
    null_acc = int(key_acc.isna().sum() + (key_acc == "nan").sum())
    if null_sku > 0:
        warnings.append(f"{side_name}: {null_sku:,} empty/NaN SKU keys.")
    if null_acc > 0:
        warnings.append(f"{side_name}: {null_acc:,} empty/NaN Account keys.")
    dup_groups = (key_sku.fillna("").astype(str) + "␟" + key_acc.fillna("").astype(str)).value_counts()
    dup_count = int((dup_groups > 1).sum())
    if dup_count > 0:
        warnings.append(f"{side_name}: {dup_count:,} duplicate key groups (aggregated via '{agg_choice}').")
    q = pd.to_numeric(df[qty], errors="coerce")
    bad_qty = int(q.isna().sum())
    if bad_qty > 0:
        warnings.append(f"{side_name}: {bad_qty:,} non-numeric Quantity values will be coerced/dropped.")
    if (q < 0).sum() > 0 and not clamp_negative:
        warnings.append(f"{side_name}: negative quantities detected (enable 'Clamp negative quantities to 0' in ⚙️ Settings).")
    ok = len(errors) == 0
    return ok, errors, warnings

def mapping_checklist_ui(ok_a: bool, err_a: List[str], warn_a: List[str],
                         ok_b: bool, err_b: List[str], warn_b: List[str]) -> bool:
    st.subheader("✅ Mapping Checklist")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Warehouse**")
        if ok_a:
            st.success("All good.")
        for msg in err_a:
            st.error(msg)
        for msg in warn_a:
            st.warning(msg)
    with c2:
        st.markdown("**E-Commerce**")
        if ok_b:
            st.success("All good.")
        for msg in err_b:
            st.error(msg)
        for msg in warn_b:
            st.warning(msg)
    return ok_a and ok_b

st.sidebar.title("App Navigation & Tools")
with st.sidebar.expander("📁 Upload Your Files", expanded=True):
    st.write("CSV or Excel (.xlsx/.xls). If Excel, pick a sheet below.")
    ss.warehouse_file = st.file_uploader("Warehouse File", type=["csv", "xlsx", "xls"], key="warehouse_upl")
    ss.ecommerce_file = st.file_uploader("E-Commerce File", type=["csv", "xlsx", "xls"], key="ecommerce_upl")

with st.sidebar.expander("⚙️ Settings", expanded=False):
    engine = st.selectbox(
        "Backend",
        options=["Auto", "Polars (fast, 5M+ rows)", "Pandas"],
        help="Excel loads via pandas; if Polars selected/Auto triggers, we convert to Polars for processing."
    )
    auto_polars_threshold_mb = st.number_input(
        "Auto-switch to Polars if total upload size ≥ (MB)",
        min_value=10, max_value=10000, value=200, step=10
    )
    preview_rows = st.slider("Preview rows", min_value=5, max_value=200, value=20, step=5)
    agg_choice = st.selectbox("Duplicate key aggregator", ["sum", "max", "min", "first", "last"])
    upper_keys = st.checkbox("Case-insensitive keys (upper)", True)
    strip_keys = st.checkbox("Trim whitespace in keys", True)
    clamp_negative = st.checkbox("Clamp negative quantities to 0", False)

st.sidebar.markdown("---")
if st.sidebar.button("🔄 Reset App"):
    ss.clear()
    safe_rerun()

def ensure_sheet_selected(label: str, file) -> Optional[str]:
    if not file:
        return None
    if ext_of(file.name) in {"xlsx", "xls"}:
        data = file.getvalue()
        sheets = excel_sheet_names(data)
        key = f"{label}_sheet"
        default = ss.get(key) or (sheets[0] if sheets else None)
        chosen = st.selectbox(f"{label}: Select sheet", sheets, index=(sheets.index(default) if default in sheets else 0))
        ss[key] = chosen
        return chosen
    return None

def should_use_polars(files: List) -> bool:
    if engine == "Pandas":
        return False
    if engine == "Polars (fast, 5M+ rows)":
        return True and HAVE_POLARS
    if not HAVE_POLARS:
        return False
    total_size = sum(getattr(f, "size", 0) or 0 for f in files if f)
    return (total_size / (1024**2)) >= auto_polars_threshold_mb

st.title("📊 Stock Comparison Dashboard")

if ss.warehouse_file and ss.ecommerce_file:
    wa_ext = ext_of(ss.warehouse_file.name)
    ec_ext = ext_of(ss.ecommerce_file.name)
    use_polars = should_use_polars([ss.warehouse_file, ss.ecommerce_file])

    if engine.startswith("Polars") and not HAVE_POLARS:
        st.warning("Polars not installed; falling back to pandas. `pip install polars` for faster big-data processing.")

    ws_sheet = ensure_sheet_selected("Warehouse", ss.warehouse_file) if wa_ext in {"xlsx","xls"} else None
    ec_sheet = ensure_sheet_selected("E-Commerce", ss.ecommerce_file) if ec_ext in {"xlsx","xls"} else None

    if wa_ext == "csv":
        df_a_pd = load_csv_pandas(ss.warehouse_file.getvalue())
    else:
        df_a_pd = load_excel_sheet_pandas(ss.warehouse_file.getvalue(), ws_sheet or 0)

    if ec_ext == "csv":
        df_b_pd = load_csv_pandas(ss.ecommerce_file.getvalue())
    else:
        df_b_pd = load_excel_sheet_pandas(ss.ecommerce_file.getvalue(), ec_sheet or 0)

    st.subheader("Step 1: Preview Your Data & File Info")
    col1, col2 = st.columns(2)
    with col1:
        with st.expander(f"Warehouse: **{ss.warehouse_file.name}**", expanded=True):
            st.metric("File Size", format_bytes(getattr(ss.warehouse_file, "size", None)))
            st.write("Columns:", df_a_pd.columns.tolist())
            show_df(df_a_pd.head(preview_rows))
    with col2:
        with st.expander(f"E-Commerce: **{ss.ecommerce_file.name}**", expanded=True):
            st.metric("File Size", format_bytes(getattr(ss.ecommerce_file, "size", None)))
            st.write("Columns:", df_b_pd.columns.tolist())
            show_df(df_b_pd.head(preview_rows))

    st.divider()

    st.subheader("Step 2: Map Your Columns")

    sku_a_guess = guess_column(df_a_pd.columns.tolist(), "sku") if not df_a_pd.empty else None
    acc_a_guess = guess_column(df_a_pd.columns.tolist(), "account") if not df_a_pd.empty else None
    qty_a_guess = guess_column(df_a_pd.columns.tolist(), "qty") if not df_a_pd.empty else None

    sku_b_guess = guess_column(df_b_pd.columns.tolist(), "sku") if not df_b_pd.empty else None
    acc_b_guess = guess_column(df_b_pd.columns.tolist(), "account") if not df_b_pd.empty else None
    qty_b_guess = guess_column(df_b_pd.columns.tolist(), "qty") if not df_b_pd.empty else None

    m1, m2 = st.columns(2)
    with m1:
        col_sku_a, col_acc_a, col_qty_a = mapping_widget(df_a_pd, "Warehouse 🏢", sku_a_guess, acc_a_guess, qty_a_guess)
    with m2:
        col_sku_b, col_acc_b, col_qty_b = mapping_widget(df_b_pd, "E-Commerce 🛒", sku_b_guess, acc_b_guess, qty_b_guess)

    ok_a, errs_a, warns_a = validate_mapping_side(
        df_a_pd, "Warehouse", col_sku_a, col_acc_a, col_qty_a,
        upper_keys=upper_keys, strip_keys=strip_keys, clamp_negative=clamp_negative, agg_choice=agg_choice
    )
    ok_b, errs_b, warns_b = validate_mapping_side(
        df_b_pd, "E-Commerce", col_sku_b, col_acc_b, col_qty_b,
        upper_keys=upper_keys, strip_keys=strip_keys, clamp_negative=clamp_negative, agg_choice=agg_choice
    )
    all_good = mapping_checklist_ui(ok_a, errs_a, warns_a, ok_b, errs_b, warns_b)

    st.divider()

    compare_clicked = st.button("🚀 Compare Data", type="primary", disabled=not all_good)
    if compare_clicked:
        ss.compare_data_clicked = True

    if ss.compare_data_clicked and all_good:
        with st.spinner(f"Processing with {'Polars' if use_polars else 'Pandas'} …"):
            t0 = time.time()
            try:
                if use_polars and HAVE_POLARS:
                    a_pl = pl.from_pandas(df_a_pd)
                    b_pl = pl.from_pandas(df_b_pd)
                    a_norm = normalize_polars(a_pl, col_sku_a, col_acc_a, col_qty_a, upper_keys, strip_keys, clamp_negative)
                    b_norm = normalize_polars(b_pl, col_sku_b, col_acc_b, col_qty_b, upper_keys, strip_keys, clamp_negative)
                    merged_pl = compare_polars(a_norm, b_norm, agg_choice)
                    rollup_pl = rollup_by_account_polars(merged_pl)

                    total_records = int(merged_pl.height)
                    match_count = int((merged_pl["status"] == "Match").sum())
                    mismatch_count = int((merged_pl["status"] == "Mismatch").sum())
                    wh_only_count = int((merged_pl["source_status"] == "Warehouse Only").sum())
                    ecom_only_count = int((merged_pl["source_status"] == "E-Commerce Only").sum())
                    both_sources_count = int((merged_pl["source_status"] == "Both").sum())
                    wh_stock_ecom_oos = int(((merged_pl["in_stock_wh"]) & (~merged_pl["in_stock_ecom"])).sum())
                    ecom_stock_wh_oos = int(((merged_pl["in_stock_ecom"]) & (~merged_pl["in_stock_wh"])).sum())
                    total_qty_a = float(merged_pl["qty_wh"].sum())
                    total_qty_b = float(merged_pl["qty_ecom"].sum())
                    total_abs_diff = float(merged_pl["qty_diff"].abs().sum())

                    merged_pd = merged_pl.to_pandas(use_pyarrow_extension_array=True)
                    rollup_pd = rollup_pl.to_pandas(use_pyarrow_extension_array=True)
                else:
                    a_norm = normalize_pandas(df_a_pd, col_sku_a, col_acc_a, col_qty_a, upper_keys, strip_keys, clamp_negative)
                    b_norm = normalize_pandas(df_b_pd, col_sku_b, col_acc_b, col_qty_b, upper_keys, strip_keys, clamp_negative)
                    merged_pd = compare_pandas(a_norm, b_norm, agg_choice)
                    rollup_pd = rollup_by_account_pandas(merged_pd)

                    total_records = len(merged_pd)
                    match_count = int((merged_pd["status"] == "Match").sum())
                    mismatch_count = int((merged_pd["status"] == "Mismatch").sum())
                    wh_only_count = int((merged_pd["source_status"] == "Warehouse Only").sum())
                    ecom_only_count = int((merged_pd["source_status"] == "E-Commerce Only").sum())
                    both_sources_count = int((merged_pd["source_status"] == "Both").sum())
                    wh_stock_ecom_oos = int((merged_pd["in_stock_wh"] & ~merged_pd["in_stock_ecom"]).sum())
                    ecom_stock_wh_oos = int((merged_pd["in_stock_ecom"] & ~merged_pd["in_stock_wh"]).sum())
                    total_qty_a = float(merged_pd["qty_wh"].sum())
                    total_qty_b = float(merged_pd["qty_ecom"].sum())
                    total_abs_diff = float(merged_pd["qty_diff"].abs().sum())

                t1 = time.time()

                # --- Dashboard ---
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
                            merged_pd, names="status", title="Comparison Status",
                            color="status", color_discrete_map={"Match": "lightgreen", "Mismatch": "lightcoral"}
                        )
                        show_chart(fig_status)
                with sub2:
                    st.subheader("Presence Across Sources")
                    if total_records > 0:
                        fig_presence = px.pie(
                            merged_pd, names="source_status", title="Item Presence",
                            color="source_status",
                            color_discrete_map={"Both": "lightblue", "Warehouse Only": "orange", "E-Commerce Only": "purple"},
                        )
                        show_chart(fig_presence)
                with sub3:
                    st.subheader("Quantity Aggregates")
                    st.metric("Total Warehouse Quantity", f"{int(total_qty_a):,}")
                    st.metric("Total E-Commerce Quantity", f"{int(total_qty_b):,}")
                    st.metric("Total Absolute Discrepancy", f"{int(total_abs_diff):,}")

                st.divider()

                ek1, ek2, ek3 = st.columns(3)
                ek1.metric("SKUs Present in Both", f"{both_sources_count:,}")
                ek2.metric("In Stock in WH & OOS in E-Com", f"{wh_stock_ecom_oos:,}")
                ek3.metric("In Stock in E-Com & OOS in WH", f"{ecom_stock_wh_oos:,}")

                st.divider()

                # --- Tabs: Detailed & Rollup ---
                tab_detail, tab_rollup = st.tabs(["📋 Detailed Results", "🧮 Account-Level Rollup"])

                with tab_detail:
                    f1, f2, f3 = st.columns(3)
                    with f1:
                        status_filter = st.multiselect(
                            "Filter by Status",
                            options=sorted(merged_pd["status"].unique().tolist()),
                            default=sorted(merged_pd["status"].unique().tolist()),
                        )
                    with f2:
                        source_filter = st.multiselect(
                            "Filter by Presence",
                            options=sorted(merged_pd["source_status"].unique().tolist()),
                            default=sorted(merged_pd["source_status"].unique().tolist()),
                        )
                    with f3:
                        show_only_nonzero = st.checkbox("Only rows where at least one qty > 0", False)

                    filtered = merged_pd[
                        merged_pd["status"].isin(status_filter)
                        & merged_pd["source_status"].isin(source_filter)
                    ].copy()
                    if show_only_nonzero:
                        filtered = filtered[(filtered["qty_wh"] > 0) | (filtered["qty_ecom"] > 0)]

                    cols_order = ["sku_key","account_key","qty_wh","qty_ecom","qty_diff","status","source_status","in_stock_wh","in_stock_ecom","present_wh","present_ecom"]
                    present = [c for c in cols_order if c in filtered.columns]
                    show_df(filtered[present] if present else filtered)

                    st.markdown("---")
                    d1, d2, d3, d4 = st.columns(4)
                    with d1:
                        st.download_button("📥 Download Full Results (CSV)",
                                           data=merged_pd.to_csv(index=False).encode("utf-8"),
                                           file_name="stock_comparison_full_results.csv", mime="text/csv")
                    with d2:
                        mm = merged_pd[merged_pd["status"] == "Mismatch"]
                        if not mm.empty:
                            st.download_button("⬇️ Mismatches (CSV)",
                                               data=mm.to_csv(index=False).encode("utf-8"),
                                               file_name="mismatches.csv", mime="text/csv")
                        else:
                            st.info("No mismatches to download.")
                    with d3:
                        set1 = merged_pd[(merged_pd["in_stock_wh"]) & (~merged_pd["in_stock_ecom"])]
                        if not set1.empty:
                            st.download_button("⬇️ WH In-Stock & E-Com OOS (CSV)",
                                               data=set1.to_csv(index=False).encode("utf-8"),
                                               file_name="wh_instock_ecom_oos.csv", mime="text/csv")
                    with d4:
                        set2 = merged_pd[(merged_pd["in_stock_ecom"]) & (~merged_pd["in_stock_wh"])]
                        if not set2.empty:
                            st.download_button("⬇️ E-Com In-Stock & WH OOS (CSV)",
                                               data=set2.to_csv(index=False).encode("utf-8"),
                                               file_name="ecom_instock_wh_oos.csv", mime="text/csv")

                with tab_rollup:
                    st.write("Per-account KPIs and totals.")
                    show_df(rollup_pd)
                    st.markdown("---")
                    r1, r2 = st.columns(2)
                    with r1:
                        st.download_button("📥 Download Rollup (CSV)",
                                           data=rollup_pd.to_csv(index=False).encode("utf-8"),
                                           file_name="account_rollup.csv", mime="text/csv")
                    with r2:
                        buf = io.BytesIO()
                        rollup_pd.to_parquet(buf, index=False)
                        st.download_button("💾 Download Rollup (Parquet)",
                                           data=buf.getvalue(),
                                           file_name="account_rollup.parquet",
                                           mime="application/octet-stream")

            except KeyError as e:
                st.error(f"❌ Column Mapping Error: {e}. Check your selections.")
                ss.compare_data_clicked = False
            except Exception as e:
                st.error(f"Unexpected error: {e}")
                ss.compare_data_clicked = False

    elif ss.compare_data_clicked and not all_good:
        st.error("Fix the mapping errors above to continue.")
        ss.compare_data_clicked = False

else:
    st.info("👈 Upload two files (CSV or Excel) in the sidebar to begin.")
    ss.compare_data_clicked = False
