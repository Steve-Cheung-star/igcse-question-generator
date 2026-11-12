import os
import re
import json
import uuid
import time
import tempfile
import platform
import subprocess
import shutil
import traceback
from datetime import datetime
import streamlit as st
import streamlit.components.v1 as components
from pdf2image import convert_from_path
from playwright.sync_api import sync_playwright

# ------------------------------------------------------------------
# SYSTEM ENVIRONMENT PATH OVERRIDE FOR MACOS / LINUX COMPILATION
# ------------------------------------------------------------------
current_path = os.environ.get("PATH", "")
homebrew_paths = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
if homebrew_paths not in current_path:
    os.environ["PATH"] = f"{homebrew_paths}:{current_path}"

SYLLABUS_FILE = "syllabus.json"
MASTER_BANK_FILE = "master_bank.json"
USER_DATA_DIR = "./gemini_user_data"

# ==============================================================================
# 0. DEBUG STATUS LOGGING HELPERS & DYNAMIC TAB TITLE NOTIFIER
# ==============================================================================
def log_status(msg: str, level: str = "INFO"):
    """Appends a timestamped log entry to session state for live tracking and debugging."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    entry = f"[{timestamp}] [{level.upper()}] {msg}"
    if "status_logs" not in st.session_state:
        st.session_state.status_logs = []
    st.session_state.status_logs.append(entry)

def set_tab_title(title: str):
    """Dynamically updates the top-level browser tab title bar via JS."""
    components.html(f"<script>window.top.document.title = '{title}';</script>", height=0, width=0)

# ==============================================================================
# 1. TOPIC METADATA & SANITIZATION HELPERS
# ==============================================================================
def get_subtopic_label(s) -> str:
    """Flexibly extracts a clean display label (e.g. '[E1.1] Types of number') from subtopic entries."""
    if isinstance(s, str):
        return s.strip()
    if isinstance(s, dict):
        code = str(s.get("code") or s.get("id") or s.get("number") or "").strip()
        title = str(
            s.get("title") or 
            s.get("content") or 
            s.get("subtopic") or 
            s.get("name") or 
            ""
        ).strip()
        
        if code and title:
            return f"[{code}] {title}"
        elif title:
            return title
        elif code:
            return code
        
        for v in s.values():
            if isinstance(v, str) and v.strip():
                return v.strip()
    return str(s)

def split_markscheme_arrow_steps(steps):
    """Splits markscheme steps containing horizontal arrows (\rightarrow, \Rightarrow, ->, etc.) into vertical lines."""
    if isinstance(steps, str):
        steps = [steps]
    if not isinstance(steps, list):
        return []

    split_steps = []
    arrow_pattern = re.compile(r"\s*(?:\\rightarrow|\\Rightarrow|\\to|->|=>)\s*")

    for step in steps:
        s = str(step).strip()
        if not s:
            continue

        if arrow_pattern.search(s):
            mark_match = re.search(r"\s*(\[[a-zA-Z0-9\s]+\])$", s)
            mark_tag = mark_match.group(1).strip() if mark_match else ""
            if mark_match:
                s = s[:mark_match.start()].strip()

            parts = [p.strip() for p in arrow_pattern.split(s) if p.strip()]
            for i, p in enumerate(parts):
                if i == len(parts) - 1 and mark_tag:
                    split_steps.append(f"{p} {mark_tag}")
                else:
                    split_steps.append(p)
        else:
            split_steps.append(s)

    return split_steps

def convert_markdown_lists_to_latex(text_str: str) -> str:
    """Converts markdown bullet lists (* item, - item) into LaTeX itemize blocks."""
    if not text_str:
        return ""
    lines = str(text_str).split("\n")
    in_list = False
    res = []

    for line in lines:
        stripped = line.strip()
        m = re.match(r"^[\*\-]\s+(.*)", stripped)
        if m:
            item_content = m.group(1)
            if not in_list:
                in_list = True
                res.append(r"\begin{itemize}[leftmargin=0.8cm, itemsep=0.1em, topsep=0.2em]")
            res.append(f"  \\item {item_content}")
        else:
            if in_list:
                in_list = False
                res.append(r"\end{itemize}")
            res.append(line)

    if in_list:
        res.append(r"\end{itemize}")

    return "\n".join(res)

def apply_auto_displaystyle(text_str: str) -> str:
    """Applies \displaystyle only to large math operators (sum, int, etc.), keeping \frac inline to preserve baseline flow."""
    if not text_str:
        return ""
    
    triggers = re.compile(
        r"\\(sum|binom|int|iint|iiint|oint|prod|coprod|lim|limsup|liminf|bigcup|bigcap|bigoplus|bigotimes|bigvee|bigwedge)\b"
    )

    pattern = re.compile(r"\$\$(.*?)\$\$|\$([^\$]+?)\$", re.DOTALL)

    def math_replacer(m):
        if m.group(1) is not None:
            return f"$${m.group(1)}$$"
        
        content = m.group(2)
        stripped = content.strip()
        if not stripped.startswith(r"\displaystyle") and triggers.search(content):
            return f"$\\displaystyle {content}$"
        return f"${content}$"

    return pattern.sub(math_replacer, text_str)

def sanitize_latex_text(text_str: str) -> str:
    if not text_str:
        return ""
    
    s = str(text_str).strip()

    # 1. Unescape JSON escaped newlines (\n -> actual newline) FIRST so lists and line breaks parse properly,
    # but protect LaTeX commands starting with \n (e.g. \neq, \nu, \notin, \noindent, \nabla) using negative lookahead.
    s = re.sub(r"\\n(?![a-zA-Z])", "\n", s)

    # 2. Convert markdown bullet lists (* item, - item) to LaTeX itemize blocks
    s = convert_markdown_lists_to_latex(s)

    # 3. Fix JSON single-backslash line breaks (e.g. \[0.15cm] -> \\[0.15cm])
    s = re.sub(r"(?<!\\)\\\[(\s*[\d\.]+\s*(?:cm|mm|pt|em|ex|in)\])", r"\\\\[\1", s)

    # 4. Clean up line-start indentation: ONLY keep \hspace*{0.8cm} if followed by math ($ or LaTeX math env).
    def clean_indent(m):
        brk = m.group(1) or ""
        rest = m.group(3).strip()
        if rest.startswith("$") or rest.startswith("\\(") or rest.startswith("\\[") or rest.startswith("\\begin"):
            return f"{brk}\\hspace*{{0.8cm}} {rest}"
        if re.search(r"\\frac|\\sqrt|\\binom|[\^\_]", rest):
            return f"{brk}\\hspace*{{0.8cm}} ${rest}$"
        return f"{brk}{rest}"

    s = re.sub(r"(^|\n|\\\\(?:\[[^\]]+\])?)\s*(\\quad|\\hspace\*?\{[^}]+\})\s*(.*)", clean_indent, s)

    # 5. Escape unescaped percent signs globally FIRST
    s = re.sub(r"(?<!\\)%", r"\\%", s)

    # 6. Apply auto displaystyle formatting to large math operators
    s = apply_auto_displaystyle(s)

    combined_pattern = re.compile(
        r"(\$\$.*?\$\$|\$[^\$]+?\$|\\\[.*?\\\]|\\\([^\)]+?\\\)|\\begin\{(?:tabular|array|align|matrix|tikzpicture|bmatrix|pmatrix|vmatrix|itemize)\}.*?\\end\{(?:tabular|array|align|matrix|tikzpicture|bmatrix|pmatrix|vmatrix|itemize)\})",
        re.DOTALL
    )
    
    protected_blocks = []
    def mask_protected(m):
        protected_blocks.append(m.group(1))
        return f"PROTECTEDBLOCK{len(protected_blocks)-1}END"
    
    masked_text = combined_pattern.sub(mask_protected, s)
    
    # 7. Auto-wrap bare math constructs (\frac, \sqrt, etc.) outside math mode
    def wrap_unwrapped_math_cmds(text):
        pattern = re.compile(r"\\(frac|dfrac|tfrac|sqrt|vec|overline)\b.*?(?=\s*(?:$|\n|\\\\|\[\d+\]))", re.DOTALL)
        def repl(m):
            val = m.group(0).strip()
            if not val.startswith("$"):
                return f"${val}$"
            return val
        return pattern.sub(repl, text)

    masked_text = wrap_unwrapped_math_cmds(masked_text)

    # 8. Wrap bare subscripts outside math mode
    bare_subscript_pattern = re.compile(r"\b([A-Za-z]+)_(\{?[a-zA-Z0-9\+\-\=]+\}?)\b")
    def wrap_subscript(m):
        var = m.group(1)
        sub = m.group(2)
        if not sub.startswith("{"):
            sub = f"{{{sub}}}"
        return f"${var}_{sub}$"

    masked_text = bare_subscript_pattern.sub(wrap_subscript, masked_text)

    masked_text = combined_pattern.sub(mask_protected, masked_text)
    
    masked_text = re.sub(r"(?<!\\)&", r"\\&", masked_text)
    masked_text = re.sub(r"(?<!\\)_", r"\\_", masked_text)
    masked_text = re.sub(r"(?<!\\)#", r"\\#", masked_text)
    
    for i, content in enumerate(protected_blocks):
        masked_text = masked_text.replace(f"PROTECTEDBLOCK{i}END", content)
        
    dollars = re.findall(r"(?<!\\)\$", masked_text)
    if len(dollars) % 2 != 0:
        masked_text += "$"

    return masked_text

def format_markscheme_step_latex(step_str: str) -> str:
    s = sanitize_latex_text(step_str)
    if not s:
        return ""

    mark_match = re.search(r"\s*\[([a-zA-Z0-9\s]+)\]$", s)
    mark_tag = ""
    if mark_match:
        tag_content = mark_match.group(1).strip()
        mark_tag = f" \\hfill \\textbf{{[{tag_content}]}}"
        s = s[:mark_match.start()].strip()

    part_match = re.match(r"^(\([a-h]\)|\([i-v]+\))\s*(.*)", s, re.IGNORECASE)
    if part_match:
        part_hdr = part_match.group(1)
        rest_text = part_match.group(2)
        return f"\\textbf{{{part_hdr}}} {rest_text}{mark_tag}"

    return f"{s}{mark_tag}"

def sanitize_intro_text(text: str) -> str:
    if not text:
        return ""
    s = str(text).strip()
    patterns = [
        r"^in\s+this\s+question,\s+we\s+(will\s+)?(explore|investigate|consider|examine)\s+.*?\.\s*",
        r"^consider\s+the\s+following\s+(mathematical\s+)?(problem|scenario|question):\s*",
        r"^this\s+question\s+(is\s+about|assesses|explores)\s+.*?\.\s*"
    ]
    for pat in patterns:
        s = re.sub(pat, "", s, flags=re.IGNORECASE).strip()
    return s

def format_prompt_for_preview(prompt_text: str) -> str:
    """Converts LaTeX newline/indentation constructs to Markdown for clean Streamlit rendering."""
    if not prompt_text:
        return ""
    s = sanitize_latex_text(prompt_text)
    s = re.sub(r"\\\\+\s*\[\s*[\d\.]+cm\s*\]", "<br/>", s)
    s = re.sub(r"\\\\+", "<br/>", s)
    s = re.sub(r"\\quad", "&nbsp;&nbsp;&nbsp;&nbsp;", s)
    s = re.sub(r"\\qquad", "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;", s)
    s = re.sub(r"\\hspace\*?\{[^}]+\}", "&nbsp;&nbsp;&nbsp;&nbsp;", s)
    return s

def sanitize_slot_label(label_str: str) -> str:
    """Sanitizes LaTeX and plain text labels: wraps in $...$ if LaTeX/math, leaves plain words as text. Avoids \\text{} entirely."""
    if not label_str:
        return ""
    s = sanitize_latex_text(label_str)

    if s.startswith("$") and s.endswith("$") and s.count("$") == 2:
        return s

    is_single_var_assign = bool(re.match(r"^\s*([a-zA-Z]|\\[a-zA-Z]+)(\s*=)?\s*$", s))
    has_math_cmds = bool(re.search(r"[\^_\\]|cm\^|m/s|°", s))

    if is_single_var_assign or has_math_cmds:
        clean_inner = s.replace("$", "")
        return f"${clean_inner}$"

    return s

def render_answer_slot_preview(ans_type, prefix="", suffix="", format_str="", marks=0, vspace_cm=3.0, prompt_text=""):
    """Displays answer line / slot UI preview with dynamic lengths, tabular multiline formatting, time unit spacing, and automatic 'no line' detection."""
    prefix_clean = sanitize_slot_label(prefix)
    suffix_clean = sanitize_slot_label(suffix)
    prefix_str = f"{prefix_clean} " if prefix_clean else ""
    suffix_str = f" {suffix_clean}" if suffix_clean else ""
    marks_tag = f" **[{marks}]**" if marks > 0 else ""
    space_label = f" *(📐 Working space: {vspace_cm:.1f} cm)*" if vspace_cm > 0 else ""

    ans_t = str(ans_type).lower().strip()
    prompt_lower = str(prompt_text).lower()

    no_answer_line_keywords = [
        "show that", "sketch", "complete the table", "complete the diagram", 
        "shade", "prove that", "correct symbol", "insert symbol", 
        "write down the correct symbol", "insert the correct symbol", 
        "fill in the symbol", "insert one of the symbols", "symbol from the list"
    ]
    is_symbol_prompt = (
        ("symbol" in prompt_lower or "notation" in prompt_lower or "inequality" in prompt_lower) 
        and any(w in prompt_lower for w in ["write down", "insert", "complete", "fill", "choose", "place"])
    )
    is_no_line_prompt = any(kw in prompt_lower for kw in no_answer_line_keywords) or is_symbol_prompt
    is_describe_fully = "describe fully" in prompt_lower

    # Standardized lengthened dot placeholders
    LINE_SHORT = "..................................."                          # 35 dots
    LINE_MEDIUM = ".........................................................................................." # 90 dots
    LINE_LONG = "........................................................................................................................" # 120 dots

    if is_describe_fully:
        st.markdown(f"**Answer Lines (Describe fully...):**\n`{LINE_LONG}`\n`{LINE_LONG}`\n`{LINE_LONG}` {space_label} \\hfill {marks_tag}")
    elif ans_t in ["null", "none"] or is_no_line_prompt:
        st.caption(f"*(No answer line — Student works/draws/fills directly on prompt/diagram/paper/table)* {space_label} {marks_tag}")
    elif ans_t in ["compound", "time"] or ("hour" in f"{prefix_clean} {suffix_clean} {format_str}".lower() and "minute" in f"{prefix_clean} {suffix_clean} {format_str}".lower()):
        st.markdown(f"**Answer Line (Time/Compound):** `{LINE_SHORT}` hours `{LINE_SHORT}` minutes {space_label} \\hfill {marks_tag}")
    elif ans_t in ["custom", "inequality_region", "region", "multi_line", "multiple", "asymptotes", "two_lines"]:
        if format_str and ("\\n" in format_str or "\n" in format_str):
            formatted_lines = format_str.replace("\\n", "\n")
            st.markdown(f"**Answer Lines (Tabular / Multi-Row):**\n{formatted_lines} {space_label} \\hfill {marks_tag}")
        elif format_str and "," in format_str:
            parts = [p.strip() for p in format_str.split(",") if p.strip()]
            stacked_lines = "\n".join([f"- Line {i+1}: `{p}`" for i, p in enumerate(parts)])
            st.markdown(f"**Answer Lines (Tabular / Multi-Row):**\n{stacked_lines} {space_label} \\hfill {marks_tag}")
        elif format_str:
            st.markdown(f"**Answer Slot (Custom Tabular):** `{format_str}` {space_label} \\hfill {marks_tag}")
        else:
            st.markdown(f"**Answer Lines (Tabular / Multi-Row):**\n- Line 1: {prefix_str}`{LINE_MEDIUM}` {suffix_str}\n- Line 2: {prefix_str}`{LINE_MEDIUM}` {suffix_str}\n- Line 3: {prefix_str}`{LINE_MEDIUM}` {suffix_str} {space_label} \\hfill {marks_tag}")
    elif ans_t == "vector":
        st.markdown(f"**Answer Slot (Column Vector):** {prefix_str} $\\begin{{pmatrix}} \\hspace{{1.5cm}} \\\\[2em] \\hspace{{1.5cm}} \\end{{pmatrix}}$ {suffix_str} {space_label} \\hfill {marks_tag}")
    elif ans_t == "coordinate":
        st.markdown(f"**Answer Slot (Coordinate Pair):** {prefix_str} $( \\dots , \\dots )$ {suffix_str} {space_label} \\hfill {marks_tag}")
    elif ans_t in ["short", "short_line", "single_value"]:
        st.markdown(f"**Answer Line (Short):** {prefix_str} `{LINE_SHORT}` {suffix_str} {space_label} \\hfill {marks_tag}")
    elif ans_t in ["long", "long_line", "ordering", "list"]:
        st.markdown(f"**Answer Line (Long):** {prefix_str} `{LINE_LONG}` {suffix_str} {space_label} \\hfill {marks_tag}")
    else:
        combined = f"{prefix_clean} {suffix_clean} {format_str}"
        if re.search(r"hour.*min", combined, re.IGNORECASE):
            st.markdown(f"**Answer Line (Time):** `{LINE_SHORT}` hours `{LINE_SHORT}` minutes {space_label} \\hfill {marks_tag}")
        elif "minute" in suffix_clean.lower() and (".." in suffix_clean or "." in suffix_clean):
            suffix_clean_expanded = re.sub(r"\.\.\s*", f"{LINE_SHORT} ", suffix_clean)
            st.markdown(f"**Answer Line:** {prefix_str} `{LINE_SHORT}` {suffix_clean_expanded} {space_label} \\hfill {marks_tag}")
        else:
            st.markdown(f"**Answer Line:** {prefix_str} `{LINE_MEDIUM}` {suffix_str} {space_label} \\hfill {marks_tag}")

# ==============================================================================
# 2. TIKZ SINGLE DIAGRAM PDFLATEX COMPILER
# ==============================================================================
def render_tikz_pdflatex(tikz_code: str, caption: str = "Diagram"):
    if not tikz_code or not str(tikz_code).strip():
        return

    code = str(tikz_code).strip()
    if "\\begin{tikzpicture}" not in code:
        code = "\\begin{tikzpicture}\n" + code + "\n\\end{tikzpicture}"

    full_tex = "\\documentclass[border=2pt]{standalone}\n\\usepackage{tikz}\n\\usetikzlibrary{arrows.meta, angles, quotes, calc, positioning, patterns}\n\\usepackage{amsmath,amssymb,amsfonts}\n\\begin{document}\n" + code + "\n\\end{document}\n"

    with tempfile.TemporaryDirectory() as tmpdir:
        tex_path = os.path.join(tmpdir, "diagram.tex")
        pdf_path = os.path.join(tmpdir, "diagram.pdf")

        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(full_tex)

        try:
            result = subprocess.run(
                ["pdflatex", "-interaction=nonstopmode", "-output-directory", tmpdir, tex_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=15
            )

            if os.path.exists(pdf_path):
                images = convert_from_path(pdf_path, dpi=200)
                if images:
                    st.image(images[0], caption=caption, use_container_width=True)
            else:
                st.warning("⚠️ `pdflatex` was unable to compile the TikZ code.")
                st.code(result.stdout.decode("utf-8", errors="ignore"))

        except FileNotFoundError:
            st.error("❌ `pdflatex` command not found. Ensure TeX Live or MacTeX is installed.")
        except Exception as e:
            st.error(f"❌ Error compiling TikZ: {e}")

# ==============================================================================
# 3. FILE I/O HELPERS & DEDUPLICATION FINGERPRINTING
# ==============================================================================
def load_syllabus():
    if not os.path.exists(SYLLABUS_FILE):
        st.error(f"Syllabus file `{SYLLABUS_FILE}` not found in project directory.")
        return []
    try:
        with open(SYLLABUS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        if isinstance(data, dict):
            if "topics" in data and isinstance(data["topics"], list):
                data = data["topics"]
            elif "syllabus" in data and isinstance(data["syllabus"], list):
                data = data["syllabus"]

        normalized = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    topic_name = item.get("topic_name") or item.get("topic") or "Unknown Topic"
                    subtopics = item.get("subtopics") or []
                    normalized.append({
                        "topic": topic_name,
                        "subtopics": subtopics
                    })
            return normalized
        elif isinstance(data, dict):
            for topic_name, topic_val in data.items():
                if isinstance(topic_val, dict):
                    item = {"topic": topic_name}
                    item.update(topic_val)
                    normalized.append(item)
                elif isinstance(topic_val, list):
                    normalized.append({"topic": topic_name, "subtopics": topic_val})
            return normalized
        return []
    except Exception as e:
        st.error(f"Error parsing `{SYLLABUS_FILE}`: {e}")
        return []

def load_master_bank():
    if not os.path.exists(MASTER_BANK_FILE):
        with open(MASTER_BANK_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, indent=2)
        return []
    try:
        with open(MASTER_BANK_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def get_question_fingerprint(item: dict) -> str:
    if not isinstance(item, dict):
        return ""
    
    text_pieces = [
        str(item.get("subtopic", "")).strip().lower(),
        str(item.get("intro_text", "")).strip().lower()
    ]
    
    for part in item.get("parts", []):
        if isinstance(part, dict):
            text_pieces.append(str(part.get("prompt", "")).strip().lower())
            for subpart in part.get("subparts", []):
                if isinstance(subpart, dict):
                    text_pieces.append(str(subpart.get("prompt", "")).strip().lower())
                    
    return "||".join([p for p in text_pieces if p])

def save_master_bank(data):
    seen_fingerprints = set()
    unique_data = []

    for item in data:
        if not isinstance(item, dict):
            continue

        fingerprint = get_question_fingerprint(item)
        if not fingerprint:
            fingerprint = str(item.get("id", ""))

        if fingerprint in seen_fingerprints:
            continue

        seen_fingerprints.add(fingerprint)
        unique_data.append(item)

    with open(MASTER_BANK_FILE, "w", encoding="utf-8") as f:
        json.dump(unique_data, f, indent=2)
        
    return len(unique_data)

def clean_question_structure(item: dict) -> dict:
    """Flattens single-part questions (stripping redundant '(a)' labels when there are no subparts) 
    and normalizes part labels."""
    if not isinstance(item, dict):
        return item

    parts = item.get("parts", [])
    if isinstance(parts, list) and len(parts) == 1:
        single_part = parts[0]
        if isinstance(single_part, dict):
            subparts = single_part.get("subparts", [])
            if not subparts:
                label = str(single_part.get("label", "")).strip().lower()
                if label in ["(a)", "a)", "a", "(a) "]:
                    single_part["label"] = ""
    return item

def assign_unique_ids(items):
    if isinstance(items, dict):
        items = [items]
    
    processed = []
    for item in items:
        if isinstance(item, dict):
            item["id"] = item.get("id") or str(uuid.uuid4())[:8]
            item["intro_text"] = sanitize_intro_text(item.get("intro_text", ""))
            item = clean_question_structure(item)
            processed.append(item)
    return processed

def clean_and_repair_json(raw_text):
    text = raw_text.strip()
    
    # Removed numbers like "1095" that caused false positive generation error triggers on valid math text
    error_keywords = [
        "encountered an error", "something went error", "something went wrong", 
        "try something else", "please try again", "try again later",
        "i'm unable to", "i can't help with", "an error occurred",
        "failed to generate", "service unavailable"
    ]
    
    lowered = text.lower()
    for kw in error_keywords:
        if kw in lowered:
            if not (text.startswith("[") and text.endswith("]")) and "```json" not in text:
                raise ValueError(f"Gemini Web UI returned an internal generation error: '{kw}'")

    if "```" in text:
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
        text = text.strip()

    bracket_starts = [i for i in [text.find("["), text.find("{")] if i != -1]
    bracket_ends = [text.rfind("]"), text.rfind("}")]
    
    if bracket_starts and max(bracket_ends) != -1:
        first_bracket = min(bracket_starts)
        last_bracket = max(bracket_ends)
        if last_bracket > first_bracket:
            text = text[first_bracket:last_bracket + 1]

    text = re.sub(r',\s*([\]\}])', r'\1', text)
    text = re.sub(r'("|\d+|true|false|null|\]|\})\s*\n\s*("|\{|\[)', r'\1,\n\2', text)

    res = []
    i = 0
    n = len(text)
    in_string = False
    
    while i < n:
        c = text[i]
        if c == '"':
            num_backslashes = 0
            k = i - 1
            while k >= 0 and text[k] == '\\':
                num_backslashes += 1
                k -= 1
            if num_backslashes % 2 == 0:
                in_string = not in_string
            res.append(c)
            i += 1
        elif c == '\n' and in_string:
            res.append('\\n')
            i += 1
        elif c == '\r' and in_string:
            res.append('\\r')
            i += 1
        elif c == '\t' and in_string:
            res.append('\\t')
            i += 1
        elif c == '\\' and in_string:
            if i + 1 < n:
                next_char = text[i+1]
                if next_char == '\\':
                    res.append('\\\\')
                    i += 2
                    continue
                elif next_char in '"/':
                    res.append('\\')
                    i += 1
                else:
                    res.append('\\\\')
                    i += 1
            else:
                res.append('\\\\')
                i += 1
        else:
            res.append(c)
            i += 1

    return "".join(res)

# ==============================================================================
# 4. IGCSE PROMPT BUILDER WITH VERTICAL MARKSCHEMES & COMMAND WORDS
# ==============================================================================
def build_igcse_prompt(
    primary_topic, 
    primary_sub_obj, 
    easy_count, 
    med_count, 
    hard_count, 
    calculator_mode="Paper 4 (Calculator)",
    tier="Extended",
    include_tikz=True
):
    total_q = easy_count + med_count + hard_count

    calc_instr = (
        "CALCULATOR POLICY (PAPER 2 - NON-CALCULATOR):\nQuestions must be solvable without a calculator. Use exact values (fractions, integers, pi, surds).\nSet 'paper_type' to 'Paper 2 (Non-Calculator)'."
        if calculator_mode == "Paper 2 (Non-Calculator)" else
        "CALCULATOR POLICY (PAPER 4 - CALCULATOR REQUIRED):\nRequires or allows calculator steps (degree rounding to 1 dp, numbers to 3 sf).\nSet 'paper_type' to 'Paper 4 (Calculator)'."
        if calculator_mode == "Paper 4 (Calculator)" else
        "CALCULATOR POLICY (MIXED):\nAssign 'paper_type' as either 'Paper 2 (Non-Calculator)' or 'Paper 4 (Calculator)' per question."
    )

    if isinstance(primary_sub_obj, dict):
        sub_code = str(primary_sub_obj.get("code") or "")
        sub_title = str(primary_sub_obj.get("title") or primary_sub_obj.get("content") or "")
        
        objs = primary_sub_obj.get("objectives", [])
        if isinstance(objs, list):
            obj_str = "\n".join([f"  * {o}" for o in objs if isinstance(o, str)])
        else:
            obj_str = f"  * {objs}"
            
        notes_dict = primary_sub_obj.get("notes_and_examples", {})
        if isinstance(notes_dict, dict):
            notes_text = notes_dict.get("text", "")
        else:
            notes_text = str(notes_dict)
            
        guidance_combined = f"Syllabus Objectives:\n{obj_str}\nNotes/Examples: {notes_text}".strip()
    else:
        sub_code = ""
        sub_title = str(primary_sub_obj)
        guidance_combined = "Standard IGCSE specification"

    tier_instr = (
        "IGCSE TIER REQUIREMENT: EXTENDED\n"
        "- Focus on multi-step reasoning, algebraic manipulation, structured multi-part problems, non-routine geometry/trigonometry, functions, or vectors."
    )

    tikz_instr = ""
    if include_tikz:
        tikz_instr = """
