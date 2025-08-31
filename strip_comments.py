"""
strip_comments.py — Remove comments from source files and write cleaned outputs.

Supported (by extension):
  - Python:        .py                (tokenizer+AST; optional docstring removal)
  - JavaScript:    .js, .mjs          (//, /* */; strings + template literals)
  - TypeScript:    .ts, .tsx          (same as JS)
  - C/C++/Java/Go: .c, .h, .cpp, .hpp, .java, .go
  - Shell:         .sh, .bash         (# comments, strings)
  - PowerShell:    .ps1               (# and <# #>)
  - CSS:           .css               (/* */)
  - SQL:           .sql               (-- and /* */)
  - HTML/XML:      .html, .htm, .xml  (<!-- -->)

Usage:
  python strip_comments.py <path> [--outdir OUT] [--inplace]
                           [--suffix .clean] [--aggressive-python-docstrings]
                           [--keep-shebang] [--keep-encoding]
                           [--preserve-linenos] [--verbose]

Examples:
  # Clean a single file next to it, producing file.clean.py
  python strip_comments.py app.py

  # Clean a directory tree into ./cleaned/
  python strip_comments.py src --outdir cleaned

  # Overwrite files in-place and strip Python docstrings as well
  python strip_comments.py project --inplace --aggressive-python-docstrings
"""
from __future__ import annotations
import argparse
import ast
import os
import re
import sys
import io
import tokenize
from pathlib import Path
from typing import Iterable, List, Tuple, Dict, Optional

# ----------------------------- Utility ---------------------------------

SUPPORTED_EXTS = {
    ".py", ".js", ".mjs", ".ts", ".tsx", ".java", ".c", ".h", ".cpp", ".hpp",
    ".go", ".sh", ".bash", ".ps1", ".css", ".sql", ".html", ".htm", ".xml",
}

C_LIKE_EXTS = {".js", ".mjs", ".ts", ".tsx", ".java", ".c", ".h", ".cpp", ".hpp", ".go", ".css"}
HASH_EXTS    = {".sh", ".bash"}
PS1_EXTS     = {".ps1"}
SQL_EXTS     = {".sql"}
HTML_EXTS    = {".html", ".htm", ".xml"}

_SHEBANG_RE = re.compile(rb'^#!.*\n?')
_PY_ENCODING_RE = re.compile(rb"^[ \t\f]*#.*?coding[:=][ \t]*([-\w.]+)")

def iter_source_files(root: Path) -> Iterable[Path]:
    if root.is_file():
        if root.suffix.lower() in SUPPORTED_EXTS:
            yield root
        return
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS:
            yield p

def read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except Exception as e:
        print(f"[WARN] Failed to read {path}: {e}", file=sys.stderr)
        return b""

def detect_python_encoding(src_bytes: bytes) -> Tuple[str, bool]:
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(src_bytes).readline)
        return encoding, True
    except Exception:
        return "utf-8", False

def safe_text_decode(b: bytes, fallback: str = "utf-8") -> str:
    for enc in (fallback, "utf-8", "utf-8-sig", "latin-1"):
        try:
            return b.decode(enc)
        except Exception:
            continue
    return b.decode("utf-8", errors="ignore")

def write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding=encoding, newline="\n")

def normalize_blank_lines(s: str, max_consecutive: int = 2) -> str:
    # Collapse runs of blank lines to at most max_consecutive (default 2)
    lines = s.splitlines()
    out, blank_run = [], 0
    for ln in lines:
        if ln.strip():
            blank_run = 0
            out.append(ln.rstrip())
        else:
            blank_run += 1
            if blank_run <= max_consecutive:
                out.append("")
    return "\n".join(out) + ("\n" if s.endswith("\n") else "")

def positions_within(tok_start: Tuple[int,int], tok_end: Tuple[int,int],
                     span: Tuple[int,int,int,int]) -> bool:
    (sline, scol) = tok_start
    (eline, ecol) = tok_end
    (a,b,c,d) = span
    # Token fully inside the span
    if (sline > a or (sline == a and scol >= b)) and (eline < c or (eline == c and ecol <= d)):
        return True
    return False

