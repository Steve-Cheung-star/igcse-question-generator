import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile

MASTER_BANK_FILE = "master_bank.json"
SYLLABUS_FILE = "syllabus.json"
EXPORTS_DIR = "exports"

os.makedirs(EXPORTS_DIR, exist_ok=True)

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

% Custom color for hyperlink visibility
\definecolor{linkblue}{RGB}{0, 102, 204}

\usepackage[colorlinks=true, linkcolor=linkblue, citecolor=linkblue, urlcolor=linkblue, pdfencoding=auto]{hyperref}

% Adjust header height for fancyhdr compliance
\setlength{\headheight}{14pt}
\addtolength{\topmargin}{-2pt}

% Custom header & footer styling: Subtopic & Paper (Left), Page (Center), Total Marks (Right)
\pagestyle{fancy}
\fancyhf{}
\renewcommand{\headrulewidth}{0.4pt}
\fancyhead[C]{\thepage}

% Force paragraph indentation to ZERO globally across all paragraphs and math blocks
\setlength{\parindent}{0pt}
\setlength{\parskip}{0.4em}
\everypar{\setlength{\parindent}{0pt}}

% Structural Question & Subpart Hanging Indentation Macros (Enumerate-style grid alignment)

% \qlabel{1}: Q number right-aligned in left margin (-1.40cm to -0.40cm), text starts at Header Left Margin (0.00cm)
\newcommand{\qlabel}[1]{%
  \par\vspace{0.20cm}%
  \leftskip=0pt%
  \noindent\hspace*{-1.40cm}\makebox[1.00cm][r]{\textbf{#1}}\hspace*{0.40cm}\ignorespaces
}

% \subpartalpha{(a)}: Subpart (a) label aligned at Header Left Margin (0.00cm), text wraps at 0.85cm
\newcommand{\subpartalpha}[1]{%
  \par\vspace{0.15cm}%
  \leftskip=0.85cm%
  \noindent\hspace*{-0.85cm}\makebox[0.85cm][l]{\textbf{#1}}\ignorespaces
}

% \qsubpartalpha{1}{(a)}: Q number in margin (-1.40cm to -0.40cm), 0.40cm gap, Subpart (a) at Header Left Margin (0.00cm)
\newcommand{\qsubpartalpha}[2]{%
  \par\vspace{0.20cm}%
  \leftskip=0.85cm%
  \noindent\hspace*{-2.25cm}\makebox[1.00cm][r]{\textbf{#1}}\hspace*{0.40cm}\makebox[0.85cm][l]{\textbf{#2}}\ignorespaces
}

% \subpartroman{(i)}: Subpart (i) label at 0.85cm, text wraps at 1.70cm
\newcommand{\subpartroman}[1]{%
  \par\vspace{0.10cm}%
  \leftskip=1.70cm%
  \noindent\hspace*{-0.85cm}\makebox[0.85cm][l]{\textbf{#1}}\ignorespaces
}

% \qsubpartroman{1}{(i)}: Q number in margin (-1.40cm to -0.40cm), Subpart (i) at 0.85cm
\newcommand{\qsubpartroman}[2]{%
  \par\vspace{0.20cm}%
  \leftskip=1.70cm%
  \noindent\hspace*{-3.10cm}\makebox[1.00cm][r]{\textbf{#1}}\hspace*{1.25cm}\makebox[0.85cm][l]{\textbf{#2}}\ignorespaces
}

% \subpartalpharoman{(a)}{(i)}: Subpart (a) at 0.00cm + Subpart (i) at 0.85cm
\newcommand{\subpartalpharoman}[2]{%
  \par\vspace{0.15cm}%
  \leftskip=1.70cm%
  \noindent\hspace*{-1.70cm}\makebox[0.85cm][l]{\textbf{#1}}\makebox[0.85cm][l]{\textbf{#2}}\ignorespaces
}

% \qsubpartalpharoman{1}{(a)}{(i)}: Q number in margin (-1.40cm to -0.40cm), (a) at 0.00cm, (i) at 0.85cm
\newcommand{\qsubpartalpharoman}[3]{%
  \par\vspace{0.20cm}%
  \leftskip=1.70cm%
  \noindent\hspace*{-3.10cm}\makebox[1.00cm][r]{\textbf{#1}}\hspace*{0.40cm}\makebox[0.85cm][l]{\textbf{#2}}\makebox[0.85cm][l]{\textbf{#3}}\ignorespaces
}

\begin{document}
"""


def load_syllabus_map(syllabus_path=SYLLABUS_FILE):
  """Loads and maps IGCSE subtopic codes (e.g. E1.1, E2.2) to their title names."""
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
            title = str(
                sub.get("title") or sub.get("content") or ""
            ).strip()
            if code:
              s_map[code] = title
  except Exception as e:
    print(f"⚠️ Warning: Could not load `{syllabus_path}`: {e}")

  return s_map


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
  """Splits markscheme steps containing horizontal arrows (\rightarrow, \Rightarrow, ->, etc.) into vertical lines."""
  if isinstance(steps, str):
    steps = [steps]
  if not isinstance(steps, list):
    return []

  split_steps = []
  arrow_pattern = re.compile(
      r"\s*(?:\\rightarrow|\\Rightarrow|\\to|->|=>)\s*"
  )

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
        res.append(
            r"\begin{itemize}[leftmargin=0.8cm, itemsep=0.1em, topsep=0.2em]"
        )
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


def balance_latex_envs(text_str: str) -> str:
  """Ensures all LaTeX environments are cleanly closed before document compilation."""
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

  s = re.sub(
      r"\\n(?!(?:eq|ne|nu|otin|oindent|abla|ewline|ewpage|ew|ormal|umber|nobreak|nolimits|natural|nsubseteq|parallel|mid)\b)",
      "\n",
      s,
  )

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

  bare_subscript_pattern = re.compile(
      r"\b([A-Za-z]+)_(\{?[a-zA-Z0-9\+\-\=]+\}?)\b"
  )

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

      curr_is_struct = stripped.startswith(structural_starts) or is_env_block(
          stripped
      )
      next_is_struct = next_stripped.startswith(
          structural_starts
      ) or is_env_block(next_stripped)

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

  num_list_pattern = re.compile(
      r"(?:\b\d+|\b[a-z]|\b[ivxlcdm]+)\.(?=\s+[A-Z])", re.IGNORECASE
  )
  masked = num_list_pattern.sub(mask_num_list, masked)

  def break_sentence(m):
    punct = m.group(1)
    return f"{punct}\\\\ "

  sentence_end_pattern = re.compile(
      r"([\.\!\?]|:(?=\s*__NUMLIST_))\s+(?=[A-Z\\[]|__MATH_|__NUMLIST_)"
  )
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
  """Strips duplicate leading labels (e.g. (a), a), (i), (a)(i)) safely using negative lookahead (?!\w) to prevent clipping words like Calculates or Angle."""
  if not text_str:
    return ""
  s = text_str.strip()

  clean_1 = re.sub(r"[^\w]", "", label_1) if label_1 else ""
  clean_2 = re.sub(r"[^\w]", "", label_2) if label_2 else ""

  def make_pattern(c1, c2=None):
    if c2:
      return rf"^\s*\(?\s*{re.escape(c1)}\s*[\)\.:]?\s*\(?\s*{re.escape(c2)}\s*[\)\.:]?(?!\w)\s*"
    else:
      return rf"^\s*\(?\s*{re.escape(c1)}\s*[\)\.:]?(?!\w)\s*"

  if clean_1 and clean_2:
    comb_pattern = make_pattern(clean_1, clean_2)
    if re.search(comb_pattern, s, flags=re.IGNORECASE):
      return re.sub(comb_pattern, "", s, count=1, flags=re.IGNORECASE).strip()

  if clean_1:
    p1 = make_pattern(clean_1)
    s = re.sub(p1, "", s, count=1, flags=re.IGNORECASE).strip()

  if clean_2:
    p2 = make_pattern(clean_2)
    s = re.sub(p2, "", s, count=1, flags=re.IGNORECASE).strip()

  return s


def flush_line_marks(text_str: str) -> str:
  if not text_str:
    return ""
  s = sanitize_latex_text(text_str)
  if (
      s.startswith(r"\begin")
      or s.startswith(r"\end")
      or s.startswith(r"\hline")
  ):
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
  return code if code else "IGCSE"


def get_q_marks(q):
  return q.get("total_marks", q.get("marks", 0))


def extract_grouped_subtopics(
    questions, syllabus_map, target_prefix="sec_target_"
):
  subtopic_order = []
  subtopic_data = {}
  target_map = {}
  target_counter = 0

  for q in questions:
    raw_code = str(q.get("subtopic", "E1.1")).strip()
    code = normalize_subtopic_code(raw_code)
    paper_type = str(
        q.get("paper_type") or q.get("paper") or "Paper 4 (Calculator)"
    ).strip()

    key = (code, paper_type)
    if key not in target_map:
      target_counter += 1
      target_map[key] = f"{target_prefix}{target_counter}"

    if code not in subtopic_data:
      content = (
          syllabus_map.get(code)
          or q.get("subtopic_title")
          or q.get("topic")
          or ""
      )
      subtopic_data[code] = {
          "code": code,
          "content": sanitize_latex_text(content),
          "papers": {},
      }
      subtopic_order.append(code)

    if paper_type not in subtopic_data[code]["papers"]:
      subtopic_data[code]["papers"][paper_type] = 0
    subtopic_data[code]["papers"][paper_type] += 1

  grouped_list = []
  for code in subtopic_order:
    item = subtopic_data[code]
    paper_info = []
    for paper_name, count in item["papers"].items():
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
    title_str, questions, syllabus_map, exam_date="", target_prefix="sec_target_"
):
  grouped_subtopics, _ = extract_grouped_subtopics(
      questions, syllabus_map, target_prefix=target_prefix
  )
  total_marks = sum(int(get_q_marks(q)) for q in questions)
  display_title = title_str.replace("_", " ")

  date_display = (
      f"\\textbf{{Date:}} {exam_date} \\quad | \\quad " if exam_date else ""
  )

  tex = f"""
\\begin{{center}}
    {{\\Huge \\textbf{{{display_title}}}}}\\\\[0.5cm]
    {date_display}\\textbf{{Total Questions:}} {len(questions)} \\quad | \\quad 
    \\textbf{{Total Marks:}} {total_marks} Marks
\\end{{center}}
\\vspace{{0.4cm}}
\\hrule
\\vspace{{0.8cm}}

\\section*{{Table of Contents \\& Quick Navigation}}
\\begin{{itemize}}[leftmargin=0.6cm, itemsep=0.6em]
"""

  for item in grouped_subtopics:
    code = item["code"]
    content = item["content"]
    papers = item["papers"]

    header_text = (
        f"\\textbf{{{code}}} {content}" if content else f"\\textbf{{{code}}}"
    )
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
  clean_code = re.sub(r"\\n(?![a-zA-Z])", "\n", tikz_str.strip())
  if "\\begin{tikzpicture}" not in clean_code:
    clean_code = f"\\begin{{tikzpicture}}\n{clean_code}\n\\end{{tikzpicture}}"
  return clean_code


def sanitize_slot_label(label_str: str) -> str:
  """Sanitizes LaTeX and plain text labels: wraps in $...$ if LaTeX/math, leaves plain words as text."""
  if not label_str:
    return ""
  s = sanitize_latex_text(label_str)
  if s.startswith("$") and s.endswith("$") and s.count("$") == 2:
    return s

  is_single_var_assign = bool(
      re.match(r"^\s*([a-zA-Z]|\\[a-zA-Z]+)(\s*=)?\s*$", s)
  )
  has_math_cmds = bool(re.search(r"[\^_\\]|cm\^|m/s|°", s))

  if is_single_var_assign or has_math_cmds:
    clean_inner = s.replace("$", "")
    return f"${clean_inner}$"

  return s


def parse_format_str_to_lines(fmt_str: str) -> list:
  if not fmt_str:
    return []
  s = str(fmt_str).strip()
  s = re.sub(
      r"\\n(?!(?:eq|ne|nu|otin|oindent|abla|ewline|ewpage|ew|ormal|umber|nobreak|nolimits|natural|nsubseteq|parallel|mid)\b)",
      "\n",
      s,
  )
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
      line_tex = re.sub(
          r"\.{3,}",
          lambda m: f"\\makebox[{default_box_w}]{{\\dotfill}}",
          line,
      )
    elif r"\dotfill" in line:
      line_tex = re.sub(
          r"(?:\\noindent\s*)?\\dotfill",
          lambda m: f"\\makebox[{default_box_w}]{{\\dotfill}}",
          line,
      )
    elif r"\dots" in line:
      line_tex = re.sub(
          r"(?:\\dots)+",
          lambda m: f"\\makebox[{default_box_w}]{{\\dotfill}}",
          line,
      )
    else:
      line_tex = line

    processed_lines.append(sanitize_latex_text(line_tex))

  if num_lines == 1:
    return processed_lines[0], 1
  else:
    tabular_rows = " \\\\[0.25cm]\n".join(processed_lines)
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
  """Generates LaTeX working space and sanitized answer slot cleanly flushed right."""
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
      and any(
          w in prompt_lower
          for w in ["write down", "insert", "complete", "fill", "choose", "place"]
      )
  )
  is_no_line_prompt = (
      any(re.search(pat, prompt_lower) for pat in no_answer_line_patterns)
      or is_symbol_prompt
  )
  is_describe_fully = "describe fully" in prompt_lower or ans_t in [
      "describe_fully",
      "describe",
      "describe_transformation",
  ]

  fmt_lines = parse_format_str_to_lines(format_str if format_str else "")
  is_pure_multiline_dots = len(fmt_lines) > 1 and all(
      re.fullmatch(r"(?:\\noindent\s*)?(?:\\dotfill|\\dots+|\.{3,})\s*", line)
      for line in fmt_lines
  )

  if is_describe_fully or is_pure_multiline_dots:
    num_lines = len(fmt_lines) if is_pure_multiline_dots else 3
    lines_tex = []
    for i in range(num_lines):
      is_last = i == num_lines - 1
      if is_last:
        lines_tex.append(
            f"\\makebox[\\dimexpr\\linewidth-1.5cm\\relax]{{\\dotfill}} \\hfill"
            f" {mark_tag}"
        )
      else:
        lines_tex.append("\\makebox[\\linewidth]{{\\dotfill}}")

    return "\\par\\vspace{\\baselineskip}\n" + " \\\\[0.25cm]\n".join(lines_tex)

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
      return (
          f"\\par\\vspace{{{v_space:.2f}cm}}\\leavevmode\\hspace*{{\\fill}}"
          f" {mark_tag}"
      )
    return f"\\leavevmode\\hspace*{{\\fill}} {mark_tag}"

  elif ans_t in [
      "multi_field",
      "inline_fields",
      "multiple_fields",
      "two_fields",
      "dual_slot",
  ]:
    box_w = "2.5cm"
    if format_str and str(format_str).strip():
      raw_parts = [p.strip() for p in format_str.split(",") if p.strip()]
      formatted_parts = []
      for part in raw_parts:
        clean_p = sanitize_slot_label(part)
        if ".." in clean_p or r"\dots" in clean_p or r"\dotfill" in clean_p:
          expanded = re.sub(
              r"(?:\.{2,}|\\dots+|\\dotfill)",
              lambda m: f"\\makebox[{box_w}]{{\\dotfill}}",
              clean_p,
          )
          formatted_parts.append(expanded)
        else:
          formatted_parts.append(f"{clean_p} \\makebox[{box_w}]{{\\dotfill}}")
      line_content = " \\quad , \\quad ".join(formatted_parts)
      return (
          f"{space_tex}\\leavevmode\\hspace*{{\\fill}} {line_content} \\quad"
          f" {mark_tag}"
      )
    else:
      p_str = prefix_tex if prefix_tex else "Field 1 = "
      s_str = suffix_tex if suffix_tex else "Field 2 = "
      return (
          f"{space_tex}\\leavevmode\\hspace*{{\\fill}}"
          f" {p_str}\\makebox[{box_w}]{{\\dotfill}} \\quad , \\quad"
          f" {s_str}\\makebox[{box_w}]{{\\dotfill}} \\quad {mark_tag}"
      )

  if format_str and str(format_str).strip():
    fmt_latex, num_lines = format_custom_str_to_latex(
        format_str, marks_tag=mark_tag
    )
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

  elif ans_t in ["vector", "matrix", "column_vector"]:
    return (
        f"{space_tex}\\leavevmode\\hspace*{{\\fill}}"
        f" {prefix_tex}$\\left( \\begin{{array}}{{c}} \\makebox[1.5cm]{{}}"
        f" \\\\[1.8em] \\makebox[1.5cm]{{}} \\end{{array}}"
        f" \\right)${suffix_tex} \\quad {mark_tag}"
    )

  elif ans_t in ["multi_line", "multiple", "asymptotes", "two_lines"]:
    return (
        f"{space_tex}\\leavevmode\\hspace*{{\\fill}}\\begin{{tabular}}[b]{{@{{}}r@{{}}}}\n"
        f"{prefix_tex}\\makebox[4.0cm]{{\\dotfill}}{suffix_tex} \\\\[0.25cm]\n"
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
        f"{prefix_tex}\\makebox[5.0cm]{{\\dotfill}}{suffix_tex} \\\\[0.25cm]\n"
        f"{prefix_tex}\\makebox[5.0cm]{{\\dotfill}}{suffix_tex} \\\\[0.25cm]\n"
        f"{prefix_tex}\\makebox[5.0cm]{{\\dotfill}}{suffix_tex}\n"
        f"\\end{{tabular}} \\quad {mark_tag}"
    )

  elif ans_t in ["short", "short_line", "single_value"]:
    return (
        f"{space_tex}\\leavevmode\\hspace*{{\\fill}}"
        f" {prefix_tex}\\makebox[3.0cm]{{\\dotfill}}{suffix_tex} \\quad"
        f" {mark_tag}"
    )

  elif ans_t in ["long", "long_line", "ordering", "list"]:
    return (
        f"{space_tex}\\leavevmode\\hspace*{{\\fill}}"
        f" {prefix_tex}\\makebox[8.5cm]{{\\dotfill}}{suffix_tex} \\quad"
        f" {mark_tag}"
    )

  else:  # Default single-line slot
    return (
        f"{space_tex}\\leavevmode\\hspace*{{\\fill}}"
        f" {prefix_tex}\\makebox[4.5cm]{{\\dotfill}}{suffix_tex} \\quad"
        f" {mark_tag}"
    )


def format_question_body(q, idx=None):
  tex = ""
  q_num_str = str(idx) if idx is not None else ""
  q_label_used = False if q_num_str else True

  raw_intro = q.get("intro_text") or q.get("question") or ""
  intro_text = split_latex_sentences(sanitize_latex_text(raw_intro))

  intro_tikz = clean_tikz_code(
      q.get("intro_diagram_tikz") or q.get("diagram_tikz") or ""
  )

  # Render intro_text first
  if intro_text:
    if not q_label_used:
      tex += f"\\qlabel{{{q_num_str}}} {intro_text}\n\n"
      q_label_used = True
    else:
      tex += f"{intro_text}\n\n"

  # Render intro_diagram_tikz second
  if intro_tikz:
    tex += f"\\begin{{center}}\n{intro_tikz}\n\\end{{center}}\n\n"

  parts = q.get("parts", [])
  for part in parts:
    p_label = part.get("label", "").strip()
    p_context = split_latex_sentences(
        sanitize_latex_text(part.get("context", ""))
    )
    p_diagram = clean_tikz_code(part.get("diagram_tikz", ""))
    p_prompt = split_latex_sentences(
        sanitize_latex_text(part.get("prompt", ""))
    )
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
        sp_label = subpart.get("label", "").strip()
        sp_context = split_latex_sentences(
            sanitize_latex_text(subpart.get("context", ""))
        )
        sp_diagram = clean_tikz_code(subpart.get("diagram_tikz", ""))
        sp_prompt = split_latex_sentences(
            sanitize_latex_text(subpart.get("prompt", ""))
        )
        sp_marks = subpart.get("marks", 0)
        sp_ans_type = subpart.get("answer_type", "line")
        sp_prefix = subpart.get("answer_prefix", "")
        sp_suffix = subpart.get("answer_suffix", "")
        sp_format = subpart.get("answer_format", "")
        sp_vspace = subpart.get("vspace_cm", None)

        sp_context = strip_redundant_label(sp_context, p_label, sp_label)
        sp_prompt = strip_redundant_label(sp_prompt, p_label, sp_label)

        slot_tex = format_answer_slot_latex(
            sp_ans_type,
            sp_prefix,
            sp_suffix,
            sp_format,
            sp_marks,
            sp_vspace,
            prompt_text=sp_prompt,
        )

        if sp_context or sp_prompt:
          sp_text = (
              f"{sp_context}\n\n{sp_prompt}"
              if (sp_context and sp_prompt)
              else (sp_context or sp_prompt)
          )
          if not q_label_used:
            if sp_idx == 0:
              tex += (
                  f"\\qsubpartalpharoman{{{q_num_str}}}{{{p_label}}}{{{sp_label}}}"
                  f" {sp_text}\n"
              )
            else:
              tex += (
                  f"\\qsubpartroman{{{q_num_str}}}{{{sp_label}}} {sp_text}\n"
              )
            q_label_used = True
          else:
            if sp_idx == 0:
              tex += (
                  f"\\subpartalpharoman{{{p_label}}}{{{sp_label}}} {sp_text}\n"
              )
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
          p_ans_type,
          p_prefix,
          p_suffix,
          p_format,
          p_marks if not subparts else 0,
          p_vspace,
          prompt_text=p_prompt,
      )

      if p_context or p_prompt:
        p_text = (
            f"{p_context}\n\n{p_prompt}"
            if (p_context and p_prompt)
            else (p_context or p_prompt)
        )
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
        sp_label = subpart.get("label", "").strip()
        sp_context = split_latex_sentences(
            sanitize_latex_text(subpart.get("context", ""))
        )
        sp_diagram = clean_tikz_code(subpart.get("diagram_tikz", ""))
        sp_prompt = split_latex_sentences(
            sanitize_latex_text(subpart.get("prompt", ""))
        )
        sp_marks = subpart.get("marks", 0)
        sp_ans_type = subpart.get("answer_type", "line")
        sp_prefix = subpart.get("answer_prefix", "")
        sp_suffix = subpart.get("answer_suffix", "")
        sp_format = subpart.get("answer_format", "")
        sp_vspace = subpart.get("vspace_cm", None)

        sp_context = strip_redundant_label(sp_context, p_label, sp_label)
        sp_prompt = strip_redundant_label(sp_prompt, p_label, sp_label)

        sp_slot_tex = format_answer_slot_latex(
            sp_ans_type,
            sp_prefix,
            sp_suffix,
            sp_format,
            sp_marks,
            sp_vspace,
            prompt_text=sp_prompt,
        )

        if sp_context or sp_prompt:
          sp_text = (
              f"{sp_context}\n\n{sp_prompt}"
              if (sp_context and sp_prompt)
              else (sp_context or sp_prompt)
          )
          if not q_label_used:
            if sp_label:
              tex += (
                  f"\\qsubpartroman{{{q_num_str}}}{{{sp_label}}} {sp_text}\n"
              )
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

      # If part has direct markscheme steps
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

      # If part has subparts with markscheme steps
      for sp_idx, subpart in enumerate(subparts):
        sp_label = subpart.get("label", "").strip()
        sp_ms_steps = split_markscheme_arrow_steps(
            subpart.get("markscheme", [])
        )

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


def build_question_paper_latex(questions, syllabus_map, title_name, exam_date=""):
  tex = LATEX_PREAMBLE
  tex += build_cover_page_latex(
      f"{title_name} — Question Paper",
      questions,
      syllabus_map,
      exam_date=exam_date,
      target_prefix="qp_sec_",
  )
  grouped_subtopics, target_map = extract_grouped_subtopics(
      questions, syllabus_map, target_prefix="qp_sec_"
  )

  current_section = None
  bookmark_count = 0

  for idx, q in enumerate(questions, 1):
    if idx > 1:
      tex += "\\newpage\n"

    paper_type = str(
        q.get("paper_type") or q.get("paper") or "Paper 4 (Calculator)"
    ).strip()
    subtopic_code = normalize_subtopic_code(str(q.get("subtopic", "E1.1")))
    marks = get_q_marks(q)
    section_key = (subtopic_code, paper_type)

    tex += f"\n\\fancyhead[L]{{\\textbf{{[{subtopic_code}]}} \\quad {paper_type}}}\n"
    tex += "\\fancyhead[C]{\\thepage}\n"
    tex += f"\\fancyhead[R]{{\\textbf{{[Total Marks: {marks}]}}}}\n"

    tex += "\n\\begingroup\\raggedright\\setlength{\\parindent}{0pt}\\leftskip=0pt\n"

    if section_key != current_section:
      current_section = section_key
      bookmark_count += 1
      target_id = target_map.get(section_key, f"qp_sec_{bookmark_count}")
      content = sanitize_latex_text(
          syllabus_map.get(subtopic_code)
          or q.get("subtopic_title")
          or q.get("topic")
          or ""
      )
      bookmark_tex = (
          f"{subtopic_code} {content} ({paper_type})"
          if content
          else f"{subtopic_code} ({paper_type})"
      )
      bookmark_plain = re.sub(r"\$([^$]+)\$", r"\1", bookmark_tex).replace(
          "\\", ""
      )

      tex += (
          f"\\pdfbookmark[1]{{\\texorpdfstring{{{bookmark_tex}}}{{{bookmark_plain}}}}}{{qp_section_{bookmark_count}}}\n"
      )
      tex += f"\\hypertarget{{{target_id}}}{{}}\n"

    q_body = format_question_body(q, idx=idx)

    tex += f"\n\\noindent\n{q_body}\n\n"
    tex += "\\endgroup\n"

  tex += "\n\\end{document}"
  return tex


def build_markscheme_latex(questions, syllabus_map, title_name, exam_date=""):
  tex = LATEX_PREAMBLE
  tex += build_cover_page_latex(
      f"{title_name} — Markscheme",
      questions,
      syllabus_map,
      exam_date=exam_date,
      target_prefix="ms_sec_",
  )
  grouped_subtopics, target_map = extract_grouped_subtopics(
      questions, syllabus_map, target_prefix="ms_sec_"
  )

  current_section = None
  bookmark_count = 0

  for idx, q in enumerate(questions, 1):
    subtopic_code = normalize_subtopic_code(str(q.get("subtopic", "E1.1")))
    marks = get_q_marks(q)
    paper_type = str(
        q.get("paper_type") or q.get("paper") or "Paper 4 (Calculator)"
    ).strip()
    section_key = (subtopic_code, paper_type)

    tex += "\n\\fancyhead[L]{}\n"
    tex += "\\fancyhead[C]{\\thepage}\n"
    tex += "\\fancyhead[R]{}\n"

    tex += "\n\\begingroup\\raggedright\\setlength{\\parindent}{0pt}\\leftskip=0pt\n"

    if section_key != current_section:
      current_section = section_key
      bookmark_count += 1
      target_id = target_map.get(section_key, f"ms_sec_{bookmark_count}")
      content = sanitize_latex_text(
          syllabus_map.get(subtopic_code)
          or q.get("subtopic_title")
          or q.get("topic")
          or ""
      )
      bookmark_tex = (
          f"{subtopic_code} {content} ({paper_type})"
          if content
          else f"{subtopic_code} ({paper_type})"
      )
      bookmark_plain = re.sub(r"\$([^$]+)\$", r"\1", bookmark_tex).replace(
          "\\", ""
      )

      tex += (
          f"\\pdfbookmark[1]{{\\texorpdfstring{{{bookmark_tex}}}{{{bookmark_plain}}}}}{{ms_section_{bookmark_count}}}\n"
      )
      tex += f"\\hypertarget{{{target_id}}}{{}}\n"

    ms_body = format_markscheme_body(q, idx=idx)
    tex += f"\n{ms_body}\n"
    tex += f"\\par\\vspace{{0.10cm}}\\leavevmode\\hspace*{{\\fill}}\\textit{{[Total Marks: {marks}]}}\n"
    tex += "\\vspace{0.8cm}\n\\endgroup\n"

  tex += "\n\\end{document}"
  return tex


def build_combined_latex(questions, syllabus_map, title_name, exam_date=""):
  tex = LATEX_PREAMBLE
  tex += build_cover_page_latex(
      f"{title_name} — Combined Questions \\& Markscheme",
      questions,
      syllabus_map,
      exam_date=exam_date,
      target_prefix="comb_qp_sec_",
  )
  grouped_subtopics, target_map = extract_grouped_subtopics(
      questions, syllabus_map, target_prefix="comb_qp_sec_"
  )

  tex += "\\cleardoublepage\n\\section*{Questions}\n"
  current_section = None
  bookmark_count = 0

  for idx, q in enumerate(questions, 1):
    if idx > 1:
      tex += "\\newpage\n"

    paper_type = str(
        q.get("paper_type") or q.get("paper") or "Paper 4 (Calculator)"
    ).strip()
    subtopic_code = normalize_subtopic_code(str(q.get("subtopic", "E1.1")))
    marks = get_q_marks(q)
    section_key = (subtopic_code, paper_type)

    tex += f"\n\\fancyhead[L]{{\\textbf{{[{subtopic_code}]}} \\quad {paper_type}}}\n"
    tex += "\\fancyhead[C]{\\thepage}\n"
    tex += f"\\fancyhead[R]{{\\textbf{{[Total Marks: {marks}]}}}}\n"

    tex += "\n\\begingroup\\raggedright\\setlength{\\parindent}{0pt}\\leftskip=0pt\n"

    if section_key != current_section:
      current_section = section_key
      bookmark_count += 1
      target_id = target_map.get(section_key, f"comb_qp_sec_{bookmark_count}")
      content = sanitize_latex_text(
          syllabus_map.get(subtopic_code)
          or q.get("subtopic_title")
          or q.get("topic")
          or ""
      )
      bookmark_tex = (
          f"{subtopic_code} {content} ({paper_type})"
          if content
          else f"{subtopic_code} ({paper_type})"
      )
      bookmark_plain = re.sub(r"\$([^$]+)\$", r"\1", bookmark_tex).replace(
          "\\", ""
      )

      tex += (
          f"\\pdfbookmark[1]{{\\texorpdfstring{{{bookmark_tex}}}{{{bookmark_plain}}}}}{{comb_section_{bookmark_count}}}\n"
      )
      tex += f"\\hypertarget{{{target_id}}}{{}}\n"

    q_body = format_question_body(q, idx=idx)

    tex += f"\\hypertarget{{q_{idx}}}{{}}\n"
    tex += (
        f"\\leavevmode\\hfill"
        f" \\hyperlink{{ms_q_{idx}}}{{\\textbf{{[Markscheme]}}}}\n\\par\\vspace{{0.1cm}}\n"
    )
    tex += f"\n{q_body}\n\n"
    tex += "\\endgroup\n"

  tex += "\n\\newpage\n\\section*{Markschemes}\n"
  current_section = None

  for idx, q in enumerate(questions, 1):
    subtopic_code = normalize_subtopic_code(str(q.get("subtopic", "E1.1")))
    marks = get_q_marks(q)
    paper_type = str(
        q.get("paper_type") or q.get("paper") or "Paper 4 (Calculator)"
    ).strip()
    section_key = (subtopic_code, paper_type)

    tex += "\n\\fancyhead[L]{}\n"
    tex += "\\fancyhead[C]{\\thepage}\n"
    tex += "\\fancyhead[R]{}\n"

    tex += "\n\\begingroup\\raggedright\\setlength{\\parindent}{0pt}\\leftskip=0pt\n"

    if section_key != current_section:
      current_section = section_key

    tex += f"\\hypertarget{{ms_q_{idx}}}{{}}\n"
    tex += (
        f"\\leavevmode\\hfill"
        f" \\hyperlink{{q_{idx}}}{{\\textbf{{[Back to Question]}}}}\n\\par\\vspace{{0.1cm}}\n"
    )
    ms_body = format_markscheme_body(q, idx=idx)
    tex += f"\n{ms_body}\n"
    tex += f"\\par\\vspace{{0.10cm}}\\leavevmode\\hspace*{{\\fill}}\\textit{{[Total Marks: {marks}]}}\n"
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

    print(
        f"⏳ Compiling `{output_pdf_filename}` via pdflatex"
        f" (timeout={dynamic_timeout}s)..."
    )
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
        print(
            f"❌ Failed to generate `{output_pdf_filename}`. pdflatex output:"
        )
        print(result.stdout.decode("utf-8", errors="ignore")[-1000:])

    except FileNotFoundError:
      print("❌ Error: `pdflatex` is not installed or not in PATH.")
    except subprocess.TimeoutExpired:
      print(f"⏰ pdflatex timed out after {dynamic_timeout} seconds.")
    except Exception as e:
      print(f"❌ Error compiling PDF: {e}")


if __name__ == "__main__":
  parser = argparse.ArgumentParser(
      description=(
          "Compile master_bank.json questions into IGCSE Mathematics PDF via"
          " pdflatex."
      )
  )
  parser.add_argument(
      "--input", default=MASTER_BANK_FILE, help="Path to input JSON file."
  )
  parser.add_argument(
      "--select",
      "-s",
      type=str,
      default=None,
      help="Select specific question numbers/ranges (e.g. 1-3, 5).",
  )
  parser.add_argument(
      "--name",
      "-n",
      type=str,
      default=None,
      help="Paper title/name used for cover pages and file naming.",
  )
  parser.add_argument(
      "--date",
      "-d",
      type=str,
      default=None,
      help="Exam date string for cover page.",
  )
  parser.add_argument(
      "--mode",
      "-m",
      choices=["all", "qp", "ms", "combined"],
      default="all",
      help="Select output target: qp, ms, combined, or all (default).",
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

  paper_prefix = (
      args.name
      if args.name
      else (
          "IGCSE Mathematics Master Selected"
          if args.select
          else "IGCSE Mathematics Master"
      )
  )

  if args.mode in ["all", "qp"]:
    qp_filename = os.path.join(EXPORTS_DIR, f"{paper_prefix}_qp.pdf")
    qp_tex = build_question_paper_latex(
        target_questions, syllabus_map, paper_prefix, exam_date=paper_date
    )
    compile_pdflatex(qp_tex, qp_filename, num_questions=len(target_questions))

  if args.mode in ["all", "ms"]:
    ms_filename = os.path.join(EXPORTS_DIR, f"{paper_prefix}_ms.pdf")
    ms_tex = build_markscheme_latex(
        target_questions, syllabus_map, paper_prefix, exam_date=paper_date
    )
    compile_pdflatex(ms_tex, ms_filename, num_questions=len(target_questions))

  if args.mode in ["all", "combined"]:
    combined_filename = os.path.join(EXPORTS_DIR, f"{paper_prefix}_combined.pdf")
    combined_tex = build_combined_latex(
        target_questions, syllabus_map, paper_prefix, exam_date=paper_date
    )
    compile_pdflatex(
        combined_tex, combined_filename, num_questions=len(target_questions)
    )