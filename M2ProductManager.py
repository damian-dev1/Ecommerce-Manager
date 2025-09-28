import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import csv, json, io, os, urllib.request, datetime as dt
from PIL import Image, ImageTk
from sqlalchemy import create_engine, Column, Integer, String, Float, Text, Boolean, DateTime, UniqueConstraint
from sqlalchemy.orm import declarative_base, sessionmaker

DB_URL = "sqlite:///products_m2.db"
Base = declarative_base()

class ProductM2(Base):
    __tablename__ = "products_m2"
    id = Column(Integer, primary_key=True)
    sku = Column(String, unique=True, index=True, nullable=False)
    name = Column(String)
    attribute_set_id = Column(Integer)
    average_rating = Column(Float)
    brand = Column(String)
    bulk_discounts = Column(Text)
    categories = Column(Text)
    created_at = Column(String)
    custom_attributes = Column(Text)
    description = Column(Text)
    dimensions = Column(Text)
    extension_attributes = Column(Text)
    fulfillment_centers = Column(Text)
    image_urls = Column(Text)
    in_stock = Column(Boolean)
    inventory = Column(Integer)
    price = Column(Text)
    related_products = Column(Text)
    review_count = Column(Integer)
    shipping_zones = Column(Text)
    status = Column(Boolean)
    tax_class = Column(String)
    type_id = Column(String)
    updated_at = Column(String)
    vendor = Column(Text)
    vendor_promotion = Column(Text)
    visibility = Column(Integer)
    weight = Column(Float)
    media_local = Column(Text)
    created_ts = Column(DateTime, default=dt.datetime.utcnow)
    updated_ts = Column(DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow)

class AppSetting(Base):
    __tablename__ = "app_settings"
    id = Column(Integer, primary_key=True)
    key = Column(String, nullable=False)
    value = Column(Text, nullable=False)
    __table_args__ = (UniqueConstraint('key', name='_key_uc'),)

engine = create_engine(DB_URL, future=True)
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine, expire_on_commit=False)

JSON_FIELDS = {"bulk_discounts","categories","custom_attributes","dimensions","extension_attributes","fulfillment_centers","image_urls","related_products","shipping_zones","vendor","vendor_promotion"}
CSV_ORDER = ["attribute_set_id","average_rating","brand","bulk_discounts","categories","created_at","custom_attributes","description","dimensions","extension_attributes","fulfillment_centers","image_urls","in_stock","inventory","name","price","related_products","review_count","shipping_zones","sku","status","tax_class","type_id","updated_at","vendor","vendor_promotion","visibility","weight"]

def _jloads(s):
    if s is None: return None
    t=str(s).strip()
    if t=="" or t.lower() in {"null","none"}: return None
    try: return json.loads(t)
    except: return None

def _jdumps(o):
    return json.dumps(o, ensure_ascii=False, separators=(",", ":")) if o is not None else ""

def upsert_from_csv(path):
    sess=Session()
    with open(path,"r",encoding="utf-8-sig",newline="") as f:
        rdr=csv.DictReader(f)
        for row in rdr:
            sku=(row.get("sku") or "").strip()
            if not sku: continue
            obj=sess.query(ProductM2).filter_by(sku=sku).one_or_none() or ProductM2(sku=sku)
            for k in set(CSV_ORDER+["weight","visibility"]):
                v=row.get(k)
                if k in JSON_FIELDS: setattr(obj,k,_jdumps(_jloads(v)))
                elif k in {"in_stock","status"}: setattr(obj,k,str(v).strip().lower() in {"1","true","yes"})
                elif k in {"average_rating","weight"}:
                    try: setattr(obj,k,float(v) if v not in (None,"") else None)
                    except: setattr(obj,k,None)
                elif k in {"attribute_set_id","inventory","review_count","visibility"}:
                    try:
                        setattr(obj,k,int(float(v))) if v not in (None,"") else setattr(obj,k,None)
                    except:
                        setattr(obj,k,None)
                else:
                    setattr(obj,k,v if v is not None else "")
            sess.merge(obj)
    sess.commit(); sess.close()