# ---------------------- Python cleaner (tokenizer+AST) ------------------

def python_docstring_spans(src_text: str) -> List[Tuple[int,int,int,int]]:
    """
    Return list of (lineno, col, end_lineno, end_col) spans for module/class/func docstrings.
    """
    spans: List[Tuple[int,int,int,int]] = []
    try:
        tree = ast.parse(src_text)
    except Exception:
        return spans

    def maybe_add(node):
        if not getattr(node, "body", None):
            return
        first = node.body[0]
        if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant) and isinstance(first.value.value, str):
            # Py3.8+ has end_lineno/end_col_offset; if missing, approximate
            if hasattr(first, "end_lineno") and hasattr(first, "end_col_offset"):
                spans.append((first.lineno, first.col_offset, first.end_lineno, first.end_col_offset))
            else:
                # Best-effort: mark the starting line; tokenizer check will be loose
                spans.append((first.lineno, first.col_offset, first.lineno, first.col_offset+1))

    maybe_add(tree)
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            maybe_add(n)
    return spans

def clean_python_text(src_text: str, aggressive_docstrings: bool) -> str:
    spans = python_docstring_spans(src_text) if aggressive_docstrings else []
    sio = io.StringIO(src_text)
    out_tokens: List[tokenize.TokenInfo] = []
    try:
        for tok in tokenize.generate_tokens(sio.readline):
            if tok.type == tokenize.COMMENT:
                continue
            if aggressive_docstrings and tok.type == tokenize.STRING:
                if any(positions_within(tok.start, tok.end, sp) for sp in spans):
                    continue
            out_tokens.append(tok)
        cleaned = tokenize.untokenize(out_tokens)
    except tokenize.TokenError:
        # Fallback: crude line-based removal of full-line comments
        cleaned = "\n".join(
            ln for ln in src_text.splitlines()
            if not ln.lstrip().startswith("#")
        ) + ("\n" if src_text.endswith("\n") else "")
    return cleaned

def process_python_file(path: Path, *,
                        aggressive_docstrings: bool,
                        keep_shebang: bool,
                        keep_encoding: bool,
                        preserve_linenos: bool) -> Tuple[str, str]:
    """Return (cleaned_text, encoding_used)."""
    b = read_bytes(path)
    # Extract shebang and encoding cookie lines
    shebang_b = b""
    enc_cookie_b = b""
    m_she = _SHEBANG_RE.match(b)
    if m_she:
        shebang_b = m_she.group(0)
    # encoding cookie must be in first two lines
    first_two = b.splitlines(keepends=True)[:2]
    for line in first_two:
        m_enc = _PY_ENCODING_RE.match(line)
        if m_enc:
            enc_cookie_b = line if keep_encoding else b""
            break

    enc, enc_ok = detect_python_encoding(b)
    text = safe_text_decode(b, fallback=enc)

    cleaned_text = clean_python_text(text, aggressive_docstrings=aggressive_docstrings)

    if not preserve_linenos:
        cleaned_text = normalize_blank_lines(cleaned_text, max_consecutive=2)

    # Re-insert shebang and/or encoding cookie in the right order
    prefix = ""
    if keep_shebang and shebang_b:
        prefix += shebang_b.decode("utf-8", errors="ignore").rstrip("\n") + "\n"
    if keep_encoding and enc_cookie_b:
        if prefix:
            # If shebang exists, encoding cookie must be on line 2 to be effective
            if not prefix.endswith("\n"):
                prefix += "\n"
            prefix += enc_cookie_b.decode("utf-8", errors="ignore").rstrip("\n") + "\n"
        else:
            prefix += enc_cookie_b.decode("utf-8", errors="ignore").rstrip("\n") + "\n"

    # Avoid duplicating newlines at boundary
    if prefix and cleaned_text.startswith("\n"):
        cleaned_text = cleaned_text.lstrip("\n")
    return prefix + cleaned_text, enc if enc_ok else "utf-8"

