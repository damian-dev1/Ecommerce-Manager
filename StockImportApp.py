import os
import csv
import sys
import time
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime
from dataclasses import dataclass
from typing import Iterable, List, Tuple, Optional

import requests
from requests.adapters import HTTPAdapter, Retry

from sqlalchemy import create_engine, Column, String, Integer, select, outerjoin
from sqlalchemy.orm import sessionmaker, declarative_base

# -------------------- SQLAlchemy Models --------------------
Base = declarative_base()

class Inventory(Base):
    __tablename__ = 'inventory'
    Account = Column(String, nullable=False)
    SupplierSKU = Column(String, primary_key=True)
    FreeStock = Column(Integer, nullable=True)

class InventoryLatest(Base):
    __tablename__ = 'inventory_latest'
    Account = Column(String, nullable=False)
    SupplierSKU = Column(String, primary_key=True)
    FreeStock = Column(Integer, nullable=True)

# -------------------- Data classes --------------------
@dataclass
class AppConfig:
    db_url: str = "sqlite:///database_name.db"
    db_chunk: int = 1000
    file_chunk: int = 1000
    db_out_dir: str = os.getcwd()
    file_out_dir: str = os.getcwd()
    sources_csv: str = "pos_337,src_virtualstock"  # comma-separated

# -------------------- Main App --------------------
class StockImportApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Magento 2 Stock Import Tool — Pro Refactor")
        self.geometry("1100x820")
        self.minsize(980, 720)

        # state
        self.cfg = AppConfig()
        self.cancel_event = threading.Event()
        self.log_q: "queue.Queue[str]" = queue.Queue()
        self.worker: Optional[threading.Thread] = None

        # Tk variables
        self.db_path_var = tk.StringVar(value=self.cfg.db_url)
        self.db_output_dir_var = tk.StringVar(value=self.cfg.db_out_dir)
        self.db_chunk_size_var = tk.IntVar(value=self.cfg.db_chunk)

        self.file_path_var = tk.StringVar()
        self.file_output_dir_var = tk.StringVar(value=self.cfg.file_out_dir)
        self.file_chunk_size_var = tk.IntVar(value=self.cfg.file_chunk)

        self.sku_col_var = tk.StringVar()
        self.qty_col_var = tk.StringVar()
        self.acc_col_var = tk.StringVar()
        self.sources_var = tk.StringVar(value=self.cfg.sources_csv)

        # REST API tab vars
        self.api_base_url_var = tk.StringVar(value="https://your-magento/rest")
        self.api_verify_ssl_var = tk.BooleanVar(value=True)
        self.api_auth_method_var = tk.StringVar(value="token")  # 'token' or 'admin'
        self.api_token_var = tk.StringVar()
        self.api_admin_user_var = tk.StringVar()
        self.api_admin_pass_var = tk.StringVar()
        self.api_dry_run_var = tk.BooleanVar(value=False)
        self.api_chunk_size_var = tk.IntVar(value=500)

        self.loaded_csv_data: Optional[str] = None  # path to CSV; read streaming
        self.csv_headers: List[str] = []

        self._build_ui()
        self._poll_log_queue()

    # -------------------- UI --------------------
    def _build_ui(self):
        style = ttk.Style(self)
        style.theme_use('clam')

        top = ttk.Frame(self, padding=(16, 12))
        top.pack(fill=tk.BOTH, expand=True)

        nb = ttk.Notebook(top)
        nb.pack(fill=tk.BOTH, expand=True)

        db_tab = ttk.Frame(nb, padding=12)
        file_tab = ttk.Frame(nb, padding=12)
        api_tab = ttk.Frame(nb, padding=12)

        nb.add(db_tab, text="Database Sync")
        nb.add(file_tab, text="File-based Import")
        nb.add(api_tab, text="REST API Send")

        # Build tabs
        self._build_db_tab(db_tab)
        self._build_file_tab(file_tab)
        self._build_api_tab(api_tab)

        # Log + progress
        log_frame = ttk.Labelframe(self, text="Process Log", padding=12)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 12))
        self.progress = ttk.Progressbar(log_frame, mode='determinate', maximum=100)
        self.progress.pack(fill=tk.X, pady=(0, 8))
        self.log_text = tk.Text(log_frame, height=12, state='disabled', wrap='word', bg='#f7f7f7')
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def _build_db_tab(self, parent: ttk.Frame):
        title = ttk.Label(parent, text="Generate from Database Comparison", font=("Segoe UI", 13, "bold"))
        title.grid(row=0, column=0, columnspan=3, sticky='w', pady=(0, 6))
        parent.grid_columnconfigure(1, weight=1)

        ttk.Label(parent, text="Database URL:").grid(row=1, column=0, sticky='w', pady=6)
        ttk.Entry(parent, textvariable=self.db_path_var).grid(row=1, column=1, sticky='ew', pady=6)
        ttk.Button(parent, text="Test", command=self._test_db).grid(row=1, column=2, padx=(6,0))

        ttk.Label(parent, text="Output Directory:").grid(row=2, column=0, sticky='w', pady=6)
        ttk.Entry(parent, textvariable=self.db_output_dir_var).grid(row=2, column=1, sticky='ew', pady=6)
        ttk.Button(parent, text="Browse", command=lambda: self._browse_dir(self.db_output_dir_var)).grid(row=2, column=2, padx=(6,0))

        ttk.Label(parent, text="Chunk Size (rows/file):").grid(row=3, column=0, sticky='w', pady=6)
        ttk.Entry(parent, textvariable=self.db_chunk_size_var, width=10).grid(row=3, column=1, sticky='w', pady=6)

        ttk.Label(parent, text="Source Codes (comma-separated):").grid(row=4, column=0, sticky='w', pady=6)
        ttk.Entry(parent, textvariable=self.sources_var).grid(row=4, column=1, sticky='ew', pady=6)

        btns = ttk.Frame(parent)
        btns.grid(row=5, column=0, columnspan=3, sticky='ew', pady=(12,0))
        btns.grid_columnconfigure(0, weight=1)
        self.db_run_btn = ttk.Button(btns, text="Generate Import Files from DB", command=self._start_db)
        self.db_run_btn.grid(row=0, column=0, sticky='ew')
        self.cancel_btn = ttk.Button(btns, text="Cancel", command=self._cancel, state='disabled')
        self.cancel_btn.grid(row=0, column=1, padx=(8,0))

    def _build_file_tab(self, parent: ttk.Frame):
        title = ttk.Label(parent, text="Generate from Imported File", font=("Segoe UI", 13, "bold"))
        title.grid(row=0, column=0, columnspan=3, sticky='w', pady=(0, 6))
        parent.grid_columnconfigure(1, weight=1)

        # File selection
        ttk.Label(parent, text="Import CSV:").grid(row=1, column=0, sticky='w', pady=6)
        ttk.Entry(parent, textvariable=self.file_path_var, state='readonly').grid(row=1, column=1, sticky='ew', pady=6)
        ttk.Button(parent, text="Load", command=self._load_csv).grid(row=1, column=2, padx=(6,0))

        # Column mapping
        map_frame = ttk.Labelframe(parent, text="Configure Columns", padding=10)
        map_frame.grid(row=2, column=0, columnspan=3, sticky='ew', pady=(6,0))
        map_frame.grid_columnconfigure(1, weight=1)
        self.sku_box = ttk.Combobox(map_frame, textvariable=self.sku_col_var, state='readonly')
        self.qty_box = ttk.Combobox(map_frame, textvariable=self.qty_col_var, state='readonly')
        self.acc_box = ttk.Combobox(map_frame, textvariable=self.acc_col_var, state='readonly')
        ttk.Label(map_frame, text="SKU Column:").grid(row=0, column=0, sticky='w', pady=4)
        self.sku_box.grid(row=0, column=1, sticky='ew', pady=4)
        ttk.Label(map_frame, text="Quantity Column:").grid(row=1, column=0, sticky='w', pady=4)
        self.qty_box.grid(row=1, column=1, sticky='ew', pady=4)
        ttk.Label(map_frame, text="Account Column:").grid(row=2, column=0, sticky='w', pady=4)
        self.acc_box.grid(row=2, column=1, sticky='ew', pady=4)

        # Output config
        out = ttk.Frame(parent)
        out.grid(row=3, column=0, columnspan=3, sticky='ew', pady=(8,0))
        out.grid_columnconfigure(1, weight=1)
        ttk.Label(out, text="Output Directory:").grid(row=0, column=0, sticky='w', pady=6)
        ttk.Entry(out, textvariable=self.file_output_dir_var).grid(row=0, column=1, sticky='ew', pady=6)
        ttk.Button(out, text="Browse", command=lambda: self._browse_dir(self.file_output_dir_var)).grid(row=0, column=2, padx=(6,0))
        ttk.Label(out, text="Chunk Size (rows/file):").grid(row=1, column=0, sticky='w', pady=6)
        ttk.Entry(out, textvariable=self.file_chunk_size_var, width=10).grid(row=1, column=1, sticky='w', pady=6)
        ttk.Label(out, text="Source Codes (comma-separated):").grid(row=2, column=0, sticky='w', pady=6)
        ttk.Entry(out, textvariable=self.sources_var).grid(row=2, column=1, sticky='ew', pady=6)

        # Preview table
        prev = ttk.Labelframe(parent, text="Preview (first 200 rows)", padding=10)
        prev.grid(row=4, column=0, columnspan=3, sticky='nsew', pady=(8,0))
        parent.grid_rowconfigure(4, weight=1)
        self.preview = ttk.Treeview(prev, columns=(), show='headings', height=6)
        self.preview.pack(fill=tk.BOTH, expand=True)

        # Run buttons
        btns = ttk.Frame(parent)
        btns.grid(row=5, column=0, columnspan=3, sticky='ew', pady=(8,0))
        btns.grid_columnconfigure(0, weight=1)
        self.file_run_btn = ttk.Button(btns, text="Generate Import File from CSV", command=self._start_file)
        self.file_run_btn.grid(row=0, column=0, sticky='ew')
        self.cancel_btn2 = ttk.Button(btns, text="Cancel", command=self._cancel, state='disabled')
        self.cancel_btn2.grid(row=0, column=1, padx=(8,0))

    def _build_api_tab(self, parent: ttk.Frame):
        title = ttk.Label(parent, text="Send Stock via REST API (Magento 2 MSI)", font=("Segoe UI", 13, "bold"))
        title.grid(row=0, column=0, columnspan=6, sticky='w', pady=(0, 6))
        for c in range(1, 5):
            parent.grid_columnconfigure(c, weight=1)

        # Base + SSL
        ttk.Label(parent, text="Base REST URL:").grid(row=1, column=0, sticky='w', pady=6)
        ttk.Entry(parent, textvariable=self.api_base_url_var).grid(row=1, column=1, columnspan=3, sticky='ew', pady=6)
        ttk.Checkbutton(parent, text="Verify SSL", variable=self.api_verify_ssl_var).grid(row=1, column=4, sticky='w', padx=(8,0))

        # Auth method
        auth_box = ttk.Labelframe(parent, text="Authentication", padding=10)
        auth_box.grid(row=2, column=0, columnspan=6, sticky='ew', pady=(6,0))
        for c in range(1, 5):
            auth_box.grid_columnconfigure(c, weight=1)

        ttk.Radiobutton(auth_box, text="Bearer token", value="token", variable=self.api_auth_method_var).grid(row=0, column=0, sticky='w')
        ttk.Radiobutton(auth_box, text="Admin login → fetch token", value="admin", variable=self.api_auth_method_var).grid(row=0, column=1, sticky='w')

        ttk.Label(auth_box, text="Token:").grid(row=1, column=0, sticky='w', pady=4)
        ttk.Entry(auth_box, textvariable=self.api_token_var, show="•").grid(row=1, column=1, columnspan=4, sticky='ew', pady=4)

        ttk.Label(auth_box, text="Admin User:").grid(row=2, column=0, sticky='w', pady=4)
        ttk.Entry(auth_box, textvariable=self.api_admin_user_var).grid(row=2, column=1, sticky='ew', pady=4)
        ttk.Label(auth_box, text="Password:").grid(row=2, column=2, sticky='w', pady=4)
        ttk.Entry(auth_box, textvariable=self.api_admin_pass_var, show="•").grid(row=2, column=3, sticky='ew', pady=4)
        ttk.Button(auth_box, text="Fetch Admin Token", command=self._fetch_admin_token).grid(row=2, column=4, padx=(6,0))

        # Options
        opt_box = ttk.Labelframe(parent, text="Options", padding=10)
        opt_box.grid(row=3, column=0, columnspan=6, sticky='ew', pady=(6,0))
        opt_box.grid_columnconfigure(1, weight=1)
        ttk.Checkbutton(opt_box, text="Dry-run (build & log payload only)", variable=self.api_dry_run_var).grid(row=0, column=0, sticky='w')
        ttk.Label(opt_box, text="Chunk Size (items/request):").grid(row=0, column=1, sticky='e')
        ttk.Entry(opt_box, textvariable=self.api_chunk_size_var, width=8).grid(row=0, column=2, sticky='w', padx=(6,0))
        ttk.Label(opt_box, text="Source Codes (comma-separated):").grid(row=0, column=3, sticky='e')
        ttk.Entry(opt_box, textvariable=self.sources_var).grid(row=0, column=4, sticky='ew', padx=(6,0))

        # Action buttons
        btns = ttk.Frame(parent)
        btns.grid(row=4, column=0, columnspan=6, sticky='ew', pady=(12,0))
        btns.grid_columnconfigure(0, weight=1)
        self.api_run_db_btn = ttk.Button(btns, text="Send from DB diff (InventoryLatest vs Inventory)", command=self._start_api_db)
        self.api_run_db_btn.grid(row=0, column=0, sticky='ew')
        self.api_run_file_btn = ttk.Button(btns, text="Send from Loaded CSV (use mapping)", command=self._start_api_file)
        self.api_run_file_btn.grid(row=0, column=1, sticky='ew', padx=(8,0))
        self.cancel_btn3 = ttk.Button(btns, text="Cancel", command=self._cancel, state='disabled')
        self.cancel_btn3.grid(row=0, column=2, sticky='w', padx=(8,0))

        # Info
        info = ttk.Label(
            parent,
            text="Endpoint used: POST {base}/V1/inventory/source-items  •  Payload: [{sku, source_code, quantity, status}]  •  Status: 1 if qty>0 else 0",
            foreground="#444"
        )
        info.grid(row=5, column=0, columnspan=6, sticky='w', pady=(8,0))

    # -------------------- Helpers --------------------
    def _browse_dir(self, var: tk.StringVar):
        path = filedialog.askdirectory()
        if path:
            var.set(path)

    def _append_log(self, msg: str):
        self.log_text.configure(state='normal')
        self.log_text.insert('end', f"{datetime.now():%H:%M:%S} - {msg}\n")
        self.log_text.configure(state='disabled')
        self.log_text.see('end')

    def log(self, msg: str):
        self.log_q.put(msg)

    def _poll_log_queue(self):
        try:
            while True:
                msg = self.log_q.get_nowait()
                self._append_log(msg)
        except queue.Empty:
            pass
        self.after(100, self._poll_log_queue)

    def _set_progress(self, value: Optional[int] = None, *, indeterminate: bool = False):
        if indeterminate:
            self.progress.configure(mode='indeterminate')
            self.progress.start(12)
        else:
            if str(self.progress['mode']) != 'determinate':
                self.progress.stop()
                self.progress.configure(mode='determinate')
            self.progress['value'] = max(0, min(100, value or 0))

    def _validate_chunk(self, n: int) -> int:
        try:
            n = int(n)
            if n <= 0:
                raise ValueError
            return n
        except Exception:
            messagebox.showerror("Invalid chunk size", "Chunk size must be a positive integer.")
            raise

    # -------------------- Common collectors (reused) --------------------
    def _collect_changes_from_db(self, db_url: str) -> List[Tuple[str, int]]:
        eng = create_engine(db_url, future=True)
        self.log("Connecting to database…")
        changes: List[Tuple[str, int]] = []
        total_processed = 0
        with eng.connect().execution_options(stream_results=True) as conn:
            j = outerjoin(InventoryLatest, Inventory, InventoryLatest.SupplierSKU == Inventory.SupplierSKU)
            stmt = select(InventoryLatest.SupplierSKU, InventoryLatest.FreeStock, Inventory.FreeStock).select_from(j)
            result = conn.execute(stmt)
            for row in result:
                if self.cancel_event.is_set():
                    self.log("Cancelled by user.")
                    return []
                sku, latest, previous = row[0], row[1], row[2]
                latest = int(latest or 0)
                previous = int(previous or 0)
                if latest != previous:
                    changes.append((sku, latest))
                total_processed += 1
                if total_processed % 5000 == 0:
                    self.log(f"Scanned {total_processed:,} rows…")
        return changes

    def _collect_rows_from_csv(self, path: str, sku_col: str, qty_col: str) -> List[Tuple[str, int]]:
        total_rows = 0
        with open(path, 'r', encoding='utf-8-sig', newline='') as f:
            total_rows = sum(1 for _ in f) - 1
        if total_rows < 0:
            total_rows = 0

        processed = 0
        rows: List[Tuple[str, int]] = []
        self.log(f"Reading {total_rows:,} rows…")
        with open(path, 'r', encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if self.cancel_event.is_set():
                    self.log("Cancelled by user.")
                    return []
                try:
                    sku = (row.get(sku_col) or '').strip()
                    qty_raw = row.get(qty_col)
                    qty = int(qty_raw) if str(qty_raw).strip() != '' else 0
                    if not sku:
                        raise ValueError("Empty SKU")
                    rows.append((sku, qty))
                except Exception:
                    self.log(f"Skipping invalid row: {row}")
                processed += 1
                if processed % 1000 == 0 or processed == total_rows:
                    self._set_progress(int(processed / max(1, total_rows) * 100))
        return rows

    # -------------------- DB Flow (CSV output) --------------------
    def _test_db(self):
        url = self.db_path_var.get().strip()
        try:
            eng = create_engine(url, future=True)
            with eng.connect() as conn:
                conn.execute(select(1))
            messagebox.showinfo("Database", "Connection OK.")
        except Exception as e:
            messagebox.showerror("Database", f"Failed to connect: {e}")

    def _start_db(self):
        if self.worker and self.worker.is_alive():
            return
        try:
            chunk = self._validate_chunk(self.db_chunk_size_var.get())
        except Exception:
            return
        self._before_run()
        args = (self.db_path_var.get().strip(), self.db_output_dir_var.get().strip(), chunk, self.sources_var.get())
        self.worker = threading.Thread(target=self._run_db_worker, args=args, daemon=True)
        self.worker.start()

    def _run_db_worker(self, db_url: str, out_dir: str, chunk_size: int, sources_csv: str):
        self.cancel_event.clear()
        try:
            if not out_dir:
                self.log("Output directory is required.")
                return
            os.makedirs(out_dir, exist_ok=True)

            changes = self._collect_changes_from_db(db_url)
            if not changes:
                self._set_progress(100)
                self.log("No stock changes detected.")
                messagebox.showinfo("Complete", "No stock changes detected.")
                return

            self.log(f"Detected {len(changes):,} changed SKUs. Writing files…")
            self._write_files(out_dir, chunk_size, changes, sources_csv)
            self._set_progress(100)
            messagebox.showinfo("Success", "Magento 2 import files generated successfully!")
        except Exception as e:
            self.log(f"ERROR: {e}")
            messagebox.showerror("Error", f"An unexpected error occurred:\n{e}")
        finally:
            self._after_run()

    # -------------------- File Flow (CSV output) --------------------
    def _load_csv(self):
        path = filedialog.askopenfilename(filetypes=[("CSV", "*.csv"), ("All Files", "*.*")])
        if not path:
            return
        try:
            # sniff dialect quickly
            with open(path, 'r', encoding='utf-8-sig', newline='') as f:
                sample = f.read(8192)
                f.seek(0)
                dialect = csv.Sniffer().sniff(sample) if sample else csv.excel
                reader = csv.reader(f, dialect)
                headers = next(reader)

            self.file_path_var.set(path)
            self.csv_headers = headers
            self.sku_box['values'] = headers
            self.qty_box['values'] = headers
            self.acc_box['values'] = headers

            # populate preview
            self._render_preview(path, headers)
            self.log(f"Loaded CSV with {len(headers)} columns: {', '.join(headers[:10])}{'…' if len(headers)>10 else ''}")
        except Exception as e:
            messagebox.showerror("CSV Load", f"Failed to load CSV: {e}")
            self.file_path_var.set("")
            self.csv_headers = []

    def _render_preview(self, path: str, headers: List[str]):
        self.preview.delete(*self.preview.get_children())
        self.preview['columns'] = headers
        for h in headers:
            self.preview.heading(h, text=h)
            self.preview.column(h, width=max(80, min(220, len(h)*10)))
        try:
            with open(path, 'r', encoding='utf-8-sig', newline='') as f:
                reader = csv.DictReader(f)
                for i, row in enumerate(reader):
                    if i >= 200:
                        break
                    values = [row.get(h, '') for h in headers]
                    self.preview.insert('', 'end', values=values)
        except Exception as e:
            self.log(f"Preview failed: {e}")

    def _start_file(self):
        if self.worker and self.worker.is_alive():
            return
        try:
            chunk = self._validate_chunk(self.file_chunk_size_var.get())
        except Exception:
            return
        if not self.file_path_var.get():
            messagebox.showerror("Input", "Please load a CSV file first.")
            return
        if not all([self.sku_col_var.get(), self.qty_col_var.get(), self.acc_col_var.get()]):
            messagebox.showerror("Mapping", "Select SKU, Quantity and Account columns.")
            return
        self._before_run()
        args = (
            self.file_path_var.get(),
            self.file_output_dir_var.get().strip(),
            chunk,
            self.sku_col_var.get(), self.qty_col_var.get(), self.acc_col_var.get(),
            self.sources_var.get()
        )
        self.worker = threading.Thread(target=self._run_file_worker, args=args, daemon=True)
        self.worker.start()

    def _run_file_worker(self, path: str, out_dir: str, chunk_size: int,
                         sku_col: str, qty_col: str, acc_col: str, sources_csv: str):
        self.cancel_event.clear()
        try:
            if not out_dir:
                self.log("Output directory is required.")
                return
            os.makedirs(out_dir, exist_ok=True)

            rows = self._collect_rows_from_csv(path, sku_col, qty_col)
            if not rows:
                self._set_progress(100)
                self.log("No valid rows found.")
                messagebox.showinfo("Complete", "No valid rows found in CSV.")
                return

            self.log(f"Writing {len(rows):,} items to files…")
            self._write_files(out_dir, chunk_size, rows, sources_csv)
            self._set_progress(100)
            messagebox.showinfo("Success", "Magento 2 import files generated successfully!")
        except Exception as e:
            self.log(f"ERROR: {e}")
            messagebox.showerror("Error", f"An unexpected error occurred:\n{e}")
        finally:
            self._after_run()

    # -------------------- REST API Flow --------------------
    def _fetch_admin_token(self):
        try:
            base = self.api_base_url_var.get().strip().rstrip('/')
            url = f"{base}/V1/integration/admin/token"
            user = self.api_admin_user_var.get().strip()
            pwd = self.api_admin_pass_var.get().strip()
            if not (user and pwd):
                messagebox.showerror("Auth", "Provide Admin User and Password.")
                return

            s = self._requests_session()
            self.log("Requesting admin token…")
            resp = s.post(url, json={"username": user, "password": pwd}, timeout=30, verify=self.api_verify_ssl_var.get())
            resp.raise_for_status()
            token = resp.json()
            if isinstance(token, str) and token:
                self.api_token_var.set(token)
                self.log("Admin token acquired.")
                messagebox.showinfo("Auth", "Admin token acquired.")
            else:
                raise ValueError("Unexpected token response")
        except Exception as e:
            self.log(f"Token fetch failed: {e}")
            messagebox.showerror("Auth", f"Failed to fetch admin token:\n{e}")

    def _start_api_db(self):
        if self.worker and self.worker.is_alive():
            return
        try:
            chunk = self._validate_chunk(self.api_chunk_size_var.get())
        except Exception:
            return
        self._before_run_api()
        args = (
            self.db_path_var.get().strip(),
            self.api_base_url_var.get().strip(),
            self.api_token_effective(),
            self.api_verify_ssl_var.get(),
            self.sources_var.get(),
            chunk,
            self.api_dry_run_var.get(),
        )
        self.worker = threading.Thread(target=self._run_api_db_worker, args=args, daemon=True)
        self.worker.start()

    def _run_api_db_worker(self, db_url: str, base_url: str, token: Optional[str], verify_ssl: bool,
                           sources_csv: str, batch_size: int, dry_run: bool):
        self.cancel_event.clear()
        try:
            if not base_url:
                self.log("Base REST URL is required.")
                return
            if not token and not dry_run:
                self.log("Token is required (or enable Dry-run).")
                return

            changes = self._collect_changes_from_db(db_url)
            if not changes:
                self._set_progress(100)
                self.log("No stock changes detected.")
                messagebox.showinfo("Complete", "No stock changes detected.")
                return

            self._send_rest_updates(base_url, token, verify_ssl, sources_csv, changes, batch_size, dry_run)
            self._set_progress(100)
            messagebox.showinfo("Success", "REST API operation completed.")
        except Exception as e:
            self.log(f"ERROR: {e}")
            messagebox.showerror("Error", f"An unexpected error occurred:\n{e}")
        finally:
            self._after_run_api()

    def _start_api_file(self):
        if self.worker and self.worker.is_alive():
            return
        if not self.file_path_var.get():
            messagebox.showerror("Input", "Please load a CSV file (File-based Import tab) and map columns.")
            return
        if not all([self.sku_col_var.get(), self.qty_col_var.get()]):
            messagebox.showerror("Mapping", "Select SKU and Quantity columns on the File-based Import tab.")
            return
        try:
            chunk = self._validate_chunk(self.api_chunk_size_var.get())
        except Exception:
            return
        self._before_run_api()
        args = (
            self.file_path_var.get(),
            self.sku_col_var.get(), self.qty_col_var.get(),
            self.api_base_url_var.get().strip(),
            self.api_token_effective(),
            self.api_verify_ssl_var.get(),
            self.sources_var.get(),
            chunk,
            self.api_dry_run_var.get(),
        )
        self.worker = threading.Thread(target=self._run_api_file_worker, args=args, daemon=True)
        self.worker.start()

    def _run_api_file_worker(self, csv_path: str, sku_col: str, qty_col: str,
                             base_url: str, token: Optional[str], verify_ssl: bool,
                             sources_csv: str, batch_size: int, dry_run: bool):
        self.cancel_event.clear()
        try:
            if not base_url:
                self.log("Base REST URL is required.")
                return
            if not token and not dry_run:
                self.log("Token is required (or enable Dry-run).")
                return

            rows = self._collect_rows_from_csv(csv_path, sku_col, qty_col)
            if not rows:
                self._set_progress(100)
                self.log("No valid rows found.")
                messagebox.showinfo("Complete", "No valid rows found in CSV.")
                return

            self._send_rest_updates(base_url, token, verify_ssl, sources_csv, rows, batch_size, dry_run)
            self._set_progress(100)
            messagebox.showinfo("Success", "REST API operation completed.")
        except Exception as e:
            self.log(f"ERROR: {e}")
            messagebox.showerror("Error", f"An unexpected error occurred:\n{e}")
        finally:
            self._after_run_api()

    # -------------------- REST helpers --------------------
    def api_token_effective(self) -> Optional[str]:
        method = self.api_auth_method_var.get()
        if method == 'token':
            return self.api_token_var.get().strip() or None
        elif method == 'admin':
            # expects token already fetched into api_token_var
            return self.api_token_var.get().strip() or None
        return None

    def _requests_session(self) -> requests.Session:
        s = requests.Session()
        retries = Retry(
            total=4,
            backoff_factor=0.75,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS'])
        )
        adapter = HTTPAdapter(max_retries=retries, pool_connections=10, pool_maxsize=10)
        s.mount('http://', adapter)
        s.mount('https://', adapter)
        return s

    def _iter_msi_batches(self, rows: List[Tuple[str, int]], sources: List[str], batch_size: int):
        """
        Build MSI items in-memory but yield in batches to limit memory.
        """
        batch = []
        for (sku, qty) in rows:
            status = 1 if (qty or 0) > 0 else 0
            for src in sources:
                batch.append({
                    "sku": sku,
                    "source_code": src,
                    "quantity": int(qty or 0),
                    "status": int(status),
                })
                if len(batch) >= batch_size:
                    yield batch
                    batch = []
        if batch:
            yield batch

    def _send_rest_updates(self, base_url: str, token: Optional[str], verify_ssl: bool,
                           sources_csv: str, rows: List[Tuple[str, int]],
                           batch_size: int, dry_run: bool):
        sources = [s.strip() for s in sources_csv.split(',') if s.strip()] or ["pos_337", "src_virtualstock"]
        if not sources_csv.strip():
            self.log("No source codes provided; using defaults: pos_337, src_virtualstock")

        total_items = len(rows) * len(sources)
        base = base_url.rstrip('/')
        endpoint = f"{base}/V1/inventory/source-items"
        self.log(f"Prepared {total_items:,} MSI items across {len(sources)} source(s). Batch size = {batch_size}.")

        sent = 0
        batches = 0
        s = self._requests_session()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        start_ts = time.time()
        for batch in self._iter_msi_batches(rows, sources, batch_size):
            if self.cancel_event.is_set():
                self.log("Cancelled during API send.")
                break
            batches += 1
            if dry_run:
                self.log(f"[Dry-run] Batch {batches}: {len(batch)} items. Example: {batch[0] if batch else {}}")
            else:
                try:
                    resp = s.post(endpoint, json=batch, headers=headers, timeout=60, verify=verify_ssl)
                    if resp.status_code >= 400:
                        self.log(f"HTTP {resp.status_code}: {resp.text[:300]}")
                        resp.raise_for_status()
                    # M2 MSI returns boolean True on success typically
                except Exception as e:
                    self.log(f"Batch {batches} failed: {e}")
                    # continue after logging; user can re-run failed subset later
                    continue
            sent += len(batch)
            # progress based on items
            pct = int((sent / max(1, total_items)) * 100)
            self._set_progress(pct)
            if batches % 5 == 0 or sent == total_items:
                self.log(f"Sent {sent:,}/{total_items:,} items…")

        dur = time.time() - start_ts
        self.log(f"Done. Batches: {batches}, Items processed: {sent:,}, Time: {dur:.1f}s")

    # -------------------- Write Files (CSV) --------------------
    def _write_files(self, out_dir: str, chunk_size: int, rows: List[Tuple[str, int]], sources_csv: str):
        sources = [s.strip() for s in sources_csv.split(',') if s.strip()]
        if not sources:
            sources = ["pos_337", "src_virtualstock"]
            self.log("No source codes provided; using defaults: pos_337, src_virtualstock")

        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        file_idx = 1
        written = 0
        path = os.path.join(out_dir, f"m2_stock_import_{ts}_{file_idx}.csv")
        f = open(path, 'w', newline='', encoding='utf-8')
        writer = csv.writer(f)
        writer.writerow(["sku", "stock_status", "source_code", "qty"])
        self.log(f"Writing: {os.path.basename(path)}")

        def rotate():
            nonlocal f, writer, file_idx
            f.close()
            file_idx += 1
            new_path = os.path.join(out_dir, f"m2_stock_import_{ts}_{file_idx}.csv")
            f = open(new_path, 'w', newline='', encoding='utf-8')
            writer = csv.writer(f)
            writer.writerow(["sku", "stock_status", "source_code", "qty"])
            self.log(f"Chunk limit reached → new file: {os.path.basename(new_path)}")

        per_file_rows = 0
        for idx, (sku, qty) in enumerate(rows, 1):
            if self.cancel_event.is_set():
                self.log("Cancelled while writing.")
                break
            stock_status = 1 if (qty or 0) > 0 else 0
            for src in sources:
                writer.writerow([sku, stock_status, src, qty or 0])
                per_file_rows += 1
                written += 1
                if per_file_rows >= chunk_size:
                    rotate()
                    per_file_rows = 0
            if idx % 5000 == 0:
                self.log(f"Wrote {written:,} rows…")

        f.close()
        self.log(f"Done. Generated {file_idx} file(s), {written:,} data rows (including multi-source). Output: {out_dir}")

    # -------------------- Run control --------------------
    def _before_run(self):
        self.progress['value'] = 0
        self._set_progress(0)
        self.log_text.configure(state='normal')
        self.log_text.delete('1.0', 'end')
        self.log_text.configure(state='disabled')
        self.db_run_btn.configure(state='disabled')
        self.file_run_btn.configure(state='disabled')
        self.cancel_btn.configure(state='normal')
        self.cancel_btn2.configure(state='normal')

    def _after_run(self):
        self.db_run_btn.configure(state='normal')
        self.file_run_btn.configure(state='normal')
        self.cancel_btn.configure(state='disabled')
        self.cancel_btn2.configure(state='disabled')
        self.cancel_event.clear()

    def _before_run_api(self):
        self.progress['value'] = 0
        self._set_progress(0)
        self.log_text.configure(state='normal')
        self.log_text.delete('1.0', 'end')
        self.log_text.configure(state='disabled')
        for b in (self.api_run_db_btn, self.api_run_file_btn):
            b.configure(state='disabled')
        self.cancel_btn3.configure(state='normal')

    def _after_run_api(self):
        for b in (self.api_run_db_btn, self.api_run_file_btn):
            b.configure(state='normal')
        self.cancel_btn3.configure(state='disabled')
        self.cancel_event.clear()

    def _cancel(self):
        if self.worker and self.worker.is_alive():
            self.cancel_event.set()
            self.log("Cancel requested. Finishing current step…")

# -------------------- Demo bootstrap --------------------
if __name__ == '__main__':
    # Optional: create a tiny demo DB if missing
    def setup_dummy():
        if os.path.exists('database_name.db'):
            return
        from sqlalchemy import text
        eng = create_engine('sqlite:///database_name.db')
        Base.metadata.create_all(eng)
        with eng.begin() as conn:
            conn.execute(text("INSERT INTO inventory(Account,SupplierSKU,FreeStock) VALUES"
                              "('ACC1','SKU001',10),('ACC1','SKU002',5),('ACC1','SKU003',0)"))
            conn.execute(text("INSERT INTO inventory_latest(Account,SupplierSKU,FreeStock) VALUES"
                              "('ACC1','SKU001',8),('ACC1','SKU002',5),('ACC1','SKU003',15),('ACC1','SKU004',20)"))
        print("Dummy database created.")

    setup_dummy()
    app = StockImportApp()
    app.mainloop()