TIKZ & DIAGRAM PLACEMENT & MULTIPLE DIAGRAM RULES:
- Generate clean, accurate LaTeX TikZ diagrams for figures (e.g. geometric shapes, Venn diagrams, statistical graphs, function sketches, frequency curves, tables, tree diagrams).
- STRICT GREYSCALE ONLY (NO COLOURS):
- DO NOT use colours under any circumstances (e.g., no `blue`, `red`, `green`, `orange`, `cyan`).
- Use ONLY black, white, and shades of grey (e.g., `black`, `white`, `gray`, `gray!70`, `black!12`, `black!25`, `black!50`).

- FUNCTION LINE & CURVE CLIPPING RULE (GRID BOUNDARY ONLY):
- ONLY plotted function curves and function lines (e.g., `\\draw[samples=...] plot (...)`, linear lines, parabolas, hyperbolas, etc.) MUST be clipped so they do not extend beyond the grid boundaries (`(x_min,y_min)` to `(x_max,y_max)`).
- Wrap ONLY the function drawing commands inside a local scope with a bounding clip:
  \\begin{scope}
    \\clip (x_min,y_min) rectangle (x_max,y_max);
    \\draw[samples=100, ...] plot (\\x, {...});
  \\end{scope}

- CIRCLE THEOREMS & ANGLES DIAGRAM RULES:
- When generating circle theorem or angle-finding geometry figures, ensure accurate geometric representations of relevant properties (e.g., angle at centre, angles in same segment, cyclic quadrilaterals, alternate segment theorem, tangent-radius angles, parallel-line angles).
- DYNAMIC ORIENTATION VARIATION (CIRCLE THEOREMS & ANGLES ONLY): Apply dynamic orientation variation ONLY to circle theorems and angles questions—do NOT apply it to any other topic or diagram type (such as coordinate graphs, general polygons, mensuration, trigonometry, or transformations). For circle theorems and angles questions, avoid defaulting to standard horizontal baselines or upright orientations; dynamically vary and rotate shapes, chords, tangents, radii, and vertex placements by non-standard angles (e.g., 25°, 70°, 115°, 210°).
- Clearly label circle center ($O$), vertices ($A, B, C, D$), tangent contact points ($T, P$), right-angle indicators, and parallel mark arrows where applicable.