# ------------------- C/JS-like generic comment stripper ------------------

def _strip_c_like(code: str, *,
                  line_comment: Optional[str],
                  block_open: Optional[str],
                  block_close: Optional[str],
                  string_delims: Tuple[str, ...],
                  allow_backtick: bool = False,
                  css_mode: bool = False,
                  preserve_linenos: bool = False) -> str:
    """
    Remove comments while respecting strings (and JS template literals if allow_backtick=True).
    """
    i, n = 0, len(code)
    out: List[str] = []
    def push(ch: str):
        out.append(ch)

    def skip_line_comment(idx: int) -> int:
        start = idx
        while idx < n and code[idx] != "\n":
            idx += 1
        if preserve_linenos:
            # keep the newline
            if idx < n and code[idx] == "\n":
                push("\n")
                idx += 1
        return idx

    def skip_block_comment(idx: int) -> int:
        depth = 1
        oc, cc = block_open or "", block_close or ""
        while idx < n:
            if cc and code.startswith(cc, idx):
                idx += len(cc)
                depth -= 1
                if depth == 0:
                    break
            elif oc and code.startswith(oc, idx):
                # nested block (common in JS/C style? not standard, but be defensive)
                idx += len(oc)
                depth += 1
            else:
                idx += 1
        if preserve_linenos:
            # replace with equivalent newlines to keep line count
            # Count how many newlines were inside; reinsert them.
            segment = code[start_idx:idx]
            out.extend("\n" for _ in segment.split("\n")[1:])
        return idx

    while i < n:
        ch = code[i]

        # HTML/XML: very simple: <!-- ... -->
        if css_mode is False and block_open == "<!--" and code.startswith("<!--", i):
            start_idx = i
            i += 4
            while i < n and not code.startswith("-->", i):
                i += 1
            i = min(n, i + 3)  # skip -->
            if preserve_linenos:
                seg = code[start_idx:i]
                out.extend("\n" for _ in seg.split("\n")[1:])
            continue

        # Line comment
        if line_comment and code.startswith(line_comment, i):
            i = skip_line_comment(i + len(line_comment))
            continue

        # Block comment
        if block_open and code.startswith(block_open, i):
            start_idx = i
            i = skip_block_comment(i + len(block_open))
            continue

        # Strings (single/double)
        if ch in string_delims:
            quote = ch
            push(ch)
            i += 1
            while i < n:
                c2 = code[i]
                push(c2)
                i += 1
                if c2 == "\\":
                    # escape next char if present
                    if i < n:
                        push(code[i]); i += 1
                    continue
                if c2 == quote:
                    break
            continue

        # JS template literal with ${ ... } expressions
        if allow_backtick and ch == "`":
            push(ch); i += 1
            while i < n:
                if code[i] == "\\":
                    push(code[i]); i += 1
                    if i < n: push(code[i]); i += 1
                    continue
                if code.startswith("${", i):
                    # Enter expression; strip comments inside using a mini parser
                    push("${"); i += 2
                    brace = 1
                    # simple nested parser inside ${...}
                    while i < n and brace > 0:
                        # handle strings inside expression
                        if code[i] in ("'", '"'):
                            q = code[i]; push(code[i]); i += 1
                            while i < n:
                                cc = code[i]; push(cc); i += 1
                                if cc == "\\" and i < n:
                                    push(code[i]); i += 1
                                elif cc == q:
                                    break
                            continue
                        # template literal inside expression (rare)
                        if code[i] == "`":
                            # treat as plain char to avoid deep recursion
                            push("`"); i += 1
                            continue
                        # comments inside expression
                        if code.startswith("//", i):
                            while i < n and code[i] != "\n":
                                i += 1
                            if preserve_linenos and i < n and code[i] == "\n":
                                push("\n"); i += 1
                            continue
                        if code.startswith("/*", i):
                            j = i + 2
                            while j < n and not code.startswith("*/", j):
                                j += 1
                            # keep internal newlines if preserving
                            if preserve_linenos:
                                seg = code[i:j+2] if j < n else code[i:j]
                                out.extend("\n" for _ in seg.split("\n")[1:])
                            i = min(n, j + 2)
                            continue
                        # braces
                        if code[i] == "{":
                            push("{"); i += 1; brace += 1; continue
                        if code[i] == "}":
                            brace -= 1
                            if brace == 0:
                                push("}"); i += 1
                                break
                            else:
                                push("}"); i += 1
                                continue
                        # default
                        push(code[i]); i += 1
                    continue
                if code[i] == "`":
                    push("`"); i += 1
                    break
                # normal content
                push(code[i]); i += 1
            continue

        # Default: copy
        push(ch)
        i += 1

    s = "".join(out)
    return s if preserve_linenos else normalize_blank_lines(s, max_consecutive=2)

