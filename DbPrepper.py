import os, sys, csv, zipfile, sqlite3, traceback, re
import xml.etree.ElementTree as ET
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_TITLE = "Data Prep Dashboard — Max UI"
DEFAULT_PREVIEW_ROWS = 20
CSV_ENCODINGS = ["Auto", "utf-8-sig", "utf-8", "utf-16", "cp1252", "latin-1"]

def ts_tag():
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def safe_log(widget, msg):
    try:
        widget.configure(state="normal")
        widget.insert("end", msg.rstrip() + "\n")
        widget.see("end")
        widget.configure(state="disabled")
    except Exception:
        pass

_snake_non_alnum = re.compile(r"[^0-9a-zA-Z]+")
_multi_us = re.compile(r"_+")
def to_snake(name: str) -> str:
    s = (name or "").strip()
    s = _snake_non_alnum.sub("_", s).strip("_").lower()
    s = _multi_us.sub("_", s)
    if not s:
        s = "col"
    if s[0].isdigit():
        s = "col_" + s
    return s

def dedupe_headers(headers):
    seen = {}
    out = []
    for h in headers:
        base = to_snake(h)
        n = seen.get(base, 0) + 1
        seen[base] = n
        out.append(base if n == 1 else f"{base}_{n}")
    return out

def _read_csv_with_encodings(path, enc_pref="Auto", max_rows=None):
    tried = [e for e in CSV_ENCODINGS if e != "Auto"] if enc_pref != "Auto" else ["utf-8-sig", "utf-8", "utf-16", "cp1252", "latin-1"]
    last_exc = None
    for enc in tried:
        try:
            out = []
            with open(path, newline="", encoding=enc, errors="replace") as f:
                r = csv.reader(f)
                for i, row in enumerate(r):
                    out.append([("" if v is None else str(v)) for v in row])
                    if max_rows and i >= max_rows:
                        break
            if out:
                return out, enc
        except Exception as e:
            last_exc = e
            continue
    if last_exc:
        raise last_exc
    raise ValueError("CSV read failed with all encodings")

def load_csv_preview(path, enc_pref, max_rows):
    return _read_csv_with_encodings(path, enc_pref, max_rows)

def load_csv_full(path, enc_pref):
    return _read_csv_with_encodings(path, enc_pref, None)

NS_MAIN = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
NS_REL  = {"rel": "http://schemas.openxmlformats.org/package/2006/relationships"}

def xlsx_list_sheets(z: zipfile.ZipFile):
    with z.open("xl/workbook.xml") as f:
        tree = ET.parse(f)
    root = tree.getroot()
    sheets = []
    for s in root.findall("main:sheets/main:sheet", NS_MAIN):
        name = s.attrib.get("name", "Sheet")
        r_id = s.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        sheets.append((name, r_id))
    with z.open("xl/_rels/workbook.xml.rels") as f:
        rels = ET.parse(f).getroot()
    rmap = {}
    for r in rels.findall("rel:Relationship", NS_REL):
        rmap[r.attrib.get("Id")] = r.attrib.get("Target")
    resolved = []
    for name, rid in sheets:
        target = (rmap.get(rid) or "worksheets/sheet1.xml").lstrip("/")
        resolved.append((name, target))
    return resolved

def _xlsx_load_shared_strings(z):
    try:
        with z.open("xl/sharedStrings.xml") as f:
            root = ET.parse(f).getroot()
        return ["".join(t.itertext()) for t in root.findall(".//main:si", NS_MAIN)]
    except KeyError:
        return []
    except Exception:
        return []

def _col_letters_to_index(col_ref):
    res = 0
    for ch in col_ref:
        if "A" <= ch <= "Z":
            res = res * 26 + (ord(ch) - ord("A") + 1)
        elif "a" <= ch <= "z":
            res = res * 26 + (ord(ch) - ord("a") + 1)
    return max(res - 1, 0)

