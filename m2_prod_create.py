from __future__ import annotations
import os
import sys
import json
import base64
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from functools import partial
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
CONFIG_FILE = "config.json"
DEFAULT_TIMEOUT = 30  # seconds
MAX_WORKERS = min(8, (os.cpu_count() or 4))
class DarkTheme:
    BG = "#0f0f10"
    PANEL = "#13141a"
    PANEL2 = "#1a1b1f"
    FG = "#f4f4f5"
    MUTED = "#c0c2c9"
    ACCENT = "#00d4ff"
    ENTRY = "#23242a"
    BTN = "#24262e"
    BTN_HOVER = "#2b2e39"
    BORDER = "#262b36"
    @classmethod
    def apply(cls, root: tk.Tk):
        style = ttk.Style(root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TFrame", background=cls.BG)
        style.configure("TLabelframe", background=cls.PANEL, foreground=cls.FG, bordercolor=cls.BORDER)
        style.configure("TLabelframe.Label", background=cls.PANEL, foreground=cls.FG)
        style.configure("TLabel", background=cls.BG, foreground=cls.FG)
        style.configure("TButton", background=cls.BTN, foreground=cls.FG, bordercolor=cls.BORDER, focusthickness=2, focuscolor=cls.ACCENT)
        style.map("TButton", background=[("active", cls.BTN_HOVER)])
        style.configure("TNotebook", background=cls.BG, bordercolor=cls.BORDER)
        style.configure("TNotebook.Tab", background=cls.PANEL2, foreground=cls.FG)
        style.map("TNotebook.Tab", background=[("selected", cls.PANEL)], foreground=[("selected", cls.FG)])
        style.configure("TEntry", fieldbackground=cls.ENTRY, foreground=cls.FG, bordercolor=cls.BORDER)
        style.configure("TCombobox", fieldbackground=cls.ENTRY, foreground=cls.FG, background=cls.ENTRY)
        style.configure("Treeview", background=cls.PANEL2, fieldbackground=cls.PANEL2, foreground=cls.FG, bordercolor=cls.BORDER)
        style.configure("TScrollbar", troughcolor=cls.PANEL, background=cls.PANEL2)
        root.configure(bg=cls.BG)
def add_context_menu(widget: tk.Widget):
    menu = tk.Menu(widget, tearoff=0)
    menu.add_command(label="Cut", command=lambda: widget.event_generate("<<Cut>>"))
    menu.add_command(label="Copy", command=lambda: widget.event_generate("<<Copy>>"))
    menu.add_command(label="Paste", command=lambda: widget.event_generate("<<Paste>>"))
    menu.add_separator()
    menu.add_command(label="Select All", command=lambda: widget.event_generate("<<SelectAll>>"))
    def show_menu(event):
        menu.tk_popup(event.x_root, event.y_root)
    widget.bind("<Button-3>", show_menu)
class MagentoAPIClient:
    def __init__(self, base_url: str, token: str, timeout: int = DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=0.4,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "POST", "PUT", "DELETE")
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }
    def _make_request(self, method, endpoint, **kwargs):
        url = f"{self.base_url}/rest/V1{endpoint}"
        try:
            if 'timeout' not in kwargs:
                kwargs['timeout'] = self.timeout
            resp = self.session.request(method, url, headers=self.headers, **kwargs)
            resp.raise_for_status()
            if not resp.text:
                return {}
            ct = resp.headers.get('Content-Type', '')
            return resp.json() if 'application/json' in ct or resp.text.strip().startswith('{') else resp.text
        except requests.exceptions.HTTPError as e:
            msg = f"HTTP {e.response.status_code} {method} {url}"
            try:
                details = e.response.json()
                msg += "\n" + json.dumps(details, indent=2)
            except Exception:
                msg += f"\n{e.response.text}"
            raise ConnectionError(msg) from e
        except requests.exceptions.RequestException as e:
            raise ConnectionError(f"Connection failed: {e}") from e
    def get_attribute_sets(self):
        sc = "searchCriteria[filter_groups][0][filters][0][field]=entity_type_id&searchCriteria[filter_groups][0][filters][0][value]=4"
        return self._make_request("GET", f"/eav/attribute-sets/list?{sc}").get('items', [])
    def get_attributes_for_set(self, set_id: int):
        return self._make_request("GET", f"/products/attribute-sets/{set_id}/attributes")
    def get_attribute_options(self, attribute_code: str):
        return self._make_request("GET", f"/products/attributes/{attribute_code}/options")
    def get_category_tree(self):
        return self._make_request("GET", "/categories")
    def get_product(self, sku: str):
        return self._make_request("GET", f"/products/{sku}")
    def create_product(self, payload: dict):
        return self._make_request("POST", "/products", data=json.dumps(payload))
    def update_product(self, sku: str, payload: dict):
        return self._make_request("PUT", f"/products/{sku}", data=json.dumps(payload))
