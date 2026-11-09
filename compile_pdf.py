import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed

MASTER_BANK_FILE = "master_bank.json"
SYLLABUS_FILE = "syllabus.json"
EXPORTS_DIR = "exports"

os.makedirs(EXPORTS_DIR, exist_ok=True)

# Guard pattern for LaTeX/TikZ macros starting with \n while unescaping literal \n string breaks
LATEX_N_CMD_PATTERN = r"\\n(?!(?:ode|odepart|odes|ote|ull|eq|ne|nu|otin|oindent|abla|ewline|ewpage|ew\w*|ormalsize|ormalfont|ormalcolor|umber|numexpr|obreak|obreakspace|olimits|atural|subseteq|supseteq|geq|leq|less|gtr|exists|leftarrow|rightarrow|Leftarrow|Rightarrow|leftrightarrow|Leftrightarrow|parallel|mid|approx|cong|prec|succ|vdash|Vdash|vDash|VDash|earrow|warrow)\b)"

# ==============================================================================
# LATEX PREAMBLE TEMPLATE
# ==============================================================================
LATEX_PREAMBLE = r"""\documentclass[11pt, a4paper]{article}
\usepackage[utf8]{inputenc}
\usepackage[margin=2cm]{geometry}
\usepackage{amsmath, amssymb, amsfonts}
\usepackage{tikz}
\usetikzlibrary{arrows.meta, angles, quotes, calc, positioning, patterns, backgrounds}
\usepackage{fancyhdr}
\usepackage{enumitem}
\usepackage{xcolor}
\usepackage{needspace}

% Custom color for hyperlink visibility
\definecolor{linkblue}{RGB}{0, 102, 204}

\usepackage[colorlinks=true, linkcolor=linkblue, citecolor=linkblue, urlcolor=linkblue, pdfencoding=auto]{hyperref}

% Adjust header height for fancyhdr compliance
\setlength{\headheight}{14pt}
\addtolength{\topmargin}{-2pt}

% Custom header & footer styling
\pagestyle{fancy}
\fancyhf{}
\renewcommand{\headrulewidth}{0.4pt}
\fancyhead[C]{\thepage}

% Force paragraph indentation to ZERO globally
\setlength{\parindent}{0pt}
\setlength{\parskip}{0.4em}
\everypar{\setlength{\parindent}{0pt}}

% Structural Question & Subpart Hanging Indentation Macros
\newcommand{\qlabel}[1]{%
  \par\vspace{0.20cm}%
  \leftskip=0pt%
  \noindent\hspace*{-1.40cm}\makebox[1.00cm][r]{\textbf{#1}}\hspace*{0.40cm}\ignorespaces
}

\newcommand{\subpartalpha}[1]{%
  \par\vspace{0.15cm}%
  \leftskip=0.85cm%
  \noindent\hspace*{-0.85cm}\makebox[0.85cm][l]{\textbf{#1}}\ignorespaces
}

\newcommand{\qsubpartalpha}[2]{%
  \par\vspace{0.20cm}%
  \leftskip=0.85cm%
  \noindent\hspace*{-2.25cm}\makebox[1.00cm][r]{\textbf{#1}}\hspace*{0.40cm}\makebox[0.85cm][l]{\textbf{#2}}\ignorespaces
}

\newcommand{\subpartroman}[1]{%
  \par\vspace{0.10cm}%
  \leftskip=1.70cm%
  \noindent\hspace*{-0.85cm}\makebox[0.85cm][l]{\textbf{#1}}\ignorespaces
}

\newcommand{\qsubpartroman}[2]{%
  \par\vspace{0.20cm}%
  \leftskip=1.70cm%
  \noindent\hspace*{-3.10cm}\makebox[1.00cm][r]{\textbf{#1}}\hspace*{1.25cm}\makebox[0.85cm][l]{\textbf{#2}}\ignorespaces
}

\newcommand{\subpartalpharoman}[2]{%
  \par\vspace{0.15cm}%
  \leftskip=1.70cm%
  \noindent\hspace*{-1.70cm}\makebox[0.85cm][l]{\textbf{#1}}\makebox[0.85cm][l]{\textbf{#2}}\ignorespaces
}

\newcommand{\qsubpartalpharoman}[3]{%
  \par\vspace{0.20cm}%
  \leftskip=1.70cm%
  \noindent\hspace*{-3.10cm}\makebox[1.00cm][r]{\textbf{#1}}\hspace*{0.40cm}\makebox[0.85cm][l]{\textbf{#2}}\makebox[0.85cm][l]{\textbf{#3}}\ignorespaces
}

\begin{document}
"""


