"""
Text Toys (Tkinter)

A comprehensive toolkit for text manipulation with a modern GUI.

Features:
- **Modern dashboard layout** with a resizable sidebar and content area.
- **Find bar** (Ctrl+F) with highlighting and navigation.
- **Workflow helpers**: Load files, save output, and move text between panes.
- **Keyboard shortcuts** for all major operations (e.g., Ctrl+S, Ctrl+B).
- **Status bar** with progress indicators and live character/line counts.
- **Diff viewer** tab to see changes between input and output.
- **Persistent settings** for theme and font size (~/.text_tools_config.json).
"""

import os
import re
import json
import difflib
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, font
import tkinter.ttk as ttk

# --- THEMES AND STYLING ---
LIGHT_THEME = {
    "bg": "#f0f0f0", "fg": "#000000", "entry_bg": "#ffffff", "cursor": "#000000",
    "frame_bg": "#e0e0e0", "frame_fg": "#000000",
    "button_bg": "#dcdcdc", "button_fg": "#000000", "button_active_bg": "#c8c8c8",
    "danger_bg": "#f2dede", "danger_fg": "#a94442", "danger_active_bg": "#ebcccc",
    "success_bg": "#dff0d8", "success_fg": "#3c763d", "success_active_bg": "#d0e9c6",
    "primary_bg": "#d9edf7", "primary_fg": "#31708f", "primary_active_bg": "#c4e3f3",
    "status_bg": "#e7e7e7", "status_fg_success": "#3c763d", "status_fg_danger": "#a94442",
    "status_fg_info": "#31708f",
}

DARK_THEME = {
    "bg": "#2e2e2e", "fg": "#ffffff", "entry_bg": "#3e3e3e", "cursor": "#ffffff",
    "frame_bg": "#3c3c3c", "frame_fg": "#ffffff",
    "button_bg": "#5e5e5e", "button_fg": "#ffffff", "button_active_bg": "#6e6e6e",
    "danger_bg": "#a94442", "danger_fg": "#ffffff", "danger_active_bg": "#843534",
    "success_bg": "#3c763d", "success_fg": "#ffffff", "success_active_bg": "#2d572d",
    "primary_bg": "#31708f", "primary_fg": "#ffffff", "primary_active_bg": "#245269",
    "status_bg": "#252525", "status_fg_success": "#77dd77", "status_fg_danger": "#ff6961",
    "status_fg_info": "#aec6cf",
}

CONFIG_PATH = Path.home() / ".text_tools_config.json"

_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U00002700-\U000027BF"  # dingbats
    "\U000024C2-\U0001F251"  # enclosed chars
    "\U0001F900-\U0001F9FF"  # supplemental
    "\U0001FA70-\U0001FAFF"  # extended
    "]+",
    flags=re.UNICODE,
)
_INVISIBLES_PATTERN = re.compile(r"[\u200D\u200C\uFE0E\uFE0F]")


class TextToolsApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Text Tools — Enhanced Dashboard")
        self.root.geometry("1200x800")
        self.root.minsize(900, 600)

        self.status_timer = None
        self.last_operation = None
        self.base_font = font.nametofont("TkTextFont").copy()
        self.base_font.configure(size=11)

        self._load_config()
        self._build_layout()
        self.apply_theme()
        self._bind_shortcuts()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_layout(self):
        self.root.configure(bg=self.current_theme["bg"])
        self.paned = tk.PanedWindow(self.root, orient=tk.HORIZONTAL, sashrelief=tk.RAISED)
        self.paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self.sidebar = tk.Frame(self.paned, width=300)
        self.paned.add(self.sidebar, minsize=220)

        right_content = tk.Frame(self.paned)
        self.paned.add(right_content, minsize=500)
        right_content.columnconfigure(0, weight=1)
        right_content.rowconfigure(1, weight=1)

        self._build_find_bar(right_content)
        self._build_notebook(right_content)
        self._build_status_bar()
        self._build_sidebar_controls()

    def _build_find_bar(self, parent):
        self.find_bar = tk.Frame(parent, height=32)
        self.find_bar.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        self.find_bar.columnconfigure(1, weight=1)
        tk.Label(self.find_bar, text="Find:").grid(row=0, column=0)
        self.find_var = tk.StringVar()
        self.find_var.trace_add("write", lambda *_: self._find(step=0))
        self.find_entry = tk.Entry(self.find_bar, textvariable=self.find_var)
        self.find_entry.grid(row=0, column=1, sticky="ew", padx=6)
        self.find_match_case = tk.IntVar(value=0)
        tk.Checkbutton(self.find_bar, text="Aa", variable=self.find_match_case).grid(row=0, column=2, padx=2)
        tk.Button(self.find_bar, text="↑", command=lambda: self._find(step=-1)).grid(row=0, column=3)
        tk.Button(self.find_bar, text="↓", command=lambda: self._find(step=+1)).grid(row=0, column=4, padx=2)
        tk.Button(self.find_bar, text="×", command=self._hide_find).grid(row=0, column=5, padx=(4, 0))
        self.find_bar.grid_remove()

    def _build_notebook(self, parent):
        self.nb = ttk.Notebook(parent)
        self.nb.grid(row=1, column=0, sticky="nsew")

        vpaned = tk.PanedWindow(self.nb, orient=tk.VERTICAL, sashrelief=tk.RAISED)
        self.nb.add(vpaned, text="Editor")

        input_wrap = tk.LabelFrame(vpaned, text="Input", padx=5, pady=5)
        self.text_input = tk.Text(input_wrap, height=10, undo=True, wrap="word", font=self.base_font)
        self.text_input.pack(fill=tk.BOTH, expand=True)
        vpaned.add(input_wrap, minsize=100)
        self.text_input.bind("<<Modified>>", lambda e: self._update_counters())


        output_wrap = tk.LabelFrame(vpaned, text="Output", padx=5, pady=5)
        self.text_output = tk.Text(output_wrap, height=12, undo=True, wrap="word", font=self.base_font)
        self.text_output.pack(fill=tk.BOTH, expand=True)
        vpaned.add(output_wrap, minsize=100)
        self.text_output.bind("<<Modified>>", lambda e: self._update_counters())


        diff_wrap = tk.Frame(self.nb)
        self.diff_text = tk.Text(diff_wrap, wrap="none", font=self.base_font, state="disabled")
        self.diff_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.nb.add(diff_wrap, text="Diff")

    def _build_status_bar(self):
        status_bar = tk.Frame(self.root, height=26)
        status_bar.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=(0, 5))
        status_bar.pack_propagate(False)
        self.status_label = tk.Label(status_bar, text="Ready", anchor="w")
        self.status_label.pack(side=tk.LEFT, padx=8)
        self.counts_label = tk.Label(status_bar, text="", anchor="e")
        self.counts_label.pack(side=tk.RIGHT, padx=8)
        self.progress = ttk.Progressbar(status_bar, mode="indeterminate", length=100)
        self.progress.pack(side=tk.RIGHT, padx=8)

    def _build_sidebar_controls(self):
        for child in self.sidebar.winfo_children():
            child.destroy()
        
        self.buttons = {}
        
        io_frame = tk.LabelFrame(self.sidebar, text="File & Workflow", padx=10, pady=10)
        io_frame.pack(fill=tk.X, padx=10, pady=(10, 10))
        tk.Button(io_frame, text="Load File → Input", command=self.load_file_to_input).pack(fill=tk.X, pady=4)
        tk.Button(io_frame, text="Send Output → Input", command=self.send_output_to_input).pack(fill=tk.X, pady=4)

        ops = tk.LabelFrame(self.sidebar, text="Operations", padx=10, pady=10)
        ops.pack(fill=tk.X, padx=10, pady=10)
        self.buttons['php_json'] = tk.Button(ops, text="PHP Serialized → JSON (Ctrl+1)", command=self._wrap_op(self.process_php_to_json, "PHP→JSON"))
        self.buttons['snake'] = tk.Button(ops, text="Convert to snake_case (Ctrl+2)", command=self._wrap_op(self.process_snake_case, "snake_case"))
        self.buttons['emoji'] = tk.Button(ops, text="Clean Emojis & Text (Ctrl+3)", command=self._wrap_op(self.process_remove_emojis, "clean_emojis"))
        for btn in (self.buttons['php_json'], self.buttons['snake'], self.buttons['emoji']):
            btn.pack(fill=tk.X, pady=4)

        out = tk.LabelFrame(self.sidebar, text="Output Actions", padx=10, pady=10)
        out.pack(fill=tk.X, padx=10, pady=10)
        self.buttons['copy'] = tk.Button(out, text="Copy to Clipboard (Ctrl+B)", command=self.copy_to_clipboard)
        self.buttons['copy'].style_type = "primary"
        self.buttons['save'] = tk.Button(out, text="Save to File… (Ctrl+S)", command=self.save_to_file)
        self.buttons['save'].style_type = "success"
        self.buttons['clear'] = tk.Button(out, text="Clear Output", command=self.clear_output)
        self.buttons['clear'].style_type = "danger"
        for btn in (self.buttons['copy'], self.buttons['save'], self.buttons['clear']):
            btn.pack(fill=tk.X, pady=4)

        settings = tk.LabelFrame(self.sidebar, text="Settings", padx=10, pady=10)
        settings.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=10)
        self.buttons['theme'] = tk.Button(settings, text="Toggle Theme (Ctrl+/)", command=self.toggle_theme)
        self.buttons['theme'].pack(fill=tk.X)

    def apply_theme(self):
        theme = self.current_theme
        self.root.configure(bg=theme["bg"])

        style = ttk.Style()
        style.theme_use('default')
        style.configure("TNotebook", background=theme["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", background=theme["frame_bg"], foreground=theme["frame_fg"], padding=[8, 4], borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", theme["bg"])])

        def apply_to_children(widget):
            cls = widget.winfo_class()
            try:
                if cls in ('Frame', 'TFrame', 'Panedwindow'):
                    widget.configure(bg=theme["bg"])
                elif cls in ('Label', 'TLabel', 'Checkbutton'):
                    widget.configure(bg=theme["bg"], fg=theme["fg"])
                elif cls in ('Labelframe',):
                    widget.configure(bg=theme["bg"], fg=theme["fg"])
                for child in widget.winfo_children():
                    apply_to_children(child)
            except tk.TclError:
                pass
        
        apply_to_children(self.root)

        for text_widget in (self.text_input, self.text_output, self.diff_text):
            text_widget.configure(bg=theme["entry_bg"], fg=theme["fg"], insertbackground=theme["cursor"],
                                  selectbackground=theme["primary_bg"], selectforeground=theme["primary_fg"])
        self.find_entry.configure(bg=theme["entry_bg"], fg=theme["fg"], insertbackground=theme["cursor"])

        for btn in self.buttons.values():
            style_type = getattr(btn, 'style_type', 'default')
            bg = theme.get(f"{style_type}_bg", theme["button_bg"])
            fg = theme.get(f"{style_type}_fg", theme["button_fg"])
            abg = theme.get(f"{style_type}_active_bg", theme["button_active_bg"])
            btn.configure(bg=bg, fg=fg, activebackground=abg, activeforeground=fg, bd=0, relief="flat", padx=10, pady=6)

        for w in self.find_bar.winfo_children():
            if isinstance(w, tk.Button):
                 w.configure(bg=theme["button_bg"], fg=theme["button_fg"], activebackground=theme["button_active_bg"], relief="flat", bd=1)

        for w in (self.status_label, self.counts_label):
             w.master.configure(bg=theme["status_bg"])
             w.configure(bg=theme["status_bg"], fg=theme["fg"])
        
        for text_widget in [self.text_input, self.text_output]:
            text_widget.tag_configure('search_hit', background=theme["primary_bg"], foreground=theme["primary_fg"])

    def toggle_theme(self):
        self.current_theme = DARK_THEME if self.current_theme == LIGHT_THEME else LIGHT_THEME
        self.apply_theme()
        self.show_status_message("Theme changed", "info")

    def show_status_message(self, text, msg_type="info", duration_ms=4000):
        if self.status_timer:
            self.root.after_cancel(self.status_timer)
        fg_color = self.current_theme.get(f"status_fg_{msg_type}", self.current_theme["fg"])
        self.status_label.config(text=text, fg=fg_color)
        self.status_timer = self.root.after(duration_ms, self._clear_status_message)

    def _clear_status_message(self):
        self.status_label.config(text="Ready", fg=self.current_theme["fg"])
        self.status_timer = None

    @contextmanager
    def busy(self, message: str = "Working…"):
        self.progress.start(10)
        self.show_status_message(message, "info", duration_ms=10000)
        self.root.update_idletasks()
        try:
            yield
        finally:
            self.progress.stop()

    def _show_find(self, *_):
        self.find_bar.grid()
        self.find_entry.focus_set()
        self.find_entry.select_range(0, tk.END)

    def _hide_find(self, *_):
        self.find_bar.grid_remove()
        for w in (self.text_input, self.text_output):
            w.tag_remove('search_hit', '1.0', tk.END)

    def _find(self, step=0):
        pattern = self.find_var.get()
        widget = self.root.focus_get()
        if widget not in (self.text_input, self.text_output):
            widget = self.text_input if self.nb.index(self.nb.select()) == 0 else self.text_output
        
        widget.tag_remove('search_hit', '1.0', tk.END)
        if not pattern: return

        start = '1.0'
        hits = []
        nocase = 0 if self.find_match_case.get() else 1
        while True:
            idx = widget.search(pattern, start, nocase=nocase, stopindex=tk.END)
            if not idx: break
            end = f"{idx}+{len(pattern)}c"
            widget.tag_add('search_hit', idx, end)
            hits.append(idx)
            start = end
        
        if not hits:
            self.show_status_message("No matches", "danger")
            return

        self.show_status_message(f"Found {len(hits)} matches", "info")
        if step == 0: return

        cur = widget.index(tk.INSERT)
        positions = sorted([widget.index(h) for h in hits], key=lambda s: list(map(int, s.split('.'))))
        
        if step > 0:
            target = next((p for p in positions if p > cur), positions[0])
        else:
            rev = list(reversed(positions))
            target = next((p for p in rev if p < cur), rev[0])
            
        widget.mark_set(tk.INSERT, target)
        widget.see(target)

    def _load_config(self):
        if CONFIG_PATH.exists():
            try:
                cfg = json.loads(CONFIG_PATH.read_text("utf-8"))
                self.current_theme = LIGHT_THEME if cfg.get("theme") == "light" else DARK_THEME
                self.base_font.configure(size=int(cfg.get("font_size", 11)))
            except (json.JSONDecodeError, TypeError):
                self.current_theme = DARK_THEME
        else:
            self.current_theme = DARK_THEME

    def _save_config(self):
        data = {"theme": "light" if self.current_theme == LIGHT_THEME else "dark",
                "font_size": self.base_font.actual("size")}
        try:
            CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except IOError as e:
            self.show_status_message(f"Could not save config: {e}", "danger")

    def get_source_text_widget(self):
        focused = self.root.focus_get()
        if focused in (self.text_input, self.text_output):
            return focused
        return self.text_input

    def write_to_output(self, text: str):
        self.text_output.delete("1.0", tk.END)
        self.text_output.insert(tk.END, text)
        self.text_output.edit_modified(False) # Reset modified flag
        self._update_diff()

    def send_output_to_input(self):
        self.text_input.delete("1.0", tk.END)
        self.text_input.insert("1.0", self.text_output.get("1.0", "end-1c"))
        self.show_status_message("Output moved to Input", "info")
    
    def load_file_to_input(self):
        path = filedialog.askopenfilename(filetypes=[("Text/JSON", "*.txt *.json *.log *.md"), ("All", "*.*")])
        if not path: return
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.text_input.delete("1.0", tk.END)
                self.text_input.insert("1.0", f.read())
            self.show_status_message(f"Loaded {os.path.basename(path)}", "success")
        except Exception as e:
            self.show_status_message(f"Error loading file: {e}", "danger")

    def _update_diff(self):
        a = self.text_input.get("1.0", "end-1c").splitlines()
        b = self.text_output.get("1.0", "end-1c").splitlines()
        diff_lines = list(difflib.unified_diff(a, b, fromfile="input", tofile="output", lineterm=""))
        
        self.diff_text.config(state="normal")
        self.diff_text.delete("1.0", tk.END)
        if not diff_lines:
            self.diff_text.insert("1.0", "No differences found.")
        else:
            self.diff_text.insert("1.0", "\n".join(diff_lines))
        self.diff_text.config(state="disabled")

    def _update_counters(self, *_):
        widget = self.root.focus_get()
        text = ""
        label = ""
        if widget == self.text_input:
            text = self.text_input.get("1.0", "end-1c")
            label = "Input"
        elif widget == self.text_output:
            text = self.text_output.get("1.0", "end-1c")
            label = "Output"
        
        if label:
            chars = len(text)
            lines = text.count('\n') + 1 if text else 0
            self.counts_label.config(text=f"{label}: {lines} lines, {chars} chars")
            widget.edit_modified(False)

        self._update_diff()

    def _bind_shortcuts(self):
        self.root.bind("<Control-f>", self._show_find)
        self.root.bind("<Escape>", self._hide_find)
        self.root.bind("<Control-slash>", lambda e: self.toggle_theme())
        self.root.bind("<Control-s>", lambda e: self.save_to_file())
        self.root.bind("<Control-b>", lambda e: self.copy_to_clipboard())
        self.root.bind("<Control-equal>", lambda e: self._zoom(+1))
        self.root.bind("<Control-plus>", lambda e: self._zoom(+1))
        self.root.bind("<Control-minus>", lambda e: self._zoom(-1))
        self.root.bind("<F5>", lambda e: self._rerun_last())
        self.root.bind("<Control-Key-1>", self.buttons['php_json'].invoke)
        self.root.bind("<Control-Key-2>", self.buttons['snake'].invoke)
        self.root.bind("<Control-Key-3>", self.buttons['emoji'].invoke)

    def _zoom(self, delta: int):
        size = max(8, self.base_font.actual("size") + delta)
        self.base_font.configure(size=size)
        for w in (self.text_input, self.text_output, self.diff_text):
            w.configure(font=self.base_font)

    def _rerun_last(self):
        if self.last_operation:
            func, label = self.last_operation
            self.show_status_message(f"Re-running: {label}", "info")
            func()

    def _wrap_op(self, fn, label):
        def inner():
            self.last_operation = (inner, label)
            with self.busy(f"Running {label}…"):
                fn()
            self.show_status_message(f"{label} finished.", "success")
        return inner
    
    def _on_close(self):
        self._save_config()
        self.root.destroy()

    @staticmethod
    def to_snake_token(s: str) -> str:
        s = re.sub(r'[^a-zA-Z0-9]+', '_', s)
        return s.strip('_').lower()

    @staticmethod
    def snake_case_text(text: str) -> str:
        try:
            obj = json.loads(text)
            def snake_keys(x):
                if isinstance(x, dict): return {TextToolsApp.to_snake_token(k): snake_keys(v) for k, v in x.items()}
                if isinstance(x, list): return [snake_keys(i) for i in x]
                return x
            return json.dumps(snake_keys(obj), indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            lines = text.splitlines()
            return "\n".join(TextToolsApp.to_snake_token(ln) if ln.strip() else ln for ln in lines)

    @staticmethod
    def remove_emojis(text: str) -> str:
        text = _EMOJI_PATTERN.sub("", text)
        return _INVISIBLES_PATTERN.sub("", text)

    @staticmethod
    def normalize_after_removal(text: str) -> str:
        processed_lines = []
        hr_pattern = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
        for line in text.splitlines():
            if hr_pattern.match(line):
                processed_lines.append(line)
                continue
            line = line.replace("\u00A0", " ")
            line = re.sub(r"[\u2010-\u2015\u2212]", " - ", line)
            line = re.sub(r"([\(\[\{])\s+", r"\1", line)
            line = re.sub(r"\s+([\)\]\}])", r"\1", line)
            line = re.sub(r"[\(\[\{]\s*[\)\]\}]", "", line)
            line = re.sub(r"\s+([,.;:!?])", r"\1", line)
            line = re.sub(r"[ \t\f\v]+", " ", line)
            processed_lines.append(line.rstrip())
        return "\n".join(processed_lines)

    def process_php_to_json(self):
        input_text = self.text_input.get("1.0", tk.END)
        pattern = r's:\d+:"(.*?)";s:\d+:"(.*?)";'
        matches = re.findall(pattern, input_text)
        data_dict = {k: v for k, v in matches if not any(x in k or x in v for x in ['\";', 'a:', 'i:', 'b:', 'N'])}
        json_output = json.dumps(data_dict, indent=2, ensure_ascii=False)
        self.write_to_output(json_output)

    def process_snake_case(self):
        source_widget = self.get_source_text_widget()
        self.write_to_output(self.snake_case_text(source_widget.get("1.0", "end-1c")))

    def process_remove_emojis(self):
        source_widget = self.get_source_text_widget()
        cleaned = self.remove_emojis(source_widget.get("1.0", "end-1c"))
        self.write_to_output(self.normalize_after_removal(cleaned))

    def copy_to_clipboard(self):
        output_text = self.text_output.get("1.0", "end-1c")
        if output_text.strip():
            self.root.clipboard_clear()
            self.root.clipboard_append(output_text)
            self.show_status_message("Output copied to clipboard.", "info")
        else:
            self.show_status_message("Output is empty.", "danger")

    def save_to_file(self):
        output_text = self.text_output.get("1.0", "end-1c")
        if not output_text.strip():
            self.show_status_message("Output is empty.", "danger")
            return
        filepath = filedialog.asksaveasfilename(
            initialfile=f"output_{datetime.now():%Y%m%d_%H%M%S}.txt",
            defaultextension=".txt",
            filetypes=[("Text", "*.txt"), ("JSON", "*.json"), ("All Files", "*.*")]
        )
        if filepath:
            try:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(output_text)
                self.show_status_message(f"Saved to {os.path.basename(filepath)}", "success")
            except Exception as e:
                self.show_status_message(f"Error saving file: {e}", "danger")

    def clear_output(self):
        self.text_output.delete("1.0", tk.END)
        self.show_status_message("Output cleared.", "info")


if __name__ == "__main__":
    root = tk.Tk()
    app = TextToolsApp(root)
    root.mainloop()

