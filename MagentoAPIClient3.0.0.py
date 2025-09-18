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
            # Handle successful responses that might not have a body (e.g., 204 No Content)
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

    def create_product(self, payload):
        return self._make_request("POST", "/products", data=json.dumps(payload))

    def create_category(self, payload):
        return self._make_request("POST", "/categories", data=json.dumps(payload))

    def create_attribute_set(self, payload):
        # Requires skeleton and entity type ID for products (4)
        return self._make_request("POST", "/eav/attribute-sets", data=json.dumps(payload))

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
    """The main Tkinter application class."""
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
        self._create_json_importer_tab() # New tab
        self._create_status_bar()

        self.load_config()

    def _create_main_layout(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(expand=True, fill='both', padx=10, pady=10)

        self.api_tab = ttk.Frame(self.notebook, padding="10")
        self.product_tab = ttk.Frame(self.notebook, padding="10")
        self.json_importer_tab = ttk.Frame(self.notebook, padding="10") # New tab frame

        self.notebook.add(self.api_tab, text='Configuration')
        self.notebook.add(self.product_tab, text='Product Creator')
        self.notebook.add(self.json_importer_tab, text='JSON Importer') # Add new tab

    def _create_json_importer_tab(self):
        frame = self.json_importer_tab
        
        # --- Controls ---
        controls_frame = ttk.LabelFrame(frame, text="Import Controls", padding=10)
        controls_frame.pack(fill='x', pady=5)
        
        ttk.Label(controls_frame, text="Entity Type:").pack(side=tk.LEFT, padx=(0, 5))
        self.entity_type_combo = ttk.Combobox(controls_frame, state='readonly', values=["Product", "Category", "Attribute Set"])
        self.entity_type_combo.pack(side=tk.LEFT, padx=5)
        self.entity_type_combo.set("Product") # Default selection

        ttk.Button(controls_frame, text="Load JSON From File...", command=self.load_json_file).pack(side=tk.LEFT, padx=10)
        self.import_button = ttk.Button(controls_frame, text="Import to Magento", command=self.start_json_import)
        self.import_button.pack(side=tk.LEFT, padx=5)

        # --- JSON Payload Preview ---
        payload_frame = ttk.LabelFrame(frame, text="JSON Payload Preview", padding=10)
        payload_frame.pack(fill='both', expand=True, pady=10)
        
        self.json_preview_text = scrolledtext.ScrolledText(payload_frame, wrap=tk.WORD, height=15)
        self.json_preview_text.pack(fill='both', expand=True)

        # --- Log Viewer ---
        log_frame = ttk.LabelFrame(frame, text="Import Log", padding=10)
        log_frame.pack(fill='both', expand=True, pady=5)
        
        self.json_import_log = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, state=tk.DISABLED, height=10)
        self.json_import_log.pack(fill='both', expand=True)

    def load_json_file(self):
        filepath = filedialog.askopenfilename(
            title="Select JSON File",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if not filepath:
            return
        
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Validate and pretty-print the JSON
            data = json.loads(content)
            pretty_json = json.dumps(data, indent=4)
            
            self.json_preview_text.delete(1.0, tk.END)
            self.json_preview_text.insert(tk.END, pretty_json)
            self.set_status(f"Loaded {os.path.basename(filepath)}")
        except (json.JSONDecodeError, IOError) as e:
            messagebox.showerror("Error Reading File", f"Could not read or parse the JSON file.\n\nError: {e}")

    def start_json_import(self):
        if not self.api_client:
            messagebox.showerror("Not Connected", "Please connect to Magento from the Configuration tab first.")
            return

        json_content = self.json_preview_text.get(1.0, tk.END).strip()
        if not json_content:
            messagebox.showwarning("No Data", "The JSON preview is empty. Please load a file.")
            return
            
        self.import_button.config(state=tk.DISABLED)
        self.run_threaded(self._json_import_task, json_content)

    def _json_import_task(self, json_content):
        entity_type = self.entity_type_combo.get()
        self.set_status(f"Importing {entity_type}...")
        self.log("", self.json_import_log, clear=True) # Clear previous log
        
        try:
            payload = json.loads(json_content)
            response = None
            
            if entity_type == "Product":
                response = self.api_client.create_product(payload)
            elif entity_type == "Category":
                response = self.api_client.create_category(payload)
            elif entity_type == "Attribute Set":
                response = self.api_client.create_attribute_set(payload)
            
            success_msg = f"Successfully imported {entity_type}!"
            self.set_status(success_msg)
            self.log(f"SUCCESS!\nResponse:\n{json.dumps(response, indent=2)}", self.json_import_log)
            messagebox.showinfo("Import Successful", success_msg)
            
        except json.JSONDecodeError as e:
            error_msg = f"Invalid JSON in preview editor: {e}"
            self.set_status("Error: Invalid JSON")
            self.log(error_msg, self.json_import_log)
            messagebox.showerror("JSON Error", error_msg)
        except (ValueError, ConnectionError) as e:
            error_msg = f"Failed to import {entity_type}: {e}"
            self.set_status("Error: Import failed")
            self.log(error_msg, self.json_import_log)
            messagebox.showerror("API Error", error_msg)
        finally:
            self.import_button.config(state=tk.NORMAL)

    # --- Methods from previous version (abridged for clarity) ---
    def _create_status_bar(self):
        self.status_bar = ttk.Label(self.root, text="Ready", relief=tk.SUNKEN, anchor=tk.W)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

    def set_status(self, text):
        self.status_bar.config(text=text)
        self.root.update_idletasks()

    def _create_api_tab(self):
        # ... (code is identical to the provided script)
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
        # ... (code is identical to the provided script)
        paned_window = ttk.PanedWindow(self.product_tab, orient=tk.HORIZONTAL)
        paned_window.pack(expand=True, fill='both')
        left_pane = ttk.Frame(paned_window, width=500)
        paned_window.add(left_pane, weight=2)
        right_pane = ttk.Frame(paned_window, width=300)
        paned_window.add(right_pane, weight=1)
        core_frame = ttk.LabelFrame(left_pane, text="Core Information", padding=10)
        core_frame.pack(fill='x', padx=5, pady=5)
        ttk.Label(core_frame, text="Attribute Set:").grid(row=0, column=0, sticky='w')
        self.attribute_set_combo = ttk.Combobox(core_frame, state='readonly')
        self.attribute_set_combo.grid(row=0, column=1, sticky='ew', padx=5, pady=5)
        self.attribute_set_combo.bind("<<ComboboxSelected>>", self.on_attribute_set_change)
        ttk.Label(core_frame, text="SKU:").grid(row=1, column=0, sticky='w')
        self.sku_entry = ttk.Entry(core_frame)
        self.sku_entry.grid(row=1, column=1, sticky='ew', padx=5, pady=5)
        ttk.Label(core_frame, text="Name:").grid(row=2, column=0, sticky='w')
        self.name_entry = ttk.Entry(core_frame)
        self.name_entry.grid(row=2, column=1, sticky='ew', padx=5, pady=5)
        ttk.Label(core_frame, text="Price:").grid(row=3, column=0, sticky='w')
        self.price_entry = ttk.Entry(core_frame)
        self.price_entry.grid(row=3, column=1, sticky='ew', padx=5, pady=5)
        ttk.Label(core_frame, text="Quantity:").grid(row=4, column=0, sticky='w')
        self.qty_entry = ttk.Entry(core_frame)
        self.qty_entry.grid(row=4, column=1, sticky='ew', padx=5, pady=5)
        core_frame.columnconfigure(1, weight=1)
        attr_container = ttk.LabelFrame(left_pane, text="Attributes", padding=10)
        attr_container.pack(expand=True, fill='both', padx=5, pady=5)
        canvas = tk.Canvas(attr_container)
        scrollbar = ttk.Scrollbar(attr_container, orient="vertical", command=canvas.yview)
        self.scrollable_frame = ttk.Frame(canvas)
        self.scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        right_notebook = ttk.Notebook(right_pane)
        right_notebook.pack(expand=True, fill='both')
        cat_tab = ttk.Frame(right_notebook, padding=5)
        img_tab = ttk.Frame(right_notebook, padding=5)
        log_tab = ttk.Frame(right_notebook, padding=5)
        right_notebook.add(cat_tab, text='Categories')
        right_notebook.add(img_tab, text='Images')
        right_notebook.add(log_tab, text='Log')
        self.category_tree = ttk.Treeview(cat_tab, selectmode='none')
        self.category_tree.pack(expand=True, fill='both')
        self.category_tree.heading("#0", text="Select Categories", anchor='w')
        self.category_tree.bind('<ButtonRelease-1>', self.on_tree_select)
        ttk.Button(img_tab, text="Add Images...", command=self.add_images).pack(fill='x', pady=5)
        self.image_frame = ttk.Frame(img_tab)
        self.image_frame.pack(expand=True, fill='both')
        self.log_text = scrolledtext.ScrolledText(log_tab, wrap=tk.WORD, state=tk.DISABLED, height=10)
        self.log_text.pack(expand=True, fill='both')
        self.create_button = ttk.Button(left_pane, text="Create Product in Magento", command=self.submit_product_creation)
        self.create_button.pack(pady=10)

    # ... all other methods from the previous code are included here ...
    def run_threaded(self, func, *args):
        thread = threading.Thread(target=func, args=args, daemon=True)
        thread.start()
    def connect_and_fetch(self):
        self.connect_button.config(state=tk.DISABLED)
        self.run_threaded(self._connect_and_fetch_task)
    def _connect_and_fetch_task(self):
        self.set_status("Connecting to Magento...")
        try:
            url = self.url_entry.get().strip()
            token = self.token_entry.get().strip()
            if not (url and token):
                raise ValueError("URL and Token cannot be empty.")
            self.api_client = MagentoAPIClient(url, token)
            self.set_status("Fetching attribute sets...")
            sets = self.api_client.get_attribute_sets()
            set_map = {s['attribute_set_name']: s['attribute_set_id'] for s in sets}
            self.attribute_set_combo['values'] = list(set_map.keys())
            self.attribute_set_map = set_map
            self.set_status("Fetching category tree...")
            root_category = self.api_client.get_category_tree()
            self.build_category_tree(root_category)
            self.set_status("Connected and data fetched successfully.")
            self.notebook.select(self.product_tab)
        except (ValueError, ConnectionError) as e:
            self.set_status("Error!")
            messagebox.showerror("Connection Failed", str(e))
        finally:
            self.connect_button.config(state=tk.NORMAL)
    def on_attribute_set_change(self, event=None):
        self.run_threaded(self._fetch_attributes_task)
    def _fetch_attributes_task(self):
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()
        self.dynamic_widgets.clear()
        set_name = self.attribute_set_combo.get()
        set_id = self.attribute_set_map.get(set_name)
        if not set_id: return
        self.set_status(f"Fetching attributes for '{set_name}'...")
        try:
            attributes = self.api_client.get_attributes_for_set(set_id)
            with ThreadPoolExecutor() as executor:
                future_to_attr = {executor.submit(self.api_client.get_attribute_options, attr['attribute_code']): attr for attr in attributes if attr['frontend_input'] in ['select', 'multiselect']}
                options_cache = {}
                for future in future_to_attr:
                    attr = future_to_attr[future]
                    try:
                        options_cache[attr['attribute_code']] = future.result()
                    except Exception as e:
                         self.log(f"Warning: Could not fetch options for {attr['attribute_code']}: {e}", self.log_text)
            self.root.after(0, self._build_attribute_ui, attributes, options_cache)
        except ConnectionError as e:
            self.set_status("Error!")
            messagebox.showerror("Error", f"Failed to fetch attributes: {e}")
    def _build_attribute_ui(self, attributes, options_cache):
        core_attrs = {'sku', 'name', 'price', 'quantity_and_stock_status', 'visibility', 'description'}
        for i, attr in enumerate(attributes):
            code = attr['attribute_code']
            if not attr['is_user_defined'] and code not in {'description'}: continue
            if code in core_attrs: continue
            label = attr.get('default_frontend_label', code)
            ttk.Label(self.scrollable_frame, text=f"{label}:").grid(row=i, column=0, sticky='w', padx=5, pady=5)
            input_type = attr['frontend_input']
            widget = None
            if input_type in ['text', 'price', 'weight']:
                widget = ttk.Entry(self.scrollable_frame)
            elif input_type == 'textarea':
                widget = scrolledtext.ScrolledText(self.scrollable_frame, height=4, width=40, wrap=tk.WORD)
            elif input_type == 'boolean':
                var = tk.IntVar()
                widget = ttk.Checkbutton(self.scrollable_frame, text="Yes", variable=var)
                self.dynamic_widgets[code] = var
            elif input_type in ['select', 'multiselect']:
                options = options_cache.get(code, [])
                display_options = [opt['label'] for opt in options if opt.get('label') and opt.get('value')]
                self.dynamic_widgets[f"{code}_map"] = {opt['label']: opt['value'] for opt in options if opt.get('label') and opt.get('value')}
                if input_type == 'select':
                    widget = ttk.Combobox(self.scrollable_frame, values=display_options, state='readonly')
                else: # multiselect
                    widget = tk.Listbox(self.scrollable_frame, selectmode=tk.MULTIPLE, height=4, exportselection=False)
                    for option in display_options:
                        widget.insert(tk.END, option)
            if widget:
                widget.grid(row=i, column=1, sticky='ew', padx=5, pady=5)
                if not isinstance(widget, ttk.Checkbutton):
                    self.dynamic_widgets[code] = widget
        self.scrollable_frame.columnconfigure(1, weight=1)
        self.set_status("Attribute form ready.")
    def build_category_tree(self, node, parent_id=""):
        self.category_tree.tag_configure('checked', image=self.checked_img)
        self.category_tree.tag_configure('unchecked', image=self.unchecked_img)
        name = node.get('name', 'Root')
        node_id = str(node.get('id', 'root'))
        item = self.category_tree.insert(parent_id, 'end', iid=node_id, text=f" {name}", tags=('unchecked',))
        if node.get('children_data'):
            for child in node['children_data']:
                self.build_category_tree(child, parent_id=item)
    def on_tree_select(self, event):
        item_id = self.category_tree.identify_row(event.y)
        if not item_id: return
        tags = self.category_tree.item(item_id, 'tags')
        if 'checked' in tags:
            self.category_tree.item(item_id, tags=('unchecked',))
        else:
            self.category_tree.item(item_id, tags=('checked',))
    def add_images(self):
        files = filedialog.askopenfilenames(title="Select Images", filetypes=[("Image Files", "*.jpg *.jpeg *.png *.gif"), ("All files", "*.*")])
        for f in files:
            if f not in [img['path'] for img in self.image_list]:
                self.image_list.append({'path': f, 'roles': {'base': tk.BooleanVar(), 'small': tk.BooleanVar(), 'thumbnail': tk.BooleanVar()}})
        self.update_image_display()
    def update_image_display(self):
        for widget in self.image_frame.winfo_children():
            widget.destroy()
        for i, img_data in enumerate(self.image_list):
            path = img_data['path']
            filename = os.path.basename(path)
            row_frame = ttk.Frame(self.image_frame)
            row_frame.pack(fill='x', pady=2)
            ttk.Label(row_frame, text=filename, wraplength=150).pack(side=tk.LEFT, expand=True, fill='x')
            ttk.Checkbutton(row_frame, text="B", variable=img_data['roles']['base']).pack(side=tk.LEFT)
            ttk.Checkbutton(row_frame, text="S", variable=img_data['roles']['small']).pack(side=tk.LEFT)
            ttk.Checkbutton(row_frame, text="T", variable=img_data['roles']['thumbnail']).pack(side=tk.LEFT)
            ttk.Button(row_frame, text="X", width=2, command=lambda p=path: self.remove_image(p)).pack(side=tk.LEFT)
    def remove_image(self, path_to_remove):
        self.image_list = [img for img in self.image_list if img['path'] != path_to_remove]
        self.update_image_display()
    def submit_product_creation(self):
        self.create_button.config(state=tk.DISABLED)
        self.run_threaded(self._submit_product_task)
    def _get_widget_value(self, widget):
        if isinstance(widget, scrolledtext.ScrolledText): return widget.get(1.0, tk.END).strip()
        elif isinstance(widget, tk.Listbox): return [widget.get(i) for i in widget.curselection()]
        elif isinstance(widget, (ttk.Combobox, ttk.Entry)): return widget.get().strip()
        elif isinstance(widget, tk.IntVar): return widget.get()
        return None
    def _submit_product_task(self):
        try:
            self.set_status("Building product payload...")
            sku = self.sku_entry.get().strip()
            if not sku: raise ValueError("SKU is a required field.")
            custom_attributes = []
            for code, widget in self.dynamic_widgets.items():
                if code.endswith("_map"): continue
                value = self._get_widget_value(widget)
                if isinstance(widget, ttk.Combobox):
                    value_map = self.dynamic_widgets.get(f"{code}_map", {})
                    value = value_map.get(value)
                elif isinstance(widget, tk.Listbox):
                    value_map = self.dynamic_widgets.get(f"{code}_map", {})
                    value = [value_map.get(v) for v in value if v in value_map]
                if value not in [None, '', []]:
                    custom_attributes.append({'attribute_code': code, 'value': value})
            selected_cats = [item for item_id in self.category_tree.get_children('') for item in self._get_checked_items(item_id)]
            if selected_cats:
                custom_attributes.append({'attribute_code': 'category_ids', 'value': selected_cats})
            media_gallery = []
            for i, img_data in enumerate(self.image_list):
                with open(img_data['path'], "rb") as image_file:
                    encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
                roles = [role for role, var in img_data['roles'].items() if var.get()]
                if not roles and i == 0: roles = ['image', 'small_image', 'thumbnail']
                media_gallery.append({"media_type": "image", "label": os.path.basename(img_data['path']), "position": i + 1, "disabled": False, "types": roles, "content": {"base64_encoded_data": encoded_string, "type": f"image/{os.path.splitext(img_data['path'])[1].strip('.')}", "name": f"{sku}-{i+1}.jpg"}})
            payload = {"product": {"sku": sku, "name": self.name_entry.get().strip(), "attribute_set_id": self.attribute_set_map.get(self.attribute_set_combo.get()), "price": float(self.price_entry.get()), "status": 1, "visibility": 4, "type_id": "simple", "extension_attributes": {"website_ids": [1], "stock_item": {"qty": int(self.qty_entry.get()), "is_in_stock": int(self.qty_entry.get()) > 0}}, "custom_attributes": custom_attributes, "media_gallery_entries": media_gallery}}
            self.log(f"Submitting payload:\n{json.dumps(payload, indent=2)}", self.log_text)
            self.set_status("Creating product in Magento...")
            response = self.api_client.create_product(payload)
            self.log(f"\nSUCCESS!\nResponse:\n{json.dumps(response, indent=2)}", self.log_text)
            self.set_status(f"Product '{sku}' created successfully!")
            messagebox.showinfo("Success", f"Product '{sku}' was created successfully!")
        except (ValueError, ConnectionError) as e:
            self.set_status("Error!")
            self.log(f"\nERROR!\n{str(e)}", self.log_text)
            messagebox.showerror("Failed to Create Product", str(e))
        finally:
            self.create_button.config(state=tk.NORMAL)
    def _get_checked_items(self, parent_item=''):
        checked = []
        for item_id in self.category_tree.get_children(parent_item):
            if 'checked' in self.category_tree.item(item_id, 'tags'):
                checked.append(item_id)
            checked.extend(self._get_checked_items(item_id))
        return checked
    def save_config(self):
        config = {"magento_url": self.url_entry.get(), "token": self.token_entry.get()}
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(config, f)
            self.set_status("Configuration saved.")
        except IOError as e:
            messagebox.showerror("Error", f"Could not save config file: {e}")
    def load_config(self):
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r') as f:
                    config = json.load(f)
                self.url_entry.insert(0, config.get("magento_url", ""))
                self.token_entry.insert(0, config.get("token", ""))
                self.set_status("Configuration loaded.")
        except (IOError, json.JSONDecodeError) as e:
            self.set_status("Could not load config file.")
    def log(self, message, log_widget, clear=False):
        log_widget.config(state=tk.NORMAL)
        if clear: log_widget.delete(1.0, tk.END)
        log_widget.insert(tk.END, message + "\n")
        log_widget.see(tk.END)
        log_widget.config(state=tk.DISABLED)
    @property
    def checked_img(self):
        return tk.PhotoImage("checked_img", data=b'R0lGODlhDQANAPcAACH5BAEAAAAALAAAAAANAA0AAAAddACPyUmOzMy8vMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zGy83My8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8vMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8-DOw==')
    @property
    def unchecked_img(self):
        return tk.PhotoImage("unchecked_img", data=b'R0lGODlhDQANAPcAACH5BAEAAAAALAAAAAANAA0AAAAddACPyUmO3My8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zGy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8vMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8zMy8-DOw==')

if __name__ == "__main__":
    root = tk.Tk()
    app = AdvancedMagentoTool(root)
    root.mainloop()