def load_syllabus_map(syllabus_path=SYLLABUS_FILE):
    """Loads and maps IGCSE subtopic codes to their title names."""
    s_map = {}
    if not os.path.exists(syllabus_path):
        return s_map

    try:
        with open(syllabus_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        topics = []
        if isinstance(data, dict):
            topics = data.get("topics", [])
        elif isinstance(data, list):
            topics = data

        for topic in topics:
            if isinstance(topic, dict):
                for sub in topic.get("subtopics", []):
                    if isinstance(sub, dict):
                        code = str(sub.get("code", "")).strip()
                        title = str(sub.get("title") or sub.get("content") or "").strip()
                        if code:
                            s_map[code] = title
    except Exception as e:
        print(f"⚠️ Warning: Could not load `{syllabus_path}`: {e}")

    return s_map


def sanitize_filename(name: str) -> str:
    """Sanitizes strings for OS-safe file naming."""
    if not name:
        return "Untitled"
    clean = re.sub(r"\$([^$]+)\$", r"\1", name)
    clean = re.sub(r"\\[a-zA-Z]+", "", clean)
    clean = re.sub(r'[\\/*?:"<>|]', "", clean)
    return re.sub(r"\s+", " ", clean).strip()


def parse_index_ranges(range_str: str, total_count: int):
    selected_indices = []
    chunks = [c.strip() for c in range_str.split(",") if c.strip()]

    for chunk in chunks:
        if "-" in chunk:
            parts = [p.strip() for p in chunk.split("-") if p.strip()]
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                start, end = int(parts[0]), int(parts[1])
                step = 1 if start <= end else -1
                for num in range(start, end + step, step):
                    if 1 <= num <= total_count:
                        selected_indices.append(num - 1)
        elif chunk.isdigit():
            num = int(chunk)
            if 1 <= num <= total_count:
                selected_indices.append(num - 1)

    return list(dict.fromkeys(selected_indices))


def split_markscheme_arrow_steps(steps):
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
                s = s[: mark_match.start()].strip()

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


def balance_latex_envs(text_str: str) -> str:
    if not text_str:
        return ""
    s = str(text_str)

    pattern = re.compile(r"\\(begin|end)\{([a-zA-Z0-9\*]+)\}")
    stack = []
    for match in pattern.finditer(s):
        kind, env_name = match.groups()
        if kind == "begin":
            stack.append(env_name)
        elif kind == "end":
            if stack and stack[-1] == env_name:
                stack.pop()
            elif env_name in stack:
                while stack and stack[-1] != env_name:
                    stack.pop()
                if stack:
                    stack.pop()

    for unclosed_env in reversed(stack):
        s += f"\n\\end{{{unclosed_env}}}"

    return s


def sanitize_latex_text(text_str: str) -> str:
    if not text_str:
        return ""

    s = str(text_str).strip()

    s = re.sub(LATEX_N_CMD_PATTERN, "\n", s)

    s = convert_markdown_lists_to_latex(s)
    s = re.sub(r"(?<!\\)\\\[(\s*[\d\.]+\s*(?:cm|mm|pt|em|ex|in)\])", r"\\\\[\1", s)

    def clean_indent(m):
        brk = m.group(1) or ""
        rest = m.group(3).strip()
        if (
            rest.startswith("$")
            or rest.startswith("\\(")
            or rest.startswith("\\[")
            or rest.startswith("\\begin")
        ):
            return f"{brk}\\hspace*{{0.8cm}} {rest}"
        if re.search(r"\\frac|\\sqrt|\\binom|[\^\_]", rest):
            return f"{brk}\\hspace*{{0.8cm}} ${rest}$"
        return f"{brk}{rest}"

    s = re.sub(
        r"(^|\n|\\\\(?:\[[^\]]+\])?)\s*(\\quad|\\hspace\*?\{[^}]+\})\s*(.*)",
        clean_indent,
        s,
    )
    s = re.sub(r"(?<!\\)%", r"\\%", s)
    s = apply_auto_displaystyle(s)

    combined_pattern = re.compile(
        r"(\$\$.*?\$\$|\$[^\$]+?\$|\\\[.*?\\\]|\\\([^\)]+?\\\)|\\begin\{(?:tabular|array|align|matrix|tikzpicture|bmatrix|pmatrix|vmatrix|itemize)\}.*?\\end\{(?:tabular|array|align|matrix|tikzpicture|bmatrix|pmatrix|vmatrix|itemize)\})",
        re.DOTALL,
    )

    protected_blocks = []

    def mask_protected(m):
        protected_blocks.append(m.group(1))
        return f"PROTECTEDBLOCK{len(protected_blocks)-1}END"

    masked_text = combined_pattern.sub(mask_protected, s)

    def wrap_unwrapped_math_cmds(text):
        pattern = re.compile(
            r"\\(frac|dfrac|tfrac|sqrt|vec|overline)\b.*?(?=\s*(?:$|\n|\\\\|\[\d+\]))",
            re.DOTALL,
        )

        def repl(m):
            val = m.group(0).strip()
            if not val.startswith("$"):
                return f"${val}$"
            return val

        return pattern.sub(repl, text)

    masked_text = wrap_unwrapped_math_cmds(masked_text)

    bare_subscript_pattern = re.compile(r"\b([A-Za-z]+)_(\{?[a-zA-Z0-9\+\-\=]+\}?)\b")

    def wrap_subscript(m):
        var = m.group(1)
        sub = m.group(2)
        if not sub.startswith("{"):
            sub = f"{{{sub}}}"
        return f"${var}_{sub}$"

    masked_text = bare_subscript_pattern.sub(wrap_subscript, masked_text)

    masked_text = re.sub(r"(?<!\\)&", r"\\&", masked_text)
    masked_text = re.sub(r"(?<!\\)_", r"\\_", masked_text)
    masked_text = re.sub(r"(?<!\\)#", r"\\#", masked_text)

    def is_env_block(line_str):
        m = re.search(r"PROTECTEDBLOCK(\d+)END", line_str)
        if m:
            idx = int(m.group(1))
            if idx < len(protected_blocks):
                content = protected_blocks[idx].strip()
                return content.startswith("\\begin")
        return False

    lines = masked_text.split("\n")
    new_lines = []
    structural_starts = (
        "\\begin",
        "\\end",
        "\\item",
        "\\section",
        "\\subsection",
        "\\subsubsection",
        "\\subpart",
        "\\hline",
        "\\\\",
        "\\[",
    )

    for i, line in enumerate(lines):
        stripped = line.strip()
        if i < len(lines) - 1:
            next_stripped = lines[i + 1].strip()

            already_has_break = (
                stripped.endswith("\\\\")
                or bool(re.search(r"\\\\\[[^\]]+\]$", stripped))
                or stripped.endswith("\\par")
            )

            curr_is_struct = stripped.startswith(structural_starts) or is_env_block(stripped)
            next_is_struct = next_stripped.startswith(structural_starts) or is_env_block(next_stripped)

            if stripped and next_stripped and not already_has_break:
                if not curr_is_struct and not next_is_struct:
                    new_lines.append(line + r" \\")
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    masked_text = "\n".join(new_lines)

    for i, content in enumerate(protected_blocks):
        masked_text = masked_text.replace(f"PROTECTEDBLOCK{i}END", content)

    dollars = re.findall(r"(?<!\\)\$", masked_text)
    if len(dollars) % 2 != 0:
        masked_text += "$"

    return balance_latex_envs(masked_text)


def split_latex_sentences(text_str: str) -> str:
    if not text_str:
        return ""

    if (
        r"\begin{tabular}" in text_str
        or r"\begin{align}" in text_str
        or r"\begin{array}" in text_str
        or r"\begin{itemize}" in text_str
    ):
        return text_str

    tex_break_dict = {}

    def mask_tex_breaks(m):
        key = f"__TEXBREAK_{len(tex_break_dict)}__"
        tex_break_dict[key] = m.group(0)
        return key

    masked = re.sub(
        r"\\\\\s*\[[\d\.]+cm\]|\\\\|\\quad|\\qquad|\\hspace\*?\{[^}]+\}",
        mask_tex_breaks,
        text_str,
    )

    math_blocks = []

    def mask_math(m):
        math_blocks.append(m.group(0))
        return f"__MATH_{len(math_blocks)-1}__"

    masked = re.sub(r"\$\$.*?\$\$|\$[^\$]+?\$", mask_math, masked, flags=re.DOTALL)

    abbrev_dict = {}

    def mask_abbrev(m):
        key = f"__ABB_{len(abbrev_dict)}__"
        abbrev_dict[key] = m.group(0)
        return key

    abbrev_pattern = re.compile(
        r"\b(e\.g|i\.e|etc|vs|fig|no|pt|ref|approx|dr|mr|mrs|ms)\.", re.IGNORECASE
    )
    masked = abbrev_pattern.sub(mask_abbrev, masked)

    initial_dict = {}

    def mask_initial(m):
        key = f"__INIT_{len(initial_dict)}__"
        initial_dict[key] = m.group(0)
        return key

    masked = re.sub(r"\b[A-Z]\.", mask_initial, masked)
    masked = re.sub(r"(\d+)\.(\d+)", r"\1__DEC__\2", masked)

    num_list_dict = {}

    def mask_num_list(m):
        key = f"__NUMLIST_{len(num_list_dict)}__"
        num_list_dict[key] = m.group(0)
        return key

    num_list_pattern = re.compile(r"(?:\b\d+|\b[a-z]|\b[ivxlcdm]+)\.(?=\s+[A-Z])", re.IGNORECASE)
    masked = num_list_pattern.sub(mask_num_list, masked)

    def break_sentence(m):
        punct = m.group(1)
        return f"{punct}\\\\ "

    sentence_end_pattern = re.compile(r"([\.\!\?]|:(?=\s*__NUMLIST_))\s+(?=[A-Z\\[]|__MATH_|__NUMLIST_)")
    masked = sentence_end_pattern.sub(break_sentence, masked)

    for key, val in num_list_dict.items():
        masked = masked.replace(key, val)

    masked = re.sub(r"(\d+)__DEC__(\d+)", r"\1.\2", masked)

    for key, val in initial_dict.items():
        masked = masked.replace(key, val)

    for key, val in abbrev_dict.items():
        masked = masked.replace(key, val)

    for idx, math_content in enumerate(math_blocks):
        masked = masked.replace(f"__MATH_{idx}__", math_content)

    for key, val in tex_break_dict.items():
        masked = masked.replace(key, val)

    return masked


def strip_redundant_label(text_str: str, label_1: str, label_2: str = "") -> str:
    if not text_str:
        return ""
    s = text_str.strip()

    clean_1 = re.sub(r"[^\w]", "", label_1) if label_1 else ""
    clean_2 = re.sub(r"[^\w]", "", label_2) if label_2 else ""

    def make_pattern(c1, c2=None):
        if c2:
            d1 = r"[\)\.:]?"
            d2 = r"[\)\.:]" if len(c2) == 1 else r"[\)\.:]?"
            return rf"^\s*\(?\s*{re.escape(c1)}\s*{d1}\s*\(?\s*{re.escape(c2)}\s*{d2}(?!\w)\s*"
        else:
            if len(c1) == 1:
                return rf"^\s*(?:\(\s*{re.escape(c1)}\s*\)|{re.escape(c1)}\s*[\)\.:])(?!\w)\s*"
            else:
                return rf"^\s*\(?\s*{re.escape(c1)}\s*[\)\.:]?(?!\w)\s*"

    if clean_1 and clean_2:
        comb_pattern = make_pattern(clean_1, clean_2)
        if re.search(comb_pattern, s, flags=re.IGNORECASE):
            return re.sub(comb_pattern, "", s, count=1, flags=re.IGNORECASE).strip()

    if clean_1:
        p1 = make_pattern(clean_1)
        if re.search(p1, s, flags=re.IGNORECASE):
            s = re.sub(p1, "", s, count=1, flags=re.IGNORECASE).strip()

    if clean_2:
        p2 = make_pattern(clean_2)
        if re.search(p2, s, flags=re.IGNORECASE):
            s = re.sub(p2, "", s, count=1, flags=re.IGNORECASE).strip()

    return s


def flush_line_marks(text_str: str) -> str:
    if not text_str:
        return ""
    s = sanitize_latex_text(text_str)
    if s.startswith(r"\begin") or s.startswith(r"\end") or s.startswith(r"\hline"):
        return s

    s = re.sub(r"\[(\d+)\s*marks?\]", r"[\1]", s, flags=re.IGNORECASE)

    mark_match = re.search(r"\s*((?:\[[^\]]+\]\s*)+)[\.]?$", s)
    if mark_match:
        raw_tags = mark_match.group(1).strip()
        body = s[: mark_match.start()].strip()
        formatted_tags = re.sub(r"\[([^\]]+)\]", r"\\textbf{[\1]}", raw_tags)

        if not body:
            return f"\\leavevmode\\hspace*{{\\fill}}\\mbox{{{formatted_tags}}}"
        return f"{body} \\hspace*{{\\fill}}\\mbox{{{formatted_tags}}}"

    return s


def normalize_subtopic_code(raw_code: str) -> str:
    code = str(raw_code).strip()
    return code if code else "IGCSE 0607"


def get_q_marks(q):
    return q.get("total_marks", q.get("marks", 0))


def natural_sort_key(s: str):
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r"(\d+)", str(s))]