def export_magento_jsonl(path, ids=None):
    sess=Session()
    q = sess.query(ProductM2).order_by(ProductM2.id.asc())
    if ids: q = q.filter(ProductM2.id.in_(list(ids)))
    with open(path,"w",encoding="utf-8") as out:
        for p in q.all():
            ea=_jloads(p.extension_attributes) or {}
            media=[]
            imgs=_jloads(p.image_urls) or []
            for i,u in enumerate(imgs):
                media.append({"media_type":"image","label":p.name or p.sku,"position":i+1,"disabled":False,"file":u})
            ca_in=_jloads(p.custom_attributes) or {}
            ca=[{"attribute_code":k,"value":v} for k,v in ca_in.items()]
            price=_jloads(p.price) or {}
            payload={"product":{
                "sku":p.sku,
                "name":p.name,
                "attribute_set_id":p.attribute_set_id or 4,
                "price":price.get("rrp"),
                "status":1 if p.status else 2,
                "visibility":p.visibility or 4,
                "type_id":p.type_id or "simple",
                "weight":p.weight or 0.0,
                "extension_attributes":ea if isinstance(ea,dict) else {},
                "media_gallery_entries":media,
                "custom_attributes":ca
            }}
            out.write(_jdumps(payload)+"\n")
    sess.close()

def export_vendor_feed_csv(path, ids=None):
    sess=Session()
    q = sess.query(ProductM2).order_by(ProductM2.id.asc())
    if ids: q = q.filter(ProductM2.id.in_(list(ids)))
    with open(path,"w",encoding="utf-8",newline="") as f:
        w=csv.writer(f); w.writerow(CSV_ORDER)
        for p in q.all():
            row=[]
            for k in CSV_ORDER:
                v=getattr(p,k)
                if k in JSON_FIELDS: row.append(v or "")
                elif k in {"in_stock","status"}: row.append("True" if v else "False")
                else: row.append(v if v is not None else "")
            w.writerow(row)
    sess.close()

def generate_thumbnails(output_dir="thumbs", size=(256,256)):
    os.makedirs(output_dir,exist_ok=True)
    sess=Session()
    for p in sess.query(ProductM2).all():
        imgs=_jloads(p.image_urls) or []
        if not imgs: continue
        u=imgs[0]
        try:
            if str(u).startswith("http"):
                with urllib.request.urlopen(u,timeout=8) as resp:
                    im=Image.open(io.BytesIO(resp.read()))
            else:
                im=Image.open(u)
            im=im.convert("RGBA"); im.thumbnail(size)
            fn=f"{p.sku}_thumb.png"; fp=os.path.join(output_dir,fn)
            im.save(fp,"PNG"); p.media_local=fp
        except:
            pass
    sess.commit(); sess.close()

