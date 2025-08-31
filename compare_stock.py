import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import sqlite3, yaml, pandas as pd
from datetime import datetime
import os
from typing import List, Tuple, Optional

CONFIG_FILE = "config.yml"

DEFAULT_CONFIG = {
    "database_url": "sqlite:///inventory.db",
    "source_table": {"name": "inventory_latest", "key_column": "SupplierSKU"},
    "target_table": {"name": "inventory", "key_column": "SupplierSKU"},
    "columns_to_compare": ["FreeStock"],
    "all_columns_to_sync": ["SupplierSKU", "Account", "FreeStock"],
}

def load_config(path=CONFIG_FILE):
    if os.path.exists(path):
        with open(path, "r") as f:
            cfg = yaml.safe_load(f) or {}
            return {**DEFAULT_CONFIG, **cfg}
    return DEFAULT_CONFIG.copy()

def sqlite_path(db_url: str) -> str:
    if not db_url.startswith("sqlite:///"):
        raise ValueError("Only sqlite:/// URLs supported")
    return db_url[len("sqlite:///"):]

def fetch_table_any_columns(conn, table: str) -> pd.DataFrame:
    return pd.read_sql_query(f'SELECT * FROM "{table}"', conn)

def get_sqlite_columns(conn, table: str) -> List[str]:
    q = f'PRAGMA table_info("{table}")'
    info = pd.read_sql_query(q, conn)
    return info["name"].tolist()

def safe_read_csv(path: str) -> pd.DataFrame:
    enc_trials = ["utf-8", "utf-8-sig", "latin-1"]
    last_err = None
    for enc in enc_trials:
        try:
            return pd.read_csv(path, encoding=enc)
        except Exception as e:
            last_err = e
            continue
    try:
        return pd.read_csv(path, encoding="utf-8", on_bad_lines="skip", engine="python")
    except Exception as e:
        raise RuntimeError(f"Failed to read CSV: {last_err or e}") from e

