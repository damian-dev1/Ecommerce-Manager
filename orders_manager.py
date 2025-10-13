import asyncio
import base64
import csv
import json
import os
import re
import threading
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Optional, List, Dict, Any
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import httpx
from sqlalchemy import Column, Integer, String, JSON, DateTime, Float, ForeignKey, select, delete
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

DEFAULT_API_BASE_URL = "https://api.virtualstock.com/restapi/v4/orders/"
CONFIG_PATH = Path(os.path.expanduser("~/.order_manager_config.json"))
DATABASE_URL = "sqlite+aiosqlite:///orders.db"

def _maybe_apply_ttkbootstrap(root: tk.Tk):
    try:
        import ttkbootstrap as tb
        tb.Style(theme="darkly")
        root._uses_ttkb = True
    except Exception:
        root._uses_ttkb = False

@dataclass
class Settings:
    api_base_url: str = DEFAULT_API_BASE_URL
    auth_type: str = "Basic"
    basic_username: str = ""
    basic_password: str = ""
    bearer_token: str = ""
    api_key_header: str = "Authorization"
    api_key_value: str = ""
    extra_headers_json: str = ""
    timeout_seconds: int = 20
    default_limit: int = 50
    default_hours_back: int = 2
    def build_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {"Accept": "application/json"}
        if self.auth_type == "Basic":
            if self.basic_username or self.basic_password:
                token = base64.b64encode(f"{self.basic_username}:{self.basic_password}".encode("utf-8")).decode("ascii")
                headers["Authorization"] = f"Basic {token}"
        elif self.auth_type == "Bearer":
            if self.bearer_token:
                headers["Authorization"] = f"Bearer {self.bearer_token.strip()}"
        elif self.auth_type == "API Key":
            if self.api_key_header and self.api_key_value:
                headers[self.api_key_header.strip()] = self.api_key_value.strip()
        if self.extra_headers_json.strip():
            try:
                extra = json.loads(self.extra_headers_json)
                if isinstance(extra, dict):
                    headers.update({str(k): str(v) for k, v in extra.items()})
            except Exception:
                pass
        return headers

def load_settings() -> Settings:
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return Settings(**{**asdict(Settings()), **data})
        except Exception:
            pass
    return Settings()

def save_settings(s: Settings) -> None:
    CONFIG_PATH.write_text(json.dumps(asdict(s), indent=2), encoding="utf-8")

engine = create_async_engine(DATABASE_URL, echo=False, future=True)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    order_id = Column(String, unique=True, nullable=False)
    retailer = Column(String)
    order_reference = Column(String)
    order_date = Column(DateTime)
    status = Column(String)
    supplier_id = Column(Integer)
    currency_code = Column(String)
    subtotal = Column(Float)
    tax = Column(Float)
    total = Column(Float)
    shipping_address = Column(JSON)
    retailer_data = Column(JSON)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))
    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")

class OrderItem(Base):
    __tablename__ = "order_items"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"))
    part_number = Column(String)
    retailer_sku_reference = Column(String)
    supplier_sku_reference = Column(String)
    line_reference = Column(String)
    quantity = Column(Integer)
    name = Column(String)
    unit_cost_price = Column(Float)
    subtotal = Column(Float)
    tax = Column(Float)
    tax_rate = Column(Float)
    total = Column(Float)
    promised_date = Column(DateTime)
    order = relationship("Order", back_populates="items")

class LogEntry(Base):
    __tablename__ = "logs"
    id = Column(Integer, primary_key=True)
    level = Column(String, default="INFO", nullable=False)
    message = Column(String, nullable=False)
    details = Column(JSON)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC), nullable=False)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def db_log(level: str, message: str, details: Optional[Dict[str, Any]] = None) -> None:
    async with AsyncSessionLocal() as db:
        db.add(LogEntry(level=level, message=message, details=details or {}))
        await db.commit()

def log_async(level: str, message: str, details: Optional[Dict[str, Any]] = None, ui=None):
    if ui is None:
        def _runner():
            asyncio.run(db_log(level, message, details))
        threading.Thread(target=_runner, daemon=True).start()
    else:
        ui._run_bg(lambda: db_log(level, message, details))