- INEQUALITY REGION SHADING RULE (DRAW SHADING FIRST BEFORE AXES & GRID):
- ALWAYS draw all grey area fill/shading FIRST at the top of the TikZ code before drawing grid lines, coordinate axes, tick marks, boundary lines, or text labels.
- Structure TikZ code in this strict layer order:
    1) Background grey fill/shading FIRST: `\\fill[black!12] (x_min,y_min) rectangle (x_max,y_max);` (use soft light grey `black!12` or `gray!20`).
    2) Target allowed region $R$ in pure white SECOND: `\\fill[white] (x1,y1) -- (x2,y2) -- (x3,y3) -- cycle;`
    3) Grid lines THIRD: `\\draw[step=1, gray!70, thin] (x_min,y_min) grid (x_max,y_max);` so grid lines remain sharp, crisp, and prominent over shaded areas.
    4) Coordinate axes ($x$ and $y$), axis arrowheads, and tick marks FOURTH.
    5) Boundary lines and region labels ($R$) LAST.

- TREE DIAGRAM SPECIFIC TIKZ GUIDELINES:
- Set `grow=right` to orient the tree horizontally from left to right with upright, non-sloped text.
- Expand spacing significantly (`level 1/.style={level distance=5.0cm, sibling distance=6.0cm}`, `level 2/.style={level distance=5.0cm, sibling distance=3.0cm}`) to provide macro-level headroom between upper and lower forks.
- Use `\\vphantom{$\\dfrac{0}{0}$}` inside edge label nodes to automatically reserve the exact vertical height needed for a display fraction without manual `yshift` tweaking.
- Always place branch labels `above` the line (`node[above, pos=0.5] {\\vphantom{$\\dfrac{0}{0}$}\\makebox[1.2cm]{\\dotfill}}`), allowing the dotted line to sit as a baseline with dynamic height reserved directly above it.
- Place stage headers ("First disc", "Second disc") using a dedicated top baseline coordinate or high anchor (e.g., `yshift=6.0cm` above the root node) so they never collide with fraction slots.