def extract_grouped_subtopics(questions, syllabus_map, target_prefix="sec_target_"):
    subtopic_data = {}
    target_map = {}
    target_counter = 0

    for q in questions:
        raw_code = str(q.get("subtopic", "E1.1")).strip()
        code = normalize_subtopic_code(raw_code)
        paper_type = str(q.get("paper_type") or q.get("paper") or "Paper 4 (Calculator)").strip()

        key = (code, paper_type)
        if key not in target_map:
            target_counter += 1
            target_map[key] = f"{target_prefix}{target_counter}"

        if code not in subtopic_data:
            content = syllabus_map.get(code) or q.get("subtopic_title") or q.get("topic") or ""
            subtopic_data[code] = {
                "code": code,
                "content": sanitize_latex_text(content),
                "papers": {},
            }

        if paper_type not in subtopic_data[code]["papers"]:
            subtopic_data[code]["papers"][paper_type] = 0
        subtopic_data[code]["papers"][paper_type] += 1

    grouped_list = []
    sorted_subtopic_codes = sorted(subtopic_data.keys(), key=natural_sort_key)
    for code in sorted_subtopic_codes:
        item = subtopic_data[code]
        paper_info = []
        sorted_papers = sorted(item["papers"].keys(), key=natural_sort_key)
        for paper_name in sorted_papers:
            count = item["papers"][paper_name]
            paper_info.append({
                "paper": paper_name,
                "count": count,
                "target_id": target_map[(code, paper_name)],
            })
        grouped_list.append({
            "code": item["code"],
            "content": item["content"],
            "papers": paper_info,
        })

    return grouped_list, target_map


def build_cover_page_latex(
    title_str,
    subtitle_str,
    questions,
    syllabus_map,
    exam_date="",
    author_str="",
    total_marks_val=None,
    target_prefix="sec_target_",
    remove_nav=False,
):
    grouped_subtopics, _ = extract_grouped_subtopics(
        questions, syllabus_map, target_prefix=target_prefix
    )

    if total_marks_val is not None and int(total_marks_val) > 0:
        total_marks = int(total_marks_val)
    else:
        total_marks = sum(int(get_q_marks(q)) for q in questions)

    display_title = title_str.replace("_", " ")
    display_subtitle = subtitle_str.replace("_", " ") if subtitle_str else ""
    display_author = author_str.replace("_", " ") if author_str else ""

    subtitle_tex = (
        f"\\\\[0.2cm]\n    {{\\Large \\textbf{{{display_subtitle}}}}}"
        if display_subtitle
        else ""
    )
    author_tex = (
        f"\\\\[0.15cm]\n    {{\\large \\textit{{Author: {display_author}}}}}"
        if display_author
        else ""
    )
    date_display = f"\\textbf{{Date:}} {exam_date} \\quad | \\quad " if exam_date else ""

    header_box = f"""
\\begin{{center}}
    {{\\Huge \\textbf{{{display_title}}}}}{subtitle_tex}{author_tex}\\\\[0.4cm]
    {date_display}\\textbf{{Total Questions:}} {len(questions)} \\quad | \\quad 
    \\textbf{{Total Marks:}} {total_marks} Marks
\\end{{center}}
\\vspace{{0.4cm}}
\\hrule
\\vspace{{0.6cm}}
"""

    if remove_nav:
        return header_box

    tex = (
        header_box
        + """
\\section*{Table of Contents \\& Quick Navigation}
\\begin{itemize}[leftmargin=0.6cm, itemsep=0.6em]
"""
    )

    for item in grouped_subtopics:
        code = item["code"]
        content = item["content"]
        papers = item["papers"]

        header_text = f"\\textbf{{{code}}} {content}" if content else f"\\textbf{{{code}}}"
        paper_links = []
        for p in papers:
            p_name = p["paper"]
            p_count = p["count"]
            p_target = p["target_id"]
            q_label = "Q" if p_count == 1 else "Qs"
            paper_links.append(
                f"\\hyperlink{{{p_target}}}{{\\textbf{{{p_name}}}}} \\textit{{({p_count}"
                f" {q_label})}}"
            )

        papers_formatted = " \\quad \\cdot \\quad ".join(paper_links)
        tex += (
            f"    \\item {header_text}\\\\\n"
            f"          \\hspace*{{0.4cm}}\\small $\\rightarrow$ {papers_formatted}\n"
        )

    tex += """\\end{itemize}
\\vspace{0.8cm}
\\hrule
\\newpage
"""
    return tex


def clean_tikz_code(tikz_str: str) -> str:
    if not tikz_str:
        return ""
    clean_code = re.sub(LATEX_N_CMD_PATTERN, "\n", tikz_str.strip())
    if "\\begin{tikzpicture}" not in clean_code:
        clean_code = f"\\begin{{tikzpicture}}\n{clean_code}\n\\end{{tikzpicture}}"
    return clean_code


def sanitize_slot_label(label_str: str) -> str:
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


def parse_format_str_to_lines(fmt_str: str) -> list:
    if not fmt_str:
        return []
    s = str(fmt_str).strip()
    s = re.sub(LATEX_N_CMD_PATTERN, "\n", s)
    s = re.sub(r"\\\\(?:\s*\[[^\]]+\])?", "\n", s)
    return [line.strip() for line in s.split("\n") if line.strip()]


def format_custom_str_to_latex(
    fmt_str: str, marks_tag: str = "", default_box_w: str = "4.0cm"
) -> tuple:
    lines = parse_format_str_to_lines(fmt_str)
    if not lines:
        return "", 0

    num_lines = len(lines)
    processed_lines = []

    for line in lines:
        if r"\makebox" in line:
            line_tex = line
        elif re.search(r"\.{3,}", line):
            line_tex = re.sub(r"\.{3,}", lambda m: f"\\makebox[{default_box_w}]{{\\dotfill}}", line)
        elif r"\dotfill" in line:
            line_tex = re.sub(r"(?:\\noindent\s*)?\\dotfill", lambda m: f"\\makebox[{default_box_w}]{{\\dotfill}}", line)
        elif r"\dots" in line:
            line_tex = re.sub(r"(?:\\dots)+", lambda m: f"\\makebox[{default_box_w}]{{\\dotfill}}", line)
        else:
            line_tex = line

        processed_lines.append(sanitize_latex_text(line_tex))

    if num_lines == 1:
        return processed_lines[0], 1
    else:
        tabular_rows = " \\\\[0.45cm]\n".join(processed_lines)
        return tabular_rows, num_lines