def clean_c_like(code: str, *, allow_backtick: bool, is_css: bool, preserve_linenos: bool) -> str:
    return _strip_c_like(
        code,
        line_comment=None if is_css else "//",
        block_open="/*",
        block_close="*/",
        string_delims=("'", '"'),
        allow_backtick=allow_backtick,
        css_mode=is_css,
        preserve_linenos=preserve_linenos,
    )

def clean_hash_style(code: str, *, preserve_linenos: bool) -> str:
    # Shell-style: # to end of line; respect strings
    return _strip_c_like(
        code,
        line_comment="#",
        block_open=None,
        block_close=None,
        string_delims=("'", '"'),
        allow_backtick=False,
        css_mode=False,
        preserve_linenos=preserve_linenos,
    )

def clean_powershell(code: str, *, preserve_linenos: bool) -> str:
    # PowerShell: # line comments; <# ... #> block; strings ' and " (with ` escapes)
    # Reuse engine; backtick escapes roughly handled by generic logic (we don't special-case `)
    return _strip_c_like(
        code,
        line_comment="#",
        block_open="<#",
        block_close="#>",
        string_delims=("'", '"'),
        allow_backtick=False,
        css_mode=False,
        preserve_linenos=preserve_linenos,
    )

def clean_sql(code: str, *, preserve_linenos: bool) -> str:
    # SQL: -- line comments; /* */ block; strings ' and " (double-quote often identifiers)
    return _strip_c_like(
        code,
        line_comment="--",
        block_open="/*",
        block_close="*/",
        string_delims=("'", '"'),
        allow_backtick=False,
        css_mode=False,
        preserve_linenos=preserve_linenos,
    )

def clean_html(code: str, *, preserve_linenos: bool) -> str:
    # Very simple HTML/XML: remove <!-- --> anywhere (doesn't parse <script> contents)
    return _strip_c_like(
        code,
        line_comment=None,
        block_open="<!--",
        block_close="-->",
        string_delims=("'", '"'),
        allow_backtick=False,
        css_mode=False,
        preserve_linenos=preserve_linenos,
    )

# ------------------------------- Driver ---------------------------------

def clean_text_by_ext(ext: str, text: str, *, preserve_linenos: bool) -> str:
    ext = ext.lower()
    if ext in C_LIKE_EXTS:
        return clean_c_like(
            text,
            allow_backtick=ext in {".js", ".mjs", ".ts", ".tsx"},
            is_css=(ext == ".css"),
            preserve_linenos=preserve_linenos,
        )
    if ext in HASH_EXTS:
        return clean_hash_style(text, preserve_linenos=preserve_linenos)
    if ext in PS1_EXTS:
        return clean_powershell(text, preserve_linenos=preserve_linenos)
    if ext in SQL_EXTS:
        return clean_sql(text, preserve_linenos=preserve_linenos)
    if ext in HTML_EXTS:
        return clean_html(text, preserve_linenos=preserve_linenos)
    # Default no-op
    return text

def compute_out_path(src: Path, root: Path, outdir: Optional[Path], inplace: bool, suffix: str) -> Path:
    if inplace:
        return src
    if outdir:
        rel = src.relative_to(root if root.is_dir() else src.parent)
        return outdir / rel
    # default: side-by-side with suffix
    return src.with_name(src.stem + suffix + src.suffix)

