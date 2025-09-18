import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import requests
import json
import base64
import os
import threading
from concurrent.futures import ThreadPoolExecutor

# --- CONFIGURATION FILE ---
CONFIG_FILE = "config.json"

class MagentoAPIClient:
    """Handles all communication with the Magento 2 API."""
    def __init__(self, base_url, token):
        self.base_url = base_url.rstrip('/')
        self.headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }

    def _make_request(self, method, endpoint, **kwargs):
        """Generic request handler."""
        url = f"{self.base_url}/rest/V1{endpoint}"
        try:
            response = requests.request(method, url, headers=self.headers, timeout=30, **kwargs)
            response.raise_for_status()
            if response.text:
                return response.json()
            return True
        except requests.exceptions.HTTPError as e:
            error_message = f"HTTP Error: {e.response.status_code}\nURL: {url}"
            try:
                error_details = e.response.json()
                error_message += f"\nResponse: {json.dumps(error_details, indent=2)}"
            except json.JSONDecodeError:
                error_message += f"\nResponse: {e.response.text}"
            raise ConnectionError(error_message) from e
        except requests.exceptions.RequestException as e:
            raise ConnectionError(f"Connection failed: {e}") from e

    # --- GET (Read) ---
    def get_product(self, sku):
        return self._make_request("GET", f"/products/{requests.utils.quote(sku)}")
    
    def get_category(self, cat_id):
        return self._make_request("GET", f"/categories/{cat_id}")

    def get_attribute_set(self, set_id):
        return self._make_request("GET", f"/eav/attribute-sets/{set_id}")

    # --- POST (Create) ---
    def create_product(self, payload):
        return self._make_request("POST", "/products", data=json.dumps(payload))

    def create_category(self, payload):
        return self._make_request("POST", "/categories", data=json.dumps(payload))

    def create_attribute_set(self, payload):
        return self._make_request("POST", "/eav/attribute-sets", data=json.dumps(payload))

    # --- PUT (Update) ---
    def update_product(self, sku, payload):
        return self._make_request("PUT", f"/products/{requests.utils.quote(sku)}", data=json.dumps(payload))
    
    def update_category(self, cat_id, payload):
        return self._make_request("PUT", f"/categories/{cat_id}", data=json.dumps(payload))
    
    def update_attribute_set(self, set_id, payload):
        return self._make_request("PUT", f"/eav/attribute-sets/{set_id}", data=json.dumps(payload))

    # --- Metadata ---
    def get_attribute_sets(self):
        search_criteria = "searchCriteria[filter_groups][0][filters][0][field]=entity_type_id&searchCriteria[filter_groups][0][filters][0][value]=4"
        return self._make_request("GET", f"/eav/attribute-sets/list?{search_criteria}").get('items', [])

    def get_attributes_for_set(self, set_id):
        return self._make_request("GET", f"/products/attribute-sets/{set_id}/attributes")

    def get_attribute_options(self, attribute_code):
        return self._make_request("GET", f"/products/attributes/{attribute_code}/options")

    def get_category_tree(self):
        return self._make_request("GET", "/categories")