def format_answer_slot_latex(
    ans_type,
    prefix="",
    suffix="",
    format_str="",
    marks=0,
    vspace_cm=None,
    prompt_text="",
) -> str:
    prefix_clean = sanitize_slot_label(prefix)
    suffix_clean = sanitize_slot_label(suffix)
    prefix_tex = f"{prefix_clean} " if prefix_clean else ""
    suffix_tex = f" {suffix_clean}" if suffix_clean else ""
    mark_tag = f"\\textbf{{[{marks}]}}" if marks > 0 else ""

    ans_t = str(ans_type).lower().strip()
    prompt_lower = str(prompt_text).lower()

    no_answer_line_patterns = [
        r"\bshow\s+that\b",
        r"\bsketch\b",
        r"\bcomplete\s+the\s+table\b",
        r"\bcomplete\s+the\s+diagram\b",
        r"\bshade\b",
        r"\bprove\s+that\b",
        r"\bcorrect\s+symbol\b",
        r"\binsert\s+symbol\b",
        r"\bwrite\s+down\s+the\s+correct\s+symbol\b",
        r"\binsert\s+the\s+correct\s+symbol\b",
        r"\bfill\s+in\s+the\s+symbol\b",
        r"\binsert\s+one\s+of\s+the\s+symbols\b",
        r"\bsymbol\s+from\s+the\s+list\b",
    ]
    is_symbol_prompt = (
        "symbol" in prompt_lower
        and any(w in prompt_lower for w in ["write down", "insert", "complete", "fill", "choose", "place"])
    )
    is_no_line_prompt = (
        any(re.search(pat, prompt_lower) for pat in no_answer_line_patterns)
        or is_symbol_prompt
    )

    fmt_lines = parse_format_str_to_lines(format_str if format_str else "")
    is_pure_multiline_dots = len(fmt_lines) > 1 and all(
        re.fullmatch(r"(?:\\noindent\s*)?(?:\\dotfill|\\dots+|\.{3,})\s*", line)
        for line in fmt_lines
    )

    if is_pure_multiline_dots:
        num_lines = len(fmt_lines)
        lines_tex = []
        for i in range(num_lines):
            is_last = (i == num_lines - 1)
            if is_last:
                lines_tex.append(f"\\makebox[\\dimexpr\\linewidth-1.5cm\\relax]{{\\dotfill}} \\hfill {mark_tag}")
            else:
                lines_tex.append("\\makebox[\\dimexpr\\linewidth-1.5cm\\relax]{{\\dotfill}}")

        return "\\par\\vspace{\\baselineskip}\n" + " \\\\[0.45cm]\n".join(lines_tex)

    if vspace_cm is not None and float(vspace_cm) >= 0:
        v_space = float(vspace_cm)
    else:
        v_space = (
            max(1.0, marks * 1.5)
            if ans_t not in ["null", "none"] and not is_no_line_prompt
            else 0.2
        )

    space_tex = (
        f"\\par\\vspace{{{v_space:.2f}cm}}\n"
        if v_space > 0
        else "\\par\\vspace{0.15cm}\n"
    )

    if ans_t in ["null", "none"] or is_no_line_prompt:
        if v_space > 0:
            return f"\\par\\vspace{{{v_space:.2f}cm}}\\leavevmode\\hspace*{{\\fill}} {mark_tag}"
        return f"\\leavevmode\\hspace*{{\\fill}} {mark_tag}"

    elif ans_t in ["vector", "matrix", "column_vector", "column", "vector_2d"]:
        return (
            f"{space_tex}\\leavevmode\\hspace*{{\\fill}}"
            f" {prefix_tex}$\\begin{{pmatrix}} \\makebox[1cm]{{}}"
            f" \\\\[1em] \\makebox[1cm]{{}} \\end{{pmatrix}}${suffix_tex} \\quad {mark_tag}"
        )

    elif ans_t in ["multi_field", "inline_fields", "multiple_fields", "two_fields", "dual_slot"]:
        box_w = "2.5cm"
        if format_str and str(format_str).strip():
            raw_parts = [p.strip() for p in format_str.split(",") if p.strip()]
            formatted_parts = []
            for part in raw_parts:
                clean_p = sanitize_slot_label(part)
                if ".." in clean_p or r"\dots" in clean_p or r"\dotfill" in clean_p:
                    expanded = re.sub(r"(?:\.{2,}|\\dots+|\\dotfill)", lambda m: f"\\makebox[{box_w}]{{\\dotfill}}", clean_p)
                    formatted_parts.append(expanded)
                else:
                    formatted_parts.append(f"{clean_p} \\makebox[{box_w}]{{\\dotfill}}")
            line_content = " \\quad , \\quad ".join(formatted_parts)
            return f"{space_tex}\\leavevmode\\hspace*{{\\fill}} {line_content} \\quad {mark_tag}"
        else:
            p_str = prefix_tex if prefix_tex else "Field 1 = "
            s_str = suffix_tex if suffix_tex else "Field 2 = "
            return (
                f"{space_tex}\\leavevmode\\hspace*{{\\fill}}"
                f" {p_str}\\makebox[{box_w}]{{\\dotfill}} \\quad , \\quad"
                f" {s_str}\\makebox[{box_w}]{{\\dotfill}} \\quad {mark_tag}"
            )

    elif format_str and str(format_str).strip():
        fmt_latex, num_lines = format_custom_str_to_latex(format_str, marks_tag=mark_tag)
        if num_lines > 1:
            return (
                f"{space_tex}\\leavevmode\\hspace*{{\\fill}}\\begin{{tabular}}[b]{{@{{}}r@{{}}}}\n{fmt_latex}\n\\end{{tabular}}"
                f" \\quad {mark_tag}"
            )
        else:
            return (
                f"{space_tex}\\leavevmode\\hspace*{{\\fill}}"
                f" {prefix_tex}{fmt_latex}{suffix_tex} \\quad {mark_tag}"
            )

    elif ans_t in ["multi_line", "multiple", "asymptotes", "two_lines"]:
        return (
            f"{space_tex}\\leavevmode\\hspace*{{\\fill}}\\begin{{tabular}}[b]{{@{{}}r@{{}}}}\n"
            f"{prefix_tex}\\makebox[4.0cm]{{\\dotfill}}{suffix_tex} \\\\[0.45cm]\n"
            f"{prefix_tex}\\makebox[4.0cm]{{\\dotfill}}{suffix_tex}\n"
            f"\\end{{tabular}} \\quad {mark_tag}"
        )

    elif ans_t in ["coordinate", "point", "coordinates"]:
        return (
            f"{space_tex}\\leavevmode\\hspace*{{\\fill}}"
            f" {prefix_tex}(\\makebox[1.5cm]{{\\dotfill}} \\, , \\,"
            f" \\makebox[1.5cm]{{\\dotfill}}){suffix_tex} \\quad {mark_tag}"
        )

    elif ans_t in ["compound", "time"]:
        return (
            f"{space_tex}\\leavevmode\\hspace*{{\\fill}}"
            f" \\makebox[2.5cm]{{\\dotfill}} \\text{{ hours }}"
            f" \\makebox[2.5cm]{{\\dotfill}} \\text{{ minutes}} \\quad {mark_tag}"
        )

    elif ans_t in ["custom", "inequality_region", "region"]:
        return (
            f"{space_tex}\\leavevmode\\hspace*{{\\fill}}\\begin{{tabular}}[b]{{@{{}}r@{{}}}}\n"
            f"{prefix_tex}\\makebox[5.0cm]{{\\dotfill}}{suffix_tex} \\\\[0.45cm]\n"
            f"{prefix_tex}\\makebox[5.0cm]{{\\dotfill}}{suffix_tex} \\\\[0.45cm]\n"
            f"{prefix_tex}\\makebox[5.0cm]{{\\dotfill}}{suffix_tex}\n"
            f"\\end{{tabular}} \\quad {mark_tag}"
        )

    elif ans_t in ["short", "short_line", "single_value"]:
        return f"{space_tex}\\leavevmode\\hspace*{{\\fill}} {prefix_tex}\\makebox[3.0cm]{{\\dotfill}}{suffix_tex} \\quad {mark_tag}"

    elif ans_t in ["long", "long_line", "ordering", "list"]:
        return f"{space_tex}\\leavevmode\\hspace*{{\\fill}} {prefix_tex}\\makebox[8.5cm]{{\\dotfill}}{suffix_tex} \\quad {mark_tag}"

    else:
        return f"{space_tex}\\leavevmode\\hspace*{{\\fill}} {prefix_tex}\\makebox[4.5cm]{{\\dotfill}}{suffix_tex} \\quad {mark_tag}"


