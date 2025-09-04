import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from collections import OrderedDict
from typing import Optional, Dict, List
import pandas as pd

class SimpleDataManager(tk.Tk):
    PREVIEW_DEFAULT_ROWS = 20

    def __init__(self):
        super().__init__()
        self.title("Simple CSV/Excel Manager")
        self.geometry("1100x720")
        self.minsize(900, 560)

        self.source_path: Optional[str] = None
        self.sheet_names: List[str] = []
        self.current_sheet = tk.StringVar(value="")
        self.preview_rows_var = tk.IntVar(value=self.PREVIEW_DEFAULT_ROWS)
        self.output_dir_var = tk.StringVar(value="")
        self.path_var = tk.StringVar(value="")

        self.state: Dict[str, Dict[str, OrderedDict]] = {}
        self.preview_dfs: Dict[str, pd.DataFrame] = {}

        self._setup_style()
        self._build_ui()
        self._bind_keys()

    def _setup_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(".", font=("Segoe UI", 10))
        style.configure("TFrame", background="#fafafa")
        style.configure("Left.TFrame", background="#f5f5f7")
        style.configure("TLabelframe", background="#f5f5f7", borderwidth=1, relief="solid")
        style.configure("TLabelframe.Label", background="#f5f5f7", foreground="#444")
        style.configure("TButton", padding=6)
        style.configure("Accent.TButton", padding=6)
        style.configure("TEntry")
        style.configure("Treeview", rowheight=24)
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))

    def _build_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        left = ttk.Frame(self, style="Left.TFrame", padding=10)
        left.grid(row=0, column=0, sticky="nsw")
        left.grid_columnconfigure(0, weight=1)
        self.left = left

        right = ttk.Frame(self, padding=(6, 8, 8, 8))
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)
        self.right = right

        src = ttk.LabelFrame(left, text="Source", padding=10)
        src.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        src.grid_columnconfigure(1, weight=1)

        ttk.Label(src, text="File:").grid(row=0, column=0, sticky="w", padx=(0, 6))
        path_entry = ttk.Entry(src, textvariable=self.path_var)
        path_entry.grid(row=0, column=1, sticky="ew")
        ttk.Button(src, text="Browse", command=self._browse_source).grid(row=0, column=2, padx=(6, 0))
        ttk.Button(src, text="Load", style="Accent.TButton",
                   command=lambda: self._run_bg(self._load_source)).grid(row=1, column=0, columnspan=3, sticky="ew", pady=(8, 0))

        sch = ttk.LabelFrame(left, text="Sheet & Columns", padding=10)
        sch.grid(row=1, column=0, sticky="nsew", pady=8)
        left.grid_rowconfigure(1, weight=1)

        ttk.Label(sch, text="Sheet:").grid(row=0, column=0, sticky="w")
        self.sheet_combo = ttk.Combobox(sch, textvariable=self.current_sheet, state="readonly")
        self.sheet_combo.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self.sheet_combo.bind("<<ComboboxSelected>>", self._on_sheet_changed)

        prw = ttk.Frame(sch)
        prw.grid(row=2, column=0, sticky="ew")
        prw.grid_columnconfigure(1, weight=1)
        ttk.Label(prw, text="Preview rows:").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(prw, from_=5, to=500, textvariable=self.preview_rows_var, width=6,
                    command=self._refresh_preview).grid(row=0, column=1, sticky="w")

        cols_wrap = ttk.Frame(sch)
        cols_wrap.grid(row=3, column=0, sticky="nsew", pady=(8, 0))
        sch.grid_rowconfigure(3, weight=1)
        cols_wrap.grid_columnconfigure(0, weight=1)

        self.col_tree = ttk.Treeview(cols_wrap, columns=("on", "name"), show="headings", height=12)
        self.col_tree.heading("on", text="Keep")
        self.col_tree.heading("name", text="Column")
        self.col_tree.column("on", width=60, anchor="center", stretch=False)
        self.col_tree.column("name", width=220, anchor="w", stretch=True)
        vsb = ttk.Scrollbar(cols_wrap, orient="vertical", command=self.col_tree.yview)
        self.col_tree.configure(yscrollcommand=vsb.set)
        self.col_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        self.col_tree.bind("<Double-1>", self._toggle_col_from_click)
        self.col_tree.bind("<space>", self._toggle_col_from_key)

        btn_row = ttk.Frame(sch)
        btn_row.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        btn_row.grid_columnconfigure(0, weight=1)
        btn_row.grid_columnconfigure(1, weight=1)
        ttk.Button(btn_row, text="Select All", command=self._select_all_cols).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(btn_row, text="Deselect All", command=self._deselect_all_cols).grid(row=0, column=1, sticky="ew", padx=(4, 0))

        exp = ttk.LabelFrame(left, text="Export", padding=10)
        exp.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        exp.grid_columnconfigure(0, weight=1)
        exp.grid_columnconfigure(1, weight=0)

        ttk.Entry(exp, textvariable=self.output_dir_var).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(exp, text="Browse Dir", command=self._browse_output_dir).grid(row=0, column=1)

        exprow = ttk.Frame(exp)
        exprow.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        exprow.grid_columnconfigure(0, weight=1)
        exprow.grid_columnconfigure(1, weight=1)
        ttk.Button(exprow, text="Export Current → CSV",
                   command=lambda: self._run_bg(self._export_current_to_csv)).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(exprow, text="Export All → CSVs",
                   command=lambda: self._run_bg(self._export_all_to_csvs)).grid(row=0, column=1, sticky="ew", padx=(4, 0))
        ttk.Button(exp, text="Export All → Excel (.xlsx)",
                   command=lambda: self._run_bg(self._export_all_to_excel)).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        self.header_label = ttk.Label(self.right, text="Preview: <no file>", font=("Segoe UI", 14, "bold"))
        self.header_label.grid(row=0, column=0, sticky="w", pady=(0, 6))

        prev_wrap = ttk.Frame(self.right)
        prev_wrap.grid(row=1, column=0, sticky="nsew")
        prev_wrap.grid_columnconfigure(0, weight=1)
        prev_wrap.grid_rowconfigure(0, weight=1)

        self.preview_tree = ttk.Treeview(prev_wrap, columns=(), show="headings")
        vsb2 = ttk.Scrollbar(prev_wrap, orient="vertical", command=self.preview_tree.yview)
        hsb2 = ttk.Scrollbar(prev_wrap, orient="horizontal", command=self.preview_tree.xview)
        self.preview_tree.configure(yscrollcommand=vsb2.set, xscrollcommand=hsb2.set)
        self.preview_tree.grid(row=0, column=0, sticky="nsew")
        vsb2.grid(row=0, column=1, sticky="ns")
        hsb2.grid(row=1, column=0, sticky="ew")

        self._show_preview_placeholder("Open an Excel or CSV file to begin.")

    def _bind_keys(self):
        self.bind_all("<Control-o>", lambda e: self._browse_source())
        self.bind_all("<F5>", lambda e: self._refresh_preview())

    # -------------------- File I/O --------------------
    def _browse_source(self):
        path = filedialog.askopenfilename(
            title="Select Excel or CSV",
            filetypes=[("Excel", "*.xlsx"), ("CSV", "*.csv"), ("All Files", "*.*")]
        )
        if not path:
            return
        self.path_var.set(path)
        self.output_dir_var.set(os.path.dirname(path))

    def _browse_output_dir(self):
        d = filedialog.askdirectory(mustexist=False)
        if d:
            self.output_dir_var.set(d)

    def _load_source(self):
        path = (self.path_var.get() or "").strip()
        if not path:
            messagebox.showwarning("No file", "Select an Excel (.xlsx) or CSV file.")
            return
        if not os.path.isfile(path):
            messagebox.showerror("Not found", f"File not found:\n{path}")
            return

        self.source_path = path
        self.preview_dfs.clear()
        self.state.clear()
        self.sheet_names = []

        try:
            if path.lower().endswith(".xlsx"):
                xls = pd.ExcelFile(path, engine="openpyxl")
                self.sheet_names = xls.sheet_names
                for s in self.sheet_names:
                    df_prev = pd.read_excel(path, sheet_name=s, nrows=max(1, int(self.preview_rows_var.get())),
                                            dtype=str, engine="openpyxl").fillna("")
                    self.preview_dfs[s] = df_prev
                    self.state[s] = {"include": OrderedDict((c, True) for c in df_prev.columns)}
            else:
                name = os.path.splitext(os.path.basename(path))[0]
                df_prev = self._read_csv_with_fallback(path, nrows=max(1, int(self.preview_rows_var.get()))).fillna("")
                self.preview_dfs[name] = df_prev
                self.state[name] = {"include": OrderedDict((c, True) for c in df_prev.columns)}
                self.sheet_names = [name]

            if not self.sheet_names:
                self._show_preview_placeholder("No data found.")
                return

            self._populate_sheet_combo()
            self.current_sheet.set(self.sheet_names[0])
            self.header_label.config(text=f"Preview: {os.path.basename(path)} — {self.sheet_names[0]}")
            self._rebuild_columns_tree(self.sheet_names[0])
            self._refresh_preview()
        except Exception as e:
            messagebox.showerror("Load failed", str(e))

    @staticmethod
    def _read_csv_with_fallback(path: str, nrows: Optional[int] = None) -> pd.DataFrame:
        errors: List[str] = []
        for enc in ("utf-8-sig", "utf-8", "latin1", "cp1252"):
            try:
                return pd.read_csv(path, dtype=str, nrows=nrows, encoding=enc, on_bad_lines="skip")
            except Exception as ex:
                errors.append(f"{enc}: {ex}")
        raise RuntimeError("Could not read CSV with any common encoding:\n" + "\n".join(errors))

    def _populate_sheet_combo(self):
        self.sheet_combo["values"] = self.sheet_names

    def _on_sheet_changed(self, _evt=None):
        s = self.current_sheet.get()
        if not s:
            return
        self.header_label.config(text=f"Preview: {os.path.basename(self.source_path or '<no file>')} — {s}")
        self._rebuild_columns_tree(s)
        self._refresh_preview()

    def _rebuild_columns_tree(self, sheet: str):
        self.col_tree.delete(*self.col_tree.get_children())
        st = self.state.get(sheet) or {"include": OrderedDict()}
        for c, on in st["include"].items():
            self.col_tree.insert("", "end", iid=c, values=("✓" if on else "", c))

    def _toggle_col_from_click(self, _evt=None):
        iid = self.col_tree.focus()
        if iid:
            self._toggle_col(iid)

    def _toggle_col_from_key(self, _evt=None):
        iid = self.col_tree.focus()
        if iid:
            self._toggle_col(iid)

    def _toggle_col(self, col: str):
        s = self.current_sheet.get()
        if not s:
            return
        st = self.state[s]
        st["include"][col] = not st["include"][col]
        self.col_tree.item(col, values=("✓" if st["include"][col] else "", col))
        self._refresh_preview()

    def _select_all_cols(self):
        s = self.current_sheet.get()
        if not s:
            return
        for k in self.state[s]["include"].keys():
            self.state[s]["include"][k] = True
        self._rebuild_columns_tree(s)
        self._refresh_preview()

    def _deselect_all_cols(self):
        s = self.current_sheet.get()
        if not s:
            return
        for k in self.state[s]["include"].keys():
            self.state[s]["include"][k] = False
        self._rebuild_columns_tree(s)
        self._refresh_preview()

    # -------------------- Preview --------------------
    def _show_preview_placeholder(self, text: str):
        self.preview_tree.delete(*self.preview_tree.get_children())
        self.preview_tree.configure(columns=("_"))
        self.preview_tree.heading("_", text=text)
        self.preview_tree.column("_", width=420, anchor="center")

    def _refresh_preview(self):
        s = self.current_sheet.get()
        if not s:
            self._show_preview_placeholder("Open an Excel or CSV file to begin.")
            return
        base = self.preview_dfs.get(s)
        if base is None or base.empty:
            self._show_preview_placeholder("No data to preview.")
            return
        nrows = max(1, int(self.preview_rows_var.get()))
        df = base.head(nrows).copy()

        st = self.state[s]
        keep = [c for c, on in st["include"].items() if on and c in df.columns]
        df = df[keep] if keep else df.iloc[:, 0:0]

        self._populate_preview_tree(df)

    def _populate_preview_tree(self, df: pd.DataFrame):
        self.preview_tree.delete(*self.preview_tree.get_children())
        cols = df.columns.tolist()
        if not cols:
            self._show_preview_placeholder("(no columns selected)")
            return
        self.preview_tree.configure(columns=cols)
        for c in cols:
            self.preview_tree.heading(c, text=c, anchor="w")
            self.preview_tree.column(c, width=140, anchor="w", stretch=False)
        for row in df.astype(str).fillna("").values.tolist():
            self.preview_tree.insert("", "end", values=row)

    # -------------------- Export --------------------
    def _ensure_output_dir(self) -> Optional[str]:
        outdir = (self.output_dir_var.get() or "").strip()
        if not outdir:
            d = filedialog.askdirectory(mustexist=False)
            if not d:
                return None
            self.output_dir_var.set(d)
            outdir = d
        os.makedirs(outdir, exist_ok=True)
        return outdir

    def _read_full(self, sheet: str) -> pd.DataFrame:
        path = self.source_path or ""
        if path.lower().endswith(".xlsx"):
            return pd.read_excel(path, sheet_name=sheet, dtype=str, engine="openpyxl").fillna("")
        return self._read_csv_with_fallback(path, nrows=None).fillna("")

    def _transform(self, sheet: str, df: pd.DataFrame) -> pd.DataFrame:
        st = self.state.get(sheet)
        if not st:
            return df
        keep = [c for c, on in st["include"].items() if on and c in df.columns]
        return df[keep] if keep else df.iloc[:, 0:0]

    def _export_current_to_csv(self):
        if not self.source_path:
            messagebox.showwarning("No file", "Load a file first.")
            return
        s = self.current_sheet.get()
        if not s:
            messagebox.showwarning("No sheet", "Select a sheet.")
            return
        outdir = self._ensure_output_dir()
        if not outdir:
            return
        try:
            df_full = self._read_full(s)
            df_t = self._transform(s, df_full)
            fname = f"{self._safe_name(s)}.csv"
            out = os.path.join(outdir, fname)
            df_t.to_csv(out, index=False)
            messagebox.showinfo("Exported", f"CSV written:\n{out}")
        except Exception as e:
            messagebox.showerror("Export failed", str(e))

    def _export_all_to_csvs(self):
        if not self.source_path:
            messagebox.showwarning("No file", "Load a file first.")
            return
        outdir = self._ensure_output_dir()
        if not outdir:
            return
        count, errs = 0, []
        for s in self.sheet_names:
            try:
                df_full = self._read_full(s)
                df_t = self._transform(s, df_full)
                out = os.path.join(outdir, f"{self._safe_name(s)}.csv")
                df_t.to_csv(out, index=False)
                count += 1
            except Exception as e:
                errs.append(f"{s}: {e}")
        msg = f"Sheets exported: {count}/{len(self.sheet_names)}\nSaved to:\n{outdir}"
        if errs:
            msg += "\n\nErrors:\n" + "\n".join(errs)
        messagebox.showinfo("Export", msg)

    def _export_all_to_excel(self):
        if not self.source_path:
            messagebox.showwarning("No file", "Load a file first.")
            return
        outdir = self._ensure_output_dir()
        if not outdir:
            return
        base = os.path.splitext(os.path.basename(self.source_path))[0]
        out_path = os.path.join(outdir, f"{base}_export.xlsx")
        try:
            with pd.ExcelWriter(out_path, engine="openpyxl") as xw:
                for s in self.sheet_names:
                    df_full = self._read_full(s)
                    df_t = self._transform(s, df_full)
                    safe_sheet = self._safe_name(s)[:31] or "Sheet1"
                    df_t.to_excel(xw, index=False, sheet_name=safe_sheet)
            messagebox.showinfo("Exported", f"Excel written:\n{out_path}")
        except Exception as e:
            messagebox.showerror("Export failed", str(e))

    # -------------------- Helpers --------------------
    @staticmethod
    def _safe_name(name: str) -> str:
        name = (name or "").strip().replace(" ", "_")
        return "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in name) or "sheet"

    def _run_bg(self, fn):
        threading.Thread(target=fn, daemon=True).start()


if __name__ == "__main__":
    app = SimpleDataManager()
    app.mainloop()