class AdvancedMagentoTool:
    def __init__(self, root):
        self.root = root
        self.root.title("Advanced Magento 2 Tool")
        self.root.geometry("1000x800")
        self.style = ttk.Style(self.root)
        self.style.theme_use("clam")

        self.api_client = None
        self.dynamic_widgets = {}
        self.image_list = []
        
        self._create_main_layout()
        self._create_api_tab()
        self._create_product_tab()
        self._create_json_transfer_tab()
        self._create_status_bar()
        self.load_config()

    def _create_main_layout(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(expand=True, fill='both', padx=10, pady=10)

        self.api_tab = ttk.Frame(self.notebook, padding="10")
        self.product_tab = ttk.Frame(self.notebook, padding="10")
        self.json_transfer_tab = ttk.Frame(self.notebook, padding="10")

        self.notebook.add(self.api_tab, text='Configuration')
        self.notebook.add(self.product_tab, text='Manual Product Creator')
        self.notebook.add(self.json_transfer_tab, text='JSON Data Transfer')

    def _create_json_transfer_tab(self):
        frame = self.json_transfer_tab
        
        # --- Main Paned Window ---
        paned_window = ttk.PanedWindow(frame, orient=tk.VERTICAL)
        paned_window.pack(expand=True, fill='both')

        # Top Pane: Controls
        controls_pane = ttk.Frame(paned_window, height=150)
        paned_window.add(controls_pane, weight=0)
        
        # Bottom Pane: Editor & Log
        editor_pane = ttk.Frame(paned_window)
        paned_window.add(editor_pane, weight=1)

        # --- Exporter ---
        exporter_frame = ttk.LabelFrame(controls_pane, text="Export from Magento", padding=10)
        exporter_frame.pack(side=tk.LEFT, fill='both', expand=True, padx=(0, 5))
        
        ttk.Label(exporter_frame, text="Entity Type:").grid(row=0, column=0, sticky='w', pady=2)
        self.export_entity_combo = ttk.Combobox(exporter_frame, state='readonly', values=["Product", "Category", "Attribute Set"])
        self.export_entity_combo.grid(row=0, column=1, sticky='ew')
        self.export_entity_combo.set("Product")
        
        ttk.Label(exporter_frame, text="Identifier (SKU or ID):").grid(row=1, column=0, sticky='w', pady=2)
        self.export_id_entry = ttk.Entry(exporter_frame)
        self.export_id_entry.grid(row=1, column=1, sticky='ew')
        
        ttk.Button(exporter_frame, text="Export to Editor", command=self.start_json_export).grid(row=2, column=1, sticky='w', pady=10)
        exporter_frame.columnconfigure(1, weight=1)

        # --- Importer ---
        importer_frame = ttk.LabelFrame(controls_pane, text="Import to Magento", padding=10)
        importer_frame.pack(side=tk.LEFT, fill='both', expand=True, padx=(5, 0))

        ttk.Label(importer_frame, text="Import Mode:").grid(row=0, column=0, sticky='w', pady=2)
        self.import_mode_combo = ttk.Combobox(importer_frame, state='readonly', values=["Create (POST)", "Update (PUT)"])
        self.import_mode_combo.grid(row=0, column=1, sticky='ew')
        self.import_mode_combo.set("Create (POST)")

        imp_btn_frame = ttk.Frame(importer_frame)
        imp_btn_frame.grid(row=2, column=0, columnspan=2, sticky='w', pady=10)
        ttk.Button(imp_btn_frame, text="Load From File...", command=self.load_json_file).pack(side=tk.LEFT, padx=(0,10))
        self.import_button = ttk.Button(imp_btn_frame, text="Run Import", command=self.start_json_import)
        self.import_button.pack(side=tk.LEFT)
        importer_frame.columnconfigure(1, weight=1)

        # --- Editor and Log ---
        editor_log_pane = ttk.PanedWindow(editor_pane, orient=tk.VERTICAL)
        editor_log_pane.pack(expand=True, fill='both')
        
        payload_frame = ttk.LabelFrame(editor_log_pane, text="JSON Payload Editor", padding=10)
        editor_log_pane.add(payload_frame, weight=3)
        self.json_preview_text = scrolledtext.ScrolledText(payload_frame, wrap=tk.WORD, height=15)
        self.json_preview_text.pack(fill='both', expand=True)
        self.save_to_file_button = ttk.Button(payload_frame, text="Save to File...", command=self.save_json_to_file, state=tk.DISABLED)
        self.save_to_file_button.pack(side=tk.LEFT, pady=(5,0))

        log_frame = ttk.LabelFrame(editor_log_pane, text="Transfer Log", padding=10)
        editor_log_pane.add(log_frame, weight=1)
        self.json_transfer_log = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, state=tk.DISABLED, height=10)
        self.json_transfer_log.pack(fill='both', expand=True)
    
    # --- JSON Transfer Logic ---
    def start_json_export(self):
        if not self.api_client:
            messagebox.showerror("Not Connected", "Please connect to Magento first.")
            return
        entity_type = self.export_entity_combo.get()
        identifier = self.export_id_entry.get().strip()
        if not identifier:
            messagebox.showwarning("Input Required", f"Please enter an identifier (SKU or ID) for the {entity_type}.")
            return
        self.run_threaded(self._export_task, entity_type, identifier)

    def _export_task(self, entity_type, identifier):
        self.set_status(f"Exporting {entity_type} '{identifier}'...")
        self.save_to_file_button.config(state=tk.DISABLED)
        try:
            data = None
            if entity_type == "Product": data = self.api_client.get_product(identifier)
            elif entity_type == "Category": data = self.api_client.get_category(identifier)
            elif entity_type == "Attribute Set": data = self.api_client.get_attribute_set(identifier)
            
            pretty_json = json.dumps(data, indent=4)
            self.json_preview_text.delete(1.0, tk.END)
            self.json_preview_text.insert(tk.END, pretty_json)
            self.log(f"SUCCESS: Exported {entity_type} '{identifier}'.", self.json_transfer_log, clear=True)
            self.set_status("Export successful. Data loaded into editor.")
            self.save_to_file_button.config(state=tk.NORMAL)
        except (ValueError, ConnectionError) as e:
            self.set_status("Error: Export failed.")
            self.log(str(e), self.json_transfer_log, clear=True)
            messagebox.showerror("Export Failed", str(e))

    def start_json_import(self):
        if not self.api_client:
            messagebox.showerror("Not Connected", "Please connect to Magento first.")
            return
        self.import_button.config(state=tk.DISABLED)
        self.run_threaded(self._json_import_task)

    def _json_import_task(self):
        mode = self.import_mode_combo.get()
        json_content = self.json_preview_text.get(1.0, tk.END).strip()
        self.set_status(f"Running Import ({mode})...")
        self.log("", self.json_transfer_log, clear=True)
        try:
            payload = json.loads(json_content)
            response = None
            
            # Extract entity type and ID for updates
            if "Create" in mode:
                entity_type = self.export_entity_combo.get() # Assume same type
                if entity_type == "Product": response = self.api_client.create_product(payload)
                elif entity_type == "Category": response = self.api_client.create_category(payload)
                elif entity_type == "Attribute Set": response = self.api_client.create_attribute_set(payload)
            else: # Update mode
                if 'product' in payload and 'sku' in payload['product']:
                    entity_type, identifier = "Product", payload['product']['sku']
                    response = self.api_client.update_product(identifier, payload)
                elif 'category' in payload and 'id' in payload['category']:
                    entity_type, identifier = "Category", payload['category']['id']
                    response = self.api_client.update_category(identifier, payload)
                elif 'attributeSet' in payload and 'attribute_set_id' in payload['attributeSet']:
                    entity_type, identifier = "Attribute Set", payload['attributeSet']['attribute_set_id']
                    response = self.api_client.update_attribute_set(identifier, payload)
                else:
                    raise ValueError("Cannot determine entity type or identifier for Update operation from JSON.")
            
            success_msg = f"Successfully imported ({mode}) {entity_type}!"
            self.set_status(success_msg)
            self.log(f"SUCCESS!\nResponse:\n{json.dumps(response, indent=2)}", self.json_transfer_log)
            messagebox.showinfo("Import Successful", success_msg)
            
        except (json.JSONDecodeError, ValueError, ConnectionError) as e:
            self.set_status("Error: Import failed.")
            self.log(str(e), self.json_transfer_log)
            messagebox.showerror("Import Failed", str(e))
        finally:
            self.import_button.config(state=tk.NORMAL)

    def save_json_to_file(self):
        content = self.json_preview_text.get(1.0, tk.END).strip()
        if not content:
            messagebox.showwarning("No Content", "Editor is empty.")
            return
        filepath = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if not filepath: return
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            self.set_status(f"Saved to {os.path.basename(filepath)}")
        except IOError as e:
            messagebox.showerror("Save Error", f"Could not save file: {e}")

    # --- Other Methods (Abridged for brevity) ---
    def _create_status_bar(self):
        self.status_bar = ttk.Label(self.root, text="Ready", relief=tk.SUNKEN, anchor=tk.W)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)
    def set_status(self, text):
        self.status_bar.config(text=text)
        self.root.update_idletasks()
    def _create_api_tab(self):
        frame = self.api_tab
        ttk.Label(frame, text="Magento URL:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.url_entry = ttk.Entry(frame, width=60)
        self.url_entry.grid(row=0, column=1, sticky=tk.EW, padx=5)
        ttk.Label(frame, text="Admin Token:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        self.token_entry = ttk.Entry(frame, width=60, show="*")
        self.token_entry.grid(row=1, column=1, sticky=tk.EW, padx=5)
        button_frame = ttk.Frame(frame)
        button_frame.grid(row=2, column=1, sticky=tk.W, pady=10)
        ttk.Button(button_frame, text="Save Config", command=self.save_config).pack(side=tk.LEFT, padx=5)
        self.connect_button = ttk.Button(button_frame, text="Connect & Fetch Data", command=self.connect_and_fetch)
        self.connect_button.pack(side=tk.LEFT, padx=5)
    def _create_product_tab(self):
        paned_window=ttk.PanedWindow(self.product_tab,orient=tk.HORIZONTAL);paned_window.pack(expand=True,fill='both');left_pane=ttk.Frame(paned_window,width=500);paned_window.add(left_pane,weight=2);right_pane=ttk.Frame(paned_window,width=300);paned_window.add(right_pane,weight=1);core_frame=ttk.LabelFrame(left_pane,text="Core Information",padding=10);core_frame.pack(fill='x',padx=5,pady=5);ttk.Label(core_frame,text="Attribute Set:").grid(row=0,column=0,sticky='w');self.attribute_set_combo=ttk.Combobox(core_frame,state='readonly');self.attribute_set_combo.grid(row=0,column=1,sticky='ew',padx=5,pady=5);self.attribute_set_combo.bind("<<ComboboxSelected>>",self.on_attribute_set_change);ttk.Label(core_frame,text="SKU:").grid(row=1,column=0,sticky='w');self.sku_entry=ttk.Entry(core_frame);self.sku_entry.grid(row=1,column=1,sticky='ew',padx=5,pady=5);ttk.Label(core_frame,text="Name:").grid(row=2,column=0,sticky='w');self.name_entry=ttk.Entry(core_frame);self.name_entry.grid(row=2,column=1,sticky='ew',padx=5,pady=5);ttk.Label(core_frame,text="Price:").grid(row=3,column=0,sticky='w');self.price_entry=ttk.Entry(core_frame);self.price_entry.grid(row=3,column=1,sticky='ew',padx=5,pady=5);ttk.Label(core_frame,text="Quantity:").grid(row=4,column=0,sticky='w');self.qty_entry=ttk.Entry(core_frame);self.qty_entry.grid(row=4,column=1,sticky='ew',padx=5,pady=5);core_frame.columnconfigure(1,weight=1);attr_container=ttk.LabelFrame(left_pane,text="Attributes",padding=10);attr_container.pack(expand=True,fill='both',padx=5,pady=5);canvas=tk.Canvas(attr_container);scrollbar=ttk.Scrollbar(attr_container,orient="vertical",command=canvas.yview);self.scrollable_frame=ttk.Frame(canvas);self.scrollable_frame.bind("<Configure>",lambda e:canvas.configure(scrollregion=canvas.bbox("all")));canvas.create_window((0,0),window=self.scrollable_frame,anchor="nw");canvas.configure(yscrollcommand=scrollbar.set);canvas.pack(side="left",fill="both",expand=True);scrollbar.pack(side="right",fill="y");right_notebook=ttk.Notebook(right_pane);right_notebook.pack(expand=True,fill='both');cat_tab=ttk.Frame(right_notebook,padding=5);img_tab=ttk.Frame(right_notebook,padding=5);log_tab=ttk.Frame(right_notebook,padding=5);right_notebook.add(cat_tab,text='Categories');right_notebook.add(img_tab,text='Images');right_notebook.add(log_tab,text='Log');self.category_tree=ttk.Treeview(cat_tab,selectmode='none');self.category_tree.pack(expand=True,fill='both');self.category_tree.heading("#0",text="Select Categories",anchor='w');self.category_tree.bind('<ButtonRelease-1>',self.on_tree_select);ttk.Button(img_tab,text="Add Images...",command=self.add_images).pack(fill='x',pady=5);self.image_frame=ttk.Frame(img_tab);self.image_frame.pack(expand=True,fill='both');self.log_text=scrolledtext.ScrolledText(log_tab,wrap=tk.WORD,state=tk.DISABLED,height=10);self.log_text.pack(expand=True,fill='both');self.create_button=ttk.Button(left_pane,text="Create Product in Magento",command=self.submit_product_creation);self.create_button.pack(pady=10)
    # The following are placeholders for brevity but exist in the full script
    def run_threaded(self, func, *args):
        thread=threading.Thread(target=func,args=args,daemon=True);thread.start()
    def connect_and_fetch(self):
        self.connect_button.config(state=tk.DISABLED);self.run_threaded(self._connect_and_fetch_task)
    def _connect_and_fetch_task(self):
        self.set_status("Connecting...");
        try:
            self.api_client = MagentoAPIClient(self.url_entry.get().strip(), self.token_entry.get().strip())
            sets=self.api_client.get_attribute_sets();set_map={s['attribute_set_name']:s['attribute_set_id']for s in sets};self.attribute_set_combo['values']=list(set_map.keys());self.attribute_set_map=set_map
            root_category=self.api_client.get_category_tree();self.build_category_tree(root_category)
            self.set_status("Connected.");self.notebook.select(self.product_tab)
        except Exception as e:
            self.set_status("Error!");messagebox.showerror("Connection Failed",str(e))
        finally:
            self.connect_button.config(state=tk.NORMAL)
    def load_json_file(self):
        filepath = filedialog.askopenfilename(title="Select JSON File", filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if not filepath: return
        try:
            with open(filepath, 'r', encoding='utf-8') as f: data = json.load(f)
            self.json_preview_text.delete(1.0, tk.END)
            self.json_preview_text.insert(tk.END, json.dumps(data, indent=4))
            self.set_status(f"Loaded {os.path.basename(filepath)}")
        except Exception as e: messagebox.showerror("Error Reading File", f"Could not parse JSON file.\n\nError: {e}")
    # ... and so on for all other existing methods ...
    def on_attribute_set_change(self, event=None): pass
    def submit_product_creation(self): pass
    def build_category_tree(self, node, parent_id=""): pass
    def on_tree_select(self, event): pass
    def add_images(self): pass
    def update_image_display(self): pass
    def remove_image(self, path): pass
    def save_config(self): pass
    def load_config(self): pass
    def log(self, message, log_widget, clear=False):
        log_widget.config(state=tk.NORMAL)
        if clear: log_widget.delete(1.0, tk.END)
        log_widget.insert(tk.END, message + "\n")
        log_widget.see(tk.END)
        log_widget.config(state=tk.DISABLED)
    @property
    def checked_img(self): return tk.PhotoImage("checked_img",data=b'R0lGODlhDQANAPcAACH5BAEAAAAALAAAAAANAA0AAAAddACPyUmOzMy8vMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zGy83My8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8vMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8-Z');
    @property
    def unchecked_img(self): return tk.PhotoImage("unchecked_img",data=b'R0lGODlhDQANAPcAACH5BAEAAAAALAAAAAANAA0AAAAddACPyUmO3My8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zGy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8vMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8-DOw==');

if __name__ == "__main__":
    root = tk.Tk()
    app = AdvancedMagentoTool(root)
    root.mainloop()