def format_question_body(q, idx=None, needspace=None):
    tex = ""
    q_num_str = str(idx) if idx is not None else ""
    q_label_used = False if q_num_str else True

    raw_intro = q.get("intro_text") or q.get("question") or ""
    intro_text = split_latex_sentences(sanitize_latex_text(raw_intro))

    intro_tikz = clean_tikz_code(q.get("intro_diagram_tikz") or q.get("diagram_tikz") or "")

    if intro_text:
        if not q_label_used:
            tex += f"\\qlabel{{{q_num_str}}} {intro_text}\n\n"
            q_label_used = True
        else:
            tex += f"{intro_text}\n\n"

    if intro_tikz:
        tex += f"\\begin{{center}}\n{intro_tikz}\n\\end{{center}}\n\n"

    parts = q.get("parts", [])
    for part in parts:
        if needspace is not None and float(needspace) > 0:
            part_ns = max(2.0, float(needspace) * 0.75)
            tex += f"\\needspace{{{part_ns:.1f}cm}}\n"

        p_label = part.get("label", "").strip()
        p_context = split_latex_sentences(sanitize_latex_text(part.get("context", "")))
        p_diagram = clean_tikz_code(part.get("diagram_tikz", ""))
        p_prompt = split_latex_sentences(sanitize_latex_text(part.get("prompt", "")))
        p_marks = part.get("marks", 0)
        p_ans_type = part.get("answer_type", "line")
        p_prefix = part.get("answer_prefix", "")
        p_suffix = part.get("answer_suffix", "")
        p_format = part.get("answer_format", "")
        p_vspace = part.get("vspace_cm", None)
        subparts = part.get("subparts", [])

        p_context = strip_redundant_label(p_context, p_label)
        p_prompt = strip_redundant_label(p_prompt, p_label)

        if not p_context and not p_prompt and subparts and p_label:
            if p_diagram:
                tex += f"\\begin{{center}}\n{p_diagram}\n\\end{{center}}\n\n"

            for sp_idx, subpart in enumerate(subparts):
                if needspace is not None and float(needspace) > 0:
                    sp_ns = max(1.5, float(needspace) * 0.5)
                    tex += f"\\needspace{{{sp_ns:.1f}cm}}\n"

                sp_label = subpart.get("label", "").strip()
                sp_context = split_latex_sentences(sanitize_latex_text(subpart.get("context", "")))
                sp_diagram = clean_tikz_code(subpart.get("diagram_tikz", ""))
                sp_prompt = split_latex_sentences(sanitize_latex_text(subpart.get("prompt", "")))
                sp_marks = subpart.get("marks", 0)
                sp_ans_type = subpart.get("answer_type", "line")
                sp_prefix = subpart.get("answer_prefix", "")
                sp_suffix = subpart.get("answer_suffix", "")
                sp_format = subpart.get("answer_format", "")
                sp_vspace = subpart.get("vspace_cm", None)

                sp_context = strip_redundant_label(sp_context, p_label, sp_label)
                sp_prompt = strip_redundant_label(sp_prompt, p_label, sp_label)

                slot_tex = format_answer_slot_latex(
                    sp_ans_type, sp_prefix, sp_suffix, sp_format, sp_marks, sp_vspace, prompt_text=sp_prompt
                )

                if sp_context or sp_prompt:
                    sp_text = (
                        f"{sp_context}\n\n{sp_prompt}" if (sp_context and sp_prompt) else (sp_context or sp_prompt)
                    )
                    if not q_label_used:
                        if sp_idx == 0:
                            tex += f"\\qsubpartalpharoman{{{q_num_str}}}{{{p_label}}}{{{sp_label}}} {sp_text}\n"
                        else:
                            tex += f"\\qsubpartroman{{{q_num_str}}}{{{sp_label}}} {sp_text}\n"
                        q_label_used = True
                    else:
                        if sp_idx == 0:
                            tex += f"\\subpartalpharoman{{{p_label}}}{{{sp_label}}} {sp_text}\n"
                        else:
                            if sp_label:
                                tex += f"\\subpartroman{{{sp_label}}} {sp_text}\n"
                            else:
                                tex += f"{sp_text}\n"

                if sp_diagram:
                    tex += f"\\begin{{center}}\n{sp_diagram}\n\\end{{center}}\n\n"

                tex += f"{slot_tex}\n\n"

        else:
            p_slot_tex = format_answer_slot_latex(
                p_ans_type, p_prefix, p_suffix, p_format, p_marks if not subparts else 0, p_vspace, prompt_text=p_prompt
            )

            if p_context or p_prompt:
                p_text = f"{p_context}\n\n{p_prompt}" if (p_context and p_prompt) else (p_context or p_prompt)
                if not q_label_used:
                    if p_label:
                        tex += f"\\qsubpartalpha{{{q_num_str}}}{{{p_label}}} {p_text}\n"
                    else:
                        tex += f"\\qlabel{{{q_num_str}}} {p_text}\n"
                    q_label_used = True
                else:
                    if p_label:
                        tex += f"\\subpartalpha{{{p_label}}} {p_text}\n"
                    else:
                        tex += f"{p_text}\n"

            if p_diagram:
                tex += f"\\begin{{center}}\n{p_diagram}\n\\end{{center}}\n\n"

            if not subparts:
                tex += f"{p_slot_tex}\n\n"

            for subpart in subparts:
                if needspace is not None and float(needspace) > 0:
                    sp_ns = max(1.5, float(needspace) * 0.5)
                    tex += f"\\needspace{{{sp_ns:.1f}cm}}\n"

                sp_label = subpart.get("label", "").strip()
                sp_context = split_latex_sentences(sanitize_latex_text(subpart.get("context", "")))
                sp_diagram = clean_tikz_code(subpart.get("diagram_tikz", ""))
                sp_prompt = split_latex_sentences(sanitize_latex_text(subpart.get("prompt", "")))
                sp_marks = subpart.get("marks", 0)
                sp_ans_type = subpart.get("answer_type", "line")
                sp_prefix = subpart.get("answer_prefix", "")
                sp_suffix = subpart.get("answer_suffix", "")
                sp_format = subpart.get("answer_format", "")
                sp_vspace = subpart.get("vspace_cm", None)

                sp_context = strip_redundant_label(sp_context, p_label, sp_label)
                sp_prompt = strip_redundant_label(sp_prompt, p_label, sp_label)

                sp_slot_tex = format_answer_slot_latex(
                    sp_ans_type, sp_prefix, sp_suffix, sp_format, sp_marks, sp_vspace, prompt_text=sp_prompt
                )

                if sp_context or sp_prompt:
                    sp_text = (
                        f"{sp_context}\n\n{sp_prompt}" if (sp_context and sp_prompt) else (sp_context or sp_prompt)
                    )
                    if not q_label_used:
                        if sp_label:
                            tex += f"\\qsubpartroman{{{q_num_str}}}{{{sp_label}}} {sp_text}\n"
                        else:
                            tex += f"\\qlabel{{{q_num_str}}} {sp_text}\n"
                        q_label_used = True
                    else:
                        if sp_label:
                            tex += f"\\subpartroman{{{sp_label}}} {sp_text}\n"
                        else:
                            tex += f"{sp_text}\n"

                if sp_diagram:
                    tex += f"\\begin{{center}}\n{sp_diagram}\n\\end{{center}}\n\n"

                tex += f"{sp_slot_tex}\n\n"

    if not q_label_used:
        tex = f"\\qlabel{{{q_num_str}}}\n" + tex

    return tex