class App:
    def __init__(self, root):
        self.root=root
        self.root.title("Magento2 Product Manager")
        self.root.geometry("1280x820")
        self.colors={"bg":"#1a1b26","bg2":"#24283b","fg":"#c0caf5","muted":"#a9b1d6","sel":"#414868","card":"#2a2e3f","accent":"#7aa2f7","accent2":"#bb9af7"}
        self.sess=Session()
        self.page_size_var=tk.IntVar(value=self._load_int_setting("page_size",12))
        self.view=tk.StringVar(value=self._load_str_setting("view_mode","table"))
        self.q=tk.StringVar()
        self.page=0
        self.cache={}
        self.data=[]
        self.filtered=[]
        self.selected_ids=set()
        self.available_columns=[("sku","SKU"),("name","Name"),("price","Price"),("inventory","Inventory"),("status","Status"),("visibility","Visibility"),("type_id","Type")]
        self.visible_columns=self._load_visible_columns()
        self._theme()
        self._ui()
        self.reload()

    def _theme(self):
        s=ttk.Style()
        try:
            s.theme_use("clam")
        except:
            pass
        s.configure("TFrame", background=self.colors["bg"])
        s.configure("TLabel", background=self.colors["bg"], foreground=self.colors["fg"])
        s.configure("Dark.TButton", background=self.colors["accent"], foreground=self.colors["bg"])
        s.map("Dark.TButton", background=[("active", self.colors["accent2"]), ("!disabled", self.colors["accent"])], foreground=[("!disabled", self.colors["bg"])])
        s.configure("Dark.TEntry", fieldbackground=self.colors["bg2"], foreground=self.colors["fg"], background=self.colors["bg2"])
        s.configure("Dark.TCombobox", fieldbackground=self.colors["bg2"], background=self.colors["bg2"], foreground=self.colors["fg"])
        s.map("Dark.TCombobox",
              fieldbackground=[("readonly", self.colors["bg2"]), ("!disabled", self.colors["bg2"]), ("disabled", self.colors["card"])],
              foreground=[("disabled", self.colors["muted"]), ("!disabled", self.colors["fg"])],
              background=[("readonly", self.colors["bg2"]), ("!disabled", self.colors["bg2"])])
        s.configure("TScrollbar", troughcolor=self.colors["bg2"], background=self.colors["bg2"])
        s.configure("Treeview", background=self.colors["bg2"], fieldbackground=self.colors["bg2"], foreground=self.colors["fg"], rowheight=26)
        s.map("Treeview", background=[("selected", self.colors["sel"])])
        s.configure("Dark.TRadiobutton", background=self.colors["bg"], foreground=self.colors["fg"])
        self.root.option_add("*TCombobox*Listbox.background", self.colors["bg2"])
        self.root.option_add("*TCombobox*Listbox.foreground", self.colors["fg"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", self.colors["sel"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", self.colors["fg"])

    def _ui(self):
        self.left=tk.Frame(self.root,bg=self.colors["bg"],width=300); self.left.pack(side=tk.LEFT,fill=tk.Y)
        right=tk.Frame(self.root,bg=self.colors["bg"]); right.pack(side=tk.RIGHT,fill=tk.BOTH,expand=True)

        ttk.Button(self.left,text="Import CSV",style="Dark.TButton",command=self.import_csv).pack(padx=12,pady=6,fill="x")
        ttk.Button(self.left,text="Export JSONL (Magento)",style="Dark.TButton",command=self.export_jsonl).pack(padx=12,pady=6,fill="x")
        ttk.Button(self.left,text="Export CSV (Vendor)",style="Dark.TButton",command=self.export_csv).pack(padx=12,pady=6,fill="x")
        ttk.Button(self.left,text="Generate Thumbs",style="Dark.TButton",command=self.thumbs).pack(padx=12,pady=(6,12),fill="x")
        ttk.Button(self.left,text="Choose Columns",style="Dark.TButton",command=self.choose_columns).pack(padx=12,pady=(0,12),fill="x")

        tk.Label(self.left,text="Bulk Actions",fg=self.colors["muted"],bg=self.colors["bg"]).pack(padx=12,anchor="w")
        self.bulk_action=ttk.Combobox(self.left,style="Dark.TCombobox",state="readonly",values=[
            "Delete Selected",
            "Enable Status",
            "Disable Status",
            "Set Visibility: Not Visible (1)",
            "Set Visibility: Catalog/Search (4)",
            "Export Selected JSONL",
            "Export Selected CSV"
        ])
        self.bulk_action.set("Delete Selected")
        self.bulk_action.pack(padx=12,pady=(0,6),fill="x")
        ttk.Button(self.left,text="Apply Bulk",style="Dark.TButton",command=self.apply_bulk).pack(padx=12,pady=(0,12),fill="x")

        ttk.Radiobutton(self.left,text="Table",style="Dark.TRadiobutton",variable=self.view,value="table",command=self._on_view_change).pack(padx=12,anchor="w")
        ttk.Radiobutton(self.left,text="Cards",style="Dark.TRadiobutton",variable=self.view,value="cards",command=self._on_view_change).pack(padx=12,anchor="w")

        tk.Label(self.left,text="Search",fg=self.colors["muted"],bg=self.colors["bg"]).pack(padx=12,anchor="w")
        e=ttk.Entry(self.left,style="Dark.TEntry",textvariable=self.q); e.pack(padx=12,pady=4,fill="x"); e.bind("<Return>",lambda *_:self.apply_search())
        ttk.Button(self.left,text="Apply",style="Dark.TButton",command=self.apply_search).pack(padx=12,fill="x")
        ttk.Button(self.left,text="Clear",style="Dark.TButton",command=lambda:(self.q.set(""),self.apply_search())).pack(padx=12,pady=(4,12),fill="x")

        tk.Label(self.left,text="Page size",fg=self.colors["muted"],bg=self.colors["bg"]).pack(padx=12,anchor="w")
        cb=ttk.Combobox(self.left,style="Dark.TCombobox",textvariable=self.page_size_var,values=[6,12,24,48],state="readonly")
        cb.pack(padx=12,pady=4,fill="x"); cb.bind("<<ComboboxSelected>>",lambda *_:self._on_page_size_change())

        self.stat=tk.Label(self.left,text="Products: 0",bg=self.colors["bg"],fg=self.colors["fg"]); self.stat.pack(padx=12,pady=8,anchor="w")

        hdr=tk.Frame(right,bg=self.colors["bg"]); hdr.pack(fill="x")
        self.status=tk.Label(hdr,text="Ready",bg=self.colors["bg"],fg=self.colors["muted"])
        self.status.pack(side=tk.LEFT,padx=12,pady=8)

        nav=tk.Frame(hdr,bg=self.colors["bg"])
        nav.pack(side=tk.RIGHT,padx=(0,12),pady=8)
        ttk.Button(nav,text="Prev",style="Dark.TButton",command=self.prev).pack(side=tk.LEFT,padx=4)
        self.page_lbl=tk.Label(nav,text="Page 1",bg=self.colors["bg"],fg=self.colors["fg"])
        self.page_lbl.pack(side=tk.LEFT)
        ttk.Button(nav,text="Next",style="Dark.TButton",command=self.next).pack(side=tk.LEFT,padx=4)

        stack=tk.Frame(right,bg=self.colors["bg"]); stack.pack(fill=tk.BOTH,expand=True)

        self.table=tk.Frame(stack,bg=self.colors["bg"])
        self.tree_scroll=ttk.Scrollbar(self.table,orient="vertical")
        self.tree=ttk.Treeview(self.table,show="headings",yscrollcommand=self.tree_scroll.set)
        self.tree_scroll.config(command=self.tree.yview)
        self._rebuild_tree_columns()
        self.tree.pack(side="left",fill=tk.BOTH,expand=True)
        self.tree_scroll.pack(side="right",fill="y")
        self.tree.bind("<Double-1>",self._edit_sel)
        self.tree.bind("<Enter>",lambda e:self._bind_mousewheel(self.tree))
        self.tree.bind("<Leave>",lambda e:self._unbind_mousewheel(self.tree))
        self.tree.bind("<Button-1>", self._on_tree_click)

        self.cards=tk.Frame(stack,bg=self.colors["bg"])
        self.cards_canvas=tk.Canvas(self.cards,bg=self.colors["bg"],highlightthickness=0)
        self.cards_scroll=ttk.Scrollbar(self.cards,orient="vertical",command=self.cards_canvas.yview)
        self.cards_inner=tk.Frame(self.cards_canvas,bg=self.colors["bg"])
        self.cards_inner.bind("<Configure>",lambda e:self.cards_canvas.configure(scrollregion=self.cards_canvas.bbox("all")))
        self.cards_canvas.create_window((0,0),window=self.cards_inner,anchor="nw")
        self.cards_canvas.configure(yscrollcommand=self.cards_scroll.set)
        self.cards_canvas.pack(side="left",fill=tk.BOTH,expand=True)
        self.cards_scroll.pack(side="right",fill="y")
        self.cards_canvas.bind("<Enter>",lambda e:self._bind_mousewheel(self.cards_canvas))
        self.cards_canvas.bind("<Leave>",lambda e:self._unbind_mousewheel(self.cards_canvas))

        self._show_table()

    def _bind_mousewheel(self, widget):
        widget.bind_all("<MouseWheel>", self._on_mousewheel)
        widget.bind_all("<Button-4>", self._on_mousewheel)
        widget.bind_all("<Button-5>", self._on_mousewheel)

    def _unbind_mousewheel(self, widget):
        widget.unbind_all("<MouseWheel>")
        widget.unbind_all("<Button-4>")
        widget.unbind_all("<Button-5>")

    def _on_mousewheel(self, event):
        if self.view.get()=="table":
            if event.num == 4 or event.delta > 0: self.tree.yview_scroll(-1, "units")
            else: self.tree.yview_scroll(1, "units")
        else:
            if event.num == 4 or event.delta > 0: self.cards_canvas.yview_scroll(-2, "units")
            else: self.cards_canvas.yview_scroll(2, "units")

    def _rebuild_tree_columns(self):
        cols = ["select"] + [c for c,_ in self.visible_columns]
        self.tree["columns"]=tuple(cols)
        hdr_map={"select":"Select","sku":"SKU","name":"Name","price":"Price","inventory":"Inventory","status":"Status","visibility":"Visibility","type_id":"Type"}
        for c in cols:
            if c=="select":
                self.tree.heading(c,text=hdr_map.get(c,c.upper()),command=self.toggle_select_all_page)
            else:
                self.tree.heading(c,text=hdr_map.get(c,c.upper()),command=lambda x=c:self.sort(x))
            w = 80 if c=="select" else (220 if c=="name" else 140)
            self.tree.column(c,width=w,anchor=("center" if c=="select" else "w"))

    def toggle_select_all_page(self):
        s,e=self.slice()
        page_ids=[r["id"] for r in self.filtered[s:e]]
        all_selected=all(pid in self.selected_ids for pid in page_ids) and page_ids!=[]
        if all_selected:
            for pid in page_ids: self.selected_ids.discard(pid)
        else:
            for pid in page_ids: self.selected_ids.add(pid)
        self._refresh_select_icons_current_page()

    def _show_table(self):
        self.cards.pack_forget()
        self.table.pack(fill=tk.BOTH,expand=True)

    def _show_cards(self):
        self.table.pack_forget()
        self.cards.pack(fill=tk.BOTH,expand=True)

    def reload(self):
        rows=self.sess.query(ProductM2).order_by(ProductM2.id.asc()).all()
        self.data=[]
        for p in rows:
            d=p.__dict__.copy(); d.pop("_sa_instance_state",None)
            price=_jloads(d.get("price")) or {}
            d["price_display"]=price.get("rrp") if price else None
            self.data.append(d)
        self.filtered=list(self.data)
        self.stat.config(text=f"Products: {len(self.filtered)}")
        self.page=0
        self.refresh()

    def apply_search(self):
        q=self.q.get().strip().lower()
        self.filtered=self.data if not q else [r for r in self.data if q in str(r.get("sku","")).lower() or q in str(r.get("name","")).lower() or q in str(r.get("brand","")).lower()]
        self.page=0
        self.stat.config(text=f"Products: {len(self.filtered)}")
        self.refresh()

    def sort(self,col):
        if col=="select": return
        rev=getattr(self,"_rev",{})
        r=rev.get(col,False)^True
        def keyf(v):
            if col=="price":
                pr=_jloads(v.get("price")) or {}
                val=pr.get("rrp")
            else:
                val=v.get(col) if col in v else v.get(col+"_display")
            return (val is None, val)
        self.filtered.sort(key=keyf, reverse=r)
        rev[col]=r; self._rev=rev
        self.refresh()

    def slice(self):
        ps=self.page_size_var.get(); s=self.page*ps; e=min(s+ps,len(self.filtered)); return s,e

    def prev(self):
        if self.page>0: self.page-=1; self.refresh()

    def next(self):
        ps=self.page_size_var.get()
        if (self.page+1)*ps<len(self.filtered): self.page+=1; self.refresh()

    def refresh(self):
        total=max(1,(len(self.filtered)-1)//self.page_size_var.get()+1)
        self.page_lbl.config(text=f"Page {self.page+1} of {total}")
        if self.view.get()=="table": self._show_table(); self._upd_table()
        else: self._show_cards(); self._upd_cards()

    def _upd_table(self):
        for i in self.tree.get_children(): self.tree.delete(i)
        s,e=self.slice()
        for r in self.filtered[s:e]:
            vals=[]
            vals.append("☑" if r["id"] in self.selected_ids else "☐")
            for c,_ in self.visible_columns:
                if c=="price":
                    pr=_jloads(r.get("price")) or {}
                    vals.append(pr.get("rrp",""))
                elif c=="status":
                    vals.append("Yes" if r.get("status") else "No")
                elif c=="inventory":
                    vals.append(int(r.get("inventory") or 0))
                else:
                    vals.append(r.get(c,""))
            self.tree.insert("", "end", iid=str(r["id"]), values=tuple(vals))

    def _upd_cards(self):
        for w in self.cards_inner.winfo_children(): w.destroy()
        s,e=self.slice(); data=self.filtered[s:e]; cols=3 if self.page_size_var.get()<=12 else 4
        for idx,r in enumerate(data):
            f=tk.Frame(self.cards_inner,bg=self.colors["bg2"],bd=1,relief="solid",highlightthickness=1,highlightbackground=self.colors["card"])
            rr,cc=divmod(idx,cols); f.grid(row=rr,column=cc,padx=12,pady=12,sticky="n")
            cb_var=tk.BooleanVar(value=r["id"] in self.selected_ids)
            cb=tk.Checkbutton(f, text="Select", variable=cb_var, command=lambda v=cb_var, rid=r["id"]: self._toggle_card_select(rid, v.get()),
                              fg=self.colors["fg"], bg=self.colors["bg2"], activebackground=self.colors["bg2"],
                              activeforeground=self.colors["fg"], selectcolor=self.colors["bg"])
            cb.pack(anchor="w", padx=12, pady=(8,2))
            img=tk.Label(f,bg=self.colors["bg2"],fg=self.colors["muted"]); img.pack(padx=12,pady=(6,6))
            self._load_img(r,img,140,140)
            pr=_jloads(r.get("price")) or {}
            tk.Label(f,text=r.get("name") or "(no name)",bg=self.colors["bg2"],fg=self.colors["fg"],font=("Segoe UI",10,"bold")).pack(padx=12,anchor="w")
            tk.Label(f,text=f"SKU: {r.get('sku','')}",bg=self.colors["bg2"],fg=self.colors["muted"]).pack(padx=12,anchor="w")
            tk.Label(f,text=f"${pr.get('rrp','')} | Inv: {int(r.get('inventory') or 0)}",bg=self.colors["bg2"],fg=self.colors["muted"]).pack(padx=12,pady=(0,8),anchor="w")
            f.bind("<Double-1>",lambda e,rr=r:self._open(rr))
            for w in f.winfo_children(): w.bind("<Double-1>",lambda e,rr=r:self._open(rr))

    def _toggle_card_select(self, rid, state):
        if state: self.selected_ids.add(rid)
        else:
            self.selected_ids.discard(rid)
        self._refresh_select_icons_current_page()

    def _on_tree_click(self, event):
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell": return
        col = self.tree.identify_column(event.x)
        if col != "#1": return
        row_id = self.tree.identify_row(event.y)
        if not row_id: return
        rid = int(row_id)
        if rid in self.selected_ids: self.selected_ids.remove(rid)
        else: self.selected_ids.add(rid)
        self._refresh_select_icons_current_page()

    def _refresh_select_icons_current_page(self):
        s,e=self.slice()
        visible_ids={r["id"] for r in self.filtered[s:e]}
        for iid in list(self.tree.get_children()):
            rid=int(iid)
            if rid in visible_ids:
                vals=list(self.tree.item(iid,"values"))
                vals[0]="☑" if rid in self.selected_ids else "☐"
                self.tree.item(iid, values=tuple(vals))

    def _open(self,r):
        d=tk.Toplevel(self.root); d.title(r.get("sku","")); d.configure(bg=self.colors["bg"]); d.geometry("640x640"); d.transient(self.root); d.grab_set()
        def row(lbl,var,dis=False):
            fr=tk.Frame(d,bg=self.colors["bg"]); fr.pack(fill="x",padx=16,pady=6)
            tk.Label(fr,text=lbl,width=14,anchor="w",bg=self.colors["bg"],fg=self.colors["muted"]).pack(side="left")
            e=ttk.Entry(fr,style="Dark.TEntry",textvariable=var); e.pack(side="left",fill="x",expand=True)
            if dis: e.configure(state="disabled")
            return e
        v_sku=tk.StringVar(value=r.get("sku",""))
        v_name=tk.StringVar(value=r.get("name",""))
        v_type=tk.StringVar(value=r.get("type_id","simple"))
        v_vis=tk.StringVar(value=str(r.get("visibility") or 4))
        v_inv=tk.StringVar(value=str(r.get("inventory") or 0))
        price=_jloads(r.get("price")) or {}
        v_rrp=tk.StringVar(value=str(price.get("rrp") or ""))
        v_weight=tk.StringVar(value=str(r.get("weight") or "0"))
        tk.Label(d,text="Edit Product (Magento2)",bg=self.colors["bg"],fg=self.colors["fg"],font=("Segoe UI",12,"bold")).pack(padx=16,pady=(16,4),anchor="w")
        row("SKU",v_sku,True); row("Name",v_name); row("Type",v_type); row("Visibility",v_vis); row("Inventory",v_inv); row("Price (rrp)",v_rrp); row("Weight",v_weight)
        jf=tk.Frame(d,bg=self.colors["bg"]); jf.pack(fill="both",expand=True,padx=16,pady=8)
        tk.Label(jf,text="Custom Attributes (JSON)",bg=self.colors["bg"],fg=self.colors["muted"]).pack(anchor="w")
        t_ca=tk.Text(jf,height=6,bg=self.colors["bg2"],fg=self.colors["fg"],wrap="word"); t_ca.pack(fill="x")
        t_ca.insert("1.0", _jdumps(_jloads(r.get("custom_attributes")) or {}))
        tk.Label(jf,text="Image URLs (JSON list)",bg=self.colors["bg"],fg=self.colors["muted"]).pack(anchor="w",pady=(8,0))
        t_imgs=tk.Text(jf,height=4,bg=self.colors["bg2"],fg=self.colors["fg"],wrap="word"); t_imgs.pack(fill="x")
        t_imgs.insert("1.0", _jdumps(_jloads(r.get("image_urls")) or []))
        bf=tk.Frame(d,bg=self.colors["bg"]); bf.pack(fill="x",padx=16,pady=12)
        ttk.Button(bf,text="Save",style="Dark.TButton",command=lambda:self._save(d,r["id"],v_name,v_type,v_vis,v_inv,v_rrp,v_weight,t_ca,t_imgs)).pack(side="left")
        ttk.Button(bf,text="Close",style="Dark.TButton",command=d.destroy).pack(side="right")

    def _save(self,dlg,pid,vn,vt,vv,vi,vr,vw,tca,timgs):
        try: inv=int(float(vi.get().strip()))
        except: messagebox.showerror("Validate","Inventory must be an integer."); return
        try: rrp=float(vr.get().strip()) if vr.get().strip()!="" else None
        except: messagebox.showerror("Validate","Price must be a number."); return
        try: wt=float(vw.get().strip()) if vw.get().strip()!="" else 0.0
        except: messagebox.showerror("Validate","Weight must be a number."); return
        ca=_jloads(tca.get("1.0","end").strip()) or {}
        imgs=_jloads(timgs.get("1.0","end").strip()) or []
        p=self.sess.get(ProductM2,pid)
        p.name=vn.get().strip()
        p.type_id=vt.get().strip() or "simple"
        p.visibility=int(vv.get().strip() or 4)
        p.inventory=inv
        p.price=_jdumps({"rrp":rrp}) if rrp is not None else _jdumps({})
        p.weight=wt
        p.custom_attributes=_jdumps(ca)
        p.image_urls=_jdumps(imgs)
        self.sess.commit(); dlg.destroy(); self.cache.pop(pid,None); self.reload(); self.status.config(text="Saved")

    def _edit_sel(self,_):
        sel=self.tree.selection()
        if not sel: return
        pid=int(sel[0]); r=next((x for x in self.data if x["id"]==pid),None)
        if r: self._open(r)

    def _load_img(self,r,label,maxw,maxh):
        pid=r["id"]
        if pid in self.cache:
            label.configure(image=self.cache[pid]); label.image=self.cache[pid]; return
        imgs=_jloads(r.get("image_urls")) or []
        path=(imgs[0] if imgs else (r.get("media_local") or "")).strip()
        img=None
        try:
            if path.startswith("http"):
                with urllib.request.urlopen(path,timeout=5) as resp:
                    img=Image.open(io.BytesIO(resp.read()))
            elif path and os.path.exists(path):
                img=Image.open(path)
            if img:
                img=img.convert("RGBA"); img.thumbnail((maxw,maxh))
                ph=ImageTk.PhotoImage(img); self.cache[pid]=ph
                label.configure(image=ph); label.image=ph
            else:
                label.configure(text="No Image",fg=self.colors["muted"],bg=self.colors["bg2"])
        except:
            label.configure(text="No Image",fg=self.colors["muted"],bg=self.colors["bg2"])

    def import_csv(self):
        p=filedialog.askopenfilename(title="Select CSV",filetypes=[("CSV","*.csv"),("All","*.*")])
        if not p: return
        try:
            upsert_from_csv(p)
            self.reload(); self.status.config(text="Imported")
        except Exception as e:
            messagebox.showerror("Import CSV",str(e))

    def export_jsonl(self):
        p=filedialog.asksaveasfilename(title="Save JSONL",defaultextension=".jsonl",filetypes=[("JSONL","*.jsonl")])
        if not p: return
        try:
            export_magento_jsonl(p)
            self.status.config(text="Exported JSONL")
        except Exception as e:
            messagebox.showerror("Export JSONL",str(e))

    def export_csv(self):
        p=filedialog.asksaveasfilename(title="Save CSV",defaultextension=".csv",filetypes=[("CSV","*.csv")])
        if not p: return
        try:
            export_vendor_feed_csv(p)
            self.status.config(text="Exported CSV")
        except Exception as e:
            messagebox.showerror("Export CSV",str(e))

    def thumbs(self):
        try:
            generate_thumbnails()
            self.reload(); self.status.config(text="Thumbnails Generated")
        except Exception as e:
            messagebox.showerror("Thumbnails",str(e))

    def _on_view_change(self):
        self._save_setting("view_mode", self.view.get())
        self.refresh()

    def _on_page_size_change(self):
        self._save_setting("page_size", str(self.page_size_var.get()))
        self.page=0
        self.refresh()

    def choose_columns(self):
        dlg=tk.Toplevel(self.root); dlg.title("Choose Columns"); dlg.configure(bg=self.colors["bg"]); dlg.transient(self.root); dlg.grab_set()
        vars={}
        body=tk.Frame(dlg,bg=self.colors["bg"]); body.pack(padx=16,pady=12,fill="both",expand=True)
        for key,label in self.available_columns:
            v=tk.BooleanVar(value=any(k==key for k,_ in self.visible_columns))
            cb=tk.Checkbutton(body,text=label,variable=v,fg=self.colors["fg"],bg=self.colors["bg"],activebackground=self.colors["bg"],activeforeground=self.colors["fg"],selectcolor=self.colors["bg2"])
            cb.pack(anchor="w")
            vars[key]=v
        btns=tk.Frame(dlg,bg=self.colors["bg"]); btns.pack(fill="x",padx=16,pady=(6,12))
        def apply():
            sel=[(k, next(lbl for kk,lbl in self.available_columns if kk==k)) for k,v in vars.items() if v.get()]
            if not sel:
                messagebox.showerror("Columns","Select at least one column."); return
            self.visible_columns=sel
            self._save_setting("visible_columns", _jdumps([k for k,_ in sel]))
            self._rebuild_tree_columns()
            self.refresh()
            dlg.destroy()
        ttk.Button(btns,text="Apply",style="Dark.TButton",command=apply).pack(side="left")
        ttk.Button(btns,text="Cancel",style="Dark.TButton",command=dlg.destroy).pack(side="right")

    def apply_bulk(self):
        if not self.selected_ids:
            messagebox.showwarning("Bulk Actions","No items selected."); return
        action=self.bulk_action.get()
        sess=self.sess
        if action=="Delete Selected":
            if not messagebox.askyesno("Confirm","Delete selected products?"): return
            for rid in list(self.selected_ids):
                obj=sess.get(ProductM2, rid)
                if obj: sess.delete(obj)
            sess.commit(); self.selected_ids.clear(); self.reload(); self.status.config(text="Deleted selected"); return
        if action in ("Enable Status","Disable Status"):
            val = (action=="Enable Status")
            for rid in self.selected_ids:
                obj=sess.get(ProductM2, rid)
                if obj: obj.status=val
            sess.commit(); self.reload(); self.status.config(text=("Enabled" if val else "Disabled")+" selected"); return
        if action.startswith("Set Visibility:"):
            vis = 1 if "Not Visible" in action else 4
            for rid in self.selected_ids:
                obj=sess.get(ProductM2, rid)
                if obj: obj.visibility=vis
            sess.commit(); self.reload(); self.status.config(text=f"Set visibility={vis}"); return
        if action == "Export Selected JSONL":
            p=filedialog.asksaveasfilename(title="Save Selected JSONL",defaultextension=".jsonl",filetypes=[("JSONL","*.jsonl")])
            if not p: return
            export_magento_jsonl(p, ids=self.selected_ids)
            self.status.config(text="Exported selected JSONL"); return
        if action == "Export Selected CSV":
            p=filedialog.asksaveasfilename(title="Save Selected CSV",defaultextension=".csv",filetypes=[("CSV","*.csv")])
            if not p: return
            export_vendor_feed_csv(p, ids=self.selected_ids)
            self.status.config(text="Exported selected CSV"); return

    def _load_visible_columns(self):
        raw=self._load_str_setting("visible_columns","")
        if raw:
            keys=_jloads(raw)
            if isinstance(keys,list) and keys:
                return [(k, next(lbl for kk,lbl in self.available_columns if kk==k)) for k in keys if any(kk==k for kk,_ in self.available_columns)]
        return [("sku","SKU"),("name","Name"),("price","Price"),("inventory","Inventory"),("status","Status"),("visibility","Visibility"),("type_id","Type")]

    def _load_str_setting(self, key, default):
        s=self.sess.query(AppSetting).filter_by(key=key).one_or_none()
        return s.value if s else default

    def _load_int_setting(self, key, default):
        v=self._load_str_setting(key,str(default))
        try: return int(v)
        except: return default

    def _save_setting(self, key, value):
        s=self.sess.query(AppSetting).filter_by(key=key).one_or_none()
        if s: s.value=str(value)
        else: self.sess.add(AppSetting(key=key,value=str(value)))
        self.sess.commit()

if __name__=="__main__":
    root=tk.Tk()
    app=App(root)
    root.mainloop()
