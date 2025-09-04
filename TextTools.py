#!/usr/bin/env python3
import tkinter as tk
from tkinter import messagebox
import re
import json
from datetime import datetime

LIGHT_THEME = {
    "bg": "#f0f0f0",
    "fg": "#000000",
    "entry_bg": "#ffffff",
    "button_bg": "#e0e0e0",
    "cursor": "#000000",
}

DARK_THEME = {
    "bg": "#2e2e2e",
    "fg": "#ffffff",
    "entry_bg": "#3e3e3e",
    "button_bg": "#5e5e5e",
    "cursor": "#ffffff",
}

current_theme = DARK_THEME

def apply_theme():
    root.configure(bg=current_theme["bg"])
    for widget in root.winfo_children():
        apply_widget_theme(widget)

def apply_widget_theme(widget):
    if isinstance(widget, tk.Frame):
        widget.configure(bg=current_theme["bg"])
        for child in widget.winfo_children():
            apply_widget_theme(child)
    elif isinstance(widget, tk.Label):
        widget.configure(bg=current_theme["bg"], fg=current_theme["fg"])
    elif isinstance(widget, tk.Button):
        widget.configure(bg=current_theme["button_bg"], fg=current_theme["fg"], bd=0, relief="flat")
    elif isinstance(widget, tk.Text):
        widget.configure(
            bg=current_theme["entry_bg"],
            fg=current_theme["fg"],
            insertbackground=current_theme["cursor"],
            undo=True,
            wrap="word",
        )

def toggle_theme():
    global current_theme
    current_theme = DARK_THEME if current_theme == LIGHT_THEME else LIGHT_THEME
    apply_theme()

def get_source_text_widget():
    """Prefer the focused Text widget; else output if it has content; else input."""
    w = root.focus_get()
    if w in (text_input, text_output):
        return w
    if text_output.get("1.0", "end-1c").strip():
        return text_output
    return text_input

def write_to_output(text: str):
    text_output.delete("1.0", tk.END)
    text_output.insert(tk.END, text)

def to_snake_token(s: str) -> str:
    s = s.strip()
    s = re.sub(r"[^\w\s]+", " ", s)                # punctuation -> space
    s = re.sub(r"[\s\-]+", "_", s)                 # spaces/hyphens -> underscore
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)  # camelCase -> snake
    s = re.sub(r"_+", "_", s)                      # collapse repeats
    return s.strip("_").lower()

def snake_case_text(text: str) -> str:
    # Mode 1: JSON -> snake_case keys recursively
    try:
        obj = json.loads(text)
        def snake_keys(x):
            if isinstance(x, dict):
                return {to_snake_token(k): snake_keys(v) for k, v in x.items()}
            if isinstance(x, list):
                return [snake_keys(i) for i in x]
            return x
        return json.dumps(snake_keys(obj), indent=2, ensure_ascii=False)
    except json.JSONDecodeError:
        pass

    # Mode 2: key: value lines -> snake_case the key part only
    lines = text.splitlines()
    colon_lines = [ln for ln in lines if ":" in ln]
    if colon_lines and len(colon_lines) >= max(1, int(0.5 * len(lines))):
        out = []
        for ln in lines:
            if ":" in ln:
                key, val = ln.split(":", 1)
                out.append(f"{to_snake_token(key)}:{val}")
            else:
                out.append(to_snake_token(ln) if ln.strip() else ln)
        return "\n".join(out)

    # Mode 3: CSV-ish single line -> snake_case items
    if ("\n" not in text) and ("," in text):
        items = [to_snake_token(part) for part in text.split(",")]
        return ",".join(items)

    # Mode 4: fallback -> snake_case each non-empty line as a whole
    return "\n".join(to_snake_token(ln) if ln.strip() else ln for ln in lines)

_EMOJI_PATTERN = re.compile(
    "["                     
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U00002700-\U000027BF"  # dingbats
    "\U000024C2-\U0001F251"  # enclosed chars
    "\U0001F900-\U0001F9FF"  # supplemental symbols & pictographs
    "\U0001FA70-\U0001FAFF"  # symbols & pictographs Extended-A
    "]+",
    flags=re.UNICODE
)

# Variation selectors, ZWJ, etc.
_INVISIBLES_PATTERN = re.compile(r"[\u200D\u200C\uFE0E\uFE0F]")

def remove_emojis(text: str) -> str:
    """Remove emoji + common modifiers/ZWJ."""
    text = _EMOJI_PATTERN.sub("", text)
    text = _INVISIBLES_PATTERN.sub("", text)
    return text