def format_markscheme_body(q, idx=None):
    tex = ""
    q_num_str = str(idx) if idx is not None else ""
    q_label_used = False if q_num_str else True

    parts = q.get("parts", [])

    if parts:
        for p_idx, part in enumerate(parts):
            p_label = part.get("label", "").strip()
            p_ms_steps = split_markscheme_arrow_steps(part.get("markscheme", []))
            subparts = part.get("subparts", [])

            if p_ms_steps:
                for step_i, step in enumerate(p_ms_steps):
                    cleaned_step = strip_redundant_label(step, p_label)
                    clean_step = flush_line_marks(cleaned_step)
                    if not clean_step:
                        continue

                    if step_i == 0:
                        if not q_label_used:
                            if p_label:
                                tex += f"\\par\\vspace{{0.20cm}}\\leftskip=1.70cm\\noindent\\hspace*{{-3.10cm}}\\makebox[1.00cm][r]{{\\textbf{{{q_num_str}}}}}\\hspace*{{0.40cm}}\\makebox[0.85cm][l]{{\\textbf{{{p_label}}}}}\\hspace*{{0.85cm}}{clean_step}\n"
                            else:
                                tex += f"\\par\\vspace{{0.20cm}}\\leftskip=1.70cm\\noindent\\hspace*{{-3.10cm}}\\makebox[1.00cm][r]{{\\textbf{{{q_num_str}}}}}\\hspace*{{2.10cm}}{clean_step}\n"
                            q_label_used = True
                        else:
                            if p_label:
                                tex += f"\\par\\vspace{{0.15cm}}\\leftskip=1.70cm\\noindent\\hspace*{{-1.70cm}}\\makebox[0.85cm][l]{{\\textbf{{{p_label}}}}}\\hspace*{{0.85cm}}{clean_step}\n"
                            else:
                                tex += f"\\par\\vspace{{0.05cm}}\\leftskip=1.70cm\\noindent {clean_step}\n"
                    else:
                        tex += f"\\par\\vspace{{0.05cm}}\\leftskip=1.70cm\\noindent {clean_step}\n"

            for sp_idx, subpart in enumerate(subparts):
                sp_label = subpart.get("label", "").strip()
                sp_ms_steps = split_markscheme_arrow_steps(subpart.get("markscheme", []))

                if sp_ms_steps:
                    for step_i, step in enumerate(sp_ms_steps):
                        cleaned_step = strip_redundant_label(step, p_label, sp_label)
                        clean_step = flush_line_marks(cleaned_step)
                        if not clean_step:
                            continue

                        if step_i == 0:
                            if not q_label_used:
                                if p_label and sp_label and sp_idx == 0 and not p_ms_steps:
                                    tex += f"\\par\\vspace{{0.20cm}}\\leftskip=1.70cm\\noindent\\hspace*{{-3.10cm}}\\makebox[1.00cm][r]{{\\textbf{{{q_num_str}}}}}\\hspace*{{0.40cm}}\\makebox[0.85cm][l]{{\\textbf{{{p_label}}}}}\\makebox[0.85cm][l]{{\\textbf{{{sp_label}}}}}{clean_step}\n"
                                elif sp_label:
                                    tex += f"\\par\\vspace{{0.20cm}}\\leftskip=1.70cm\\noindent\\hspace*{{-3.10cm}}\\makebox[1.00cm][r]{{\\textbf{{{q_num_str}}}}}\\hspace*{{1.25cm}}\\makebox[0.85cm][l]{{\\textbf{{{sp_label}}}}}{clean_step}\n"
                                elif p_label:
                                    tex += f"\\par\\vspace{{0.20cm}}\\leftskip=1.70cm\\noindent\\hspace*{{-3.10cm}}\\makebox[1.00cm][r]{{\\textbf{{{q_num_str}}}}}\\hspace*{{0.40cm}}\\makebox[0.85cm][l]{{\\textbf{{{p_label}}}}}\\hspace*{{0.85cm}}{clean_step}\n"
                                else:
                                    tex += f"\\par\\vspace{{0.20cm}}\\leftskip=1.70cm\\noindent\\hspace*{{-3.10cm}}\\makebox[1.00cm][r]{{\\textbf{{{q_num_str}}}}}\\hspace*{{2.10cm}}{clean_step}\n"
                                q_label_used = True
                            else:
                                if p_label and sp_label and sp_idx == 0 and not p_ms_steps:
                                    tex += f"\\par\\vspace{{0.15cm}}\\leftskip=1.70cm\\noindent\\hspace*{{-1.70cm}}\\makebox[0.85cm][l]{{\\textbf{{{p_label}}}}}\\makebox[0.85cm][l]{{\\textbf{{{sp_label}}}}}{clean_step}\n"
                                elif sp_label:
                                    tex += f"\\par\\vspace{{0.10cm}}\\leftskip=1.70cm\\noindent\\hspace*{{-0.85cm}}\\makebox[0.85cm][l]{{\\textbf{{{sp_label}}}}}{clean_step}\n"
                                else:
                                    tex += f"\\par\\vspace{{0.05cm}}\\leftskip=1.70cm\\noindent {clean_step}\n"
                        else:
                            tex += f"\\par\\vspace{{0.05cm}}\\leftskip=1.70cm\\noindent {clean_step}\n"

    elif q.get("markscheme"):
        ms_steps = split_markscheme_arrow_steps(q.get("markscheme", []))
        for step_i, step in enumerate(ms_steps):
            clean_step = flush_line_marks(step)
            if not clean_step:
                continue

            if step_i == 0:
                if not q_label_used:
                    tex += f"\\par\\vspace{{0.20cm}}\\leftskip=1.70cm\\noindent\\hspace*{{-3.10cm}}\\makebox[1.00cm][r]{{\\textbf{{{q_num_str}}}}}\\hspace*{{2.10cm}}{clean_step}\n"
                    q_label_used = True
                else:
                    tex += f"\\par\\vspace{{0.05cm}}\\leftskip=1.70cm\\noindent {clean_step}\n"
            else:
                tex += f"\\par\\vspace{{0.05cm}}\\leftskip=1.70cm\\noindent {clean_step}\n"

    if not q_label_used:
        tex = f"\\qlabel{{{q_num_str}}}\n" + tex

    return tex


def build_question_paper_latex(
    questions,
    syllabus_map,
    title_name,
    subtitle_name="",
    exam_date="",
    author_name="",
    total_marks_val=None,
    remove_nav=False,
    clean_header=False,
    needspace=None,
):
    tex = LATEX_PREAMBLE
    tex += build_cover_page_latex(
        title_name,
        subtitle_name,
        questions,
        syllabus_map,
        exam_date=exam_date,
        author_str=author_name,
        total_marks_val=total_marks_val,
        target_prefix="qp_sec_",
        remove_nav=remove_nav,
    )
    grouped_subtopics, target_map = extract_grouped_subtopics(
        questions, syllabus_map, target_prefix="qp_sec_"
    )

    current_section = None
    bookmark_count = 0

    for idx, q in enumerate(questions, 1):
        if idx > 1:
            if needspace is not None and float(needspace) > 0:
                tex += f"\n\\par\\vspace{{0.6cm}}\n\\needspace{{{float(needspace):.1f}cm}}\n"
            else:
                tex += "\\newpage\n"
        elif needspace is not None and float(needspace) > 0:
            tex += f"\\needspace{{{float(needspace):.1f}cm}}\n"

        paper_type = str(q.get("paper_type") or q.get("paper") or "Paper 4 (Calculator)").strip()
        subtopic_code = normalize_subtopic_code(str(q.get("subtopic", "E1.1")))
        marks = get_q_marks(q)
        section_key = (subtopic_code, paper_type)

        if clean_header:
            tex += "\n\\fancyhead[L]{}\n"
            tex += "\\fancyhead[C]{\\thepage}\n"
            tex += "\\fancyhead[R]{}\n"
            tex += "\\renewcommand{\\headrulewidth}{0pt}\n"
        else:
            tex += f"\n\\fancyhead[L]{{\\textbf{{[{subtopic_code}]}} \\quad {paper_type}}}\n"
            tex += "\\fancyhead[C]{\\thepage}\n"
            tex += f"\\fancyhead[R]{{\\textbf{{[Total Marks: {marks}]}}}}\n"
            tex += "\\renewcommand{\\headrulewidth}{0.4pt}\n"

        tex += "\n\\begingroup\\raggedright\\setlength{\\parindent}{0pt}\\leftskip=0pt\n"

        if section_key != current_section:
            current_section = section_key
            bookmark_count += 1
            target_id = target_map.get(section_key, f"qp_sec_{bookmark_count}")
            content = sanitize_latex_text(
                syllabus_map.get(subtopic_code) or q.get("subtopic_title") or q.get("topic") or ""
            )
            bookmark_tex = (
                f"{subtopic_code} {content} ({paper_type})" if content else f"{subtopic_code} ({paper_type})"
            )
            bookmark_plain = re.sub(r"\$([^$]+)\$", r"\1", bookmark_tex).replace("\\", "")

            if not remove_nav:
                tex += f"\\pdfbookmark[1]{{\\texorpdfstring{{{bookmark_tex}}}{{{bookmark_plain}}}}}{{qp_section_{bookmark_count}}}\n"
            tex += f"\\hypertarget{{{target_id}}}{{}}\n"

        q_body = format_question_body(q, idx=idx, needspace=needspace)

        tex += f"\\hypertarget{{q_{idx}}}{{}}\n"
        tex += f"\n\\noindent\n{q_body}\n\n"
        tex += "\\endgroup\n"

    tex += "\n\\end{document}"
    return tex


def build_markscheme_latex(
    questions,
    syllabus_map,
    title_name,
    subtitle_name="",
    exam_date="",
    author_name="",
    total_marks_val=None,
    remove_nav=False,
    clean_header=False,
    needspace=None,
):
    tex = LATEX_PREAMBLE
    grouped_subtopics, target_map = extract_grouped_subtopics(
        questions, syllabus_map, target_prefix="ms_sec_"
    )

    current_section = None
    bookmark_count = 0

    for idx, q in enumerate(questions, 1):
        if idx > 1:
            if needspace is not None and float(needspace) > 0:
                tex += f"\n\\par\\vspace{{0.4cm}}\n\\needspace{{{max(2.0, float(needspace)*0.5):.1f}cm}}\n"
        elif needspace is not None and float(needspace) > 0:
            tex += f"\\needspace{{{max(2.0, float(needspace)*0.5):.1f}cm}}\n"

        subtopic_code = normalize_subtopic_code(str(q.get("subtopic", "E1.1")))
        marks = get_q_marks(q)
        paper_type = str(q.get("paper_type") or q.get("paper") or "Paper 4 (Calculator)").strip()
        section_key = (subtopic_code, paper_type)

        if clean_header:
            tex += "\n\\fancyhead[L]{}\n"
            tex += "\\fancyhead[C]{\\thepage}\n"
            tex += "\\fancyhead[R]{}\n"
            tex += "\\renewcommand{\\headrulewidth}{0pt}\n"
        else:
            tex += "\n\\fancyhead[L]{}\n"
            tex += "\\fancyhead[C]{\\thepage}\n"
            tex += "\\fancyhead[R]{}\n"
            tex += "\\renewcommand{\\headrulewidth}{0.4pt}\n"

        tex += "\n\\begingroup\\raggedright\\setlength{\\parindent}{0pt}\\leftskip=0pt\n"

        if section_key != current_section:
            current_section = section_key
            bookmark_count += 1
            target_id = target_map.get(section_key, f"ms_sec_{bookmark_count}")
            content = sanitize_latex_text(
                syllabus_map.get(subtopic_code) or q.get("subtopic_title") or q.get("topic") or ""
            )
            bookmark_tex = (
                f"{subtopic_code} {content} ({paper_type})" if content else f"{subtopic_code} ({paper_type})"
            )
            bookmark_plain = re.sub(r"\$([^$]+)\$", r"\1", bookmark_tex).replace("\\", "")

            if not remove_nav:
                tex += f"\\pdfbookmark[1]{{\\texorpdfstring{{{bookmark_tex}}}{{{bookmark_plain}}}}}{{ms_section_{bookmark_count}}}\n"
            tex += f"\\hypertarget{{{target_id}}}{{}}\n"

        ms_body = format_markscheme_body(q, idx=idx)
        tex += f"\n{ms_body}\n"
        tex += f"\\par\\vspace{{0.10cm}}\\leavevmode\\hspace*{{\\fill}}\\textit{{[Total Marks: {marks}]}}\n"
        tex += "\\vspace{0.8cm}\n\\endgroup\n"

    tex += "\n\\end{document}"
    return tex