- INTRODUCTORY SCENARIO DIAGRAMS ('intro_diagram_tikz'):
- If a diagram accompanies or sets up the overall introductory text/paragraph before part (a) (e.g., given geometric figures, initial shape context, setup Venn diagram, given coordinate grid, or given table context), it MUST be placed directly inside 'intro_diagram_tikz' at the root question level.
- DO NOT push introductory setup diagrams down into part (a)'s 'diagram_tikz'.

- PART-SPECIFIC & COMPLETION DIAGRAMS ('diagram_tikz' inside parts/subparts):
- If a specific question part or subpart requires student interaction or completion (e.g., "Complete the table", "On the grid, draw the image of...", "Shade the region...", "Complete the Venn diagram", "Sketch the graph...", "Complete the tree diagram"), provide a separate TikZ diagram or table template inside 'diagram_tikz' under that specific part or subpart.
- SEQUENCE ORDER IN PARTS: Always place the question prompt/stem text FIRST, followed by 'diagram_tikz' (for completion diagrams), followed by the answer line / working space.
"""

    prompt = f"""Act as a Chief Senior Examiner for Cambridge IGCSE International Mathematics (0607 / Extended).

Generate exactly {total_q} exam-style IGCSE practice question(s) distributed by difficulty:
- Easy: {easy_count} | Medium: {med_count} | Hard: {hard_count}

Primary Topic & Subtopic Details:
- Topic: {primary_topic}
- Subtopic Code: {sub_code}
- Subtopic Title: {sub_title}
- Detailed Syllabus Specifics:
{guidance_combined}

{tier_instr}
{calc_instr}
{tikz_instr}

QUESTION STRUCTURE & SUBPARTS HIERARCHY RULES:
1. STANDALONE QUESTIONS: If a question is a single self-contained task without subparts (e.g., a single 2-mark factorisation or 3-mark equation), set `parts: [{{"label": "", "prompt": "...", "subparts": []}}]`.
2. NESTED SUBPARTS HIERARCHY: When a main part (e.g. part "(a)") contains sub-questions (e.g. "(i)", "(ii)"), YOU MUST populate the `subparts` array inside that part!
- Main Part `(a)`: Set `label: "(a)"`. Place shared heading/context (e.g., "Factorise completely:") or overall stem prompt in `prompt` or `context`. Set `subparts` to an array containing each subpart object.
- Subpart Objects inside `subparts`: Each subpart MUST be an individual object containing its own `label` (e.g. `"(i)"`, `"(ii)"`), `prompt`, `marks`, `vspace_cm`, `answer_type`, `answer_prefix`, `answer_suffix`, `answer_format`, and `markscheme`.
- DELEGATED METRICS: When `subparts` is non-empty, set `marks: 0`, `vspace_cm: 0.0`, and `markscheme: []` on the parent part. The individual marks, working space, and markscheme steps MUST be defined on each subpart object.
- DO NOT list subparts like `(i)` and `(ii)` as flat items in the root `parts` array. They MUST be nested inside the parent part's `subparts` array.

OFFICIAL CAMBRIDGE IGCSE COMMAND WORDS DICTIONARY:
- Calculate: work out from given facts, figures or information.
- Comment: give an informed opinion.
- Compare: identify/comment on similarities and/or differences.
- Determine: establish with certainty.
- Describe: state the points of a topic / give characteristics and main features.
- Explain: set out purposes or reasons / make the relationships between things clear / say why and/or how.
- Give: produce an answer from a given source or recall/memory.
- Plot: mark point(s) on a graph.
- Revise: change to reflect further given information.
- Show (that): provide structured evidence that leads to a given result.
- Sketch: make a simple freehand drawing showing key features.
- State: express in clear terms.
- Work out: calculate from given facts, figures or information.
- Write: give an answer in a specific form.
- Write down: give an answer without significant working.