def normalize_after_removal(text: str) -> str:
    """
    Normalize spacing/punctuation after emoji removal:
    - Convert em/en/etc. dashes to ASCII '-' with spacing
    - Tidy spaces around dashes and punctuation
    - Trim spaces inside (), [], {}
    - Remove empty bracket pairs
    - Collapse repeated spaces (preserve newlines)
    """
    # Normalize non-breaking space to normal space (keep newlines intact)
    text = text.replace("\u00A0", " ")

    # Convert Unicode dash-like chars to a spaced ASCII dash " - "
    text = re.sub(r"[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]", " - ", text)

    # For plain ASCII hyphen, if there are surrounding spaces, normalize to " - "
    text = re.sub(r"\s+-\s+|\s+-|-\s+", " - ", text)

    # Trim spaces just inside brackets
    text = re.sub(r"([\(\[\{])\s+", r"\1", text)  # after opener
    text = re.sub(r"\s+([\)\]\}])", r"\1", text)  # before closer

    # Remove empty bracket pairs
    text = re.sub(r"\(\s*\)", "", text)
    text = re.sub(r"\[\s*\]", "", text)
    text = re.sub(r"\{\s*\}", "", text)

    # Remove stray spaces before punctuation like , . ; : ! ?
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)

    # Collapse multiple spaces (not affecting newlines)
    text = re.sub(r"[ \t\f\v]+", " ", text)

    # Strip trailing spaces at line ends
    lines = [ln.rstrip() for ln in text.splitlines()]
    return "\n".join(lines)

def process_input_1():
    """PHP serialized pairs -> JSON dict (simple key/value extractor)."""
    input_text = text_input.get("1.0", tk.END)
    pattern = r's:\d+:"(.*?)";s:\d+:"(.*?)";'
    matches = re.findall(pattern, input_text)

    data_dict = {}
    for key, value in matches:
        if not any(x in key for x in ['\";', 'a:', 'i:', 'b:', 'N']) and not any(x in value for x in ['a:', 'i:', 'b:', 'N']):
            data_dict[key] = value

    json_output = json.dumps(data_dict, indent=2, ensure_ascii=False)
    write_to_output(json_output)

def process_input_2():
    """Snake-case TEXT (JSON keys, key:value lines, CSV-ish, or free text)."""
    src = get_source_text_widget()
    raw = src.get("1.0", "end-1c")
    result = snake_case_text(raw)
    write_to_output(result)

def process_input_3():
    """Remove emojis from TEXT and normalize artifacts (spaces, dashes, parens)."""
    src = get_source_text_widget()
    raw = src.get("1.0", "end-1c")
    cleaned = remove_emojis(raw)
    cleaned = normalize_after_removal(cleaned)
    write_to_output(cleaned)

def copy_to_clipboard():
    output_text = text_output.get("1.0", tk.END)
    root.clipboard_clear()
    root.clipboard_append(output_text)

def save_to_file():
    output_text = text_output.get("1.0", tk.END)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"cleaned_text_{timestamp}.txt"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(output_text)
    messagebox.showinfo("Saved", f"Output saved to {filename}")

def clear_output():
    text_output.delete("1.0", tk.END)

root = tk.Tk()
root.title("Text Tools — PHP→JSON, Snake Case, Emoji Clean")
root.geometry("980x680")

# Controls (left)
control_frame = tk.Frame(root)
control_frame.pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=10)

tk.Button(control_frame, text="Convert PHP→JSON", command=process_input_1).pack(fill=tk.X, pady=5)
tk.Button(control_frame, text="Snake Case Text", command=process_input_2).pack(fill=tk.X, pady=5)
tk.Button(control_frame, text="Remove Emojis", command=process_input_3).pack(fill=tk.X, pady=5)
tk.Button(control_frame, text="Copy Output", command=copy_to_clipboard).pack(fill=tk.X, pady=5)
tk.Button(control_frame, text="Save Output", command=save_to_file).pack(fill=tk.X, pady=5)
tk.Button(control_frame, text="Clear Output", command=clear_output).pack(fill=tk.X, pady=5)
tk.Button(control_frame, text="Toggle Theme", command=toggle_theme).pack(fill=tk.X, pady=5)

# Main panes (right)
main_frame = tk.Frame(root)
main_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=10)

tk.Label(main_frame, text="Input (focus here to make tools read from this pane):").pack(anchor="w")
text_input = tk.Text(main_frame, height=10, width=100)
text_input.pack(pady=5, fill=tk.X)

tk.Label(main_frame, text="Output (tools always write here):").pack(anchor="w")
text_output = tk.Text(main_frame, height=20, width=100)
text_output.pack(pady=5, fill=tk.BOTH, expand=True)

apply_theme()
root.mainloop()