def build_combined_latex(
    questions,
    syllabus_map,
    title_name,
    subtitle_name="",
    exam_date="",
    author_name="",
    total_marks_val=None,
    remove_nav=False,
    clean_header=False,
    needspace=None,
):
    tex = LATEX_PREAMBLE
    tex += build_cover_page_latex(
        f"{title_name} — Combined Questions \\& Markscheme",
        subtitle_name,
        questions,
        syllabus_map,
        exam_date=exam_date,
        author_str=author_name,
        total_marks_val=total_marks_val,
        target_prefix="comb_qp_sec_",
        remove_nav=remove_nav,
    )
    grouped_subtopics, target_map = extract_grouped_subtopics(
        questions, syllabus_map, target_prefix="comb_qp_sec_"
    )

    current_section = None
    bookmark_count = 0

    # Questions Section
    for idx, q in enumerate(questions, 1):
        if idx > 1:
            if needspace is not None and float(needspace) > 0:
                tex += f"\n\\par\\vspace{{0.6cm}}\n\\needspace{{{float(needspace):.1f}cm}}\n"
            else:
                tex += "\\newpage\n"
        elif needspace is not None and float(needspace) > 0:
            tex += f"\\needspace{{{float(needspace):.1f}cm}}\n"

        paper_type = str(q.get("paper_type") or q.get("paper") or "Paper 4 (Calculator)").strip()
        subtopic_code = normalize_subtopic_code(str(q.get("subtopic", "E1.1")))
        marks = get_q_marks(q)
        section_key = (subtopic_code, paper_type)

        ms_link = f"\\hyperlink{{ms_q_{idx}}}{{\\textbf{{[Markscheme]}}}}"

        if clean_header:
            tex += "\n\\fancyhead[L]{}\n"
            tex += "\\fancyhead[C]{\\thepage}\n"
            tex += "\\fancyhead[R]{}\n"
            tex += "\\renewcommand{\\headrulewidth}{0pt}\n"
        else:
            tex += f"\n\\fancyhead[L]{{\\textbf{{[{subtopic_code}]}} \\quad {paper_type}}}\n"
            tex += "\\fancyhead[C]{\\thepage}\n"
            tex += f"\\fancyhead[R]{{{ms_link} \\quad \\textbf{{[Total Marks: {marks}]}}}}\n"
            tex += "\\renewcommand{\\headrulewidth}{0.4pt}\n"

        tex += "\n\\begingroup\\raggedright\\setlength{\\parindent}{0pt}\\leftskip=0pt\n"

        if section_key != current_section:
            current_section = section_key
            bookmark_count += 1
            target_id = target_map.get(section_key, f"comb_qp_sec_{bookmark_count}")
            content = sanitize_latex_text(
                syllabus_map.get(subtopic_code) or q.get("subtopic_title") or q.get("topic") or ""
            )
            bookmark_tex = (
                f"{subtopic_code} {content} ({paper_type})" if content else f"{subtopic_code} ({paper_type})"
            )
            bookmark_plain = re.sub(r"\$([^$]+)\$", r"\1", bookmark_tex).replace("\\", "")

            if not remove_nav:
                tex += f"\\pdfbookmark[1]{{\\texorpdfstring{{{bookmark_tex}}}{{{bookmark_plain}}}}}{{comb_section_{bookmark_count}}}\n"
            tex += f"\\hypertarget{{{target_id}}}{{}}\n"

        q_body = format_question_body(q, idx=idx, needspace=needspace)

        tex += f"\\hypertarget{{q_{idx}}}{{}}\n"
        tex += f"\n{q_body}\n\n"
        tex += "\\endgroup\n"

    # Markschemes Section
    tex += "\n\\newpage\n"
    current_section = None

    for idx, q in enumerate(questions, 1):
        if idx > 1 and needspace is not None and float(needspace) > 0:
            tex += f"\n\\par\\vspace{{0.4cm}}\n\\needspace{{{max(2.0, float(needspace)*0.5):.1f}cm}}\n"

        subtopic_code = normalize_subtopic_code(str(q.get("subtopic", "E1.1")))
        marks = get_q_marks(q)
        paper_type = str(q.get("paper_type") or q.get("paper") or "Paper 4 (Calculator)").strip()
        section_key = (subtopic_code, paper_type)

        if clean_header:
            tex += "\n\\fancyhead[L]{}\n"
            tex += "\\fancyhead[C]{\\thepage}\n"
            tex += "\\fancyhead[R]{}\n"
            tex += "\\renewcommand{\\headrulewidth}{0pt}\n"
        else:
            tex += "\n\\fancyhead[L]{}\n"
            tex += "\\fancyhead[C]{\\thepage}\n"
            tex += "\\fancyhead[R]{}\n"
            tex += "\\renewcommand{\\headrulewidth}{0.4pt}\n"

        tex += "\n\\begingroup\\raggedright\\setlength{\\parindent}{0pt}\\leftskip=0pt\n"

        if section_key != current_section:
            current_section = section_key

        tex += f"\\hypertarget{{ms_q_{idx}}}{{}}\n"
        ms_body = format_markscheme_body(q, idx=idx)
        tex += f"\n{ms_body}\n"

        back_link = f"\\hyperlink{{q_{idx}}}{{\\textbf{{[Back to Question]}}}}"
        tex += f"\\par\\vspace{{0.10cm}}\\leavevmode\\hspace*{{\\fill}}{back_link} \\quad \\textit{{[Total Marks: {marks}]}}\n"
        tex += "\\vspace{0.8cm}\n\\endgroup\n"

    tex += "\n\\end{document}"
    return tex