def _to_int(x, default=None):
    try:
        return int(x)
    except Exception:
        return default

def _to_float(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default

def _extract_supplier_id(supplier):
    if supplier is None:
        return None
    if isinstance(supplier, int):
        return supplier
    if isinstance(supplier, dict):
        for k in ("id", "supplier_id"):
            if k in supplier:
                v = _to_int(supplier[k])
                if v is not None:
                    return v
        return None
    if isinstance(supplier, str):
        parts = [p for p in supplier.strip("/").split("/") if p]
        for p in reversed(parts):
            if p.isdigit():
                return int(p)
        m = re.search(r"(\d+)", supplier)
        return int(m.group(1)) if m else None
    return None

def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        s2 = s.replace("Z", "+00:00") if isinstance(s, str) and s.endswith("Z") else s
        return datetime.fromisoformat(s2)
    except Exception:
        return None

async def fetch_orders(s: Settings, http: httpx.AsyncClient, limit: int, offset: int, updated_since: Optional[str]):
    params = {"limit": limit, "offset": offset}
    if updated_since:
        params["updated_since"] = updated_since
    r = await http.get(s.api_base_url, headers=s.build_headers(), params=params, timeout=s.timeout_seconds)
    r.raise_for_status()
    return r.json()

async def save_orders_to_db(payload: dict, db: AsyncSession):
    results: List[dict] = payload.get("results", [])
    for od in results:
        key = od.get("order_reference") or od.get("order_id") or od.get("id")
        if not key:
            continue
        q = await db.execute(select(Order).where(Order.order_id == key))
        order_rec: Optional[Order] = q.scalar_one_or_none()
        supplier_id_val = _extract_supplier_id(od.get("supplier"))
        subtotal_val = _to_float(od.get("subtotal"))
        tax_val = _to_float(od.get("tax"))
        total_val = _to_float(od.get("total"))
        if order_rec is None:
            order_rec = Order(
                order_id=key,
                retailer=od.get("retailer"),
                order_reference=od.get("order_reference"),
                order_date=_parse_dt(od.get("order_date")),
                status=od.get("status"),
                supplier_id=supplier_id_val,
                currency_code=od.get("currency_code"),
                subtotal=subtotal_val,
                tax=tax_val,
                total=total_val,
                shipping_address=od.get("shipping_address"),
                retailer_data=od.get("retailer_data"),
            )
            db.add(order_rec)
        else:
            order_rec.status = od.get("status", order_rec.status)
            order_rec.order_date = _parse_dt(od.get("order_date")) or order_rec.order_date
            order_rec.subtotal = subtotal_val if subtotal_val is not None else order_rec.subtotal
            order_rec.tax = tax_val if tax_val is not None else order_rec.tax
            order_rec.total = total_val if total_val is not None else order_rec.total
            order_rec.currency_code = od.get("currency_code", order_rec.currency_code)
            order_rec.shipping_address = od.get("shipping_address", order_rec.shipping_address)
            order_rec.retailer_data = od.get("retailer_data", order_rec.retailer_data)
            order_rec.supplier_id = supplier_id_val if supplier_id_val is not None else order_rec.supplier_id
        order_rec.items.clear()
        for it in od.get("items", []):
            order_rec.items.append(
                OrderItem(
                    part_number=it.get("part_number"),
                    retailer_sku_reference=it.get("retailer_sku_reference"),
                    supplier_sku_reference=it.get("supplier_sku_reference"),
                    line_reference=it.get("line_reference"),
                    quantity=_to_int(it.get("quantity"), 0) or 0,
                    name=it.get("name"),
                    unit_cost_price=_to_float(it.get("unit_cost_price")),
                    subtotal=_to_float(it.get("subtotal")),
                    tax=_to_float(it.get("tax")),
                    tax_rate=_to_float(it.get("tax_rate")),
                    total=_to_float(it.get("total")),
                    promised_date=_parse_dt(it.get("promised_date")),
                )
            )
    await db.commit()

async def fetch_and_process_orders(s: Settings, limit: Optional[int] = None, hours_back: Optional[int] = None) -> int:
    saved = 0
    limit = int(limit or s.default_limit)
    hours_back = int(hours_back or s.default_hours_back)
    async with httpx.AsyncClient() as http, AsyncSessionLocal() as db:
        offset = 0
        updated_since = (datetime.now(UTC) - timedelta(hours=hours_back)).isoformat(timespec="seconds").replace("+00:00", "Z")
        while True:
            data = await fetch_orders(s, http, limit, offset, updated_since)
            rows = data.get("results", [])
            if not rows:
                break
            await save_orders_to_db(data, db)
            saved += len(rows)
            if not data.get("next"):
                break
            offset += limit
    return saved

class OrderManagerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Order Manager")
        self.geometry("1180x720")
        self.minsize(1000, 640)
        _maybe_apply_ttkbootstrap(self)
        self.settings = load_settings()
        self._build_ui()
        self._run_bg(self._init_and_initial_load)

    def _run_bg(self, coro_func, on_done=None):
        def _runner():
            try:
                result = asyncio.run(coro_func())
            except Exception as exc:
                err_msg = f"{type(exc).__name__}: {exc}"
                log_async("ERROR", "Background task failed", {"error": err_msg}, ui=self)
                self._ui(lambda m=err_msg: messagebox.showerror("Error", m))
                return
            if on_done:
                self._ui(lambda r=result: on_done(r))
        threading.Thread(target=_runner, daemon=True).start()

    def _ui(self, fn):
        self.after(0, fn)

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.nb = ttk.Notebook(self)
        self.nb.grid(row=0, column=0, sticky="nsew")
        self.tab_orders = ttk.Frame(self.nb, padding=8)
        self.nb.add(self.tab_orders, text="Orders")
        self._build_orders_tab(self.tab_orders)
        self.tab_settings = ttk.Frame(self.nb, padding=8)
        self.nb.add(self.tab_settings, text="Settings")
        self._build_settings_tab(self.tab_settings)
        self.tab_logs = ttk.Frame(self.nb, padding=8)
        self.nb.add(self.tab_logs, text="Logs")
        self._build_logs_tab(self.tab_logs)
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(self, textvariable=self.status_var, anchor="w").grid(row=1, column=0, sticky="ew")

    def _build_orders_tab(self, parent: ttk.Frame):
        parent.rowconfigure(2, weight=1)
        parent.columnconfigure(0, weight=1)
        toolbar = ttk.Frame(parent)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(toolbar, text="Fetch Latest", command=self._on_fetch).pack(side="left")
        ttk.Button(toolbar, text="Refresh View", command=self._refresh_view).pack(side="left", padx=(6, 0))
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Label(toolbar, text="Status:").pack(side="left")
        self.filter_status = ttk.Combobox(toolbar, width=16, values=["All", "Open", "In Progress", "Monitoring", "Resolved", "Closed", "Deferred", "Cancelled", "Dispatched"], state="readonly")
        self.filter_status.set("All")
        self.filter_status.pack(side="left", padx=(4, 8))
        ttk.Label(toolbar, text="Retailer:").pack(side="left")
        self.filter_retailer = ttk.Entry(toolbar, width=18)
        self.filter_retailer.pack(side="left", padx=(4, 8))
        ttk.Label(toolbar, text="From (YYYY-MM-DD):").pack(side="left")
        self.filter_from = ttk.Entry(toolbar, width=12)
        self.filter_from.pack(side="left", padx=(4, 8))
        ttk.Label(toolbar, text="To (YYYY-MM-DD):").pack(side="left")
        self.filter_to = ttk.Entry(toolbar, width=12)
        self.filter_to.pack(side="left", padx=(4, 8))
        ttk.Button(toolbar, text="Apply", command=self._refresh_view).pack(side="left")
        ttk.Button(toolbar, text="Clear", command=self._clear_filters).pack(side="left", padx=(6, 0))
        ttk.Separator(parent).grid(row=1, column=0, sticky="ew", pady=(0, 6))
        top = ttk.Frame(parent)
        top.grid(row=2, column=0, sticky="nsew")
        top.rowconfigure(0, weight=1)
        top.columnconfigure(0, weight=1)
        self.orders_tree = ttk.Treeview(top, columns=("order_id", "retailer", "order_date", "status", "subtotal", "tax", "total", "currency"), show="headings", height=12)
        for col, w in [("order_id", 160), ("retailer", 120), ("order_date", 160), ("status", 120), ("subtotal", 90), ("tax", 70), ("total", 90), ("currency", 80)]:
            self.orders_tree.heading(col, text=col.replace("_", " ").title())
            self.orders_tree.column(col, width=w, anchor="w")
        self.orders_tree.grid(row=0, column=0, sticky="nsew")
        sb_orders = ttk.Scrollbar(top, orient="vertical", command=self.orders_tree.yview)
        sb_orders.grid(row=0, column=1, sticky="ns")
        self.orders_tree.configure(yscrollcommand=sb_orders.set)
        self.orders_tree.bind("<<TreeviewSelect>>", self._on_order_select)
        ttk.Separator(parent).grid(row=3, column=0, sticky="ew", pady=6)
        bottom = ttk.Frame(parent)
        bottom.grid(row=4, column=0, sticky="nsew")
        bottom.rowconfigure(0, weight=1)
        bottom.columnconfigure(0, weight=1)
        parent.rowconfigure(4, weight=1)
        self.items_tree = ttk.Treeview(bottom, columns=("line_reference", "part_number", "name", "qty", "unit_cost", "subtotal", "tax", "tax_rate", "total", "promised_date", "retailer_sku", "supplier_sku"), show="headings")
        for col, w in [("line_reference", 120), ("part_number", 120), ("name", 220), ("qty", 60), ("unit_cost", 90), ("subtotal", 90), ("tax", 80), ("tax_rate", 80), ("total", 90), ("promised_date", 160), ("retailer_sku", 140), ("supplier_sku", 140)]:
            self.items_tree.heading(col, text=col.replace("_", " ").title())
            self.items_tree.column(col, width=w, anchor="w")
        self.items_tree.grid(row=0, column=0, sticky="nsew")
        sb_items = ttk.Scrollbar(bottom, orient="vertical", command=self.items_tree.yview)
        sb_items.grid(row=0, column=1, sticky="ns")
        self.items_tree.configure(yscrollcommand=sb_items.set)
        footer = ttk.Frame(parent)
        footer.grid(row=5, column=0, sticky="ew", pady=(6, 0))
        ttk.Button(footer, text="Export Orders CSV", command=self._export_orders).pack(side="left")
        ttk.Button(footer, text="Export Items CSV", command=self._export_items).pack(side="left", padx=(6, 0))
        ttk.Button(footer, text="Delete Selected", command=self._delete_selected).pack(side="right")

    def _build_settings_tab(self, parent: ttk.Frame):
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text="API Base URL").grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.var_api_url = tk.StringVar(value=self.settings.api_base_url)
        ttk.Entry(parent, textvariable=self.var_api_url).grid(row=0, column=1, sticky="ew", pady=(0, 4))
        ttk.Label(parent, text="Auth Type").grid(row=1, column=0, sticky="w")
        self.var_auth_type = tk.StringVar(value=self.settings.auth_type)
        cb = ttk.Combobox(parent, textvariable=self.var_auth_type, values=["Basic", "Bearer", "API Key"], state="readonly", width=12)
        cb.grid(row=1, column=1, sticky="w")
        cb.bind("<<ComboboxSelected>>", lambda _e: self._render_auth_fields())
        self.auth_fields = ttk.Frame(parent)
        self.auth_fields.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 6))
        self.auth_fields.columnconfigure(1, weight=1)
        self._render_auth_fields()
        ttk.Label(parent, text="Extra Headers (JSON)").grid(row=3, column=0, sticky="nw", pady=(6, 0))
        self.txt_extra_headers = tk.Text(parent, height=5)
        self.txt_extra_headers.grid(row=3, column=1, sticky="ew", pady=(6, 0))
        self.txt_extra_headers.insert("1.0", self.settings.extra_headers_json)
        ttk.Label(parent, text="Timeout (sec)").grid(row=4, column=0, sticky="w", pady=(6, 0))
        self.var_timeout = tk.IntVar(value=self.settings.timeout_seconds)
        ttk.Spinbox(parent, from_=5, to=120, textvariable=self.var_timeout, width=8).grid(row=4, column=1, sticky="w")
        ttk.Label(parent, text="Default Limit").grid(row=5, column=0, sticky="w", pady=(6, 0))
        self.var_limit = tk.IntVar(value=self.settings.default_limit)
        ttk.Spinbox(parent, from_=1, to=500, textvariable=self.var_limit, width=8).grid(row=5, column=1, sticky="w")
        ttk.Label(parent, text="Default Hours Back").grid(row=6, column=0, sticky="w", pady=(6, 0))
        self.var_hours_back = tk.IntVar(value=self.settings.default_hours_back)
        ttk.Spinbox(parent, from_=1, to=72, textvariable=self.var_hours_back, width=8).grid(row=6, column=1, sticky="w")
        btns = ttk.Frame(parent)
        btns.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(btns, text="Validate Headers", command=self._validate_headers).pack(side="left")
        ttk.Button(btns, text="Test Connection", command=self._test_connection).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text="Save Settings", command=self._save_settings_from_ui).pack(side="right")
        ttk.Button(btns, text="Reload Settings", command=self._reload_settings_into_ui).pack(side="right", padx=(6, 0))

    def _build_logs_tab(self, parent: ttk.Frame):
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)
        self.log_txt = tk.Text(parent, height=12, state="normal")
        sb = ttk.Scrollbar(parent, orient="vertical", command=self.log_txt.yview)
        self.log_txt.configure(yscrollcommand=sb.set)
        self.log_txt.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")

    def _render_auth_fields(self):
        for w in self.auth_fields.winfo_children():
            w.destroy()
        t = self.var_auth_type.get()
        if t == "Basic":
            ttk.Label(self.auth_fields, text="Username").grid(row=0, column=0, sticky="w")
            self.var_basic_user = tk.StringVar(value=self.settings.basic_username)
            ttk.Entry(self.auth_fields, textvariable=self.var_basic_user).grid(row=0, column=1, sticky="ew", pady=2)
            ttk.Label(self.auth_fields, text="Password").grid(row=1, column=0, sticky="w")
            self.var_basic_pass = tk.StringVar(value=self.settings.basic_password)
            ttk.Entry(self.auth_fields, textvariable=self.var_basic_pass, show="*").grid(row=1, column=1, sticky="ew", pady=2)
        elif t == "Bearer":
            ttk.Label(self.auth_fields, text="Bearer Token").grid(row=0, column=0, sticky="w")
            self.var_bearer = tk.StringVar(value=self.settings.bearer_token)
            ttk.Entry(self.auth_fields, textvariable=self.var_bearer, show="*").grid(row=0, column=1, sticky="ew", pady=2)
        else:
            ttk.Label(self.auth_fields, text="Header Name").grid(row=0, column=0, sticky="w")
            self.var_key_hdr = tk.StringVar(value=self.settings.api_key_header)
            ttk.Entry(self.auth_fields, textvariable=self.var_key_hdr).grid(row=0, column=1, sticky="ew", pady=2)
            ttk.Label(self.auth_fields, text="Header Value").grid(row=1, column=0, sticky="w")
            self.var_key_val = tk.StringVar(value=self.settings.api_key_value)
            ttk.Entry(self.auth_fields, textvariable=self.var_key_val, show="*").grid(row=1, column=1, sticky="ew", pady=2)

    async def _init_and_initial_load(self):
        self._set_status("Initializing database…")
        await init_db()
        self._set_status("Loading orders…")
        await self._refresh_view_async()
        self._set_status("Ready.")

    def _on_fetch(self):
        s = self._collect_settings_from_ui()
        if not self._auth_present(s):
            messagebox.showwarning("Missing Credentials", "Please configure auth in Settings.")
            self.nb.select(self.tab_settings)
            return
        self._append_log("Fetching latest orders…", level="INFO", details={"hours_back": int(s.default_hours_back), "limit": int(s.default_limit)})
        self._set_status("Fetching latest orders…")
        def on_done(saved_count: int):
            self._append_log(f"Fetched and saved {saved_count} orders.", level="INFO", details={"saved_count": saved_count})
            messagebox.showinfo("Fetch Complete", f"Fetched and saved {saved_count} orders.")
            self._refresh_view()
            self._set_status("Fetch complete.")
        self._run_bg(lambda: fetch_and_process_orders(s, s.default_limit, s.default_hours_back), on_done=on_done)

    def _refresh_view(self):
        self._run_bg(self._refresh_view_async)

    async def _refresh_view_async(self):
        status = self.filter_status.get().strip()
        retailer = self.filter_retailer.get().strip()
        df = self._parse_date(self.filter_from.get().strip())
        dt = self._parse_date(self.filter_to.get().strip())
        if df and dt and df > dt:
            df, dt = dt, df
        async with AsyncSessionLocal() as db:
            stmt = select(Order)
            if status and status != "All":
                stmt = stmt.where(Order.status == status)
            if retailer:
                stmt = stmt.where(Order.retailer == retailer)
            if df:
                stmt = stmt.where(Order.order_date >= df)
            if dt:
                stmt = stmt.where(Order.order_date < dt + timedelta(days=1))
            stmt = stmt.order_by(Order.order_date.desc().nullslast())
            res = await db.execute(stmt)
            orders: List[Order] = res.scalars().all()
            self._ui(lambda o=orders: self._render_orders(o))

    def _on_order_select(self, _evt=None):
        sel = self.orders_tree.selection()
        if not sel:
            return
        oid = int(sel[0])
        async def load_items():
            async with AsyncSessionLocal() as db:
                q = await db.execute(select(Order).where(Order.id == oid))
                order = q.scalar_one_or_none()
                return list(order.items) if order else []
        self._run_bg(load_items, on_done=lambda items: self._render_items(items))

    def _delete_selected(self):
        sel = self.orders_tree.selection()
        if not sel:
            messagebox.showinfo("Delete", "Select one or more orders.")
            return
        if not messagebox.askyesno("Confirm", f"Delete {len(sel)} selected order(s)?"):
            return
        ids = [int(i) for i in sel]
        async def _do_delete():
            async with AsyncSessionLocal() as db:
                await db.execute(delete(OrderItem).where(OrderItem.order_id.in_(ids)))
                await db.execute(delete(Order).where(Order.id.in_(ids)))
                await db.commit()
        def _after(_r):
            self._append_log(f"Deleted {len(ids)} order(s).", level="WARN", details={"ids": ids})
            self._set_status(f"Deleted {len(ids)} order(s).")
            self._refresh_view()
        self._run_bg(_do_delete, on_done=_after)

    def _export_orders(self):
        path = filedialog.asksaveasfilename(defaultextension=".csv", initialfile="orders.csv")
        if not path:
            return
        async def _do():
            async with AsyncSessionLocal() as db:
                res = await db.execute(select(Order).order_by(Order.order_date.desc().nullslast()))
                return res.scalars().all()
        def _done(orders: List[Order]):
            try:
                with open(path, "w", newline="", encoding="utf-8") as f:
                    w = csv.writer(f)
                    w.writerow(["id", "order_id", "retailer", "order_reference", "order_date", "status", "supplier_id", "currency", "subtotal", "tax", "total"])
                    for o in orders:
                        w.writerow([o.id, o.order_id, o.retailer, o.order_reference, o.order_date.isoformat() if o.order_date else "", o.status, o.supplier_id, o.currency_code, o.subtotal, o.tax, o.total])
                messagebox.showinfo("Export", f"Exported {len(orders)} orders.")
            except Exception as exc:
                messagebox.showerror("Export Failed", str(exc))
        self._run_bg(_do, on_done=_done)

    def _export_items(self):
        path = filedialog.asksaveasfilename(defaultextension=".csv", initialfile="order_items.csv")
        if not path:
            return
        async def _do():
            async with AsyncSessionLocal() as db:
                res = await db.execute(select(OrderItem))
                return res.scalars().all()
        def _done(items: List[OrderItem]):
            try:
                with open(path, "w", newline="", encoding="utf-8") as f:
                    w = csv.writer(f)
                    w.writerow(["id", "order_fk", "line_reference", "part_number", "name", "qty", "unit_cost", "subtotal", "tax", "tax_rate", "total", "promised_date", "retailer_sku", "supplier_sku"])
                    for it in items:
                        w.writerow([it.id, it.order_id, it.line_reference, it.part_number, it.name, it.quantity, it.unit_cost_price, it.subtotal, it.tax, it.tax_rate, it.total, it.promised_date.isoformat() if it.promised_date else "", it.retailer_sku_reference, it.supplier_sku_reference])
                messagebox.showinfo("Export", f"Exported {len(items)} items.")
            except Exception as exc:
                messagebox.showerror("Export Failed", str(exc))
        self._run_bg(_do, on_done=_done)

    def _collect_settings_from_ui(self) -> Settings:
        s = Settings(
            api_base_url=self.var_api_url.get().strip() or DEFAULT_API_BASE_URL,
            auth_type=self.var_auth_type.get().strip() or "Basic",
            extra_headers_json=self.txt_extra_headers.get("1.0", "end").strip(),
            timeout_seconds=int(self.var_timeout.get() or 20),
            default_limit=int(self.var_limit.get() or 50),
            default_hours_back=int(self.var_hours_back.get() or 2),
        )
        if s.auth_type == "Basic":
            s.basic_username = getattr(self, "var_basic_user", tk.StringVar()).get()
            s.basic_password = getattr(self, "var_basic_pass", tk.StringVar()).get()
        elif s.auth_type == "Bearer":
            s.bearer_token = getattr(self, "var_bearer", tk.StringVar()).get()
        else:
            s.api_key_header = getattr(self, "var_key_hdr", tk.StringVar(value="Authorization")).get()
            s.api_key_value = getattr(self, "var_key_val", tk.StringVar()).get()
        return s

    def _save_settings_from_ui(self):
        s = self._collect_settings_from_ui()
        if s.extra_headers_json.strip():
            try:
                parsed = json.loads(s.extra_headers_json)
                if not isinstance(parsed, dict):
                    raise ValueError("Extra headers must be a JSON object.")
            except Exception as exc:
                messagebox.showerror("Invalid Headers JSON", str(exc))
                return
        save_settings(s)
        self.settings = s
        messagebox.showinfo("Settings", f"Saved to {CONFIG_PATH}")

    def _reload_settings_into_ui(self):
        self.settings = load_settings()
        self.var_api_url.set(self.settings.api_base_url)
        self.var_auth_type.set(self.settings.auth_type)
        self._render_auth_fields()
        if self.settings.auth_type == "Basic":
            self.var_basic_user.set(self.settings.basic_username)
            self.var_basic_pass.set(self.settings.basic_password)
        elif self.settings.auth_type == "Bearer":
            self.var_bearer.set(self.settings.bearer_token)
        else:
            self.var_key_hdr.set(self.settings.api_key_header)
            self.var_key_val.set(self.settings.api_key_value)
        self.txt_extra_headers.delete("1.0", "end")
        self.txt_extra_headers.insert("1.0", self.settings.extra_headers_json)
        self.var_timeout.set(self.settings.timeout_seconds)
        self.var_limit.set(self.settings.default_limit)
        self.var_hours_back.set(self.settings.default_hours_back)
        messagebox.showinfo("Settings", "Reloaded from disk.")

    def _validate_headers(self):
        try:
            txt = self.txt_extra_headers.get("1.0", "end").strip()
            if txt:
                obj = json.loads(txt)
                if not isinstance(obj, dict):
                    raise ValueError("JSON must be an object of key/value pairs.")
            s = self._collect_settings_from_ui()
            headers = s.build_headers()
            pretty = json.dumps(headers, indent=2)
            messagebox.showinfo("Headers Preview", pretty)
        except Exception as exc:
            messagebox.showerror("Invalid Headers JSON", str(exc))

    def _test_connection(self):
        s = self._collect_settings_from_ui()
        if not self._auth_present(s):
            messagebox.showwarning("Missing Credentials", "Please configure auth first.")
            return
        async def _do():
            updated_since = (datetime.now(UTC) - timedelta(hours=1)).isoformat(timespec="seconds").replace("+00:00", "Z")
            async with httpx.AsyncClient() as http:
                r = await http.get(s.api_base_url, headers=s.build_headers(), params={"limit": 1, "offset": 0, "updated_since": updated_since}, timeout=s.timeout_seconds)
                r.raise_for_status()
                return r.json()
        def _done(resp: Dict[str, Any]):
            cnt = len(resp.get("results", [])) if isinstance(resp, dict) else 0
            messagebox.showinfo("Test OK", f"Endpoint reachable. Example results: {cnt}")
        self._run_bg(_do, on_done=_done)

    @staticmethod
    def _auth_present(s: Settings) -> bool:
        if s.auth_type == "Basic":
            return bool(s.basic_username or s.basic_password)
        if s.auth_type == "Bearer":
            return bool(s.bearer_token)
        return bool(s.api_key_header and s.api_key_value)

    def _render_orders(self, orders: List[Order]):
        self.orders_tree.delete(*self.orders_tree.get_children())
        for o in orders:
            vals = (o.order_id, o.retailer or "", o.order_date.isoformat(timespec="seconds") if o.order_date else "", o.status or "", f"{o.subtotal:.2f}" if o.subtotal is not None else "", f"{o.tax:.2f}" if o.tax is not None else "", f"{o.total:.2f}" if o.total is not None else "", o.currency_code or "")
            self.orders_tree.insert("", "end", iid=str(o.id), values=vals)
        self.items_tree.delete(*self.items_tree.get_children())
        self._set_status(f"Loaded {len(orders)} order(s).")

    def _render_items(self, items: List[OrderItem]):
        self.items_tree.delete(*self.items_tree.get_children())
        for it in items:
            vals = (it.line_reference or "", it.part_number or "", it.name or "", it.quantity or 0, f"{it.unit_cost_price:.2f}" if it.unit_cost_price is not None else "", f"{it.subtotal:.2f}" if it.subtotal is not None else "", f"{it.tax:.2f}" if it.tax is not None else "", f"{it.tax_rate:.2f}" if it.tax_rate is not None else "", f"{it.total:.2f}" if it.total is not None else "", it.promised_date.isoformat(timespec="seconds") if it.promised_date else "", it.retailer_sku_reference or "", it.supplier_sku_reference or "")
            self.items_tree.insert("", "end", values=vals)

    def _append_log(self, msg: str, level: str = "INFO", details: Optional[Dict[str, Any]] = None):
        log_async(level, msg, details or {}, ui=self)
        ts = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        if hasattr(self, "log_txt") and isinstance(getattr(self, "log_txt"), tk.Text):
            try:
                self.log_txt.insert("end", f"[{ts}] {level:<5} {msg}\n")
                self.log_txt.see("end")
            except Exception:
                pass

    def _set_status(self, message: str):
        self.status_var.set(message)
        self._append_log(message, level="INFO")

    def _clear_filters(self):
        self.filter_status.set("All")
        self.filter_retailer.delete(0, tk.END)
        self.filter_from.delete(0, tk.END)
        self.filter_to.delete(0, tk.END)
        self._set_status("Filters cleared.")
        self._refresh_view()

    @staticmethod
    def _parse_date(s: str) -> Optional[datetime]:
        if not s:
            return None
        try:
            return datetime.strptime(s, "%Y-%m-%d")
        except ValueError:
            messagebox.showerror("Date Error", f"Invalid date: {s}. Use YYYY-MM-DD.")
            return None

if __name__ == "__main__":
    app = OrderManagerApp()
    app.mainloop()