def read_file_any(path: str) -> pd.DataFrame:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xls", ".xlsm"):
        try:
            return pd.read_excel(path)
        except ImportError:
            raise RuntimeError("Reading Excel requires 'openpyxl' to be installed (pip install openpyxl).")
    elif ext in (".csv", ".txt"):
        return safe_read_csv(path)
    else:
        raise RuntimeError(f"Unsupported file type: {ext}. Use CSV or Excel.")

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Trim spaces in column names; leave original values."""
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    return df

def guess_primary_key(cols: List[str]) -> Optional[str]:
    cset = {c.lower(): c for c in cols}
    for cand in ("suppliersku", "sku", "part_number", "product_code", "item_code"):
        if cand in cset: return cset[cand]
    return None

def guess_account_key(cols: List[str]) -> Optional[str]:
    cset = {c.lower(): c for c in cols}
    for cand in ("account", "account_id", "retailer_id", "vendor_id", "supplier_id"):
        if cand in cset: return cset[cand]
    return None

def ensure_cols(df: pd.DataFrame, cols: List[str]) -> List[str]:
    return [c for c in cols if c in df.columns]

def as_multi_index(df: pd.DataFrame, key_cols: List[str]) -> pd.DataFrame:
    df = df.copy()
    df = df.drop_duplicates(subset=key_cols)
    return df.set_index(key_cols if len(key_cols) > 1 else key_cols[0])

def is_in_stock(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    return (s.fillna(0) > 0)

def compute_diff_multi(
    df_src: pd.DataFrame,
    df_tgt: pd.DataFrame,
    src_keys: List[str],
    tgt_keys: List[str],
    compare_cols: List[str],
) -> Tuple[pd.DataFrame, dict]:
    """
    Diff on possibly composite keys (len(keys) 1 or 2).
    compare_cols must exist in BOTH frames; we auto-intersect inside.
    """
    df_src = normalize_columns(df_src)
    df_tgt = normalize_columns(df_tgt)

    compare_cols = [c for c in compare_cols if c in df_src.columns and c in df_tgt.columns]
    src_idx_df = df_src[src_keys].copy()
    tgt_idx_df = df_tgt[tgt_keys].copy()
    std_src_keys = [f"__K{i}__" for i in range(len(src_keys))]
    std_tgt_keys = [f"__K{i}__" for i in range(len(tgt_keys))]

    df_src_std = df_src.copy()
    df_tgt_std = df_tgt.copy()
    for old, new in zip(src_keys, std_src_keys):
        df_src_std[new] = df_src_std[old]
    for old, new in zip(tgt_keys, std_tgt_keys):
        df_tgt_std[new] = df_tgt_std[old]

    src_index_cols = std_src_keys
    tgt_index_cols = std_tgt_keys

    src_idxed = as_multi_index(df_src_std, src_index_cols)
    tgt_idxed = as_multi_index(df_tgt_std, tgt_index_cols)

    all_keys = src_idxed.index.union(tgt_idxed.index)

    rows = []
    stats = dict(added=0, removed=0, modified=0, same=0, total_src=len(df_src), total_tgt=len(df_tgt))
    per_col_mod = {c: 0 for c in compare_cols}

    for k in all_keys:
        in_s = k in src_idxed.index
        in_t = k in tgt_idxed.index
        if in_s and not in_t:
            stats["added"] += 1
            change = "added"
        elif (not in_s) and in_t:
            stats["removed"] += 1
            change = "removed"
        else:
            diffs = []
            srow = src_idxed.loc[k]
            trow = tgt_idxed.loc[k]
            for c in compare_cols:
                v_s = srow[c] if c in srow else None
                v_t = trow[c] if c in trow else None
                if (pd.isna(v_s) and pd.isna(v_t)):
                    continue
                if pd.isna(v_s) != pd.isna(v_t) or v_s != v_t:
                    diffs.append(c)
            if diffs:
                stats["modified"] += 1
                for c in diffs:
                    per_col_mod[c] += 1
                change = "modified"
            else:
                stats["same"] += 1
                continue

        key_tuple = k if isinstance(k, tuple) else (k,)
        row = {
            "key": " | ".join(map(lambda x: "" if pd.isna(x) else str(x), key_tuple)),
            "change": change
        }
        for c in compare_cols:
            row[f"{c}_src"] = (src_idxed.loc[k][c] if in_s and c in src_idxed.columns else None)
            row[f"{c}_tgt"] = (tgt_idxed.loc[k][c] if in_t and c in tgt_idxed.columns else None)
        rows.append(row)

    diff_df = pd.DataFrame(rows)
    stats["per_column_modified"] = per_col_mod

    flips = {}
    for c in compare_cols:
        try:
            common = src_idxed.index.intersection(tgt_idxed.index)
            if len(common) == 0:
                flips[c] = {"src_in_tgt_out": 0, "src_out_tgt_in": 0}
                continue
            svals = src_idxed.loc[common, c]
            tvals = tgt_idxed.loc[common, c]
            s_in = is_in_stock(svals)
            t_in = is_in_stock(tvals)
            src_in_tgt_out = int((s_in & (~t_in)).sum())
            src_out_tgt_in = int(((~s_in) & t_in).sum())
            flips[c] = {"src_in_tgt_out": src_in_tgt_out, "src_out_tgt_in": src_out_tgt_in}
        except Exception:
            flips[c] = {"src_in_tgt_out": 0, "src_out_tgt_in": 0}

    stats["in_stock_flips"] = flips
    return diff_df, stats

def apply_sync_multi(
    conn,
    df_src: pd.DataFrame,
    df_tgt: pd.DataFrame,
    src_keys: List[str],
    tgt_keys: List[str],
    sync_cols: List[str],
    target_table: str,
) -> int:
    """
    Upsert from src into tgt on composite keys (src_keys -> tgt_keys).
    Only updates columns present in BOTH frames from sync_cols.
    """
    df_src = normalize_columns(df_src)
    df_tgt = normalize_columns(df_tgt)

    std_src_keys = [f"__K{i}__" for i in range(len(src_keys))]
    std_tgt_keys = [f"__K{i}__" for i in range(len(tgt_keys))]
    s = df_src.copy()
    t = df_tgt.copy()
    for old, new in zip(src_keys, std_src_keys):
        s[new] = s[old]
    for old, new in zip(tgt_keys, std_tgt_keys):
        t[new] = t[old]

    s = as_multi_index(s, std_src_keys)
    t = as_multi_index(t, std_tgt_keys)

    sync_cols_final = [c for c in sync_cols if (c in s.columns and c in t.columns)]

    common = t.index.intersection(s.index)
    added = s.index.difference(t.index)

    if len(common) > 0 and sync_cols_final:
        t.loc[common, sync_cols_final] = s.loc[common, sync_cols_final].values

    if len(added) > 0:
        to_add = s.loc[added, sync_cols_final].copy()
        for col in t.columns:
            if col not in to_add.columns:
                to_add[col] = pd.NA
        to_add = to_add[t.columns]
        t = pd.concat([t, to_add])

    out = t.reset_index()

    for i, tgt_col in enumerate(tgt_keys):
        out[tgt_col] = out[f"__K{i}__"]
        out.drop(columns=[f"__K{i}__"], inplace=True)

    out.to_sql(target_table, conn, if_exists="replace", index=False)
    return int(len(common) + len(added))

def render_report(stats: dict, compare_cols: List[str]) -> str:
    lines = []
    lines.append("# Inventory Comparison Report")
    lines.append(f"- Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append("## Totals")
    lines.append(f"- Source rows: {stats.get('total_src', 0)}")
    lines.append(f"- Target rows: {stats.get('total_tgt', 0)}")
    lines.append(f"- Added: {stats.get('added', 0)}")
    lines.append(f"- Removed: {stats.get('removed', 0)}")
    lines.append(f"- Modified: {stats.get('modified', 0)}")
    lines.append(f"- Unchanged: {stats.get('same', 0)}")
    lines.append("")
    lines.append("## Per-Column Modified Counts")
    pcm = stats.get("per_column_modified", {})
    if pcm:
        for c in compare_cols:
            if c in pcm:
                lines.append(f"- {c}: {pcm[c]}")
    else:
        lines.append("- (no per-column data)")
    lines.append("")
    lines.append("## In-Stock / Out-of-Stock Flips")
    flips = stats.get("in_stock_flips", {})
    if flips:
        for c, v in flips.items():
            lines.append(f"- {c}: src_in & tgt_out = {v.get('src_in_tgt_out', 0)}, src_out & tgt_in = {v.get('src_out_tgt_in', 0)}")
    else:
        lines.append("- (no flip data)")
    lines.append("")
    return "\n".join(lines)

class InventoryGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Inventory Sync (Tkinter)")
        self.geometry("1200x720")

        self.cfg = load_config()

        self.df_src_file: Optional[pd.DataFrame] = None
        self.df_tgt_file: Optional[pd.DataFrame] = None
        self.df_src: Optional[pd.DataFrame] = None
        self.df_tgt: Optional[pd.DataFrame] = None

        self.src_key1: Optional[str] = None
        self.src_key2: Optional[str] = None
        self.tgt_key1: Optional[str] = None
        self.tgt_key2: Optional[str] = None
        self.compare_cols: List[str] = list(self.cfg.get("columns_to_compare", []) or ["FreeStock"])

        self._build_ui()

        self._setup_tree(columns=("key", "change", "FreeStock_src", "FreeStock_tgt"))

    def _build_ui(self):
        toolbar = tk.Frame(self, bg="#2c2c2c")
        toolbar.pack(side="top", fill="x")

        btn = lambda txt, cmd: tk.Button(toolbar, text=txt, command=cmd, padx=10, pady=6)
        btn("Import Source", self.import_source).pack(side="left", padx=4, pady=6)
        btn("Import Target", self.import_target).pack(side="left", padx=4, pady=6)
        btn("Select Keys / Columns", self.select_keys_and_columns).pack(side="left", padx=4, pady=6)

        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=6)

        btn("Compare", self.compare).pack(side="left", padx=4, pady=6)
        btn("Apply Sync", self.apply_sync_action).pack(side="left", padx=4, pady=6)
        btn("Export Diff CSV", self.export_csv).pack(side="left", padx=4, pady=6)
        btn("Export Report", self.export_report).pack(side="left", padx=4, pady=6)

        self.stats_label = tk.Label(self, text="Stats: -", anchor="w")
        self.stats_label.pack(fill="x", padx=6, pady=(6, 0))

        self.tree = ttk.Treeview(self, show="headings")
        self.tree.pack(fill="both", expand=True, padx=6, pady=6)

        report_frame = tk.LabelFrame(self, text="Report")
        report_frame.pack(fill="x", padx=6, pady=(0, 6))
        self.report_text = tk.Text(report_frame, height=10, wrap="word")
        self.report_text.pack(fill="both", expand=True)

        self.log = tk.Text(self, height=6, bg="#111", fg="#0f0", insertbackground="white")
        self.log.pack(fill="x", padx=6, pady=(0, 6))

    def _setup_tree(self, columns: Tuple[str, ...]):
        for c in self.tree["columns"]:
            self.tree.heading(c, text="")
            self.tree.column(c, width=0)
        self.tree["columns"] = columns
        for c in columns:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=160, anchor="w")

    def log_msg(self, msg):
        self.log.insert("end", f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
        self.log.see("end")

    def import_source(self):
        path = filedialog.askopenfilename(
            title="Import Source (CSV/Excel)",
            filetypes=[("CSV/Excel", "*.csv;*.txt;*.xlsx;*.xls;*.xlsm"), ("All files", "*.*")]
        )
        if not path:
            return
        try:
            df = read_file_any(path)
            self.df_src_file = normalize_columns(df)
            self.log_msg(f"Loaded SOURCE file: {path} ({len(df)} rows)")
        except Exception as e:
            messagebox.showerror("Import Source", str(e))

    def import_target(self):
        path = filedialog.askopenfilename(
            title="Import Target (CSV/Excel)",
            filetypes=[("CSV/Excel", "*.csv;*.txt;*.xlsx;*.xls;*.xlsm"), ("All files", "*.*")]
        )
        if not path:
            return
        try:
            df = read_file_any(path)
            self.df_tgt_file = normalize_columns(df)
            self.log_msg(f"Loaded TARGET file: {path} ({len(df)} rows)")
        except Exception as e:
            messagebox.showerror("Import Target", str(e))

    def _peek_columns(self, side: str) -> List[str]:
        """Return available columns for 'src' or 'tgt' based on file or DB."""
        if side == "src":
            if self.df_src_file is not None:
                return self.df_src_file.columns.tolist()
            db_file = sqlite_path(self.cfg["database_url"])
            src_tbl = self.cfg["source_table"]["name"]
            with sqlite3.connect(db_file) as conn:
                return get_sqlite_columns(conn, src_tbl)
        else:
            if self.df_tgt_file is not None:
                return self.df_tgt_file.columns.tolist()
            db_file = sqlite_path(self.cfg["database_url"])
            tgt_tbl = self.cfg["target_table"]["name"]
            with sqlite3.connect(db_file) as conn:
                return get_sqlite_columns(conn, tgt_tbl)

    def select_keys_and_columns(self):
        cols_src = self._peek_columns("src")
        cols_tgt = self._peek_columns("tgt")

        win = tk.Toplevel(self)
        win.title("Select Keys & Columns")
        win.grab_set()

        container = ttk.Frame(win, padding=10)
        container.grid(row=0, column=0, sticky="nsew")
        win.grid_rowconfigure(0, weight=1)
        win.grid_columnconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)
        container.grid_columnconfigure(1, weight=1)
        container.grid_columnconfigure(2, weight=1)

        src_k1_d = self.src_key1 or guess_primary_key(cols_src) or (cols_src[0] if cols_src else "")
        src_k2_d = self.src_key2 or guess_account_key(cols_src) or (cols_src[1] if len(cols_src) > 1 else src_k1_d)

        tgt_k1_d = self.tgt_key1 or guess_primary_key(cols_tgt) or (cols_tgt[0] if cols_tgt else "")
        tgt_k2_d = self.tgt_key2 or guess_account_key(cols_tgt) or (cols_tgt[1] if len(cols_tgt) > 1 else tgt_k1_d)

        ttk.Label(container, text="Source Primary Key").grid(row=0, column=0, sticky="w")
        src_k1 = ttk.Combobox(container, values=cols_src, state="readonly")
        src_k1.set(src_k1_d)
        src_k1.grid(row=1, column=0, sticky="ew", padx=(0, 8))

        ttk.Label(container, text="Source Account Key").grid(row=2, column=0, sticky="w")
        src_k2 = ttk.Combobox(container, values=cols_src, state="readonly")
        src_k2.set(src_k2_d)
        src_k2.grid(row=3, column=0, sticky="ew", padx=(0, 8), pady=(0, 8))

        ttk.Label(container, text="Target Primary Key").grid(row=0, column=1, sticky="w")
        tgt_k1 = ttk.Combobox(container, values=cols_tgt, state="readonly")
        tgt_k1.set(tgt_k1_d)
        tgt_k1.grid(row=1, column=1, sticky="ew", padx=(0, 8))

        ttk.Label(container, text="Target Account Key").grid(row=2, column=1, sticky="w")
        tgt_k2 = ttk.Combobox(container, values=cols_tgt, state="readonly")
        tgt_k2.set(tgt_k2_d)
        tgt_k2.grid(row=3, column=1, sticky="ew", padx=(0, 8), pady=(0, 8))

        inter = sorted(set(cols_src).intersection(set(cols_tgt)))
        default_compare = [c for c in (self.compare_cols or []) if c in inter]
        if not default_compare:
            if "FreeStock" in inter:
                default_compare = ["FreeStock"]
            elif inter:
                default_compare = inter[:1]

        ttk.Label(container, text="Stock / Compare Columns (multi-select)").grid(row=0, column=2, sticky="w")
        lb = tk.Listbox(container, selectmode="multiple", exportselection=False, height=14)
        for c in inter:
            lb.insert("end", c)
        lb.grid(row=1, column=2, rowspan=3, sticky="nsew")

        idx_map = {lb.get(i): i for i in range(lb.size())}
        for c in default_compare:
            if c in idx_map:
                lb.selection_set(idx_map[c])

        btn_frame = ttk.Frame(container)
        btn_frame.grid(row=4, column=0, columnspan=3, sticky="e", pady=(10, 0))
        ttk.Button(btn_frame, text="Cancel", command=win.destroy).pack(side="right", padx=6)
        def save_and_close():
            self.src_key1 = src_k1.get().strip()
            self.src_key2 = src_k2.get().strip()
            self.tgt_key1 = tgt_k1.get().strip()
            self.tgt_key2 = tgt_k2.get().strip()
            self.compare_cols = [lb.get(i) for i in lb.curselection()]
            if not self.compare_cols:
                messagebox.showwarning("Keys/Columns", "Please select at least one compare column.")
                return
            self.log_msg(f"Selected keys and columns. Compare: {self.compare_cols}")
            win.destroy()
        ttk.Button(btn_frame, text="Save", command=save_and_close).pack(side="right")

    def _load_src_df(self) -> pd.DataFrame:
        if self.df_src_file is not None:
            return self.df_src_file
        db_file = sqlite_path(self.cfg["database_url"])
        src_tbl = self.cfg["source_table"]["name"]
        with sqlite3.connect(db_file) as conn:
            return fetch_table_any_columns(conn, src_tbl)

    def _load_tgt_df(self) -> pd.DataFrame:
        if self.df_tgt_file is not None:
            return self.df_tgt_file
        db_file = sqlite_path(self.cfg["database_url"])
        tgt_tbl = self.cfg["target_table"]["name"]
        with sqlite3.connect(db_file) as conn:
            return fetch_table_any_columns(conn, tgt_tbl)

    def _resolve_keys(self, df_src: pd.DataFrame, df_tgt: pd.DataFrame) -> Tuple[List[str], List[str]]:
        if self.src_key1 and self.src_key2 and self.tgt_key1 and self.tgt_key2:
            return [self.src_key1, self.src_key2], [self.tgt_key1, self.tgt_key2]

        def cfg_to_keys(section: dict, df: pd.DataFrame) -> List[str]:
            if "key_columns" in section:
                return [k for k in section["key_columns"] if k in df.columns][:2]
            if "key_column" in section and section["key_column"] in df.columns:
                return [section["key_column"]]
            k1 = guess_primary_key(df.columns) or (df.columns[0] if len(df.columns) else None)
            k2 = guess_account_key(df.columns)
            return [k for k in [k1, k2] if k]

        src_keys = cfg_to_keys(self.cfg["source_table"], df_src)
        tgt_keys = cfg_to_keys(self.cfg["target_table"], df_tgt)

        if len(src_keys) != len(tgt_keys):
            n = min(len(src_keys), len(tgt_keys))
            src_keys = src_keys[:n]
            tgt_keys = tgt_keys[:n]
        return src_keys, tgt_keys

    def compare(self):
        try:
            df_src = self._load_src_df()
            df_tgt = self._load_tgt_df()
        except Exception as e:
            messagebox.showerror("Compare", f"Failed to load data: {e}")
            return

        self.df_src = normalize_columns(df_src)
        self.df_tgt = normalize_columns(df_tgt)

        src_keys, tgt_keys = self._resolve_keys(self.df_src, self.df_tgt)
        if not src_keys or not tgt_keys:
            messagebox.showwarning("Keys", "Could not resolve key columns. Use 'Select Keys / Columns'.")
            return

        compare_cols = self.compare_cols or self.cfg.get("columns_to_compare", ["FreeStock"])
        self.diff_df, stats = compute_diff_multi(self.df_src, self.df_tgt, src_keys, tgt_keys, compare_cols)

        cols = []
        if len(src_keys) == 2: 
            cols.extend(["key"])
        else:
            cols.extend(["key"])
        cols.append("change")
        for c in compare_cols:
            cols.extend([f"{c}_src", f"{c}_tgt"])
        self._setup_tree(tuple(cols))

        self.tree.delete(*self.tree.get_children())
        for _, row in self.diff_df.iterrows():
            values = [row.get("key", ""), row.get("change", "")]
            for c in compare_cols:
                values.append(row.get(f"{c}_src"))
                values.append(row.get(f"{c}_tgt"))
            self.tree.insert("", "end", values=values)

        self.stats_label.config(text=f"Stats: added={stats.get('added',0)} | removed={stats.get('removed',0)} | modified={stats.get('modified',0)} | same={stats.get('same',0)}")
        self.report_text.delete("1.0", "end")
        self.report_text.insert("end", render_report(stats, compare_cols))
        self.log_msg(f"Compared src({len(self.df_src)}) vs tgt({len(self.df_tgt)}); diff rows: {len(self.diff_df)}")

    def apply_sync_action(self):
        if self.df_src is None or self.df_tgt is None:
            self.log_msg("Nothing to apply. Run Compare first.")
            return
        if self.diff_df is None or self.diff_df.empty:
            self.log_msg("No differences detected; nothing to apply.")
            return

        db_file = sqlite_path(self.cfg["database_url"])
        tgt_tbl = self.cfg["target_table"]["name"]

        sync_cols = self.cfg.get("all_columns_to_sync", [])
        if not sync_cols:
            sync_cols = [c for c in self.df_src.columns if c in self.df_tgt.columns]

        src_keys, tgt_keys = self._resolve_keys(self.df_src, self.df_tgt)

        try:
            with sqlite3.connect(db_file) as conn:
                n_up = apply_sync_multi(conn, self.df_src, self.df_tgt, src_keys, tgt_keys, sync_cols, tgt_tbl)
            self.log_msg(f"Applied sync. Upserted/updated {n_up} rows into {tgt_tbl}")
        except Exception as e:
            messagebox.showerror("Apply Sync", str(e))

    def export_csv(self):
        if getattr(self, "diff_df", None) is None or self.diff_df.empty:
            messagebox.showinfo("Export", "No diff to export")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", title="Save Diff CSV")
        if not path:
            return
        try:
            self.diff_df.to_csv(path, index=False)
            self.log_msg(f"Exported diff to {path}")
        except Exception as e:
            messagebox.showerror("Export Diff CSV", str(e))

    def export_report(self):
        txt = self.report_text.get("1.0", "end").strip()
        if not txt:
            messagebox.showinfo("Export Report", "No report to export. Run Compare first.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".md", title="Save Report (Markdown)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(txt + "\n")
            self.log_msg(f"Exported report to {path}")
        except Exception as e:
            messagebox.showerror("Export Report", str(e))

if __name__ == "__main__":
    app = InventoryGUI()
    app.mainloop()