def compile_pdflatex(tex_content, output_pdf_filename, num_questions=0):
    dynamic_timeout = int(45 + (num_questions * 0.2))

    with tempfile.TemporaryDirectory() as tmpdir:
        tex_path = os.path.join(tmpdir, "document.tex")
        pdf_path = os.path.join(tmpdir, "document.pdf")

        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(tex_content)

        print(f"⏳ Compiling `{output_pdf_filename}` via pdflatex (timeout={dynamic_timeout}s)...")
        try:
            for _ in range(2):
                result = subprocess.run(
                    [
                        "pdflatex",
                        "-interaction=nonstopmode",
                        "-output-directory",
                        tmpdir,
                        tex_path,
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=dynamic_timeout,
                )

            if os.path.exists(pdf_path):
                shutil.move(pdf_path, output_pdf_filename)
                print(f"✅ Successfully compiled: `{output_pdf_filename}`")
            else:
                print(f"❌ Failed to generate `{output_pdf_filename}`. pdflatex output:")
                print(result.stdout.decode("utf-8", errors="ignore")[-1000:])

        except FileNotFoundError:
            print("❌ Error: `pdflatex` is not installed or not in PATH.")
        except subprocess.TimeoutExpired:
            print(f"⏰ pdflatex timed out after {dynamic_timeout} seconds.")
        except Exception as e:
            print(f"❌ Error compiling PDF: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Compile master_bank.json questions into IGCSE Mathematics PDF via pdflatex."
    )
    parser.add_argument("--input", default=MASTER_BANK_FILE, help="Path to input JSON file.")
    parser.add_argument("--output", "-o", default=None, help="Path to output PDF file.")
    parser.add_argument("--select", "-s", type=str, default=None, help="Select specific question numbers/ranges (e.g. 1-3, 5).")
    parser.add_argument("--name", "-n", type=str, default=None, help="Paper title/name used for cover pages and file naming.")
    parser.add_argument("--title", type=str, default=None, help="Document title string.")
    parser.add_argument("--subtitle", type=str, default=None, help="Document subtitle string.")
    parser.add_argument("--author", type=str, default="", help="Document author string.")
    parser.add_argument("--date", "-d", type=str, default=None, help="Exam date string.")
    parser.add_argument("--total-marks", type=int, default=None, help="Total marks override.")
    parser.add_argument("--mode", "-m", choices=["all", "qp", "ms", "combined"], default=None, help="Select output target.")
    parser.add_argument("--remove-nav", action="store_true", help="Remove section navigation links and cover page ToC.")
    parser.add_argument("--clean-header", action="store_true", help="Clean running header (page number only, no rule/text).")
    parser.add_argument(
        "--needspace",
        type=float,
        nargs="?",
        const=4.0,
        default=None,
        help="Optional needspace value in cm for continuous layout (e.g. --needspace 4.0). If omitted, defaults to page break per question."
    )
    parser.add_argument("--combined", action="store_true", help="Combined PDF export mode.")
    parser.add_argument("--include-markscheme", action="store_true", help="Include markscheme in PDF output.")
    parser.add_argument(
        "--bytopic",
        action="store_true",
        help="Group questions by subtopic and compile qp, ms, and combined PDFs into exports/bytopic/ folder in parallel."
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=os.cpu_count() or 4,
        help="Max worker threads to use for parallel compilation (defaults to CPU count)."
    )

    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"❌ Error: `{args.input}` not found.")
        exit(1)

    syllabus_map = load_syllabus_map(SYLLABUS_FILE)

    with open(args.input, "r", encoding="utf-8") as f:
        loaded_data = json.load(f)

    if isinstance(loaded_data, dict) and "questions" in loaded_data:
        master_questions = loaded_data.get("questions", [])
        paper_date = loaded_data.get("exam_date") or args.date or ""
    elif isinstance(loaded_data, list):
        master_questions = loaded_data
        paper_date = args.date or ""
    else:
        master_questions = []
        paper_date = args.date or ""

    if not master_questions:
        print(f"⚠️ `{args.input}` is empty or contains no valid questions.")
        exit(0)

    total_master_count = len(master_questions)

    if args.select:
        selected_indices = parse_index_ranges(args.select, total_master_count)
        if not selected_indices:
            print("❌ Error: No valid questions matched your range selection.")
            exit(1)
        target_questions = [master_questions[i] for i in selected_indices]
    else:
        target_questions = master_questions

    paper_title = args.title or args.name or "IGCSE 0607 Practice Questions"
    paper_subtitle = args.subtitle or ""
    paper_author = args.author or ""
    paper_prefix = paper_title.replace(" ", "_")

    if args.bytopic:
        bytopic_dir = os.path.join(EXPORTS_DIR, "bytopic")
        os.makedirs(bytopic_dir, exist_ok=True)

        subtopic_groups = {}
        for q in target_questions:
            code = normalize_subtopic_code(str(q.get("subtopic", "E1.1")))
            subtopic_groups.setdefault(code, []).append(q)

        sorted_codes = sorted(subtopic_groups.keys(), key=natural_sort_key)
        print(f"📁 Processing {len(sorted_codes)} subtopics concurrently ({args.workers} worker threads) into `{bytopic_dir}`...\n")

        def compile_subtopic_files(sub_code):
            sub_qs = subtopic_groups[sub_code]
            raw_title = (
                syllabus_map.get(sub_code)
                or sub_qs[0].get("subtopic_title")
                or sub_qs[0].get("topic")
                or ""
            )
            base_file_name = f"{sub_code} {raw_title}".strip() if raw_title else sub_code
            safe_base_name = sanitize_filename(base_file_name)
            full_sub_title = f"{paper_title} — {sub_code}" if sub_code else paper_title

            # 1. Question Paper (QP)
            qp_filename = os.path.join(bytopic_dir, f"{safe_base_name} QP.pdf")
            qp_tex = build_question_paper_latex(
                sub_qs,
                syllabus_map,
                title_name=full_sub_title,
                subtitle_name=raw_title,
                exam_date=paper_date,
                author_name=paper_author,
                total_marks_val=None,
                remove_nav=args.remove_nav,
                clean_header=args.clean_header,
                needspace=args.needspace,
            )
            compile_pdflatex(qp_tex, qp_filename, num_questions=len(sub_qs))

            # 2. Markscheme (MS)
            ms_filename = os.path.join(bytopic_dir, f"{safe_base_name} MS.pdf")
            ms_tex = build_markscheme_latex(
                sub_qs,
                syllabus_map,
                title_name=full_sub_title,
                subtitle_name=raw_title,
                exam_date=paper_date,
                author_name=paper_author,
                total_marks_val=None,
                remove_nav=args.remove_nav,
                clean_header=args.clean_header,
                needspace=args.needspace,
            )
            compile_pdflatex(ms_tex, ms_filename, num_questions=len(sub_qs))

            # 3. Combined
            combined_filename = os.path.join(bytopic_dir, f"{safe_base_name} Combined.pdf")
            combined_tex = build_combined_latex(
                sub_qs,
                syllabus_map,
                title_name=full_sub_title,
                subtitle_name=raw_title,
                exam_date=paper_date,
                author_name=paper_author,
                total_marks_val=None,
                remove_nav=args.remove_nav,
                clean_header=args.clean_header,
                needspace=args.needspace,
            )
            compile_pdflatex(combined_tex, combined_filename, num_questions=len(sub_qs))

        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = [executor.submit(compile_subtopic_files, code) for code in sorted_codes]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    print(f"❌ Error during subtopic compilation: {e}")

    elif args.output:
        if args.mode == "combined" or args.combined:
            tex_content = build_combined_latex(
                target_questions,
                syllabus_map,
                title_name=paper_title,
                subtitle_name=paper_subtitle,
                exam_date=paper_date,
                author_name=paper_author,
                total_marks_val=args.total_marks,
                remove_nav=args.remove_nav,
                clean_header=args.clean_header,
                needspace=args.needspace,
            )
        elif args.mode == "ms":
            tex_content = build_markscheme_latex(
                target_questions,
                syllabus_map,
                title_name=paper_title,
                subtitle_name=paper_subtitle,
                exam_date=paper_date,
                author_name=paper_author,
                total_marks_val=args.total_marks,
                remove_nav=args.remove_nav,
                clean_header=args.clean_header,
                needspace=args.needspace,
            )
        else:
            tex_content = build_question_paper_latex(
                target_questions,
                syllabus_map,
                title_name=paper_title,
                subtitle_name=paper_subtitle,
                exam_date=paper_date,
                author_name=paper_author,
                total_marks_val=args.total_marks,
                remove_nav=args.remove_nav,
                clean_header=args.clean_header,
                needspace=args.needspace,
            )

        compile_pdflatex(tex_content, args.output, num_questions=len(target_questions))
    else:
        target_mode = args.mode or ("combined" if args.combined else "all")
        tasks = []

        if target_mode in ["all", "qp"]:
            qp_filename = os.path.join(EXPORTS_DIR, f"{paper_prefix}_qp.pdf")
            qp_tex = build_question_paper_latex(
                target_questions,
                syllabus_map,
                title_name=paper_title,
                subtitle_name=paper_subtitle,
                exam_date=paper_date,
                author_name=paper_author,
                total_marks_val=args.total_marks,
                remove_nav=args.remove_nav,
                clean_header=args.clean_header,
                needspace=args.needspace,
            )
            tasks.append((qp_tex, qp_filename))

        if target_mode in ["all", "ms"]:
            ms_filename = os.path.join(EXPORTS_DIR, f"{paper_prefix}_ms.pdf")
            ms_tex = build_markscheme_latex(
                target_questions,
                syllabus_map,
                title_name=paper_title,
                subtitle_name=paper_subtitle,
                exam_date=paper_date,
                author_name=paper_author,
                total_marks_val=args.total_marks,
                remove_nav=args.remove_nav,
                clean_header=args.clean_header,
                needspace=args.needspace,
            )
            tasks.append((ms_tex, ms_filename))

        if target_mode in ["all", "combined"]:
            combined_filename = os.path.join(EXPORTS_DIR, f"{paper_prefix}_combined.pdf")
            combined_tex = build_combined_latex(
                target_questions,
                syllabus_map,
                title_name=paper_title,
                subtitle_name=paper_subtitle,
                exam_date=paper_date,
                author_name=paper_author,
                total_marks_val=args.total_marks,
                remove_nav=args.remove_nav,
                clean_header=args.clean_header,
                needspace=args.needspace,
            )
            tasks.append((combined_tex, combined_filename))

        # Parallelize compilation across standard target outputs
        num_workers = min(len(tasks), args.workers) if tasks else 1
        print(f"🚀 Compiling {len(tasks)} standard PDF targets in parallel ({num_workers} worker threads)...\n")
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [
                executor.submit(compile_pdflatex, tex, path, len(target_questions))
                for tex, path in tasks
            ]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    print(f"❌ Error during PDF compilation: {e}")