class SimpleCategoryTree:
    def __init__(self, parent: ttk.Frame):
        self.frame = ttk.Frame(parent)
        self.search_var = tk.StringVar()
        top = ttk.Frame(self.frame)
        top.pack(fill='x')
        ttk.Label(top, text="Filter:").pack(side='left')
        self.search_entry = ttk.Entry(top, textvariable=self.search_var)
        self.search_entry.pack(side='left', fill='x', expand=True, padx=5, pady=5)
        self.search_entry.bind("<KeyRelease>", self._on_filter)
        self.tree = ttk.Treeview(self.frame, show='tree')
        self.tree.pack(fill='both', expand=True)
        self.tree.bind('<ButtonRelease-1>', self._on_click)
        self._checked = set()  # item ids that are checked
    def widget(self):
        return self.frame
    def clear(self):
        self._checked.clear()
        for i in self.tree.get_children(""):
            self.tree.delete(i)
    def build(self, root_node: dict):
        self.clear()
        def add_node(node, parent=""):
            nid = str(node.get('id', 'root'))
            name = str(node.get('name', '(unnamed)'))
            self.tree.insert(parent, 'end', iid=nid, text=self._text_for(nid, name))
            for child in (node.get('children_data') or []):
                add_node(child, nid)
        add_node(root_node)
        self.tree.heading('#0', text='Select Categories', anchor='w')
    def build_from_json(self, data):
        self.clear()
        nodes = []
        if isinstance(data, list):
            nodes = data
        elif isinstance(data, dict):
            if 'children' in data or 'children_data' in data:
                nodes = (data.get('children') or data.get('children_data') or [])
                if 'id' in data and 'name' in data:
                    nodes = [data]
            else:
                nodes = [data]
        else:
            return
        def add_any(node, parent=""):
            nid = str(node.get('id', node.get('value', node.get('code', node.get('name', 'node')))))
            name = str(node.get('name', node.get('label', nid)))
            self.tree.insert(parent, 'end', iid=nid, text=self._text_for(nid, name))
            kids = node.get('children') or node.get('children_data') or []
            for ch in kids:
                add_any(ch, nid)
        for n in nodes:
            add_any(n, "")
        self.tree.heading('#0', text='Select Categories', anchor='w')
    def _on_click(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self.toggle(iid)
    def toggle(self, iid: str):
        if iid in self._checked:
            self._checked.remove(iid)
        else:
            self._checked.add(iid)
        text = self.tree.item(iid, 'text')
        name = text.replace('[x] ', '').replace('[ ] ', '')
        self.tree.item(iid, text=self._text_for(iid, name))
    def _on_filter(self, *_):
        q = self.search_var.get().strip().lower()
        def match_any(item):
            txt = self.tree.item(item, 'text').strip().lower()
            if q in txt:
                return True
            return any(match_any(ch) for ch in self.tree.get_children(item))
        for item in self.tree.get_children(""):
            self._apply_filter(item, match_any)
    def _apply_filter(self, item, match_any):
        visible = match_any(item)
        if visible:
            self.tree.reattach(item, self.tree.parent(item) or '', 'end')
            for ch in self.tree.get_children(item):
                self._apply_filter(ch, match_any)
        else:
            self.tree.detach(item)
    def get_checked_ids(self):
        ordered = []
        def walk(item):
            if item in self._checked:
                ordered.append(item)
            for ch in self.tree.get_children(item):
                walk(ch)
        for root in self.tree.get_children(""):
            walk(root)
        return ordered
    def _text_for(self, iid: str, name: str) -> str:
        return ("[x] " if iid in self._checked else "[ ] ") + name
class AdvancedMagentoToolPro:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Magento 2 Product Creator — Pro")
        self.root.geometry("1200x820")
        DarkTheme.apply(self.root)
        self.api_client: MagentoAPIClient | None = None
        self.attribute_set_map: dict[str, int] = {}
        self.dynamic_widgets: dict[str, tk.Widget | tk.Variable | dict] = {}
        self.image_list: list[dict] = []  # {'path': str, 'roles': {role: tk.BooleanVar}}
        self._build_layout()
        self._build_api_tab()
        self._build_product_tab()
        self._build_status_bar()
        self._wire_global_context_menus()
        self._load_config()
    def _build_layout(self):
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(expand=True, fill='both', padx=10, pady=10)
        self.api_tab = ttk.Frame(self.nb, padding=10)
        self.product_tab = ttk.Frame(self.nb, padding=10)
        self.nb.add(self.api_tab, text='Configuration')
        self.nb.add(self.product_tab, text='Product Creator')
    def _build_status_bar(self):
        self.status_var = tk.StringVar(value="Ready")
        bar = ttk.Frame(self.root)
        bar.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_lbl = ttk.Label(bar, textvariable=self.status_var, anchor='w')
        self.status_lbl.pack(side=tk.LEFT, padx=6, pady=3)
    def set_status(self, text: str):
        self.status_var.set(text)
        self.root.update_idletasks()
    def _build_api_tab(self):
        f = self.api_tab
        f.columnconfigure(1, weight=1)
        ttk.Label(f, text="Magento Base URL:").grid(row=0, column=0, sticky='w', pady=5)
        self.url_entry = ttk.Entry(f)
        self.url_entry.grid(row=0, column=1, sticky='ew', padx=5)
        add_context_menu(self.url_entry)
        ttk.Label(f, text="Admin Token:").grid(row=1, column=0, sticky='w', pady=5)
        self.token_entry = ttk.Entry(f, show="*")
        self.token_entry.grid(row=1, column=1, sticky='ew', padx=5)
        add_context_menu(self.token_entry)
        show_var = tk.BooleanVar(value=False)
        def toggle_token():
            self.token_entry.configure(show='' if show_var.get() else '*')
        ttk.Checkbutton(f, text="Show", variable=show_var, command=toggle_token).grid(row=1, column=2, sticky='w')
        ttk.Label(f, text="Website IDs (comma)").grid(row=2, column=0, sticky='w', pady=5)
        self.websites_entry = ttk.Entry(f)
        self.websites_entry.insert(0, "1")
        self.websites_entry.grid(row=2, column=1, sticky='ew', padx=5)
        btns = ttk.Frame(f)
        btns.grid(row=3, column=1, sticky='w', pady=8)
        ttk.Button(btns, text="Save Config", command=self._save_config).pack(side='left', padx=4)
        ttk.Button(btns, text="Connect & Fetch", command=self.connect_and_fetch).pack(side='left', padx=4)
        ttk.Button(btns, text="Test Token", command=self.test_token).pack(side='left', padx=4)
    def _build_product_tab(self):
        pw = ttk.Panedwindow(self.product_tab, orient=tk.HORIZONTAL)
        pw.pack(expand=True, fill='both')
        left = ttk.Frame(pw)
        right = ttk.Frame(pw)
        pw.add(left, weight=2)
        pw.add(right, weight=1)
        core = ttk.Labelframe(left, text="Core Information", padding=10)
        core.pack(fill='x')
        for i in range(2):
            core.columnconfigure(i, weight=1)
        ttk.Label(core, text="Attribute Set:").grid(row=0, column=0, sticky='w')
        self.attribute_set_combo = ttk.Combobox(core, state='readonly')
        self.attribute_set_combo.grid(row=0, column=1, sticky='ew', padx=5, pady=5)
        self.attribute_set_combo.bind("<<ComboboxSelected>>", self.on_attribute_set_change)
        ttk.Label(core, text="Mode:").grid(row=0, column=2, sticky='e')
        self.mode_var = tk.StringVar(value="create")
        ttk.Radiobutton(core, text="Create", variable=self.mode_var, value="create").grid(row=0, column=3, sticky='w')
        ttk.Radiobutton(core, text="Update", variable=self.mode_var, value="update").grid(row=0, column=4, sticky='w')
        ttk.Label(core, text="SKU *").grid(row=1, column=0, sticky='w')
        self.sku_entry = ttk.Entry(core)
        self.sku_entry.grid(row=1, column=1, sticky='ew', padx=5, pady=5)
        add_context_menu(self.sku_entry)
        ttk.Button(core, text="Check SKU", command=self.check_sku).grid(row=1, column=2, sticky='w')
        ttk.Label(core, text="Name").grid(row=2, column=0, sticky='w')
        self.name_entry = ttk.Entry(core)
        self.name_entry.grid(row=2, column=1, sticky='ew', padx=5, pady=5)
        add_context_menu(self.name_entry)
        ttk.Label(core, text="Price").grid(row=3, column=0, sticky='w')
        self.price_entry = ttk.Entry(core)
        self.price_entry.grid(row=3, column=1, sticky='ew', padx=5, pady=5)
        add_context_menu(self.price_entry)
        ttk.Label(core, text="Qty").grid(row=4, column=0, sticky='w')
        self.qty_entry = ttk.Entry(core)
        self.qty_entry.grid(row=4, column=1, sticky='ew', padx=5, pady=5)
        add_context_menu(self.qty_entry)
        ttk.Label(core, text="Visibility").grid(row=3, column=2, sticky='e')
        self.visibility_combo = ttk.Combobox(core, state='readonly', values=[
            ("Not Visible Individually", 1), ("Catalog", 2), ("Search", 3), ("Catalog, Search", 4)
        ])
        self.visibility_combo.set("Catalog, Search")
        self.visibility_combo.grid(row=3, column=3, sticky='w')
        ttk.Label(core, text="Status").grid(row=4, column=2, sticky='e')
        self.status_combo = ttk.Combobox(core, state='readonly', values=[("Disabled", 2), ("Enabled", 1)])
        self.status_combo.set("Enabled")
        self.status_combo.grid(row=4, column=3, sticky='w')
        ttk.Label(core, text="Type").grid(row=5, column=2, sticky='e')
        self.type_combo = ttk.Combobox(core, state='readonly', values=['simple','virtual'])
        self.type_combo.set('simple')
        self.type_combo.grid(row=5, column=3, sticky='w')
        desc = ttk.Labelframe(left, text="Description", padding=10)
        desc.pack(fill='x', pady=(6,6))
        self.desc_text = scrolledtext.ScrolledText(desc, height=5, wrap=tk.WORD, bg=DarkTheme.PANEL2, fg=DarkTheme.FG, insertbackground=DarkTheme.FG)
        self.desc_text.pack(fill='both', expand=True)
        add_context_menu(self.desc_text)
        attrs = ttk.Labelframe(left, text="Attributes", padding=10)
        attrs.pack(fill='both', expand=True)
        self.attr_canvas = tk.Canvas(attrs, highlightthickness=0, bg=DarkTheme.PANEL)
        self.attr_scroll = ttk.Scrollbar(attrs, orient='vertical', command=self.attr_canvas.yview)
        self.attr_body = ttk.Frame(self.attr_canvas)
        self.attr_body.bind("<Configure>", lambda e: self.attr_canvas.configure(scrollregion=self.attr_canvas.bbox("all")))
        self.attr_canvas.create_window((0,0), window=self.attr_body, anchor='nw')
        self.attr_canvas.configure(yscrollcommand=self.attr_scroll.set)
        self.attr_canvas.pack(side='left', fill='both', expand=True)
        self.attr_scroll.pack(side='right', fill='y')
        rnb = ttk.Notebook(right)
        rnb.pack(fill='both', expand=True)
        self.cat_tab = ttk.Frame(rnb, padding=6)
        self.img_tab = ttk.Frame(rnb, padding=6)
        self.log_tab = ttk.Frame(rnb, padding=6)
        self.tools_tab = ttk.Frame(rnb, padding=6)
        rnb.add(self.cat_tab, text='Categories')
        rnb.add(self.img_tab, text='Images')
        rnb.add(self.log_tab, text='Log')
        rnb.add(self.tools_tab, text='Tools')
        cat_top = ttk.Frame(self.cat_tab); cat_top.pack(fill='x')
        ttk.Button(cat_top, text="Load Categories JSON…", command=self.load_categories_json).pack(side='left')
        ttk.Button(cat_top, text="Clear", command=self.clear_categories).pack(side='left', padx=4)
        self.cb_tree = SimpleCategoryTree(self.cat_tab)
        self.cb_tree.widget().pack(fill='both', expand=True)
        img_top = ttk.Frame(self.img_tab)
        img_top.pack(fill='x')
        ttk.Button(img_top, text="Add Images…", command=self.add_images).pack(side='left')
        ttk.Button(img_top, text="Remove Selected", command=self.remove_selected_image).pack(side='left', padx=4)
        self.img_list = ttk.Treeview(self.img_tab, columns=("file","roles"), show='headings', selectmode='browse')
        self.img_list.heading('file', text='File')
        self.img_list.heading('roles', text='Roles')
        self.img_list.pack(fill='both', expand=True, pady=6)
        self.img_list.bind('<Double-1>', self._toggle_image_role)
        img_btns = ttk.Frame(self.img_tab)
        img_btns.pack(fill='x')
        ttk.Button(img_btns, text="Up", command=lambda: self._move_image(-1)).pack(side='left', padx=2)
        ttk.Button(img_btns, text="Down", command=lambda: self._move_image(1)).pack(side='left', padx=2)
        ttk.Button(img_btns, text="Toggle image/small/thumbnail", command=self._cycle_roles_selected).pack(side='left', padx=6)
        self.log_text = scrolledtext.ScrolledText(self.log_tab, wrap=tk.WORD, state=tk.DISABLED, height=10, bg=DarkTheme.PANEL2, fg=DarkTheme.FG, insertbackground=DarkTheme.FG)
        self.log_text.pack(expand=True, fill='both')
        ttk.Button(self.tools_tab, text="Preview Payload", command=self.preview_payload).pack(fill='x', pady=4)
        ttk.Button(self.tools_tab, text="Copy Payload JSON", command=self.copy_payload).pack(fill='x', pady=4)
        ttk.Button(self.tools_tab, text="Save Draft to File", command=self.save_draft).pack(fill='x', pady=4)
        ttk.Button(self.tools_tab, text="Load Draft from File", command=self.load_draft).pack(fill='x', pady=4)
        self.submit_btn = ttk.Button(left, text="Submit to Magento", command=self.submit_product_creation)
        self.submit_btn.pack(pady=8)
    def _wire_global_context_menus(self):
        def walker(w):
            add_context_menu(w)
            if isinstance(w, (ttk.Frame, ttk.Labelframe)):
                for ch in w.winfo_children():
                    walker(ch)
        for tab in (self.api_tab, self.product_tab):
            for ch in tab.winfo_children():
                walker(ch)
    def log(self, message: str):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)
    def _save_config(self):
        cfg = {
            "magento_url": self.url_entry.get().strip(),
            "token": self.token_entry.get().strip(),
            "website_ids": self.websites_entry.get().strip()
        }
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, indent=2)
            self.set_status("Configuration saved.")
        except Exception as e:
            messagebox.showerror("Error", f"Could not save config: {e}")
    def _load_config(self):
        try:
            if Path(CONFIG_FILE).exists():
                cfg = json.loads(Path(CONFIG_FILE).read_text(encoding='utf-8'))
                self.url_entry.insert(0, cfg.get("magento_url", ""))
                self.token_entry.insert(0, cfg.get("token", ""))
                self.websites_entry.delete(0, tk.END)
                self.websites_entry.insert(0, cfg.get("website_ids", "1"))
                self.set_status("Configuration loaded.")
        except Exception:
            self.set_status("Could not load config.")
    def connect_and_fetch(self):
        self.set_status("Connecting…")
        t = threading.Thread(target=self._connect_task, daemon=True)
        t.start()
    def _connect_task(self):
        try:
            url = self.url_entry.get().strip()
            token = self.token_entry.get().strip()
            if not (url and token):
                raise ValueError("URL and Token cannot be empty.")
            self.api_client = MagentoAPIClient(url, token)
            self.set_status("Fetching attribute sets…")
            sets = self.api_client.get_attribute_sets()
            set_map = {s['attribute_set_name']: s['attribute_set_id'] for s in sets}
            self.attribute_set_map = set_map
            self._ui(self.attribute_set_combo.configure, values=list(set_map.keys()))
            self.set_status("Connected. Load categories JSON from the Categories tab when ready.")
            self._ui(self.nb.select, self.product_tab)
            self.set_status("Connected.")
        except Exception as e:
            self.set_status("Error")
            self._ui(messagebox.showerror, "Connection Failed", str(e))
    def test_token(self):
        if not self.api_client:
            try:
                self.api_client = MagentoAPIClient(self.url_entry.get().strip(), self.token_entry.get().strip())
            except Exception as e:
                messagebox.showerror("Error", f"Invalid config: {e}")
                return
        try:
            _ = self.api_client.get_attribute_sets()
            messagebox.showinfo("Success", "Token appears valid (attribute sets fetched).")
        except Exception as e:
            messagebox.showerror("Invalid", f"Token test failed:\n{e}")
    def on_attribute_set_change(self, _=None):
        t = threading.Thread(target=self._fetch_attributes_task, daemon=True)
        t.start()
    def _fetch_attributes_task(self):
        for w in self.attr_body.winfo_children():
            w.destroy()
        self.dynamic_widgets.clear()
        set_name = self.attribute_set_combo.get()
        set_id = self.attribute_set_map.get(set_name)
        if not set_id or not self.api_client:
            return
        self.set_status(f"Fetching attributes for '{set_name}'…")
        try:
            attrs = self.api_client.get_attributes_for_set(set_id)
            options_cache = {}
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
                futures = {}
                for attr in attrs:
                    if attr.get('frontend_input') in ('select','multiselect'):
                        code = attr['attribute_code']
                        futures[ex.submit(self.api_client.get_attribute_options, code)] = code
                for fut, code in futures.items():
                    try:
                        options_cache[code] = fut.result()
                    except Exception as e:
                        self._ui(self.log, f"Warning: options for {code} failed: {e}")
            self._ui(self._build_attribute_ui, attrs, options_cache)
            self.set_status("Attribute form ready.")
        except Exception as e:
            self._ui(messagebox.showerror, "Error", f"Failed to fetch attributes: {e}")
    def _build_attribute_ui(self, attributes: list[dict], options_cache: dict[str, list]):
        core_exclude = {'sku','name','price','quantity_and_stock_status','visibility','description',
                        'status','type_id'}
        r = 0
        for attr in attributes:
            code = attr['attribute_code']
            if (not attr.get('is_user_defined') and code not in {'description'}) or code in core_exclude:
                continue
            label = attr.get('default_frontend_label') or code
            required = bool(attr.get('is_required'))
            lab = ttk.Label(self.attr_body, text=f"{label}{' *' if required else ''}:")
            lab.grid(row=r, column=0, sticky='w', padx=5, pady=4)
            input_type = attr.get('frontend_input')
            widget = None
            if input_type in ('text','price','weight'):
                widget = ttk.Entry(self.attr_body)
                if attr.get('default_value'):
                    widget.insert(0, str(attr['default_value']))
            elif input_type == 'textarea':
                widget = scrolledtext.ScrolledText(self.attr_body, height=4, width=40, wrap=tk.WORD,
                                                   bg=DarkTheme.PANEL2, fg=DarkTheme.FG, insertbackground=DarkTheme.FG)
            elif input_type == 'boolean':
                var = tk.IntVar(value=0)
                widget = ttk.Checkbutton(self.attr_body, text="Yes", variable=var)
                self.dynamic_widgets[code] = var
            elif input_type in ('select','multiselect'):
                opts = options_cache.get(code, [])
                display = [o['label'] for o in opts if o.get('label') and o.get('value')]
                self.dynamic_widgets[f"{code}__map"] = {o['label']: o['value'] for o in opts if o.get('label') and o.get('value')}
                if input_type == 'select':
                    widget = ttk.Combobox(self.attr_body, values=display, state='readonly')
                else:
                    widget = tk.Listbox(self.attr_body, selectmode=tk.MULTIPLE, height=4, exportselection=False,
                                        bg=DarkTheme.PANEL2, fg=DarkTheme.FG)
                    for d in display:
                        widget.insert(tk.END, d)
            else:
                widget = ttk.Entry(self.attr_body)
            if widget:
                widget.grid(row=r, column=1, sticky='ew', padx=5, pady=4)
                if not isinstance(widget, ttk.Checkbutton):
                    self.dynamic_widgets[code] = widget
            r += 1
        self.attr_body.columnconfigure(1, weight=1)
    def load_categories_json(self):
        path = filedialog.askopenfilename(title="Open Categories JSON",
                                          filetypes=[("JSON","*.json"),("All","*.*")])
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding='utf-8'))
        except Exception as e:
            messagebox.showerror("Invalid JSON", f"Failed to read JSON: {e}")
            return
        try:
            self.cb_tree.build_from_json(data)
            self.set_status(f"Loaded categories from {os.path.basename(path)}")
        except Exception as e:
            messagebox.showerror("Build Error", f"Could not build tree: {e}")
    def clear_categories(self):
        try:
            self.cb_tree.clear()
            self.set_status("Categories cleared.")
        except Exception as e:
            messagebox.showerror("Error", str(e))
    def add_images(self):
        files = filedialog.askopenfilenames(title="Select Images",
                                            filetypes=[("Image Files","*.jpg *.jpeg *.png *.gif"),("All","*.*")])
        for p in files:
            p = str(p)
            if not any(p == it.get('path') for it in self.image_list):
                self.image_list.append({
                    'path': p,
                    'roles': ['image','small_image','thumbnail'] if len(self.image_list)==0 else []
                })
        self._refresh_img_list()
    def remove_selected_image(self):
        sel = self.img_list.selection()
        if not sel:
            return
        idx = int(sel[0])
        self.image_list.pop(idx)
        self._refresh_img_list()
    def _move_image(self, delta: int):
        sel = self.img_list.selection()
        if not sel:
            return
        idx = int(sel[0])
        new = max(0, min(len(self.image_list)-1, idx+delta))
        if new == idx:
            return
        self.image_list[idx], self.image_list[new] = self.image_list[new], self.image_list[idx]
        self._refresh_img_list()
        self.img_list.selection_set(new)
    def _cycle_roles_selected(self):
        sel = self.img_list.selection()
        if not sel:
            return
        idx = int(sel[0])
        roles = self.image_list[idx]['roles']
        order = [[], ['image'], ['image','small_image'], ['image','small_image','thumbnail']]
        try:
            i = order.index(roles)
        except ValueError:
            i = 0
        roles = order[(i+1) % len(order)]
        self.image_list[idx]['roles'] = roles
        self._refresh_img_list()
    def _toggle_image_role(self, _evt=None):
        self._cycle_roles_selected()
    def _refresh_img_list(self):
        self.img_list.delete(*self.img_list.get_children())
        for i, it in enumerate(self.image_list):
            fn = os.path.basename(it['path'])
            roles = ','.join(it['roles']) if it['roles'] else '-'
            self.img_list.insert('', 'end', iid=str(i), values=(fn, roles))
    def check_sku(self):
        if not self.api_client:
            messagebox.showwarning("Not connected", "Connect first.")
            return
        sku = self.sku_entry.get().strip()
        if not sku:
            messagebox.showwarning("Missing SKU", "Enter a SKU.")
            return
        t = threading.Thread(target=self._check_sku_task, args=(sku,), daemon=True)
        t.start()
    def _check_sku_task(self, sku: str):
        try:
            prod = self.api_client.get_product(sku)
            self._ui(messagebox.showinfo, "SKU", f"SKU '{sku}' exists. Update mode recommended.")
        except Exception as e:
            if "404" in str(e):
                self._ui(messagebox.showinfo, "SKU", f"SKU '{sku}' not found. Create mode is valid.")
            else:
                self._ui(messagebox.showerror, "Error", str(e))
    def _get_widget_value(self, widget):
        if isinstance(widget, scrolledtext.ScrolledText):
            return widget.get('1.0', tk.END).strip()
        if isinstance(widget, tk.Listbox):
            return [widget.get(i) for i in widget.curselection()]
        if isinstance(widget, ttk.Combobox) or isinstance(widget, ttk.Entry):
            return widget.get().strip()
        if isinstance(widget, tk.Variable):
            return widget.get()
        return None
    def build_payload(self) -> dict:
        sku = self.sku_entry.get().strip()
        if not sku:
            raise ValueError("SKU is required.")
        price_text = self.price_entry.get().strip()
        qty_text = self.qty_entry.get().strip()
        price = float(price_text) if price_text else 0.0
        qty = int(float(qty_text)) if qty_text else 0
        vis_label, vis_code = self._pair_from_combo(self.visibility_combo)
        status_label, status_code = self._pair_from_combo(self.status_combo)
        websites = [int(x) for x in self.websites_entry.get().split(',') if x.strip().isdigit()]
        attribute_set_id = self.attribute_set_map.get(self.attribute_set_combo.get())
        if not attribute_set_id:
            raise ValueError("Select an Attribute Set.")
        custom_attributes = []
        for code, widget in list(self.dynamic_widgets.items()):
            if code.endswith("__map"):
                continue
            value = self._get_widget_value(widget)
            if isinstance(widget, ttk.Combobox):
                mp = self.dynamic_widgets.get(f"{code}__map", {})
                value = mp.get(value, value)
            elif isinstance(widget, tk.Listbox):
                mp = self.dynamic_widgets.get(f"{code}__map", {})
                value = [mp.get(v, v) for v in value]
            if value in (None, '', []):
                continue
            custom_attributes.append({'attribute_code': code, 'value': value})
        category_ids = self.cb_tree.get_checked_ids()
        if category_ids:
            custom_attributes.append({'attribute_code': 'category_ids', 'value': [int(x) for x in category_ids if x.isdigit()]})
        media_gallery_entries = []
        if self.image_list:
            def encode_img(path: str) -> tuple[str, str]:
                with open(path, 'rb') as f:
                    b64 = base64.b64encode(f.read()).decode('utf-8')
                ext = os.path.splitext(path)[1].lower().lstrip('.') or 'jpg'
                mime = 'image/jpeg' if ext in ('jpg','jpeg') else f'image/{ext}'
                return b64, mime
            jobs = []
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
                for it in self.image_list:
                    jobs.append(ex.submit(encode_img, it['path']))
                results = [j.result() for j in jobs]
            for idx, (it, (b64, mime)) in enumerate(zip(self.image_list, results), start=1):
                roles = it['roles']
                media_gallery_entries.append({
                    "media_type": "image",
                    "label": os.path.basename(it['path']),
                    "position": idx,
                    "disabled": False,
                    "types": roles,
                    "content": {
                        "base64_encoded_data": b64,
                        "type": mime,
                        "name": f"{sku}-{idx}.{mime.split('/')[-1]}"
                    }
                })
        payload = {
            "product": {
                "sku": sku,
                "name": self.name_entry.get().strip() or sku,
                "attribute_set_id": attribute_set_id,
                "price": price,
                "status": int(status_code),
                "visibility": int(vis_code),
                "type_id": self.type_combo.get(),
                "extension_attributes": {
                    "website_ids": websites,
                    "stock_item": {
                        "qty": qty,
                        "is_in_stock": bool(qty > 0)
                    }
                },
                "custom_attributes": custom_attributes,
                "media_gallery_entries": media_gallery_entries
            }
        }
        desc = self.desc_text.get('1.0', tk.END).strip()
        if desc:
            payload['product']["custom_attributes"].append({"attribute_code": "description", "value": desc})
        return payload
    def _pair_from_combo(self, cb: ttk.Combobox):
        val = cb.get()
        if isinstance(cb['values'], (list, tuple)):
            for v in cb['values']:
                if isinstance(v, (list, tuple)) and v and v[0] == val:
                    return v[0], v[1]
        try:
            return val, int(val)
        except Exception:
            return val, val
    def preview_payload(self):
        try:
            payload = self.build_payload()
        except Exception as e:
            messagebox.showerror("Invalid", str(e))
            return
        win = tk.Toplevel(self.root)
        win.title("Payload Preview")
        DarkTheme.apply(win)
        txt = scrolledtext.ScrolledText(win, wrap=tk.WORD, width=120, height=40, bg=DarkTheme.PANEL2, fg=DarkTheme.FG, insertbackground=DarkTheme.FG)
        txt.pack(fill='both', expand=True)
        txt.insert('1.0', json.dumps(payload, indent=2))
        txt.configure(state=tk.DISABLED)
        ttk.Button(win, text="Copy", command=lambda: (self.root.clipboard_clear(), self.root.clipboard_append(json.dumps(payload, indent=2)), self.set_status("Payload copied."))).pack(pady=6)
    def copy_payload(self):
        try:
            payload = self.build_payload()
            s = json.dumps(payload, indent=2)
            self.root.clipboard_clear(); self.root.clipboard_append(s)
            self.set_status("Payload copied to clipboard.")
        except Exception as e:
            messagebox.showerror("Invalid", str(e))
    def save_draft(self):
        try:
            payload = self.build_payload()
        except Exception as e:
            messagebox.showerror("Invalid", str(e)); return
        path = filedialog.asksaveasfilename(title="Save Draft", defaultextension=".json", filetypes=[("JSON","*.json")])
        if not path:
            return
        Path(path).write_text(json.dumps(payload, indent=2), encoding='utf-8')
        self.set_status(f"Draft saved: {path}")
    def load_draft(self):
        path = filedialog.askopenfilename(title="Load Draft", filetypes=[("JSON","*.json"),("All","*.*")])
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding='utf-8'))
        except Exception as e:
            messagebox.showerror("Invalid", f"Failed to load JSON: {e}")
            return
        prod = (data or {}).get('product') or {}
        self.sku_entry.delete(0, tk.END); self.sku_entry.insert(0, prod.get('sku',''))
        self.name_entry.delete(0, tk.END); self.name_entry.insert(0, prod.get('name',''))
        self.price_entry.delete(0, tk.END); self.price_entry.insert(0, str(prod.get('price','')))
        qty = ((prod.get('extension_attributes') or {}).get('stock_item') or {}).get('qty', '')
        self.qty_entry.delete(0, tk.END); self.qty_entry.insert(0, str(qty))
        for _ in range(1):
            self.desc_text.delete('1.0', tk.END)
            for ca in prod.get('custom_attributes', []):
                if ca.get('attribute_code') == 'description':
                    self.desc_text.insert('1.0', str(ca.get('value','')))
                    break
        self.set_status("Draft loaded (core fields). Adjust attribute set & attributes as needed.")
    def submit_product_creation(self):
        if not self.api_client:
            messagebox.showwarning("Not connected", "Connect first.")
            return
        self.submit_btn.configure(state=tk.DISABLED)
        t = threading.Thread(target=self._submit_product_task, daemon=True)
        t.start()
    def _submit_product_task(self):
        try:
            self.set_status("Building payload…")
            payload = self.build_payload()
            sku = payload['product']['sku']
            mode = self.mode_var.get()
            if mode == 'update':
                self.set_status(f"Updating product {sku}…")
                resp = self.api_client.update_product(sku, payload)
            else:
                try:
                    _ = self.api_client.get_product(sku)
                    if not messagebox.askyesno("Exists", f"SKU '{sku}' already exists. Switch to Update?"):
                        self.set_status("Cancelled.")
                        self._ui(None)  # no-op
                        return
                    self._ui(self.mode_var.set, 'update')
                    self.set_status(f"Updating product {sku}…")
                    resp = self.api_client.update_product(sku, payload)
                except Exception as e:
                    if "404" in str(e):
                        self.set_status(f"Creating product {sku}…")
                        resp = self.api_client.create_product(payload)
                    else:
                        raise
            self._ui(self.log, f"SUCCESS\n{json.dumps(resp, indent=2)}")
            self.set_status("Done.")
            self._ui(messagebox.showinfo, "Success", f"Product '{sku}' processed.")
        except Exception as e:
            self.set_status("Error")
            self._ui(self.log, f"ERROR\n{e}")
            self._ui(messagebox.showerror, "Failed", str(e))
        finally:
            self._ui(self.submit_btn.configure, state=tk.NORMAL)
    def _ui(self, fn, *args, **kwargs):
        if not fn:
            return
        self.root.after(0, lambda: fn(*args, **kwargs))
if __name__ == "__main__":
    root = tk.Tk()
    app = AdvancedMagentoToolPro(root)
    root.mainloop()