def _xlsx_read_sheet(z, target_rel_path, shared, max_rows=None):
    sheet_path = "xl/" + target_rel_path.lstrip("/")
    with z.open(sheet_path) as f:
        root = ET.parse(f).getroot()
    rows = []
    for r_i, row in enumerate(root.findall("main:sheetData/main:row", NS_MAIN)):
        max_ci = -1
        parsed = []
        for c in row.findall("main:c", NS_MAIN):
            addr = c.attrib.get("r", "A1")
            letters = "".join([ch for ch in addr if ch.isalpha()])
            ci = _col_letters_to_index(letters)
            max_ci = max(max_ci, ci)
            v = c.find("main:v", NS_MAIN)
            val = ""
            if v is not None and v.text is not None:
                if c.attrib.get("t") == "s":
                    try:
                        idx = int(v.text)
                        val = shared[idx] if 0 <= idx < len(shared) else v.text
                    except Exception:
                        val = v.text
                else:
                    val = v.text
            parsed.append((ci, str(val)))
        row_vals = ["" for _ in range(max_ci + 1)] if max_ci >= 0 else []
        for ci, val in parsed:
            if 0 <= ci < len(row_vals):
                row_vals[ci] = val
        rows.append(row_vals)
        if max_rows and r_i >= max_rows:
            break
    return rows

def load_xlsx_preview(path, sheet_target, max_rows):
    with zipfile.ZipFile(path) as z:
        shared = _xlsx_load_shared_strings(z)
        return _xlsx_read_sheet(z, sheet_target, shared, max_rows)

def load_xlsx_full(path, sheet_target):
    with zipfile.ZipFile(path) as z:
        shared = _xlsx_load_shared_strings(z)
        return _xlsx_read_sheet(z, sheet_target, shared, None)

def preview_slice(data, n):
    if not data:
        return []
    return [data[0]] + data[1:n+1]

def fuzzy_match(headers, target):
    t = (target or "").lower().strip()
    out = []
    for h in headers:
        hn = (h or "").lower().strip()
        score = sum(1 for a, b in zip(hn, t) if a == b)
        if t and (t in hn or score >= max(len(t)-1, 0)):
            out.append(h)
    return out

def detect_duplicates(data, key_columns):
    if not data or not key_columns:
        return []
    hdr = data[0]
    try:
        idx = [hdr.index(c) for c in key_columns]
    except ValueError:
        return []
    seen, dupes = set(), []
    for r in data[1:]:
        key = tuple((r[i] if i < len(r) else "") for i in idx)
        if key in seen:
            dupes.append(r)
        else:
            seen.add(key)
    return dupes

def export_csv(rows, out_path):
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)

def _table_exists(cur, table):
    row = cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?;", (table,)).fetchone()
    return row is not None

def _existing_columns(cur, table):
    return [r[1] for r in cur.execute(f'PRAGMA table_info("{table}")').fetchall()]

def upsert_to_db(rows, db_path, table, *, unique_cols=None, empty_as_null=False, mode="append"):
    """
    mode:
      - 'replace': drop existing table and recreate
      - 'append' : keep table, add missing TEXT columns, then insert
      - 'error'  : raise if table exists
    """
    if not rows or not rows[0]:
        raise ValueError("No data to upsert")
    headers = rows[0]
    body = rows[1:]

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    exists = _table_exists(cur, table)

    if exists and mode == "error":
        conn.close()
        raise ValueError(f"Table '{table}' already exists (mode=error).")

    if exists and mode == "replace":
        cur.execute(f'DROP TABLE IF EXISTS "{table}"')

    if not _table_exists(cur, table):
        cols_def = ", ".join(f'"{c}" TEXT' for c in headers)
        cur.execute(f'CREATE TABLE "{table}" ({cols_def})')
    elif mode == "append":
        current = set(_existing_columns(cur, table))
        for c in headers:
            if c not in current:
                cur.execute(f'ALTER TABLE "{table}" ADD COLUMN "{c}" TEXT')

    if unique_cols:
        idx_name = f"uniq_{table}_" + "_".join([str(abs(hash(c)))[:6] for c in unique_cols])
        cols_list = ", ".join(f'"{c}"' for c in unique_cols if c in headers)
        if cols_list:
            cur.execute(f'CREATE UNIQUE INDEX IF NOT EXISTS "{idx_name}" ON "{table}" ({cols_list})')

    placeholders = ", ".join("?" for _ in headers)
    col_names   = ", ".join(f'"{c}"' for c in headers)
    verb = "INSERT OR REPLACE" if unique_cols else "INSERT"
    sql = f'{verb} INTO "{table}" ({col_names}) VALUES ({placeholders})'

    def norm_row(r):
        vals = [(None if (empty_as_null and v == "") else v) for v in r]
        vals += [""] * max(0, len(headers) - len(vals))
        return tuple(vals[:len(headers)])

    cur.executemany(sql, (norm_row(r) for r in body))
    conn.commit()
    cnt = cur.rowcount if cur.rowcount != -1 else len(body)
    conn.close()
    return cnt

