"""
Tokyo Midnight — Modern Tk GUI
Inventory Reconciliation (HNAU vs Virtualstock) with CSV & SFTP inputs

• Robust pipeline reused from your CLI (normalize → SQLite → joined stats → exports →
  inventory_latest ↔ inventory comparison + stats logging)
• GUI additions:
  – Tokyo Midnight dark theme
  – Live stats cards (now includes 2 new stats):
      1) In Stock in HNAU & Out of Stock in VS
      2) In Stock in VS & Out of Stock in HNAU
  – Data view switcher (Mismatches / Only in HNAU / Only in VS /
    HNAU in & VS out / VS in & HNAU out)
  – Exports include CSVs for each of the 5 result sets
  – CSV one‑shot OR SFTP polling loop (start/stop)

Deps: pandas, SQLAlchemy, (optional) paramiko for SFTP
Run:   python inventory_reconcile_gui.py
"""
from __future__ import annotations

import os
import sys
import fnmatch
import queue
import threading
import time
import sqlite3
import tempfile
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional, Iterable, Tuple, Dict

import pandas as pd
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, Integer, String

try:
    import paramiko
except Exception:
    paramiko = None

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

class Stats(Base):
    __tablename__ = 'stats'
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(String, nullable=False)
    total_skus = Column(Integer, nullable=False)
    changes_detected = Column(Integer, nullable=False)
    execution_time = Column(String, nullable=False)
    action = Column(String, nullable=False)

class IngestionRun(Base):
    __tablename__ = 'ingestion_runs'
    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String, nullable=False)
    hnau_file = Column(String, nullable=True)
    vs_file = Column(String, nullable=True)
    hnau_mtime = Column(Integer, nullable=True)
    vs_mtime = Column(Integer, nullable=True)
    started_at = Column(String, nullable=False)
    finished_at = Column(String, nullable=True)
    status = Column(String, nullable=False)  # STARTED / SUCCESS / SKIPPED / ERROR
    note = Column(String, nullable=True)


def default_downloads() -> str:
    return os.path.join(os.path.expanduser("~"), "Downloads")

def clean_sku(x: object) -> Optional[str]:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    s = str(x).strip()
    return s.upper() or None


def parse_qty_to_int(x: object) -> int:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return 0
    s = str(x).strip()
    if s == "" or s.upper() in {"NULL", "NAN"}:
        return 0
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    s = s.replace(",", "")
    try:
        d = Decimal(s)
        i = int(d.to_integral_value(rounding=ROUND_HALF_UP))
        return -i if neg else i
    except (InvalidOperation, ValueError):
        try:
            i = int(float(s))
            return -i if neg else i
        except Exception:
            return 0


def to_nullable_int_series(series: pd.Series) -> pd.Series:
    return series.map(parse_qty_to_int).astype("Int64")


def drop_object(conn: sqlite3.Connection, name: str) -> None:
    row = conn.execute("SELECT type FROM sqlite_master WHERE name = ?", (name,)).fetchone()
    if not row:
        return
    t = row[0]
    if t == "view":
        conn.execute(f"DROP VIEW IF EXISTS {name}")
    elif t == "table":
        conn.execute(f"DROP TABLE IF EXISTS {name}")


def make_session(db_path: str):
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_ingestion_pair ON ingestion_runs("\
            "source, IFNULL(hnau_file,''), IFNULL(vs_file,''), IFNULL(hnau_mtime,0), IFNULL(vs_mtime,0))"
        )
    return sessionmaker(bind=engine)(), engine