STRICT MATH & PREFIX FORMATTING RULES:
1. EVERY mathematical expression, fraction (`\\dfrac` or `\\frac`), root (`\\sqrt`), exponent (`^`), or variable MUST be enclosed in inline math mode (`$...$`).
- FRACTION RULE: ALWAYS use `\\dfrac` instead of `\\frac` for standard fractions (e.g., `$\\dfrac{{a}}{{b}}$`). However, for fractional exponents/powers, ALWAYS use `\\frac` instead of `\\dfrac` (e.g., `$x^{{\\frac{{1}}{{2}}}}$, `$8^{{\\frac{{2}}{{3}}}}$`).
- UNIVERSAL SET SYMBOL: ALWAYS use `$\\mathsf{{U}}$` for the universal set in set notation and Venn diagrams (e.g., `$\\mathsf{{U}} = \\{{x : 1 \\le x \\le 20\\}}$`).
- Correct: "Work out the value of \\\\[0.15cm] \\hspace*{{0.8cm}} $\\dfrac{{\\sqrt[3]{{64}} + 12^2}}{{5^2}}$"
- DO NOT USE `\\text{{}}` inside math mode or LaTeX expressions under any circumstances. Avoid `\\text{{}}` entirely.

2. ANSWER PREFIX & SUFFIX FORMATTING:
- If `answer_prefix` or `answer_suffix` is in LaTeX or contains mathematical expressions, variables, symbols, or math units (e.g., `$x =$`, `$k =$`, `$cm^2$`, `$cm^3$`, `$m/s$`, `$^\\circ$`), specifically enclose the LaTeX/math part in inline math mode (`$ ... $`). DO NOT use `\\text{{}}` inside math mode or LaTeX expressions.
- Plain text word descriptions without math symbols (e.g. `answer_prefix: "Area ="`, `answer_prefix: "Volume ="`, `answer_suffix: "hours"`, `answer_suffix: "minutes"`) must remain plain text without `$`.

3. DATA & CONTEXT LAYOUT RULES:
- RAW DATA / NUMBER LISTS (CENTERED): Place raw data sets or lists of numbers on a NEW LINE below the prompt text, CENTERED horizontally across the page, separated by `\\qquad` between elements inside math mode (commas can be dropped): `\\\\[0.15cm] \\begin{{center}} $3 \\qquad 7 \\qquad 12 \\qquad 15 \\qquad 22$ \\end{{center}}`. Do NOT format lists of numbers using bullet points.
- FUNCTION DEFINITIONS: Present multiple functions inline on the same line separated by commas (e.g., "$f(x) = 2x + 3$, $g(x) = x^2 - 1$, $h(x) = \\dfrac{{1}}{{x}}$").
- STRUCTURED SET CONDITIONS & VENN DIAGRAM INFORMATION: For multi-statement conditions or set definitions where separating items by line improves readability (e.g., set element descriptions, Venn diagram region counts, or multi-line system conditions), format each condition on a clean separate line or indented list (`\\n- ...`).

4. SHAPE TRANSFORMATION ANSWER LINES ("Describe fully..."):
- For questions asking to "Describe fully...", keep `answer_prefix` strictly as plain text (or empty string `""` if no text label is required).
- DO NOT place LaTeX dotted lines (`\\dotfill`) inside `answer_prefix`.
- Output the 3 full-width left-aligned dotted lines exclusively in the dedicated `answer_format` JSON key:
        \\noindent \\dotfill \\\\
        \\noindent \\dotfill \\\\
        \\noindent \\dotfill

5. COLUMN VECTOR ANSWER FORMATTING:
- For column vectors (2x1), format answer space using a pmatrix with custom vertical padding and empty space inside components: `\\begin{{pmatrix}} \\hspace{{1.5cm}} \\\\[2em] \\hspace{{1.5cm}} \\end{{pmatrix}}`. DO NOT put dotted lines inside vector components.

ONE-LINER COMMAND WORD FORMATTING RULE (Expand, Factorise, Solve, Simplify, Express):
- For single-line algebraic or arithmetic tasks (e.g. Expand and simplify, Factorise fully, Solve the equation, Simplify), place ONLY the mathematical expression/equation on a NEW LINE below the command word and INDENT IT: `\\\\[0.15cm] \\hspace*{{0.8cm}} $...$`.
- STRICT INDENTATION RULE: ONLY indent standalone mathematical expressions, equations, or formulas (e.g. `$6x^2 - 7x - 20$`, `$3x + 4 = 19$`).
- DO NOT INDENT text sentences, word problems, explanations, or multi-word command phrases (e.g. "Show that...", "Explain why...", "Calculate the area of..."). Text sentences MUST ALWAYS start flush left without `\\hspace*` or `\\quad`.

ANSWER TYPE & LINE LENGTH SELECTION GUIDELINES:
Assign 'answer_type' appropriately based on the required response:
- "null": MUST be used whenever the student writes/draws directly on paper/diagram/table, for proof/multi-step show tasks, OR for questions where answer slots/lines are provided directly inside the prompt text itself (e.g. writing down or inserting the correct symbol for set notations like $A \\dots B$, completing inequality symbols $<, >, =, \\le, \\ge$ between expressions, filling symbols in set theory/logic, etc. - no separate final answer line is needed).
- "multi_field": MUST be used for questions requiring 2 or more inline answer slots on the same line (e.g. "Gradient = ....., $y$-intercept = ....." or "$a = ....., b = ....., c = .....$"). Specify all field labels as a comma-separated list in 'answer_format' (e.g., "answer_format": "Gradient =, $y$-intercept =" OR "answer_format": "$a =$, $b =$, $c =$").
- "inequality_region" / "multi_line": MUST be used when asking for multiple inequalities or multi-line tabular responses (e.g., "Write down the three inequalities that define region R").
- ALWAYS structure multiline answers in stacked multi-row / tabular format (stacked lines on separate rows).
- DO NOT use single-line comma-separated (`....., ....., .....`) formats.
- Set `answer_type: "inequality_region"` or `"multi_line"` with empty `answer_format` or multi-line newline (`\\n`) separated row templates.
- "short_line": For single numerical values, integers, simple fractions, degree measurements, or single variable answers.
- "line": Standard single-line answer format.
- "long_line": For putting numbers/fractions in order, expanded algebraic expressions, or long mathematical sentences.
- "compound" / "time": For multi-unit answers like time (expressing hours in hours and minutes).
- "coordinate": For coordinate pairs (x, y).
- "vector": For column vectors (2x1), using `\\begin{{pmatrix}} \\hspace{{1.5cm}} \\\\[2em] \\hspace{{1.5cm}} \\end{{pmatrix}}` without dotted lines inside components.

MARKSCHEME VERTICAL STEP-BY-STEP FORMATTING RULE:
- Each calculation/method step in the markscheme MUST be given as a separate element in the `markscheme` array vertically.
- DO NOT concatenate steps horizontally with `\\rightarrow`, `\\Rightarrow`, `\\to`, or `->`.

WORKING SPACE ESTIMATION RULE (`vspace_cm`):
For every part and subpart, estimate the vertical working space required in centimeters (`vspace_cm` as float):
- Calculation/Algebraic working questions: ~1.5cm to 2.0cm per mark or markscheme step.
- Simple 1-mark state/write down: ~1.0cm to 1.5cm.
- Null/Diagram/Show questions: 0.0cm to 3.0cm working space (without an answer line).

JSON Schema:
```json
[
  {{
    "id": "<generate_unique_8_char_hex_id>",
    "topic": "{primary_topic}",
    "subtopic": "{sub_code}",
    "tier": "Extended",
    "paper_type": "Paper 2 (Non-Calculator)|Paper 4 (Calculator)",
    "difficulty": "Easy|Medium|Hard",
    "total_marks": 7,
    "intro_text": "Consider the line $L$ given in the coordinate system.",
    "intro_diagram_tikz": "",
    "parts": [
      {{
        "label": "(a)",
        "context": "",
        "diagram_tikz": "",
        "prompt": "Find the gradient and the $y$-intercept of line $L$.",
        "marks": 3,
        "vspace_cm": 3.0,
        "answer_type": "multi_field",
        "answer_prefix": "",
        "answer_suffix": "",
        "answer_format": "Gradient =, $y$-intercept =",
        "markscheme": [
          "Gradient = $2$ [B2]",
          "$y$-intercept = $-5$ [B1]"
        ],
        "subparts": []
      }}
    ]
  }}
]
```"""
    return prompt

# ==============================================================================
# 5. PLAYWRIGHT AUTOMATION ENGINE
# ==============================================================================
MODEL_KEYWORD_MAP = {
    "3.6 Flash": ["3.6 Flash", "3.6-flash", "flash 3.6", "flash"],
    "3.6 Thinking": ["3.6 Thinking", "3.6-thinking", "thinking 3.6", "thinking"],
    "3.1 Pro": ["3.1 Pro", "3.1-pro", "pro 3.1", "pro"]
}

def normalize_model_str(s: str) -> str:
    return re.sub(r'[\s\-_]+', '', str(s).lower())

def is_model_matching(target_model: str, current_ui_text: str) -> str:
    if not current_ui_text or current_ui_text.lower() in ["unknown", ""]:
        return True

    target_kws = MODEL_KEYWORD_MAP.get(target_model, [target_model])
    norm_ui = normalize_model_str(current_ui_text)

    for kw in target_kws:
        norm_kw = normalize_model_str(kw)
        if norm_kw in norm_ui or norm_ui in norm_kw:
            return True

    return False

def get_current_ui_model(page) -> str:
    selectors = [
        "button[aria-label*='model']",
        "uroller-model-picker button",
        "[data-test-id='model-switcher']",
        "button[aria-haspopup='true']"
    ]
    for sel in selectors:
        try:
            elems = page.locator(sel).all()
            for btn in elems:
                if btn.is_visible():
                    txt = btn.inner_text().strip()
                    if any(kw.lower() in txt.lower() for kw in ["flash", "thinking", "pro"]):
                        return txt
        except Exception:
            continue

    for kw in ["Thinking", "Flash", "Pro"]:
        candidates = page.locator(f"button:has-text('{kw}'), div[role='button']:has-text('{kw}')").all()
        for btn in reversed(candidates):
            try:
                if btn.is_visible():
                    txt = btn.inner_text().strip()
                    if txt and len(txt) < 40:
                        return txt
            except Exception:
                continue
    return "Unknown"

def ensure_selected_model(page, target_model_name):
    input_selector = "rich-textarea p, div[contenteditable='true'], textarea"
    page.wait_for_selector(input_selector, timeout=30000)

    current_ui = get_current_ui_model(page)
    log_status(f"Current Gemini Web UI model detected: '{current_ui}' (Target: '{target_model_name}')")

    if is_model_matching(target_model_name, current_ui):
        log_status(f"Active UI model '{current_ui}' matches target '{target_model_name}'. No switch needed.")
        return

    log_status(f"Switching UI model from '{current_ui}' to '{target_model_name}'...")

    prompt_box = page.locator(input_selector).first
    prompt_box.scroll_into_view_if_needed()

    picker_button = None
    selectors = [
        "button[aria-label*='model']",
        "uroller-model-picker button",
        "[data-test-id='model-switcher']"
    ]
    for sel in selectors:
        try:
            btns = page.locator(sel).all()
            for btn in btns:
                if btn.is_visible():
                    picker_button = btn
                    break
        except Exception:
            continue
        if picker_button:
            break

    if not picker_button:
        for kw in ["Thinking", "Flash", "Pro"]:
            candidates = page.locator(f"button:has-text('{kw}'), div[role='button']:has-text('{kw}')").all()
            for btn in reversed(candidates):
                try:
                    if btn.is_visible() and len(btn.inner_text().strip()) < 40:
                        picker_button = btn
                        break
                except Exception:
                    continue
            if picker_button:
                break

    if not picker_button:
        prompt_box.click()
        page.keyboard.press("Tab")
        time.sleep(2)
        focused_el = page.locator("*:focus")
        if focused_el.is_visible():
            picker_button = focused_el

    if not picker_button or not picker_button.is_visible():
        err = "⚠️ Could not locate the model picker dropdown button in the Web UI text area."
        log_status(err, "WARNING")
        raise RuntimeError(err)

    picker_button.click()
    time.sleep(1.5)

    target_kws = MODEL_KEYWORD_MAP.get(target_model_name, [target_model_name])
    search_terms = [target_model_name] + target_kws

    visible_option = None
    for term in search_terms:
        for el in page.get_by_text(term, exact=False).all():
            try:
                if el.is_visible() and el != picker_button:
                    visible_option = el
                    break
            except Exception:
                continue
        if visible_option:
            break

    if not visible_option:
        page.keyboard.press("Escape")
        err = f"❌ Model option matching '{target_model_name}' was not found in Web UI dropdown options."
        log_status(err, "ERROR")
        raise RuntimeError(err)

    is_disabled = False
    try:
        aria_dis = visible_option.get_attribute("aria-disabled")
        classes = (visible_option.get_attribute("class") or "").lower()
        if aria_dis == "true" or "disabled" in classes:
            is_disabled = True
    except Exception:
        pass

    if is_disabled:
        page.keyboard.press("Escape")
        err = f"⛔ ABORTED: Target model '{target_model_name}' is currently DISABLED on your account!"
        log_status(err, "ERROR")
        raise RuntimeError(err)

    visible_option.click()
    time.sleep(1.2)
    
    new_ui = get_current_ui_model(page)
    log_status(f"Model switch complete. Active UI model string is now: '{new_ui}'")

def check_gemini_ui_errors(page):
    error_selectors = [
        "snack-bar", 
        ".error-notification", 
        "[data-test-id='error-notification']", 
        "div[role='alert']",
        ".mat-snack-bar-container"
    ]
    for sel in error_selectors:
        try:
            elems = page.locator(sel).all()
            for el in elems:
                if el.is_visible():
                    txt = el.inner_text().strip()
                    # Filter out benign UI toast notifications so they don't break generation
                    if txt and not any(ign in txt.lower() for ign in ["draft saved", "copied", "undo", "restored"]):
                        return txt
        except Exception:
            continue
    return None

def execute_single_prompt(page, prompt_text, target_model="3.6 Flash", pre_send_delay=6.0, post_gen_delay=4.0):
    ensure_selected_model(page, target_model)

    msg_selector = "message-content, .model-response-text, .response-container"
    initial_responses = page.locator(msg_selector).all()
    initial_count = len(initial_responses)

    input_selector = "rich-textarea p, div[contenteditable='true'], textarea"
    page.wait_for_selector(input_selector, timeout=30000)
    prompt_box = page.locator(input_selector).first
    prompt_box.scroll_into_view_if_needed()
    prompt_box.click()
    time.sleep(0.5)

    mod_key = "Meta" if platform.system() == "Darwin" else "Control"
    page.keyboard.press(f"{mod_key}+A")
    page.keyboard.press("Backspace")
    time.sleep(0.3)

    log_status(f"Injecting prompt text into Gemini input area ({len(prompt_text)} chars)...")
    page.evaluate("(text) => document.execCommand('insertText', false, text)", prompt_text)

    log_status(f"Pausing {pre_send_delay:.1f}s before sending prompt...")
    time.sleep(pre_send_delay)

    page.mouse.wheel(0, -200)
    time.sleep(0.5)

    send_btn = page.locator(
        "button[aria-label='Send message'], "
        "button[aria-label='Send prompt'], "
        "button[data-test-id='send-button']"
    ).first

    if send_btn.is_visible() and send_btn.is_enabled():
        send_btn.click()
    else:
        page.keyboard.press(f"{mod_key}+Enter")

    log_status("Prompt submitted. Awaiting output from Gemini...")
    time.sleep(4.0)
    stop_btn = page.locator("button[aria-label*='Stop']")
    
    for wait_idx in range(120):
        ui_err = check_gemini_ui_errors(page)
        if ui_err:
            log_status(f"Gemini UI Error detected: {ui_err}", "ERROR")
            raise ValueError(f"Gemini Web UI Error Popup: {ui_err}")

        try:
            if stop_btn.count() == 0 or not stop_btn.first.is_visible():
                break
        except Exception:
            break
        time.sleep(1.0)

    log_status(f"Generation complete. Waiting {post_gen_delay:.1f}s for response stabilization...")
    time.sleep(post_gen_delay)
    
    responses = page.locator(msg_selector).all()
    if len(responses) <= initial_count:
        err = f"Gemini failed to output a new response node (Count remained at {initial_count})."
        log_status(err, "ERROR")
        raise ValueError(err)

    raw_output = responses[-1].inner_text().strip()
    if not raw_output:
        err = "Gemini returned an empty response string."
        log_status(err, "ERROR")
        raise ValueError(err)

    log_status(f"Received raw output from Gemini ({len(raw_output)} chars). Verifying active model...")

    final_model_ui = get_current_ui_model(page)
    if not is_model_matching(target_model, final_model_ui):
        log_status(f"⚠️ Model UI label mismatch ('{final_model_ui}' vs target '{target_model}'), proceeding with generated JSON response.", "WARNING")

    return raw_output

def run_playwright_gemini_batch(tasks, selected_model="3.6 Flash", headless=False, auto_approve=False, max_retries=3, progress_bar=None, status_box=None):
    collected_questions = []
    success_count = 0
    fail_count = 0

    log_status(f"Starting IGCSE batch execution of {len(tasks)} task(s) using '{selected_model}'...")

    try:
        with sync_playwright() as p:
            try:
                log_status("Connecting to local browser over CDP (localhost:9222)...")
                browser = p.chromium.connect_over_cdp("http://localhost:9222")
                context = browser.contexts[0]
                page = None
                
                for p_tab in context.pages:
                    try:
                        if "gemini.google.com" in p_tab.url:
                            page = p_tab
                            log_status(f"Found active Gemini tab: {p_tab.url}")
                            break
                    except Exception:
                        continue
                
                if not page:
                    for p_tab in context.pages:
                        try:
                            tab_url = p_tab.url.lower()
                            if any(tab_url.startswith(proto) for proto in ["chrome://", "chrome-extension://", "devtools://"]):
                                continue
                            log_status("Navigating existing tab to Gemini...")
                            p_tab.goto("https://gemini.google.com/app", wait_until="domcontentloaded")
                            page = p_tab
                            break
                        except Exception:
                            continue
                
                if not page:
                    log_status("Opening clean new browser tab for Gemini...")
                    page = context.new_page()
                    page.goto("https://gemini.google.com/app", wait_until="domcontentloaded")

            except Exception as cdp_err:
                log_status(f"CDP connection unavailable ({cdp_err}). Launching persistent context...", "WARNING")
                context = p.chromium.launch_persistent_context(
                    user_data_dir=USER_DATA_DIR,
                    headless=headless,
                    viewport={"width": 1280, "height": 900},
                    args=["--disable-blink-features=AutomationControlled"]
                )
                context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
                
                page = None
                for p_tab in context.pages:
                    try:
                        if "gemini.google.com" in p_tab.url:
                            page = p_tab
                            break
                    except Exception:
                        continue
                        
                if not page:
                    page = context.new_page()
                    page.goto("https://gemini.google.com/app", wait_until="domcontentloaded")

            time.sleep(2)
            input_selector = "rich-textarea p, div[contenteditable='true'], textarea"
            
            if not page.locator(input_selector).first.is_visible() or "accounts.google.com" in page.url:
                log_status("Google Sign-In page detected. Waiting up to 3 minutes for authentication...", "WARNING")
                if status_box:
                    status_box.warning("⚠️ Google Sign-In required in opened browser window. Waiting up to 3 minutes...")
                start_time = time.time()
                while time.time() - start_time < 180:
                    if page.locator(input_selector).first.is_visible():
                        break
                    time.sleep(2)

            total_tasks = len(tasks)
            
            for idx, task in enumerate(tasks):
                sub_obj = task["subtopic_obj"]
                sub_label = get_subtopic_label(sub_obj)
                topic_name = task["topic"]
                log_status(f"--- Task {idx+1}/{total_tasks}: {sub_label} ({topic_name}) ---")
                
                for attempt in range(1, max_retries + 1):
                    retry_label = f" (Attempt {attempt}/{max_retries})" if attempt > 1 else ""
                    status_msg = f"⏳ Batch Progress ({idx+1}/{total_tasks}): Processing `{sub_label}`{retry_label}..."
                    if status_box:
                        status_box.info(status_msg)
                    if progress_bar:
                        progress_bar.progress((idx + (attempt - 1) / max_retries) / total_tasks)
                    set_tab_title(f"⏳ ({idx+1}/{total_tasks}) {sub_label}{retry_label}...")

                    prompt = build_igcse_prompt(
                        topic_name,
                        task["subtopic_obj"],
                        task["easy"],
                        task["med"],
                        task["hard"],
                        calculator_mode=task["calc_mode"],
                        tier=task.get("tier", "Extended"),
                        include_tikz=task["include_tikz"]
                    )

                    try:
                        raw_text = execute_single_prompt(page, prompt, target_model=selected_model, pre_send_delay=6.0, post_gen_delay=4.0)
                        log_status(f"Cleaning and repairing output string for {sub_label}...")
                        repaired_text = clean_and_repair_json(raw_text)
                        
                        parsed_items = json.loads(repaired_text, strict=False)

                        if not isinstance(parsed_items, list) or len(parsed_items) == 0:
                            raise ValueError("Gemini returned empty or invalid question JSON structure.")

                        parsed_items = assign_unique_ids(parsed_items)

                        now_stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        for item in parsed_items:
                            if isinstance(item, dict):
                                item["generated_by_model"] = selected_model
                                item["generated_at"] = now_stamp

                        log_status(f"✅ Subtopic {sub_label} parsed successfully! ({len(parsed_items)} question(s))")

                        if auto_approve and parsed_items:
                            current_master = load_master_bank()
                            prev_count = len(current_master)
                            current_master.extend(parsed_items)
                            new_total = save_master_bank(current_master)
                            added_count = new_total - prev_count
                            log_status(f"Saved to master bank: +{added_count} new unique item(s) (Total Bank Count: {new_total})")

                        collected_questions.extend(parsed_items)
                        success_count += 1
                        break

                    except RuntimeError as stop_err:
                        log_status(f"Execution halted: {stop_err}", "ERROR")
                        if status_box:
                            status_box.error(f"{stop_err}")
                        return collected_questions, success_count, fail_count, str(stop_err)

                    except Exception as err:
                        err_tb = traceback.format_exc()
                        log_status(f"Subtopic {sub_label} attempt {attempt} failed: {err}", "WARNING")
                        log_status(f"Traceback snippet: {err_tb[-300:]}", "DEBUG")
                        if attempt < max_retries:
                            log_status("Pausing 6 seconds before retrying...")
                            time.sleep(6)
                        else:
                            fail_count += 1
                            log_status(f"❌ Subtopic {sub_label} permanently failed after {max_retries} attempts.", "ERROR")

                if idx < total_tasks - 1:
                    log_status("Pausing 30 seconds before next subtopic batch...")
                    for wait_sec in range(30, 0, -1):
                        set_tab_title(f"⏸️ Pacing Pause ({wait_sec}s)...")
                        time.sleep(2)

            return collected_questions, success_count, fail_count, None

    except Exception as global_err:
        log_status(f"Global batch execution error: {global_err}", "ERROR")
        return collected_questions, success_count, fail_count, str(global_err)

# ==============================================================================
# 6. STREAMLIT UI SETUP & SESSION STATE MANAGEMENT
# ==============================================================================
st.set_page_config(page_title="IGCSE Mathematics Question Generator & Batch Viewer", layout="wide")

syllabus_data = load_syllabus()

if "master_count" not in st.session_state:
    st.session_state.master_count = 3
if "easy_count" not in st.session_state:
    st.session_state.easy_count = 1
if "med_count" not in st.session_state:
    st.session_state.med_count = 1
if "hard_count" not in st.session_state:
    st.session_state.hard_count = 1
if "status_logs" not in st.session_state:
    st.session_state.status_logs = []

def sync_from_master():
    total = st.session_state.master_count
    st.session_state.easy_count = total // 3
    st.session_state.med_count = total // 3 + (1 if total % 3 >= 1 else 0)
    st.session_state.hard_count = total // 3 + (1 if total % 3 == 2 else 0)

def sync_to_master():
    st.session_state.master_count = (
        st.session_state.easy_count + 
        st.session_state.med_count + 
        st.session_state.hard_count
    )

st.title("📐 IGCSE Mathematics Question Generator & Batch Viewer")

if not syllabus_data:
    st.error("Please ensure `syllabus.json` exists in your directory and has valid JSON structure.")
else:
    col1, col2 = st.columns([1, 1])
    
    # --- LEFT COLUMN: GENERATOR CONFIGURATION & RUNNER ---
    with col1:
        st.subheader("🤖 Step 1: Configure & Run Generator")
        
        workflow_mode = st.radio(
            "⚙️ Choose Workflow Mode:",
            options=["📋 Manual Copy & Paste", "🔄 Automated Batch Run"],
            index=1,
            horizontal=True
        )

        selected_model = "3.6 Flash"
        
        if workflow_mode == "🔄 Automated Batch Run":
            selected_model = st.selectbox(
                "Web UI Model Choice:",
                options=["3.6 Flash", "3.6 Thinking", "3.1 Pro"],
                index=0,
                help="Execution halts automatically if target model is disabled on your account."
            )

        calc_mode = st.radio(
            "🧮 Calculator Policy / Paper Type:",
            options=["Paper 2 (Non-Calculator)", "Paper 4 (Calculator)", "Mixed / Flexible"],
            index=1,
            horizontal=True
        )

        include_tikz = st.checkbox("🎨 Include TikZ Diagrams / Drawings", value=True)
        
        st.markdown("---")
        st.markdown("#### Question Count & Difficulty Controls")
        
        st.slider("🎛️ Master Control: Questions Per Subtopic", min_value=1, max_value=15, key="master_count", on_change=sync_from_master)
        
        col_e, col_m, col_h = st.columns(3)
        with col_e:
            st.slider("🟢 Easy", min_value=0, max_value=10, key="easy_count", on_change=sync_to_master)
        with col_m:
            st.slider("🟡 Medium", min_value=0, max_value=10, key="med_count", on_change=sync_to_master)
        with col_h:
            st.slider("🔴 Hard", min_value=0, max_value=10, key="hard_count", on_change=sync_to_master)

        st.markdown("---")

        if workflow_mode == "📋 Manual Copy & Paste":
            topic_list = [t.get("topic", "") for t in syllabus_data if isinstance(t, dict) and t.get("topic")]
            topic_choice = st.selectbox("Select Primary Topic", topic_list, key="gem_topic_filter")
            
            topic_obj = next((t for t in syllabus_data if isinstance(t, dict) and t.get("topic") == topic_choice), None)
            subtopic_list = topic_obj.get("subtopics", []) if topic_obj and isinstance(topic_obj, dict) else []
            
            primary_sub_obj = st.selectbox(
                "Select Primary Subtopic", 
                options=subtopic_list,
                format_func=get_subtopic_label,
                key="primary_sub_selector"
            )
            
            generated_prompt = build_igcse_prompt(
                topic_choice, 
                primary_sub_obj, 
                st.session_state.easy_count, 
                st.session_state.med_count, 
                st.session_state.hard_count, 
                calculator_mode=calc_mode,
                tier="Extended",
                include_tikz=include_tikz
            )
            
            st.markdown("**Copy this prompt to Gemini:**")
            st.code(generated_prompt, language="text")

        else:
            st.subheader("🔄 Configure Multi-Subtopic Queue")
            
            all_subtopic_options = []
            for t in syllabus_data:
                if isinstance(t, dict):
                    topic_name = t.get("topic", "Unknown Topic")
                    subtopics = t.get("subtopics", [])
                    if isinstance(subtopics, list):
                        for s in subtopics:
                            sub_label = get_subtopic_label(s)
                            sub_obj = s if isinstance(s, dict) else {"code": "", "title": str(s)}
                            all_subtopic_options.append({
                                "label": f"{sub_label} ({topic_name})" if topic_name else sub_label,
                                "topic": topic_name,
                                "subtopic_obj": sub_obj
                            })

            topic_names = [t.get("topic") for t in syllabus_data if isinstance(t, dict) and t.get("topic")]

            if "selected_topics_filter" not in st.session_state:
                st.session_state.selected_topics_filter = []
            if "batch_subtopics_selection" not in st.session_state:
                st.session_state.batch_subtopics_selection = []

            def sync_subtopics_by_topic():
                chosen_topics = set(st.session_state.get("selected_topics_filter", []))
                st.session_state.batch_subtopics_selection = [
                    item for item in all_subtopic_options if item["topic"] in chosen_topics
                ]

            def select_all_topics():
                st.session_state.selected_topics_filter = list(topic_names)
                st.session_state.batch_subtopics_selection = list(all_subtopic_options)

            def clear_all_topics():
                st.session_state.selected_topics_filter = []
                st.session_state.batch_subtopics_selection = []

            col_top_sel, col_btn1, col_btn2 = st.columns([4, 1.5, 1.5])
            with col_top_sel:
                st.multiselect(
                    "🎯 Select Topic(s):",
                    options=topic_names,
                    key="selected_topics_filter",
                    on_change=sync_subtopics_by_topic,
                    help="Filter subtopics strictly by selected topics."
                )

            with col_btn1:
                st.write("")
                st.write("")
                st.button("Select All", key="btn_sel_all_topics", on_click=select_all_topics, use_container_width=True)

            with col_btn2:
                st.write("")
                st.write("")
                st.button("Clear All", key="btn_clr_all_topics", on_click=clear_all_topics, use_container_width=True)

            selected_batch_items = st.multiselect(
                "Select Subtopics to Queue for Batch Generation:",
                options=all_subtopic_options,
                key="batch_subtopics_selection",
                format_func=lambda item: item["label"]
            )
            
            col_batch_cfg1, col_batch_cfg2 = st.columns(2)
            with col_batch_cfg1:
                auto_approve_toggle = st.checkbox("⚡ Auto-Approve & Merge directly into master_bank.json", value=True)
                show_batch_browser = st.checkbox("🖥️ Visible Browser Window", value=True, key="batch_browser_toggle")
            with col_batch_cfg2:
                batch_max_retries = st.number_input("Max Auto-Retries Per Failed Subtopic", min_value=1, max_value=5, value=3, key="batch_retries")
            
            if st.button("🚀 Start Automated Batch Queue Run", use_container_width=True):
                if not selected_batch_items:
                    st.warning("Please select at least one subtopic for the batch queue.")
                else:
                    batch_tasks = []
                    for item in selected_batch_items:
                        batch_tasks.append({
                            "topic": item["topic"],
                            "subtopic_obj": item["subtopic_obj"],
                            "easy": st.session_state.easy_count,
                            "med": st.session_state.med_count,
                            "hard": st.session_state.hard_count,
                            "calc_mode": calc_mode,
                            "tier": "Extended",
                            "include_tikz": include_tikz
                        })
                    
                    p_bar = st.progress(0.0)
                    s_box = st.empty()
                    set_tab_title(f"⏳ Batch Running (0/{len(batch_tasks)})...")
                    
                    items, succ, fail, global_err = run_playwright_gemini_batch(
                        batch_tasks, 
                        selected_model=selected_model,
                        headless=not show_batch_browser, 
                        auto_approve=auto_approve_toggle,
                        max_retries=batch_max_retries,
                        progress_bar=p_bar,
                        status_box=s_box
                    )
                    
                    if global_err:
                        st.error(f"❌ Batch Engine Stopped: {global_err}")
                        set_tab_title("❌ Batch Error")
                    else:
                        st.success(f"🎉 Batch Finished! Successful Subtopics: {succ}/{len(batch_tasks)} | Total Questions: {len(items)}")
                        set_tab_title("✅ Batch Complete!")
                        
                        if not auto_approve_toggle and items:
                            items = assign_unique_ids(items)
                            st.session_state["parsed_questions_list"] = items
                            st.session_state["json_error_details"] = None

    # --- RIGHT COLUMN: BATCH VIEWER & REVIEW BUFFER ---
    with col2:
        st.subheader("📦 Step 2: Batch Viewer & Review Buffer")
        
        if workflow_mode == "📋 Manual Copy & Paste":
            raw_pasted_text = st.text_area("Paste Gemini JSON response here:", height=260)
            
            if st.button("🔍 Parse & Preview Question(s)", use_container_width=True):
                set_tab_title("⏳ Parsing...")
                log_status("Starting manual JSON parse attempt...")
                if not raw_pasted_text.strip():
                    st.warning("Please paste a JSON response from Gemini first.")
                else:
                    try:
                        repaired_text = clean_and_repair_json(raw_pasted_text)
                        parsed_json = json.loads(repaired_text, strict=False)
                        parsed_json = assign_unique_ids(parsed_json)
                        
                        now_stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        for item in parsed_json:
                            if isinstance(item, dict):
                                item["generated_by_model"] = item.get("generated_by_model") or "Manual Copy & Paste"
                                item["generated_at"] = item.get("generated_at") or now_stamp
                                
                        st.session_state["parsed_questions_list"] = parsed_json
                        st.session_state["json_error_details"] = None
                        log_status(f"Manual parse successful! Created {len(parsed_json)} candidate question(s).")
                        st.success(f"Parsed {len(parsed_json)} question(s) successfully!")
                        set_tab_title("✅ Parsing Complete!")
                    except (json.JSONDecodeError, ValueError) as err:
                        log_status(f"Manual parse error: {err}", "ERROR")
                        st.session_state["parsed_questions_list"] = None
                        st.session_state["json_error_details"] = {
                            "error": err,
                            "raw_text": raw_pasted_text,
                            "repaired_text": repaired_text if 'repaired_text' in locals() else raw_pasted_text
                        }
                        set_tab_title("⚠️ Parse Error")

        # System Status Logs & Debug Terminal
        with st.expander("🐛 Status Logs & Debug Terminal", expanded=True if st.session_state.get("json_error_details") else False):
            log_col1, log_col2 = st.columns([3, 1])
            with log_col1:
                st.markdown("**Live Execution & Diagnostic Log Entries:**")
            with log_col2:
                if st.button("🗑️ Clear Logs", key="clear_logs_btn"):
                    st.session_state.status_logs = []
                    st.rerun()

            logs_list = st.session_state.get("status_logs", [])
            if logs_list:
                logs_text = "\n".join(logs_list)
                st.text_area("Status Log Viewer", value=logs_text, height=200, disabled=True, key="status_terminal_view")
                st.download_button(
                    "📥 Download Debug Log File (.txt)",
                    data=logs_text,
                    file_name=f"debug_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
                    mime="text/plain"
                )
            else:
                st.info("No status logs recorded yet.")

        # JSON Parse Error Inspector
        if "json_error_details" in st.session_state and st.session_state["json_error_details"]:
            err_info = st.session_state["json_error_details"]
            err = err_info["error"]
            lines = err_info["raw_text"].splitlines()
            
            if isinstance(err, ValueError):
                st.error(f"❌ **Gemini Generation Error:** {err}")
            else:
                st.error(f"❌ **JSON Decode Error:** {err.msg} at **Line {getattr(err, 'lineno', '?')}**, **Column {getattr(err, 'colno', '?')}**")
            
            st.markdown("### 🔍 Line Inspector")
            if hasattr(err, 'lineno'):
                start_line = max(0, err.lineno - 4)
                end_line = min(len(lines), err.lineno + 4)
                snippet_lines = []
                for idx_l in range(start_line, end_line):
                    line_num = idx_l + 1
                    marker = ">>>" if line_num == err.lineno else "   "
                    snippet_lines.append(f"{marker} Line {line_num:4d}: {lines[idx_l]}")
                    if line_num == err.lineno and getattr(err, 'colno', None):
                        pointer_space = " " * 12 + " " * max(0, err.colno - 1)
                        snippet_lines.append(f"{pointer_space}^ [Delimiter or string error around here]")
                st.code("\n".join(snippet_lines), language="text")

        # Candidate Review & Batch Viewer
        if "parsed_questions_list" in st.session_state and st.session_state["parsed_questions_list"]:
            q_list = st.session_state["parsed_questions_list"]
            
            st.markdown("---")
            st.subheader(f"Step 3: Review & Approve ({len(q_list)} Candidate Item(s))")
            
            edited_q_list = []
            
            for idx, q_data in enumerate(q_list):
                paper_info = q_data.get('paper_type', 'Paper 4 (Calculator)')
                paper_icon = "🚫" if "Non-Calculator" in paper_info else "🧮"
                tier_info = "Extended"
                tot_marks = q_data.get('total_marks', 4)
                model_used = q_data.get('generated_by_model', selected_model)
                gen_time = q_data.get('generated_at', datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                
                with st.expander(f"Item #{idx+1} | {tier_info} ({tot_marks} Marks) | {paper_icon} {paper_info} | Subtopic {q_data.get('subtopic', '')}", expanded=(idx==0)):
                    col_id, col_tier, col_marks, col_diff = st.columns([2, 2, 1, 1])
                    with col_id:
                        item_id = st.text_input("ID", value=q_data.get("id", str(uuid.uuid4())[:8]), key=f"id_{idx}")
                    with col_tier:
                        item_tier = st.text_input("Tier", value="Extended", disabled=True, key=f"tier_{idx}")
                    with col_marks:
                        item_marks = st.number_input("Total Marks", value=int(tot_marks), min_value=1, max_value=30, key=f"marks_{idx}")
                    with col_diff:
                        item_diff = st.selectbox("Difficulty", ["Easy", "Medium", "Hard"], index=["Easy", "Medium", "Hard"].index(q_data.get("difficulty", "Medium")), key=f"diff_{idx}")
                    
                    col_paper, col_sub = st.columns(2)
                    with col_paper:
                        item_paper = st.selectbox("Paper Type", ["Paper 2 (Non-Calculator)", "Paper 4 (Calculator)"], index=0 if "Non-Calculator" in paper_info else 1, key=f"paper_{idx}")
                    with col_sub:
                        item_subtopic = st.text_input("Subtopic Code", value=q_data.get("subtopic", ""), key=f"sub_{idx}")

                    edited_intro_text = st.text_area("Intro Text (Raw LaTeX)", value=q_data.get("intro_text", ""), height=80, key=f"intro_{idx}")
                    edited_intro_tikz = st.text_area("Intro TikZ Diagram Code", value=q_data.get("intro_diagram_tikz", ""), height=80, key=f"intro_tikz_{idx}")
                    
                    st.markdown("**📄 Rendered IGCSE Exam Preview:**")
                    with st.container(border=True):
                        st.markdown(f"### 📝 {item_tier} Tier | Subtopic [{item_subtopic}] ({item_marks} Marks)")
                        st.caption(f"**Paper:** {paper_icon} {item_paper} | **Difficulty:** {item_diff} | **Model:** 🤖 {model_used} | **Created:** 🕒 {gen_time}")
                        st.markdown("---")
                        
                        if edited_intro_text:
                            st.markdown(format_prompt_for_preview(edited_intro_text), unsafe_allow_html=True)
                            
                        if edited_intro_tikz.strip():
                            render_tikz_pdflatex(edited_intro_tikz, caption="Diagram")
                            
                        parts = q_data.get("parts", [])
                        for p in parts:
                            p_prompt = p.get("prompt", "")
                            p_tikz = p.get("diagram_tikz", "").strip()
                            subparts = p.get("subparts", [])

                            if subparts:
                                if p.get("context"):
                                    st.markdown(f"*{format_prompt_for_preview(p['context'])}*", unsafe_allow_html=True)
                                    
                                p_label_str = f"**{p.get('label', '')}** " if p.get('label') else ""
                                if p_prompt:
                                    st.markdown(f"{p_label_str}{format_prompt_for_preview(p_prompt)}", unsafe_allow_html=True)
                                elif p_label_str:
                                    st.markdown(p_label_str)
                                    
                                if p_tikz:
                                    render_tikz_pdflatex(p_tikz, caption="Diagram")

                                for sp in subparts:
                                    if sp.get("context"):
                                        st.markdown(f"*{format_prompt_for_preview(sp['context'])}*", unsafe_allow_html=True)
                                    sp_prompt = sp.get("prompt", "")
                                    sp_tikz = sp.get("diagram_tikz", "").strip()
                                    
                                    sp_ans_type = sp.get("answer_type", "line")
                                    sp_prefix = sp.get("answer_prefix", "")
                                    sp_suffix = sp.get("answer_suffix", "")
                                    sp_format = sp.get("answer_format", "")
                                    sp_marks = sp.get("marks", 0)
                                    sp_vspace = float(sp.get("vspace_cm", max(1.0, sp_marks * 1.5)))

                                    sp_label_str = f"**{sp.get('label', '')}** " if sp.get('label') else ""
                                    st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;{sp_label_str}{format_prompt_for_preview(sp_prompt)}", unsafe_allow_html=True)
                                    if sp_tikz:
                                        render_tikz_pdflatex(sp_tikz, caption="Diagram")
                                    render_answer_slot_preview(sp_ans_type, sp_prefix, sp_suffix, sp_format, sp_marks, sp_vspace, prompt_text=sp_prompt)
                                    
                                    with st.expander(f"Markscheme {p.get('label', '')}{sp.get('label', '')}"):
                                        vert_steps = split_markscheme_arrow_steps(sp.get("markscheme", []))
                                        for step in vert_steps:
                                            st.markdown(f"- {format_markscheme_step_latex(step)}")
                            else:
                                if p.get("context"):
                                    st.markdown(f"*{format_prompt_for_preview(p['context'])}*", unsafe_allow_html=True)

                                p_label = str(p.get('label', '')).strip()
                                show_label = p_label if (len(parts) > 1 or p_label.lower() not in ["(a)", "a)", "a", ""]) else ""
                                label_prefix = f"**{show_label}** " if show_label else ""

                                p_ans_type = p.get("answer_type", "line")
                                p_prefix = p.get("answer_prefix", "")
                                p_suffix = p.get("answer_suffix", "")
                                p_format = p.get("answer_format", "")
                                p_marks = p.get("marks", 0)
                                p_vspace = float(p.get("vspace_cm", max(1.0, p_marks * 1.5)))

                                # SEQUENCE: 1. Prompt -> 2. TikZ Diagram -> 3. Answer Line
                                if p_prompt:
                                    st.markdown(f"{label_prefix}{format_prompt_for_preview(p_prompt)}", unsafe_allow_html=True)
                                if p_tikz:
                                    render_tikz_pdflatex(p_tikz, caption="Diagram")
                                render_answer_slot_preview(p_ans_type, p_prefix, p_suffix, p_format, p_marks, p_vspace, prompt_text=p_prompt)
                                
                                with st.expander(f"Markscheme {p.get('label', 'Solution')}"):
                                    vert_steps = split_markscheme_arrow_steps(p.get("markscheme", []))
                                    for step in vert_steps:
                                        st.markdown(f"- {format_markscheme_step_latex(step)}")

                    edited_q_list.append({
                        "id": item_id,
                        "topic": q_data.get("topic", "IGCSE Maths"),
                        "subtopic": item_subtopic,
                        "tier": "Extended",
                        "paper_type": item_paper,
                        "difficulty": item_diff,
                        "total_marks": item_marks,
                        "generated_by_model": model_used,
                        "generated_at": gen_time,
                        "intro_text": edited_intro_text,
                        "intro_diagram_tikz": edited_intro_tikz,
                        "parts": q_data.get("parts", [])
                    })

            btn_col1, btn_col2 = st.columns(2)
            
            with btn_col1:
                if st.button(f"✅ Approve & Merge All ({len(edited_q_list)}) to master_bank.json", use_container_width=True):
                    master_bank = load_master_bank()
                    prev_count = len(master_bank)
                    master_bank.extend(edited_q_list)
                    new_total = save_master_bank(master_bank)
                    added_count = new_total - prev_count
                    
                    if added_count > 0:
                        st.success(f"Successfully merged {added_count} new unique question(s) to `{MASTER_BANK_FILE}` (Total: {new_total})!")
                    else:
                        st.warning(f"All {len(edited_q_list)} question(s) were duplicates of existing items in `{MASTER_BANK_FILE}`.")
                        
                    st.session_state["parsed_questions_list"] = None
                    st.session_state["json_error_details"] = None
                    set_tab_title("✅ Questions Processed!")
                    st.rerun()
                    
            with btn_col2:
                if st.button("❌ Discard All", use_container_width=True):
                    st.session_state["parsed_questions_list"] = None
                    st.session_state["json_error_details"] = None
                    st.info("Candidate questions discarded.")
                    set_tab_title("📐 IGCSE Mathematics Question Generator")
                    st.rerun()