class ScrollableFrame(ttk.Frame):
    def __init__(self, master, *args, **kwargs):
        super().__init__(master, *args, **kwargs)
        style = ttk.Style(self)
        bg = style.lookup("TFrame", "background") or self.winfo_toplevel().cget("background")
        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0, bg=bg)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)

        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vsb.set)

        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vsb.grid(row=0, column=1, sticky="ns")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Shift-MouseWheel>", self._on_wheel_h)

    def _on_wheel(self, e):
        self.canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
    def _on_wheel_h(self, e):
        self.canvas.xview_scroll(int(-1 * (e.delta / 120)), "units")

class DataPrepApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1460x900")
        self.minsize(1200, 720)

        self.bg = "#0f111a"
        self.panel = "#1b1f2a"
        self.accent = "#2f3545"
        self.fg = "#d7dce2"
        self.fg_dim = "#a6acb3"
        self.hl = "#7aa2f7"
        self.warn = "#f78c6c"
        self.err = "#ff5370"
        self.ok = "#c3e88d"

        self.configure(bg=self.bg)
        self._setup_styles()

        self.path_var = tk.StringVar(value="")
        self.preview_rows_var = tk.IntVar(value=DEFAULT_PREVIEW_ROWS)
        self.csv_enc_var = tk.StringVar(value="Auto")
        self.selected_sheet_target = None
        self.selected_sheet_name = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="Ready.")
        self.table_name_var = tk.StringVar(value="records")
        self.unique_as_selected_var = tk.BooleanVar(value=True)
        self.empty_as_null_var = tk.BooleanVar(value=False)
        self.db_mode_var = tk.StringVar(value="append")
        self.filter_var = tk.StringVar(value="")
        self.fuzzy_var = tk.StringVar(value="")
        self.fuzzy_msg_var = tk.StringVar(value="")

        self.headers = []
        self.data_preview = []
        self.data_full = None
        self.col_vars = {}
        self.xlsx_sheet_map = []

        self._build_layout()

    def _setup_styles(self):
        s = ttk.Style()
        try: s.theme_use("clam")
        except Exception: pass
        s.configure("TFrame", background=self.bg)
        s.configure("Panel.TFrame", background=self.panel)
        s.configure("TLabel", background=self.panel, foreground=self.fg)
        s.configure("Dim.TLabel", background=self.panel, foreground=self.fg_dim)
        s.configure("Warn.TLabel", background=self.panel, foreground=self.warn)
        s.configure("Ok.TLabel", background=self.panel, foreground=self.ok)
        s.configure("TButton", background=self.accent, foreground=self.fg, padding=8, relief="flat")
        s.map("TButton", background=[("active", self.hl)])
        s.configure("TEntry", fieldbackground=self.bg, foreground=self.fg)
        s.configure("TLabelframe", background=self.panel, foreground=self.fg)
        s.configure("TLabelframe.Label", background=self.panel, foreground=self.fg)
        s.configure("TCheckbutton", background=self.panel, foreground=self.fg)
        s.configure("Treeview", background=self.bg, fieldbackground=self.bg, foreground=self.fg)
        s.configure("Treeview.Heading", background=self.accent, foreground=self.fg)

    def _build_layout(self):
        tb = ttk.Frame(self, style="Panel.TFrame")
        tb.grid(row=0, column=0, sticky="ew")
        tb.grid_columnconfigure(10, weight=1)

        ttk.Button(tb, text="Open…", command=self.on_open).grid(row=0, column=0, padx=8, pady=8)
        ttk.Button(tb, text="Preview", command=self.on_load_preview).grid(row=0, column=1, padx=8, pady=8)
        ttk.Button(tb, text="Analyze", command=self.on_analyze).grid(row=0, column=2, padx=8, pady=8)
        ttk.Button(tb, text="Export CSV…", command=self.on_export_csv).grid(row=0, column=3, padx=8, pady=8)
        ttk.Button(tb, text="Upsert DB…", command=self.on_upsert).grid(row=0, column=4, padx=8, pady=8)
        ttk.Label(tb, textvariable=self.status_var, style="Dim.TLabel").grid(row=0, column=10, sticky="e", padx=12)

        main = ttk.Frame(self, style="TFrame")
        main.grid(row=1, column=0, sticky="nsew")
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(0, weight=1)
        main.grid_columnconfigure(1, weight=1)

        left = ttk.Frame(main, style="Panel.TFrame")
        left.grid(row=0, column=0, sticky="nsw")
        for i in range(20):
            left.grid_rowconfigure(i, weight=0)
        left.grid_rowconfigure(19, weight=1)
        left.grid_columnconfigure(0, weight=1)

        src = ttk.LabelFrame(left, text="Source", style="TLabelframe")
        src.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))
        src.grid_columnconfigure(0, weight=1)

        ttk.Entry(src, textvariable=self.path_var).grid(row=0, column=0, sticky="ew", padx=8, pady=6)
        ttk.Button(src, text="Browse…", command=self.on_open).grid(row=0, column=1, padx=8, pady=6)

        ttk.Label(src, text="CSV encoding").grid(row=1, column=0, sticky="w", padx=8)
        ttk.Combobox(src, state="readonly", values=CSV_ENCODINGS, textvariable=self.csv_enc_var, width=14).grid(row=1, column=1, sticky="e", padx=8)

        ttk.Label(src, text="Preview rows").grid(row=2, column=0, sticky="w", padx=8)
        ttk.Spinbox(src, from_=5, to=500, width=8, textvariable=self.preview_rows_var).grid(row=2, column=1, sticky="e", padx=8, pady=(0,6))

        self.sheet_combo = ttk.Combobox(src, state="disabled", values=[], textvariable=self.selected_sheet_name)
        ttk.Label(src, text="Excel sheet").grid(row=3, column=0, sticky="w", padx=8)
        self.sheet_combo.grid(row=3, column=1, sticky="e", padx=8, pady=(0,8))
        self.sheet_combo.bind("<<ComboboxSelected>>", lambda e: self._on_sheet_changed())

        ttk.Button(src, text="Load Preview", command=self.on_load_preview).grid(row=4, column=0, columnspan=2, sticky="ew", padx=8, pady=(6,8))

        fz = ttk.LabelFrame(left, text="Fuzzy Column Search", style="TLabelframe")
        fz.grid(row=1, column=0, sticky="ew", padx=12, pady=8)
        fz.grid_columnconfigure(0, weight=1)
        ttk.Entry(fz, textvariable=self.fuzzy_var).grid(row=0, column=0, sticky="ew", padx=8, pady=(8,4))
        ttk.Button(fz, text="Find", command=self.on_fuzzy_find).grid(row=0, column=1, padx=8, pady=(8,4))
        ttk.Label(fz, textvariable=self.fuzzy_msg_var, style="Dim.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", padx=8, pady=(0,8))

        colgrp = ttk.LabelFrame(left, text="Columns", style="TLabelframe")
        colgrp.grid(row=2, column=0, sticky="nsew", padx=12, pady=8)
        colgrp.grid_rowconfigure(1, weight=1)
        colgrp.grid_columnconfigure(0, weight=1)
        btns = ttk.Frame(colgrp, style="Panel.TFrame")
        btns.grid(row=0, column=0, sticky="ew", padx=8, pady=(8,4))
        ttk.Button(btns, text="Select All", command=lambda: self._toggle_all_columns(True)).grid(row=0, column=0, padx=(0,6))
        ttk.Button(btns, text="Select None", command=lambda: self._toggle_all_columns(False)).grid(row=0, column=1, padx=(6,0))
        self.cols_scroll = ScrollableFrame(colgrp)
        self.cols_scroll.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0,8))

        dbgrp = ttk.LabelFrame(left, text="Database", style="TLabelframe")
        dbgrp.grid(row=3, column=0, sticky="ew", padx=12, pady=(8, 12))
        dbgrp.grid_columnconfigure(1, weight=1)
        ttk.Label(dbgrp, text="Table").grid(row=0, column=0, sticky="w", padx=8)
        ttk.Entry(dbgrp, textvariable=self.table_name_var).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Label(dbgrp, text="If table exists").grid(row=1, column=0, sticky="w", padx=8, pady=(6,0))
        ttk.Combobox(dbgrp, state="readonly",
                     values=["append", "replace", "error"],
                     textvariable=self.db_mode_var, width=10).grid(row=1, column=1, sticky="w", padx=8, pady=(6,0))
        ttk.Checkbutton(dbgrp, text="Use selected columns as unique key", variable=self.unique_as_selected_var).grid(row=2, column=0, columnspan=2, sticky="w", padx=8, pady=(6,0))
        ttk.Checkbutton(dbgrp, text="Treat empty strings as NULL", variable=self.empty_as_null_var).grid(row=3, column=0, columnspan=2, sticky="w", padx=8, pady=(0,8))

        right = ttk.Notebook(main)
        right.grid(row=0, column=1, sticky="nsew", padx=12, pady=12)
        main.grid_rowconfigure(0, weight=1)
        main.grid_columnconfigure(1, weight=1)

        self.tab_data = ttk.Frame(right, style="TFrame")
        right.add(self.tab_data, text="Data")
        self._build_tab_data(self.tab_data)
      
        self.tab_analysis = ttk.Frame(right, style="TFrame")
        right.add(self.tab_analysis, text="Analysis")
        self._build_tab_analysis(self.tab_analysis)

        self.tab_settings = ttk.Frame(right, style="TFrame")
        right.add(self.tab_settings, text="Settings")
        self._build_tab_settings(self.tab_settings)

        self.tab_logs = ttk.Frame(right, style="TFrame")
        right.add(self.tab_logs, text="Logs")
        self._build_tab_logs(self.tab_logs)

        self.status_bar = ttk.Label(self, textvariable=self.status_var, anchor="w", background=self.panel, foreground=self.fg)
        self.status_bar.grid(row=2, column=0, sticky="ew")

    def _build_tab_data(self, parent):
        top = ttk.Frame(parent, style="Panel.TFrame")
        top.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        top.grid_columnconfigure(2, weight=1)

        ttk.Label(top, text="Filter").grid(row=0, column=0, padx=(8,6))
        ent = ttk.Entry(top, textvariable=self.filter_var)
        ent.grid(row=0, column=1, sticky="ew", padx=(0,8))
        ent.bind("<KeyRelease>", lambda e: self._apply_filter())
        ttk.Button(top, text="Refresh", command=lambda: self._render_tree(self._filtered_preview_rows(apply_filter=True))).grid(row=0, column=2, sticky="e", padx=8)

        frame = ttk.Frame(parent, style="Panel.TFrame")
        frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0,8))
        parent.grid_rowconfigure(1, weight=1)
        parent.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(frame, columns=(), show="headings")
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscroll=vsb.set, xscroll=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        info = ttk.Frame(parent, style="Panel.TFrame")
        info.grid(row=2, column=0, sticky="ew", padx=8, pady=(0,8))
        info.grid_columnconfigure(0, weight=1)
        self.stats_label = ttk.Label(info, text="No data loaded.", style="Dim.TLabel")
        self.stats_label.grid(row=0, column=0, sticky="w", padx=8, pady=6)

    def _build_tab_analysis(self, parent):
        frame = ttk.Frame(parent, style="Panel.TFrame")
        frame.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        parent.grid_rowconfigure(0, weight=1)
        parent.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        self.tree_dupes = ttk.Treeview(frame, columns=(), show="headings")
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.tree_dupes.yview)
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=self.tree_dupes.xview)
        self.tree_dupes.configure(yscroll=vsb.set, xscroll=hsb.set)
        self.tree_dupes.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        self.analysis_label = ttk.Label(parent, text="Run Analyze to populate duplicates.", style="Dim.TLabel")
        self.analysis_label.grid(row=1, column=0, sticky="w", padx=8, pady=(0,8))

    def _build_tab_settings(self, parent):
        grp = ttk.LabelFrame(parent, text="Preview & Import", style="TLabelframe")
        grp.grid(row=0, column=0, sticky="ew", padx=12, pady=12)
        grp.grid_columnconfigure(1, weight=1)

        ttk.Label(grp, text="Preview rows").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        ttk.Spinbox(grp, from_=5, to=1000, width=8, textvariable=self.preview_rows_var).grid(row=0, column=1, sticky="w", padx=8, pady=6)

        ttk.Label(grp, text="CSV encoding override").grid(row=1, column=0, sticky="w", padx=8, pady=6)
        ttk.Combobox(grp, state="readonly", values=CSV_ENCODINGS, textvariable=self.csv_enc_var, width=14).grid(row=1, column=1, sticky="w", padx=8, pady=6)

        ttk.Label(grp, text="Excel sheet (if .xlsx)").grid(row=2, column=0, sticky="w", padx=8, pady=6)
        c = ttk.Combobox(grp, state="readonly", values=[], textvariable=self.selected_sheet_name, width=28)
        c.grid(row=2, column=1, sticky="w", padx=8, pady=6)
        c.bind("<<ComboboxSelected>>", lambda e: self._on_sheet_changed())
        self.settings_sheet_combo = c

        db = ttk.LabelFrame(parent, text="Database Upsert", style="TLabelframe")
        db.grid(row=1, column=0, sticky="ew", padx=12, pady=(0,12))
        db.grid_columnconfigure(1, weight=1)
        ttk.Label(db, text="Table name").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        ttk.Entry(db, textvariable=self.table_name_var).grid(row=0, column=1, sticky="ew", padx=8, pady=6)
        ttk.Label(db, text="If table exists").grid(row=1, column=0, sticky="w", padx=8, pady=6)
        ttk.Combobox(db, state="readonly",
                     values=["append", "replace", "error"],
                     textvariable=self.db_mode_var, width=10).grid(row=1, column=1, sticky="w", padx=8, pady=6)
        ttk.Checkbutton(db, text="Use selected columns as unique key", variable=self.unique_as_selected_var).grid(row=2, column=0, columnspan=2, sticky="w", padx=8, pady=6)
        ttk.Checkbutton(db, text="Treat empty strings as NULL", variable=self.empty_as_null_var).grid(row=3, column=0, columnspan=2, sticky="w", padx=8, pady=6)

    def _build_tab_logs(self, parent):
        text = tk.Text(parent, height=10, wrap="word", bg=self.bg, fg=self.fg, insertbackground=self.hl)
        text.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        text.configure(state="disabled")
        parent.grid_rowconfigure(0, weight=1)
        parent.grid_columnconfigure(0, weight=1)
        self.log_widget = text

    def on_open(self):
        path = filedialog.askopenfilename(
            title="Select data file",
            filetypes=[("Excel workbook", "*.xlsx"), ("CSV file", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        self.path_var.set(path)
        self._discover_xlsx_sheets_if_any(path)

    def _discover_xlsx_sheets_if_any(self, path):
        self.xlsx_sheet_map = []
        self.selected_sheet_target = None
        self.selected_sheet_name.set("")
        self.sheet_combo.configure(state="disabled", values=[])
        self.settings_sheet_combo.configure(state="readonly", values=[])

        if path.lower().endswith(".xlsx") and os.path.exists(path):
            try:
                with zipfile.ZipFile(path) as z:
                    self.xlsx_sheet_map = xlsx_list_sheets(z)
                names = [n for (n, _) in self.xlsx_sheet_map]
                if names:
                    self.selected_sheet_name.set(names[0])
                    self.selected_sheet_target = self._sheet_target_by_name(names[0])
                    self.sheet_combo.configure(state="readonly", values=names)
                    self.settings_sheet_combo.configure(values=names)
            except Exception as e:
                safe_log(self.log_widget, f"[warn] Failed to enumerate sheets: {e}")

    def _sheet_target_by_name(self, name):
        for n, t in self.xlsx_sheet_map:
            if n == name:
                return t
        return None

    def _on_sheet_changed(self):
        name = self.selected_sheet_name.get().strip()
        self.selected_sheet_target = self._sheet_target_by_name(name)

    def _apply_snake_headers(self, data):
        """Mutates data[0] to snake_case, returns the new header list."""
        raw = data[0]
        snake = dedupe_headers(raw)
        data[0] = snake
        return snake

    def on_load_preview(self):
        path = self.path_var.get().strip()
        if not path or not os.path.exists(path):
            messagebox.showerror("Error", "File not found.")
            return
        try:
            n = max(1, int(self.preview_rows_var.get()))
        except Exception:
            n = DEFAULT_PREVIEW_ROWS
            self.preview_rows_var.set(n)

        try:
            if path.lower().endswith(".csv"):
                data, used_enc = load_csv_preview(path, self.csv_enc_var.get(), n)
                safe_log(self.log_widget, f"[info] CSV preview loaded using encoding={used_enc}")
            elif path.lower().endswith(".xlsx"):
                target = self.selected_sheet_target or "worksheets/sheet1.xml"
                data = load_xlsx_preview(path, target, n)
                safe_log(self.log_widget, f"[info] XLSX preview loaded from sheet={self.selected_sheet_name.get() or 'sheet1'}")
            else:
                messagebox.showerror("Error", "Unsupported file. Use .csv or .xlsx")
                return
            if not data:
                messagebox.showwarning("No Data", "No rows found.")
                return

            self.headers = self._apply_snake_headers(data)
            self.data_preview = data

            self._build_column_checklist(self.headers)
            self._render_tree(self._filtered_preview_rows(apply_filter=True))
            self._update_stats()
            self.status_var.set("Preview loaded.")
        except Exception as e:
            self._show_exception("Load preview failed", e)

    def on_fuzzy_find(self):
        if not self.headers:
            self.fuzzy_msg_var.set("Load data first.")
            return
        matches = fuzzy_match(self.headers, self.fuzzy_var.get())
        self.fuzzy_msg_var.set(("Matches: " + ", ".join(matches)) if matches else "No matches.")

    def on_analyze(self):
        if not self.path_var.get():
            messagebox.showerror("Error", "Choose a file first.")
            return
        sel = self._selected_columns()
        if not sel:
            messagebox.showerror("Error", "Select at least one column.")
            return
        try:
            if self.data_full is None:
                if self.path_var.get().lower().endswith(".csv"):
                    self.data_full, used = load_csv_full(self.path_var.get(), self.csv_enc_var.get())
                    safe_log(self.log_widget, f"[info] CSV full load encoding={used}")
                else:
                    tgt = self.selected_sheet_target or "worksheets/sheet1.xml"
                    self.data_full = load_xlsx_full(self.path_var.get(), tgt)
                    safe_log(self.log_widget, f"[info] XLSX full load from sheet={self.selected_sheet_name.get() or 'sheet1'}")
                self._apply_snake_headers(self.data_full)

            hdr = self.data_full[0]
            idx = [hdr.index(c) for c in sel if c in hdr]
            filtered_full = [sel] + [[(r[i] if i < len(r) else "") for i in idx] for r in self.data_full[1:]]
            dupes = detect_duplicates(filtered_full, sel)

            self.analysis_label.configure(text=f"Duplicates found: {len(dupes)}", style=("Warn.TLabel" if dupes else "Ok.TLabel"))
            self._render_tree_generic(self.tree_dupes, [sel] + dupes)
            self._render_tree(preview_slice(filtered_full, self.preview_rows_var.get()))
            self.status_var.set("Analyze complete.")
        except Exception as e:
            self._show_exception("Analyze failed", e)

    def on_export_csv(self):
        if not self.headers or not self._selected_columns():
            messagebox.showerror("Error", "Load preview and select columns first.")
            return
        out = filedialog.asksaveasfilename(
            title="Export CSV",
            defaultextension=".csv",
            filetypes=[("CSV file", "*.csv")],
            initialfile=f"cleaned_{ts_tag()}.csv",
        )
        if not out:
            return
        try:
            rows = self._filtered_full_or_preview_rows(for_export=True, apply_filter=False)
            export_csv(rows, out)
            self.status_var.set(f"Exported {len(rows)-1} rows")
            messagebox.showinfo("Export", f"Exported {len(rows)-1} rows to:\n{out}")
            safe_log(self.log_widget, f"[ok] Exported CSV -> {out}")
        except Exception as e:
            self._show_exception("Export failed", e)

    def on_upsert(self):
        if not self.headers or not self._selected_columns():
            messagebox.showerror("Error", "Load preview and select columns first.")
            return
        db = filedialog.asksaveasfilename(
            title="Select / Create SQLite DB",
            defaultextension=".db",
            filetypes=[("SQLite DB", "*.db *.sqlite")],
            initialfile=f"data_{ts_tag()}.db",
        )
        if not db:
            return
        t = (self.table_name_var.get() or "records").strip()
        if not t.isidentifier():
            messagebox.showerror("Error", "Invalid table name.")
            return
        try:
            rows = self._filtered_full_or_preview_rows(for_export=True, apply_filter=False)
            uniq = self._selected_columns() if self.unique_as_selected_var.get() else None
            cnt = upsert_to_db(
                rows, db, t,
                unique_cols=uniq,
                empty_as_null=self.empty_as_null_var.get(),
                mode=self.db_mode_var.get()
            )
            self.status_var.set(f"Upserted {cnt} rows into {t} (mode={self.db_mode_var.get()})")
            messagebox.showinfo("SQLite", f"Upserted {cnt} rows into '{t}'\nDB: {db}")
            safe_log(self.log_widget, f"[ok] Upserted {cnt} rows into {t} ({db}) mode={self.db_mode_var.get()}")
        except Exception as e:
            self._show_exception("Upsert failed", e)

    def _build_column_checklist(self, headers):
        for w in list(self.cols_scroll.inner.children.values()):
            w.destroy()
        self.col_vars.clear()
        for i, h in enumerate(headers):
            var = tk.BooleanVar(value=True)
            cb = ttk.Checkbutton(self.cols_scroll.inner, text=h or f"(col {i})", variable=var, command=self._on_col_toggle)
            cb.grid(row=i, column=0, sticky="w", padx=6, pady=2)
            self.col_vars[h] = var

    def _on_col_toggle(self):
        if self.data_preview:
            self._render_tree(self._filtered_preview_rows(apply_filter=True))
            self._update_stats()

    def _toggle_all_columns(self, state: bool):
        for var in self.col_vars.values():
            var.set(state)
        self._on_col_toggle()

    def _selected_columns(self):
        return [h for h, v in self.col_vars.items() if v.get()]

    def _apply_filter(self):
        self._render_tree(self._filtered_preview_rows(apply_filter=True))

    def _filtered_preview_rows(self, apply_filter=False):
        if not self.data_preview:
            return []
        sel = self._selected_columns()
        hdr = self.data_preview[0]
        idx = [hdr.index(c) for c in sel if c in hdr]
        body = self.data_preview[1:]
        rows = [sel] + [[(r[i] if i < len(r) else "") for i in idx] for r in body]
        if apply_filter:
            q = (self.filter_var.get() or "").lower().strip()
            if q:
                flt = [rows[0]] + [r for r in rows[1:] if any((q in (v or "").lower()) for v in r)]
                rows = flt
        return rows

    def _filtered_full_or_preview_rows(self, for_export=False, apply_filter=False):
        sel = self._selected_columns()
        if not sel:
            raise ValueError("No columns selected.")
        if for_export:
            if self.data_full is None:
                path = self.path_var.get()
                if path.lower().endswith(".csv"):
                    self.data_full, _ = load_csv_full(path, self.csv_enc_var.get())
                else:
                    tgt = self.selected_sheet_target or "worksheets/sheet1.xml"
                    self.data_full = load_xlsx_full(path, tgt)
                self._apply_snake_headers(self.data_full)
            src = self.data_full
        else:
            src = self.data_preview

        hdr = src[0]
        idx = [hdr.index(c) for c in sel if c in hdr]
        body = src[1:]
        rows = [sel] + [[(r[i] if i < len(r) else "") for i in idx] for r in body]

        if apply_filter:
            q = (self.filter_var.get() or "").lower().strip()
            if q:
                rows = [rows[0]] + [r for r in rows[1:] if any((q in (v or "").lower()) for v in r)]
        return rows

    def _render_tree(self, rows):
        self._render_tree_generic(self.tree, rows)

    def _render_tree_generic(self, tree, rows):
        for c in tree["columns"]:
            tree.heading(c, text="")
            tree.column(c, width=100, anchor="w")
        tree.delete(*tree.get_children())

        if not rows:
            return
        hdr = rows[0]
        tree["columns"] = [f"c{i}" for i in range(len(hdr))]
        for i, h in enumerate(hdr):
            tree.heading(f"c{i}", text=h)
            tree.column(f"c{i}", anchor="w", width=max(120, min(280, len(h)*10)))
        for r in rows[1:]:
            values = [r[i] if i < len(r) else "" for i in range(len(hdr))]
            tree.insert("", "end", values=values)

    def _update_stats(self):
        if not self.data_preview:
            self.stats_label.config(text="No data loaded.", style="Dim.TLabel")
            return
        rows = len(self.data_preview) - 1
        cols = len(self.data_preview[0])
        sel = len(self._selected_columns())
        msg = f"Preview: {rows} rows, {cols} columns. Selected: {sel}"
        self.stats_label.config(text=msg, style="Dim.TLabel")

    def _show_exception(self, title, exc):
        msg = f"{title}: {exc}"
        self.status_var.set(msg)
        safe_log(self.log_widget, f"[error] {msg}")
        traceback.print_exc()
        messagebox.showerror(title, f"{exc}\n\n{traceback.format_exc()}")

def main():
    app = DataPrepApp()
    app.mainloop()

if __name__ == "__main__":
    main()