def load_and_normalize(hnau_csv: str, vs_csv: str):
    hnau_df = pd.read_csv(
        hnau_csv,
        dtype={
            "sku_oms_details_sku": "string",
            "online_salable_qty_quantity": "string",
            "sku_oms_details_sap_supplier_id": "string",
        },
        on_bad_lines='skip'
    )
    vs_df = pd.read_csv(
        vs_csv,
        dtype={
            "account": "string",
            "supplier_sku": "string",
            "free_stock": "string",
        },
        on_bad_lines='skip'
    )
    hnau_norm = (
        hnau_df.assign(
            sku=hnau_df["sku_oms_details_sku"].map(clean_sku),
            qty=to_nullable_int_series(hnau_df["online_salable_qty_quantity"]),
            supplier_id=hnau_df["sku_oms_details_sap_supplier_id"].astype("string").str.strip(),
        )
        .dropna(subset=["sku"])
        .groupby("sku", as_index=False)
        .agg(qty=("qty","sum"), supplier_id=("supplier_id","min"))
    )
    vs_norm = (
        vs_df.assign(
            sku=vs_df["supplier_sku"].map(clean_sku),
            qty=to_nullable_int_series(vs_df["free_stock"]),
            account=vs_df["account"].astype("string").str.strip(),
        )
        .dropna(subset=["sku"])
        .groupby("sku", as_index=False)
        .agg(qty=("qty","sum"), account=("account","min"))
    )
    hnau_norm["qty"] = hnau_norm["qty"].astype("Int64")
    vs_norm["qty"] = vs_norm["qty"].astype("Int64")
    return hnau_norm, vs_norm