def main():
    ap = argparse.ArgumentParser(description="Strip comments from source files.")
    ap.add_argument("path", type=str, help="File or directory to process")
    ap.add_argument("--outdir", type=str, default=None, help="Directory for cleaned outputs (for dir or file).")
    ap.add_argument("--inplace", action="store_true", help="Overwrite original files.")
    ap.add_argument("--suffix", type=str, default=".clean", help="Filename suffix when not using --inplace (default .clean).")
    ap.add_argument("--aggressive-python-docstrings", action="store_true",
                    help="Also remove Python docstrings (module/class/function). May break tools relying on __doc__.")
    ap.add_argument("--keep-shebang", action="store_true", default=True, help="Preserve shebang lines (default on).")
    ap.add_argument("--no-keep-shebang", dest="keep_shebang", action="store_false")
    ap.add_argument("--keep-encoding", action="store_true", default=True, help="Preserve Python encoding cookie if present (default on).")
    ap.add_argument("--no-keep-encoding", dest="keep_encoding", action="store_false")
    ap.add_argument("--preserve-linenos", action="store_true",
                    help="Try to preserve original line counts by keeping newlines where comments were.")
    ap.add_argument("--verbose", "-v", action="store_true", help="Verbose logging.")
    args = ap.parse_args()

    root = Path(args.path).resolve()
    outdir = Path(args.outdir).resolve() if args.outdir else None
    if outdir and args.inplace:
        print("[ERROR] --outdir and --inplace are mutually exclusive.", file=sys.stderr)
        sys.exit(2)

    files = list(iter_source_files(root))
    if not files:
        print(f"[WARN] No supported files found under {root}.", file=sys.stderr)
        sys.exit(0)

    for src in files:
        try:
            ext = src.suffix.lower()
            if ext == ".py":
                cleaned, enc = process_python_file(
                    src,
                    aggressive_docstrings=args.aggressive_python_docstrings,
                    keep_shebang=args.keep_shebang,
                    keep_encoding=args.keep_encoding,
                    preserve_linenos=args.preserve_linenos,
                )
                dst = compute_out_path(src, root, outdir, args.inplace, args.suffix)
                if args.verbose:
                    print(f"[PY]  {src} -> {dst} (enc={enc})")
                write_text(dst, cleaned, encoding=enc)
            else:
                raw = read_bytes(src)
                text = safe_text_decode(raw, fallback="utf-8")
                if ext in C_LIKE_EXTS:
                    cleaned = clean_c_like(
                        text,
                        allow_backtick=ext in {".js", ".mjs", ".ts", ".tsx"},
                        is_css=(ext == ".css"),
                        preserve_linenos=args.preserve_linenos,
                    )
                elif ext in HASH_EXTS:
                    cleaned = clean_hash_style(text, preserve_linenos=args.preserve_linenos)
                elif ext in PS1_EXTS:
                    cleaned = clean_powershell(text, preserve_linenos=args.preserve_linenos)
                elif ext in SQL_EXTS:
                    cleaned = clean_sql(text, preserve_linenos=args.preserve_linenos)
                elif ext in HTML_EXTS:
                    cleaned = clean_html(text, preserve_linenos=args.preserve_linenos)
                else:
                    cleaned = text  # no-op fallback

                # Preserve shebang for shell/others if requested
                if args.keep_shebang and raw.startswith(b"#!"):
                    first_line = raw.splitlines(keepends=True)[0].decode("utf-8", errors="ignore")
                    if not cleaned.startswith(first_line):
                        cleaned = first_line.rstrip("\n") + "\n" + cleaned.lstrip("\n")

                dst = compute_out_path(src, root, outdir, args.inplace, args.suffix)
                if args.verbose:
                    print(f"[{ext}] {src} -> {dst}")
                write_text(dst, cleaned, encoding="utf-8")
        except Exception as e:
            print(f"[ERROR] {src}: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()