def materialize_norm_tables(db_path: str, hnau_norm: pd.DataFrame, vs_norm: pd.DataFrame) -> None:
    with sqlite3.connect(db_path) as conn:
        for name in ("hnau_norm","vs_norm"):
            drop_object(conn, name)
        conn.execute("""
            CREATE TABLE hnau_norm (
                sku TEXT PRIMARY KEY,
                qty INTEGER,
                supplier_id TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE vs_norm (
                sku TEXT PRIMARY KEY,
                qty INTEGER,
                account TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hnau_norm_qty ON hnau_norm(qty)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_vs_norm_qty ON vs_norm(qty)")
        hnau_norm.to_sql("hnau_norm", conn, if_exists="append", index=False)
        vs_norm.to_sql("vs_norm", conn, if_exists="append", index=False)


def compute_joined_and_stats(db_path: str):
    base = """
    WITH joined AS (
      SELECT
        h.sku AS sku,
        CAST(h.qty AS INTEGER) AS hnau_qty,
        CAST(v.qty AS INTEGER) AS vs_qty,
        CAST((COALESCE(v.qty,0) - COALESCE(h.qty,0)) AS INTEGER) AS qty_diff,
        h.supplier_id AS supplier_id,
        v.account AS account,
        CASE WHEN v.sku IS NULL THEN 'ONLY_IN_HNAU'
             WHEN COALESCE(h.qty,0) = COALESCE(v.qty,0) THEN 'MATCH'
             ELSE 'QTY_MISMATCH' END AS status
      FROM hnau_norm h
      LEFT JOIN vs_norm v ON v.sku = h.sku
      UNION ALL
      SELECT
        v.sku, CAST(h.qty AS INTEGER), CAST(v.qty AS INTEGER),
        CAST((COALESCE(v.qty,0) - COALESCE(h.qty,0)) AS INTEGER),
        h.supplier_id, v.account, 'ONLY_IN_VS'
      FROM vs_norm v
      LEFT JOIN hnau_norm h ON h.sku = v.sku
      WHERE h.sku IS NULL
    )
    """
    stats_sql = base + """
    SELECT
      (SELECT COUNT(*) FROM hnau_norm) AS hnau_rows,
      (SELECT COUNT(*) FROM vs_norm)   AS vs_rows,
      SUM(CASE WHEN status='MATCH' THEN 1 ELSE 0 END) AS matches,
      SUM(CASE WHEN status='QTY_MISMATCH' THEN 1 ELSE 0 END) AS qty_mismatches,
      SUM(CASE WHEN status='ONLY_IN_HNAU' THEN 1 ELSE 0 END) AS only_in_hnau,
      SUM(CASE WHEN status='ONLY_IN_VS'   THEN 1 ELSE 0 END) AS only_in_vs,
      SUM(COALESCE(hnau_qty,0)) AS total_hnau_qty,
      SUM(COALESCE(vs_qty,0))   AS total_vs_qty,
      SUM(CASE WHEN status='QTY_MISMATCH' THEN ABS(qty_diff) ELSE 0 END) AS sum_abs_qty_diff,
      AVG(CASE WHEN status='QTY_MISMATCH' THEN ABS(qty_diff) END)        AS avg_abs_qty_diff,
      SUM(CASE WHEN COALESCE(hnau_qty,0)>0 AND COALESCE(vs_qty,0)<=0 THEN 1 ELSE 0 END) AS hnau_in_vs_out,
      SUM(CASE WHEN COALESCE(vs_qty,0)>0 AND COALESCE(hnau_qty,0)<=0 THEN 1 ELSE 0 END) AS vs_in_hnau_out
    FROM joined;
    """
    mis_sql   = base + "SELECT * FROM joined WHERE status='QTY_MISMATCH' ORDER BY ABS(qty_diff) DESC, sku;"
    only_h_sql= base + "SELECT * FROM joined WHERE status='ONLY_IN_HNAU' ORDER BY sku;"
    only_v_sql= base + "SELECT * FROM joined WHERE status='ONLY_IN_VS' ORDER BY sku;"
    h_in_v_out_sql = base + "SELECT * FROM joined WHERE COALESCE(hnau_qty,0)>0 AND COALESCE(vs_qty,0)<=0 ORDER BY sku;"
    v_in_h_out_sql = base + "SELECT * FROM joined WHERE COALESCE(vs_qty,0)>0 AND COALESCE(hnau_qty,0)<=0 ORDER BY sku;"

    with sqlite3.connect(db_path) as conn:
        stats_df = pd.read_sql_query(stats_sql, conn)
        mism = pd.read_sql_query(mis_sql, conn)
        only_h = pd.read_sql_query(only_h_sql, conn)
        only_v = pd.read_sql_query(only_v_sql, conn)
        h_in_v_out = pd.read_sql_query(h_in_v_out_sql, conn)
        v_in_h_out = pd.read_sql_query(v_in_h_out_sql, conn)
    for df in (mism, only_h, only_v, h_in_v_out, v_in_h_out):
        for c in ("hnau_qty","vs_qty","qty_diff"):
            if c in df.columns:
                df[c] = df[c].astype("Int64")
    return stats_df, mism, only_h, only_v, h_in_v_out, v_in_h_out


def log_stats(session, action: str, total_skus: int, changes_detected: int, seconds: float):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    session.add(Stats(timestamp=ts, total_skus=total_skus,
                      changes_detected=changes_detected,
                      execution_time=f"{seconds:.2f} seconds", action=action))
    session.commit()


def upsert_inventory_latest_from_vs_norm(db_path: str) -> int:
    session, _ = make_session(db_path)
    with sqlite3.connect(db_path) as conn:
        vs_norm = pd.read_sql_query("SELECT sku, qty, account FROM vs_norm", conn)
    start = time.time()
    session.query(InventoryLatest).delete()
    objs = [InventoryLatest(Account=str(getattr(r,'account','') or ''),
                            SupplierSKU=str(r.sku),
                            FreeStock=int(r.qty) if pd.notna(r.qty) else None)
            for r in vs_norm.itertuples(index=False)]
    if objs:
        session.bulk_save_objects(objs)
    session.commit()
    log_stats(session, "refresh_inventory_latest", len(objs), 0, time.time()-start)
    return len(objs)


def compare_tables(db_path: str) -> int:
    session, _ = make_session(db_path)
    start = time.time()
    stmt = select(InventoryLatest.SupplierSKU, InventoryLatest.FreeStock, Inventory.FreeStock)\
           .outerjoin(Inventory, Inventory.SupplierSKU == InventoryLatest.SupplierSKU)
    results = session.execute(stmt).all()
    changes = [(sku, latest, prev) for sku, latest, prev in results if latest != prev]
    log_stats(session, "compare_tables", len(results), len(changes), time.time()-start)
    return len(changes)


def update_inventory_from_latest(db_path: str) -> int:
    session, _ = make_session(db_path)
    start = time.time()
    session.query(Inventory).delete()
    latest = session.query(InventoryLatest).all()
    if latest:
        session.bulk_save_objects([Inventory(Account=r.Account, SupplierSKU=r.SupplierSKU, FreeStock=r.FreeStock)
                                   for r in latest])
    session.commit()
    log_stats(session, "update_inventory", len(latest), 0, time.time()-start)
    return len(latest)


def export_reports(prefix: str,
                   stats_df: pd.DataFrame,
                   mism: pd.DataFrame,
                   only_h: pd.DataFrame,
                   only_v: pd.DataFrame,
                   h_in_v_out: pd.DataFrame,
                   v_in_h_out: pd.DataFrame) -> Dict[str, str]:
    tag = datetime.today().strftime('%d_%m_%Y')
    base = prefix if (os.path.isdir(prefix) or prefix.endswith(os.sep) or prefix == "") else os.path.dirname(prefix)
    os.makedirs(base or default_downloads(), exist_ok=True)
    paths = {
        'mismatches': os.path.join(base, f"stock_mismatches_{tag}.csv"),
        'only_hnau':  os.path.join(base, f"only_in_hnau_{tag}.csv"),
        'only_vs':    os.path.join(base, f"only_in_vs_{tag}.csv"),
        'hnau_in_vs_out': os.path.join(base, f"hnau_in_vs_out_{tag}.csv"),
        'vs_in_hnau_out': os.path.join(base, f"vs_in_hnau_out_{tag}.csv"),
    }
    mism.to_csv(paths['mismatches'], index=False)
    only_h.to_csv(paths['only_hnau'], index=False)
    only_v.to_csv(paths['only_vs'], index=False)
    h_in_v_out.to_csv(paths['hnau_in_vs_out'], index=False)
    v_in_h_out.to_csv(paths['vs_in_hnau_out'], index=False)
    return paths

def require_paramiko():
    if paramiko is None:
        raise RuntimeError("Paramiko is required for SFTP mode. pip install paramiko")


def sftp_connect(host: str, port: int, user: str, password: Optional[str], keyfile: Optional[str]):
    require_paramiko()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    if keyfile:
        try:
            pkey = paramiko.RSAKey.from_private_key_file(keyfile)
        except Exception:
            pkey = paramiko.Ed25519Key.from_private_key_file(keyfile)
        client.connect(host, port=port, username=user, pkey=pkey)
    else:
        client.connect(host, port=port, username=user, password=password)
    sftp = client.open_sftp()
    return client, sftp


def sftp_latest_matching(sftp, remote_dir: str, pattern: str) -> Tuple[str, float]:
    items = sftp.listdir_attr(remote_dir)
    cands = [it for it in items if fnmatch.fnmatch(it.filename, pattern)]
    if not cands:
        raise FileNotFoundError(f"No files matching '{pattern}' in {remote_dir}")
    latest = max(cands, key=lambda x: x.st_mtime)
    return latest.filename, latest.st_mtime


def sftp_atomic_download(sftp, remote_dir: str, filename: str, local_dir: str) -> str:
    os.makedirs(local_dir, exist_ok=True)
    remote_path = os.path.join(remote_dir, filename).replace("\","/"")
    tmp = os.path.join(local_dir, f".{filename}.part")
    final = os.path.join(local_dir, filename)
    sftp.get(remote_path, tmp)
    os.replace(tmp, final)
    return final

def run_cycle(hnau_csv: str, vs_csv: str, db_path: str, export_prefix: str, do_update: bool):
    hnau_norm, vs_norm = load_and_normalize(hnau_csv, vs_csv)
    materialize_norm_tables(db_path, hnau_norm, vs_norm)
    stats_df, mism, only_h, only_v, h_in_v_out, v_in_h_out = compute_joined_and_stats(db_path)
    upsert_inventory_latest_from_vs_norm(db_path)
    changes = compare_tables(db_path)
    if do_update:
        update_inventory_from_latest(db_path)
    paths = export_reports(export_prefix, stats_df, mism, only_h, only_v, h_in_v_out, v_in_h_out)
    datasets = {
        'Mismatches': mism,
        'Only in HNAU': only_h,
        'Only in VS': only_v,
        'HNAU in & VS out': h_in_v_out,
        'VS in & HNAU out': v_in_h_out,
    }
    return stats_df, datasets, paths, changes

# =============================== GUI =========================================

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

class LogHandler:
    def __init__(self, text_widget: tk.Text):
        self.text = text_widget
        self.q = queue.Queue()
        self.text.after(100, self._drain)

    def write(self, msg: str):
        self.q.put(msg)

    def writeln(self, msg: str):
        self.write(msg + "")

    def _drain(self):
        try:
            while True:
                msg = self.q.get_nowait()
                self.text.insert('end', msg)
                self.text.see('end')
        except queue.Empty:
            pass
        self.text.after(100, self._drain)

class InventoryGUIApp(tk.Tk):
    # Tokyo Midnight palette
    BG = '#0f0f10'; PANEL = '#1b1c20'; PANEL_2 = '#161821'; ACCENT = '#252736'
    FG = '#c8d3f5'; MUTED = '#9aa5ce'; HI = '#00d4ff'; BRAND = '#7aa2f7'

    def __init__(self):
        super().__init__()
        self.title("Inventory Reconciliation — HNAU vs Virtualstock")
        self.geometry("1320x860")
        self.minsize(1120, 760)
        self.configure(bg=self.BG)

        self.worker: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.datasets: Dict[str, pd.DataFrame] = {}

        dld = default_downloads()
        self.source_var = tk.StringVar(value='csv')
        self.update_var = tk.BooleanVar(value=True)
        self.db_var = tk.StringVar(value=os.path.join(dld, 'inventory.db'))
        self.export_var = tk.StringVar(value=dld)

        self.hnau_csv_var = tk.StringVar(value=os.path.join(dld, 'hnau_production_skus_29_08_2025.csv'))
        self.vs_csv_var   = tk.StringVar(value=os.path.join(dld, 'vs_products_snapshot_29_08_2025.csv'))

        self.sftp_host = tk.StringVar(value=os.environ.get('SFTP_HOST','v-source.co.uk'))
        self.sftp_port = tk.IntVar(value=int(os.environ.get('SFTP_PORT','22')))
        self.sftp_user = tk.StringVar(value=os.environ.get('SFTP_USER',''))
        self.sftp_pass = tk.StringVar(value=os.environ.get('SFTP_PASS',''))
        self.sftp_key  = tk.StringVar(value=os.environ.get('SFTP_KEY',''))
        self.hnau_remote = tk.StringVar(value=os.environ.get('HNAU_REMOTE','/live/incoming/hnau/'))
        self.vs_remote   = tk.StringVar(value=os.environ.get('VS_REMOTE','/live/incoming/vs/'))
        self.hnau_pattern= tk.StringVar(value=os.environ.get('HNAU_PATTERN','hnau*_*.csv'))
        self.vs_pattern  = tk.StringVar(value=os.environ.get('VS_PATTERN','vs*_*.csv'))
        self.poll_secs   = tk.IntVar(value=120)

        self._setup_style()
        self._build_layout()

    def _setup_style(self):
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('.', background=self.BG, foreground=self.FG, fieldbackground=self.PANEL)
        for name in ('TFrame','Labelframe','TLabelframe','TLabelframe.Label'):
            style.configure(name, background=self.BG, foreground=self.FG)
        style.configure('Panel.TFrame', background=self.PANEL, relief='flat')
        style.configure('Panel2.TFrame', background=self.PANEL_2, relief='flat')
        style.configure('TLabel', background=self.BG, foreground=self.FG)
        style.configure('HL.TLabel', background=self.PANEL, foreground=self.BRAND, font=('Segoe UI', 11, 'bold'))
        style.configure('Stat.TLabel', background=self.PANEL, foreground=self.FG, font=('Segoe UI', 14, 'bold'))
        style.configure('Accent.TButton', padding=8)
        style.map('TButton', background=[('active', self.ACCENT)])
        style.configure('TEntry', fieldbackground=self.PANEL, insertcolor=self.FG)
        style.configure('TCombobox', fieldbackground=self.PANEL, arrowncolor=self.FG)
        style.configure('Treeview', background=self.PANEL, fieldbackground=self.PANEL, foreground=self.FG, bordercolor=self.ACCENT, rowheight=26)
        style.map('Treeview', background=[('selected', self.BRAND)], foreground=[('selected', '#0b1021')])
        style.configure('Vertical.TScrollbar', background=self.PANEL)

    def _build_layout(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        side = ttk.Frame(self, style='Panel.TFrame'); side.grid(row=0, column=0, sticky='nsw')
        side.grid_propagate(False); side.configure(width=380)

        src = ttk.LabelFrame(side, text=' Data Source ', style='Panel.TFrame'); src.grid(row=0, column=0, sticky='ew', padx=12, pady=(12,8))
        for i in range(4): src.grid_columnconfigure(i, weight=1)
        ttk.Radiobutton(src, text='CSV (one‑shot)', value='csv', variable=self.source_var).grid(row=0, column=0, sticky='w', padx=8, pady=6)
        ttk.Radiobutton(src, text='SFTP (loop)',   value='sftp', variable=self.source_var).grid(row=0, column=1, sticky='w', padx=8, pady=6)

        csvf = ttk.LabelFrame(side, text=' CSV Inputs ', style='Panel.TFrame'); csvf.grid(row=1, column=0, sticky='ew', padx=12, pady=8)
        csvf.grid_columnconfigure(1, weight=1)
        ttk.Label(csvf, text='HNAU CSV').grid(row=0, column=0, sticky='w', padx=8, pady=4)
        ttk.Entry(csvf, textvariable=self.hnau_csv_var).grid(row=0, column=1, sticky='ew', padx=8, pady=4)
        ttk.Button(csvf, text='Browse', command=lambda: self._pick_file(self.hnau_csv_var)).grid(row=0, column=2, padx=8)
        ttk.Label(csvf, text='VS CSV').grid(row=1, column=0, sticky='w', padx=8, pady=4)
        ttk.Entry(csvf, textvariable=self.vs_csv_var).grid(row=1, column=1, sticky='ew', padx=8, pady=4)
        ttk.Button(csvf, text='Browse', command=lambda: self._pick_file(self.vs_csv_var)).grid(row=1, column=2, padx=8)

        sftpf = ttk.LabelFrame(side, text=' SFTP ', style='Panel.TFrame'); sftpf.grid(row=2, column=0, sticky='ew', padx=12, pady=8)
        for i in range(2): sftpf.grid_columnconfigure(i, weight=1)
        self._row(sftpf, 0, 'Host', ttk.Entry(sftpf, textvariable=self.sftp_host))
        self._row(sftpf, 1, 'Port', ttk.Spinbox(sftpf, from_=1, to=65535, textvariable=self.sftp_port, width=6))
        self._row(sftpf, 2, 'User', ttk.Entry(sftpf, textvariable=self.sftp_user))
        self._row(sftpf, 3, 'Pass', ttk.Entry(sftpf, textvariable=self.sftp_pass, show='•'))
        self._row(sftpf, 4, 'Key ', ttk.Entry(sftpf, textvariable=self.sftp_key));
        ttk.Button(sftpf, text='Key…', command=lambda: self._pick_file(self.sftp_key)).grid(row=4, column=2, padx=8, pady=4)
        self._row(sftpf, 5, 'HNAU dir', ttk.Entry(sftpf, textvariable=self.hnau_remote))
        self._row(sftpf, 6, 'VS dir', ttk.Entry(sftpf, textvariable=self.vs_remote))
        self._row(sftpf, 7, 'HNAU pattern', ttk.Entry(sftpf, textvariable=self.hnau_pattern))
        self._row(sftpf, 8, 'VS pattern', ttk.Entry(sftpf, textvariable=self.vs_pattern))
        self._row(sftpf, 9, 'Poll (s)', ttk.Spinbox(sftpf, from_=10, to=86400, textvariable=self.poll_secs, width=8))

        opt = ttk.LabelFrame(side, text=' Options ', style='Panel.TFrame'); opt.grid(row=3, column=0, sticky='ew', padx=12, pady=8)
        opt.grid_columnconfigure(1, weight=1)
        ttk.Label(opt, text='SQLite DB').grid(row=0, column=0, sticky='w', padx=8, pady=4)
        ttk.Entry(opt, textvariable=self.db_var).grid(row=0, column=1, sticky='ew', padx=8, pady=4)
        ttk.Button(opt, text='Browse', command=lambda: self._pick_file(self.db_var, save=True, defext='db')).grid(row=0, column=2, padx=8)
        ttk.Label(opt, text='Export dir').grid(row=1, column=0, sticky='w', padx=8, pady=4)
        ttk.Entry(opt, textvariable=self.export_var).grid(row=1, column=1, sticky='ew', padx=8, pady=4)
        ttk.Button(opt, text='Pick', command=lambda: self._pick_dir(self.export_var)).grid(row=1, column=2, padx=8)
        ttk.Checkbutton(opt, text='Update inventory after compare', variable=self.update_var).grid(row=2, column=0, columnspan=3, sticky='w', padx=8, pady=6)

        act = ttk.Frame(side, style='Panel2.TFrame'); act.grid(row=4, column=0, sticky='ew', padx=12, pady=(8,12))
        ttk.Button(act, text='Start', style='Accent.TButton', command=self.start).grid(row=0, column=0, padx=6)
        ttk.Button(act, text='Stop',  command=self.stop).grid(row=0, column=1, padx=6)

        main = ttk.Frame(self); main.grid(row=0, column=1, sticky='nsew')
        main.grid_rowconfigure(3, weight=1)
        main.grid_columnconfigure(0, weight=1)

        stats = ttk.Frame(main); stats.grid(row=0, column=0, sticky='ew', padx=12, pady=(12,8))
        for i in range(8): stats.grid_columnconfigure(i, weight=1)
        self.stat_labels = {}
        keys = ['hnau_rows','vs_rows','matches','qty_mismatches','only_in_hnau','only_in_vs','hnau_in_vs_out','vs_in_hnau_out']
        titles = ['HNAU Rows','VS Rows','Matches','Qty Mismatch','Only in HNAU','Only in VS','HNAU in & VS out','VS in & HNAU out']
        for i, (key, title) in enumerate(zip(keys, titles)):
            fr = ttk.Frame(stats, style='Panel.TFrame'); fr.grid(row=0, column=i, sticky='ew', padx=6)
            ttk.Label(fr, text=title, style='HL.TLabel').grid(row=0, column=0, padx=8, pady=(8,0))
            lbl = ttk.Label(fr, text='0', style='Stat.TLabel'); lbl.grid(row=1, column=0, padx=8, pady=(0,8))
            self.stat_labels[key] = lbl

        switch = ttk.Frame(main); switch.grid(row=1, column=0, sticky='ew', padx=12, pady=(0,6))
        ttk.Label(switch, text='View').grid(row=0, column=0, sticky='w')
        self.view_var = tk.StringVar(value='Mismatches')
        self.view_combo = ttk.Combobox(switch, textvariable=self.view_var, state='readonly', values=[
            'Mismatches','Only in HNAU','Only in VS','HNAU in & VS out','VS in & HNAU out'
        ])
        self.view_combo.grid(row=0, column=1, sticky='w', padx=8)
        self.view_combo.bind('<<ComboboxSelected>>', lambda e: self._refresh_table())

        table_frame = ttk.LabelFrame(main, text=' Data ', style='Panel.TFrame')
        table_frame.grid(row=2, column=0, sticky='nsew', padx=12, pady=8)
        cols = ('sku','hnau_qty','vs_qty','qty_diff','supplier_id','account','status')
        self.tree = ttk.Treeview(table_frame, columns=cols, show='headings', height=14)
        for c in cols:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=120 if c!='sku' else 240, anchor='w')
        self.tree.grid(row=0, column=0, sticky='nsew')
        table_frame.grid_columnconfigure(0, weight=1)
        table_frame.grid_rowconfigure(0, weight=1)
        vsb = ttk.Scrollbar(table_frame, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set); vsb.grid(row=0, column=1, sticky='ns')

        logf = ttk.LabelFrame(main, text=' Console ', style='Panel.TFrame')
        logf.grid(row=3, column=0, sticky='nsew', padx=12, pady=(6,12))
        logf.grid_rowconfigure(0, weight=1); logf.grid_columnconfigure(0, weight=1)
        self.log = tk.Text(logf, bg=self.PANEL, fg=self.FG, insertbackground=self.FG, relief='flat', height=8)
        self.log.grid(row=0, column=0, sticky='nsew')
        lvsb = ttk.Scrollbar(logf, orient='vertical', command=self.log.yview)
        self.log['yscrollcommand'] = lvsb.set; lvsb.grid(row=0, column=1, sticky='ns')
        self.logger = LogHandler(self.log)

        self._banner()

    def _banner(self):
        self.logger.writeln("Tokyo Midnight GUI ready. Select source and Start.")

    def _row(self, parent, r, label, widget):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky='w', padx=8, pady=4)
        widget.grid(row=r, column=1, sticky='ew', padx=8, pady=4)

    def _pick_file(self, var: tk.StringVar, save: bool=False, defext: str=''):
        if save:
            path = filedialog.asksaveasfilename(defaultextension=defext)
        else:
            path = filedialog.askopenfilename()
        if path:
            var.set(path)

    def _pick_dir(self, var: tk.StringVar):
        path = filedialog.askdirectory()
        if path:
            var.set(path)

    def start(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Running", "A job is already running.")
            return
        self.stop_event.clear()
        mode = self.source_var.get()
        if mode == 'csv':
            hnau = self.hnau_csv_var.get(); vs = self.vs_csv_var.get()
            if not (os.path.exists(hnau) and os.path.exists(vs)):
                messagebox.showerror("Missing files", "Provide valid HNAU and VS CSV paths.")
                return
            self.worker = threading.Thread(target=self._run_csv_once, daemon=True)
        else:
            if paramiko is None:
                messagebox.showerror("SFTP missing", "Install paramiko for SFTP: pip install paramiko")
                return
            self.worker = threading.Thread(target=self._run_sftp_loop, daemon=True)
        self.worker.start()

    def stop(self):
        self.stop_event.set()
        self.logger.writeln("Stop requested. Waiting for worker…")

    def _run_csv_once(self):
        try:
            self.logger.writeln("Running CSV one‑shot…")
            stats_df, datasets, paths, changes = run_cycle(
                self.hnau_csv_var.get(), self.vs_csv_var.get(), self.db_var.get(), self.export_var.get(), self.update_var.get()
            )
            self.datasets = datasets
            self._update_stats(stats_df)
            self._refresh_table()
            self._print_exports(paths)
            self.logger.writeln(f"Changes detected: {changes}")
        except Exception as e:
            self.logger.writeln(f"ERROR: {e}")

    def _run_sftp_loop(self):
        try:
            client, sftp = sftp_connect(self.sftp_host.get(), self.sftp_port.get(), self.sftp_user.get(),
                                        self.sftp_pass.get(), self.sftp_key.get())
            self.logger.writeln(f"Connected to SFTP {self.sftp_host.get()}:{self.sftp_port.get()} as {self.sftp_user.get()}")
        except Exception as e:
            self.logger.writeln(f"SFTP connect failed: {e}")
            return
        staging = os.path.join(tempfile.gettempdir(), 'invrecon_gui')
        while not self.stop_event.is_set():
            try:
                hnau_name, _ = sftp_latest_matching(sftp, self.hnau_remote.get(), self.hnau_pattern.get())
                vs_name, _   = sftp_latest_matching(sftp, self.vs_remote.get(), self.vs_pattern.get())
                hnau_local = sftp_atomic_download(sftp, self.hnau_remote.get(), hnau_name, staging)
                vs_local   = sftp_atomic_download(sftp, self.vs_remote.get(),   vs_name,   staging)
                self.logger.writeln(f"Processing {hnau_name} & {vs_name}…")
                stats_df, datasets, paths, changes = run_cycle(
                    hnau_local, vs_local, self.db_var.get(), self.export_var.get(), self.update_var.get()
                )
                self.datasets = datasets
                self._update_stats(stats_df)
                self._refresh_table()
                self._print_exports(paths)
                self.logger.writeln(f"Changes detected: {changes}")
            except Exception as e:
                self.logger.writeln(f"Loop error: {e}")
            for _ in range(self.poll_secs.get()):
                if self.stop_event.is_set():
                    break
                time.sleep(1)
        try:
            sftp.close(); client.close()
        except Exception:
            pass
        self.logger.writeln("SFTP loop stopped.")

    def _update_stats(self, stats_df: pd.DataFrame):
        if stats_df.empty:
            return
        row = stats_df.iloc[0].fillna(0)
        int_keys = ['hnau_rows','vs_rows','matches','qty_mismatches','only_in_hnau','only_in_vs','hnau_in_vs_out','vs_in_hnau_out']
        for k in int_keys:
            try:
                val = int(row.get(k, 0))
            except Exception:
                val = 0
            self.stat_labels[k].config(text=str(val))

    def _refresh_table(self):
        self.tree.delete(*self.tree.get_children())
        key = self.view_var.get()
        df = self.datasets.get(key, pd.DataFrame())
        if df.empty:
            return
        for r in df.itertuples(index=False):
            self.tree.insert('', 'end', values=(
                getattr(r,'sku',''), getattr(r,'hnau_qty',''), getattr(r,'vs_qty',''), getattr(r,'qty_diff',''),
                getattr(r,'supplier_id',''), getattr(r,'account',''), getattr(r,'status','')
            ))

    def _print_exports(self, paths: Dict[str,str]):
        self.logger.writeln("Exports saved:")
        for name, p in paths.items():
            self.logger.writeln(f" - {name}: {p}")


if __name__ == '__main__':
    app = InventoryGUIApp()
    app.mainloop()
