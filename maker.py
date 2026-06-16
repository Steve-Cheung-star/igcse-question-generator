import streamlit as st
import json
import os
import re
import random
import pandas as pd
import datetime
import subprocess
from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
import textwrap

# ------------------------------------------------------------------
# SYSTEM ENVIRONMENT PATH OVERRIDE FOR MACOS COMPILATION STEPS
# ------------------------------------------------------------------
current_path = os.environ.get("PATH", "")
homebrew_paths = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/Library/TeX/texbin"
if homebrew_paths not in current_path:
    os.environ["PATH"] = f"{homebrew_paths}:{current_path}"

# --- 1. CONFIGURATION ---
st.set_page_config(layout="wide", page_title="Exam Maker", page_icon="📝")

# --- 2. MAIN APP CONTENT ---
st.sidebar.title("🛠️ Exam Builder")

st.markdown("""
    <style>
    .stButton>button { width: 100%; border-radius: 8px; height: 3em; background-color: #007BFF; color: white; }
    .stDownloadButton>button { width: 100%; background-color: #28a745; color: white; }
    [data-testid="stMetricValue"] { font-size: 22px; }
    </style>
    """, unsafe_allow_html=True)

if "test_cart" not in st.session_state:
    st.session_state.test_cart = []
if "shuffle_seed" not in st.session_state:
    st.session_state.shuffle_seed = 42


# --- 3. DATA LOADING ---
@st.cache_data
def load_db():
    if not os.path.exists("master_bank.json"):
        st.error("master_bank.json not found!")
        return []

    with open("master_bank.json", "r", encoding="utf-8") as f:
        try:
            raw_data = json.load(f)
        except json.JSONDecodeError:
            st.error("Error parsing master_bank.json. Please ensure it is valid JSON.")
            return []

    db = []
    for i, item in enumerate(raw_data):
        meta = item.get("metadata", {})

        topics_raw = meta.get("topics", ["Uncategorized"])
        if isinstance(topics_raw, str): topics_raw = [topics_raw]

        q_paths = item.get("question_paths", [])
        if isinstance(q_paths, str): q_paths = [q_paths]

        ms_paths = item.get("ms_paths", [])
        if isinstance(ms_paths, str): ms_paths = [ms_paths]

        db.append({
            "question_base": str(item.get("question_base", f"q_{i}")),
            "paper": str(item.get("paper", "General Paper")),
            "question_paths": q_paths,
            "ms_paths": ms_paths,
            "question_code": item.get("question_code", ""),
            "ms_code": item.get("ms_code", ""),
            "metadata": {
                "topics": topics_raw,
                "difficulty": str(meta.get("difficulty", "Medium")),
                "marks": int(meta.get("marks", 0)),
                "year": str(meta.get("year", "N/A"))
            }
        })
    return db


db = load_db()


def get_all_papers(database): return sorted(list(set([item.get('paper') for item in database if item.get('paper')])))


def get_all_topics(database): return sorted(
    list(set([t for item in database for t in item.get('metadata', {}).get('topics', [])])))


def get_all_difficulties(database): return sorted(list(
    set([item.get('metadata', {}).get('difficulty') for item in database if
         item.get('metadata', {}).get('difficulty')])))


# --- 4. SIDEBAR: FILTERS ---
st.sidebar.subheader("Filters")
all_papers = get_all_papers(db)
all_topics = get_all_topics(db)
all_diffs = get_all_difficulties(db)

sel_papers = st.sidebar.multiselect("Select Paper", all_papers, default=all_papers)
sel_topics = st.sidebar.multiselect("Select Topics", all_topics)
sel_diffs = st.sidebar.multiselect("Select Difficulty", all_diffs)

filtered_items = []
for item in db:
    meta = item.get('metadata', {})
    if (not sel_papers or item.get('paper') in sel_papers) and \
            (not sel_topics or any(t in sel_topics for t in meta.get('topics', []))) and \
            (not sel_diffs or meta.get('difficulty') in sel_diffs):
        filtered_items.append(item)

col_a, col_b = st.sidebar.columns(2)
with col_a:
    if st.button("➕ Add All"):
        current_bases = {c.get('question_base') for c in st.session_state.test_cart}
        for f_item in filtered_items:
            if f_item.get('question_base') not in current_bases:
                st.session_state.test_cart.append(f_item)
        st.rerun()
with col_b:
    if st.button("🗑️ Clear Cart"):
        st.session_state.test_cart = []
        st.rerun()

# RESTORED: Sidebar Random Pick (1 Per Topic) button feature
if st.sidebar.button("🎲 Random Pick (1 Per Topic)", disabled=not sel_topics,
                     help="Select at least one topic above to use this feature."):
    current_bases = {c.get('question_base') for c in st.session_state.test_cart}
    for topic in sel_topics:
        topic_items = [item for item in filtered_items if topic in item.get('metadata', {}).get('topics', [])]
        available_items = [item for item in topic_items if item.get('question_base') not in current_bases]
        if available_items:
            chosen = random.choice(available_items)
            st.session_state.test_cart.append(chosen)
            current_bases.add(chosen.get('question_base'))
    st.rerun()

st.sidebar.divider()
total_marks = sum([item.get('metadata', {}).get('marks', 0) for item in st.session_state.test_cart])
est_time = int(total_marks * 1.2)

col_metric, col_sync = st.sidebar.columns([2, 1])
with col_metric:
    st.metric("Questions in Cart", len(st.session_state.test_cart))
with col_sync:
    st.write("")
    if st.button("Sync", use_container_width=True, type="secondary"): st.rerun()

st.sidebar.metric("Total Marks", total_marks)
st.sidebar.caption(f"⏱️ Estimated Time: ~{est_time} mins")


# --- 5. LATEX PARSER HELPER ---
def parse_to_pure_latex(latex_body, is_markscheme=False):
    """Converts the raw JSON tags into standard LaTeX logic with smart list promotion"""
    if not latex_body: return ""

    latex_body = re.sub(
        r"\$\$(.*?)\$\$",
        r"\n\\par\\vspace{0.1cm}\\hspace*{0.6cm}$\1$\\par\\vspace{0.15cm}\n",
        latex_body,
        flags=re.DOTALL
    )

    raw_lines = latex_body.splitlines()

    main_item_count = 0
    temp_in_sub = False
    for line in raw_lines:
        s = line.strip()
        if s.startswith("[SUBENUM_START]"):
            temp_in_sub = True
        elif s.startswith("[SUBENUM_END]"):
            temp_in_sub = False
        elif s.startswith("[ITEM]") and not temp_in_sub:
            main_item_count += 1

    is_single_main = (main_item_count <= 1) and not is_markscheme

    assembled_lines = []
    active_environments = []
    in_sub = False

    for line in raw_lines:
        line = re.sub(r'(\d+)%\s*', r'\1\\% ', line)
        stripped = line.strip()
        if not stripped: continue

        if is_markscheme and "Part" in stripped and "Answer" in stripped and "Marks" in stripped:
            continue

        start_match = re.search(r"\\begin\{([A-Za-z0-9\*]+)\}", stripped)
        if start_match: active_environments.append(start_match.group(1))
        end_match = re.search(r"\\end\{([A-Za-z0-9\*]+)\}", stripped)
        if end_match and active_environments and active_environments[-1] == end_match.group(1):
            active_environments.pop()

        if stripped.startswith("[ENUM_START]"):
            if not is_single_main: assembled_lines.append(r"\begin{enumerate}[label=\textbf{(\alph*)}, leftmargin=*]")
            continue
        elif stripped.startswith("[ENUM_END]"):
            if not is_single_main: assembled_lines.append(r"\end{enumerate}")
            continue
        elif stripped.startswith("[SUBENUM_START]"):
            in_sub = True
            if is_single_main:
                assembled_lines.append(r"\begin{enumerate}[label=\textbf{(\alph*)}, leftmargin=*]")
            else:
                assembled_lines.append(r"\begin{enumerate}[label=\textbf{(\roman*)}, leftmargin=0.6cm]")
            continue
        elif stripped.startswith("[SUBENUM_END]"):
            in_sub = False
            assembled_lines.append(r"\end{enumerate}")
            continue
        elif stripped.startswith("[ITEM]"):
            item_text = stripped.replace("[ITEM]", "").strip()
            if in_sub:
                assembled_lines.append(f"\\item {item_text}")
            else:
                if is_single_main:
                    assembled_lines.append(f"\\noindent {item_text} \\par\\vspace{{0.1cm}}")
                else:
                    assembled_lines.append(f"\\item {item_text}")
            continue

        if len(active_environments) == 0 and not is_markscheme:
            if not stripped.startswith("\\") and not stripped.endswith(r"\\") and not stripped.startswith(
                    "[") and not stripped.startswith(r"\par"):
                line = line + r" \\"

        assembled_lines.append(line)

    processed_latex = "\n".join(assembled_lines)
    if "\\begin{tikzpicture}" in processed_latex or "\\begin{tabular}" in processed_latex:
        processed_latex = "\\needspace{5cm}\n" + processed_latex

    return processed_latex


def compile_full_latex_exam(cart, output_filename, is_markscheme=False, custom_title="Exam Paper"):
    """Compiles the entire cart into a single LaTeX document."""
    macro_definition = r"""
\newcommand{\examanswerslot}[3]{%
  \par\nopagebreak\vspace*{#1}%
  \par\nopagebreak\vspace*{0.2cm}%
  \phantom{.} \hfill \makebox[#2\linewidth]{\dotfill}\hspace{4pt}\makebox[15pt][r]{{[#3]}}\par
}
"""
    if is_markscheme:
        macro_definition += r"""
\newenvironment{officialmarkscheme}{
    \renewcommand{\arraystretch}{2.0}
    \tikzset{every picture/.style={baseline=(current bounding box.north)}}
    \begin{longtable}{|p{1.5cm}|p{4.8cm}|p{1.2cm}|p{7.7cm}|}
    \hline
    \textbf{Part} & \textbf{Answer} & \textbf{Marks} & \textbf{Partial Marks / Guidance} \\ \hline
    \endhead
}{
    \end{longtable}
}
"""

    document_body = f"\\begin{{center}} \\LARGE \\textbf{{{custom_title}}} \\end{{center}}\\vspace{{1cm}}\n\n"

    if is_markscheme:
        document_body += "\\begin{officialmarkscheme}\n"
    else:
        document_body += "\\begin{enumerate}[label=\\textbf{\\arabic*}, leftmargin=0.2cm, labelsep=0.5cm, align=right]\n"

    for i, item in enumerate(cart):
        raw_code = item.get('ms_code') if is_markscheme else item.get('question_code')
        parsed_code = parse_to_pure_latex(raw_code, is_markscheme=is_markscheme)

        if is_markscheme:
            parsed_code = re.sub(r"\\begin\{officialmarkscheme\}", "", parsed_code)
            parsed_code = re.sub(r"\\end\{officialmarkscheme\}", "", parsed_code)

            lines = parsed_code.split('\n')
            new_lines = []

            # Keep track of whether we've printed the main question number for this block yet
            has_printed_first_row = False

            for line in lines:
                if 'Part' in line and 'Answer' in line and 'Marks' in line:
                    continue
                if '&' in line and not line.strip().startswith('\\'):
                    parts = line.split('&', 1)
                    label = parts[0].strip()

                    # 1. Clean out old bold tags and spaces to see what text we actually have
                    clean_label = re.sub(r'\\textbf\{.*?\}', '', label).strip()

                    # 2. Extract just the sub-part characters (letters, roman numerals, brackets)
                    subpart_only = re.sub(r'\d+', '', clean_label).strip()

                    if subpart_only:
                        # If there is an explicit subpart (e.g. "(a)", "b(i)"), force it to pair with the question number
                        new_label = f"\\textbf{{{i + 1}}}{subpart_only}"
                        has_printed_first_row = True
                    else:
                        # If there are no sub-part characters left, it's either a main number or a blank row
                        if not has_printed_first_row:
                            # If it's the very first row of this question block, it MUST display the question number
                            new_label = f"\\textbf{{{i + 1}}}"
                            has_printed_first_row = True
                        else:
                            # If we already printed the main number/subpart above, this is a true blank continuation row
                            new_label = ""

                    new_lines.append(f"{new_label} &" + parts[1])
                elif line.strip():
                    new_lines.append(line)

            document_body += '\n'.join(new_lines) + "\n"

        else:
            document_body += "\\needspace{6cm}\n"
            document_body += "\\item " + parsed_code + "\n"
            if i < len(cart) - 1:
                document_body += "\\vspace{1cm}\n"

    if is_markscheme:
        document_body += "\\end{officialmarkscheme}\n"
    else:
        document_body += "\\end{enumerate}\n"

    full_latex_document = f"""\\documentclass[12pt]{{article}}
\\usepackage[utf8]{{inputenc}}
\\usepackage{{amsmath,amssymb,amsfonts}}
\\usepackage{{tikz}}
\\usetikzlibrary{{arrows.meta, angles, quotes}}
\\usepackage[a4paper, margin=0.8in]{{geometry}}
\\usepackage{{enumitem}}
\\usepackage{{mathptmx}}
\\usepackage{{needspace}}
\\usepackage{{array}}
\\usepackage{{longtable}} 
{macro_definition}
\\setlength{{\\parindent}}{{0pt}}
\\begin{{document}}
{document_body}
\\end{{document}}
"""
    tex_path = f"{output_filename}.tex"
    with open(tex_path, "w", encoding="utf-8") as f:
        f.write(full_latex_document)

    try:
        subprocess.run(["pdflatex", "-interaction=nonstopmode", tex_path], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, text=True)
    except Exception as e:
        import streamlit as st
        st.error(f"LaTeX Compilation Failed: {e}")


# --- 6. REPORTLAB IMAGE EXPORT HELPERS ---
def natural_key(string_):
    return [int(s) if s.isdigit() else s.lower() for s in re.split(r'(\d+)', string_)]


def generate_exam_pdf_images(cart, filename, is_ms=False, custom_title="Exam"):
    c = canvas.Canvas(filename, pagesize=A4)
    width, height = A4
    margin = 50
    y = height - 80
    draw_w = width - (2 * margin)

    if not is_ms and len(cart) > 0:
        c.setFont("Helvetica-Bold", 24)
        c.drawString(margin, y, custom_title)
        y -= 50
        c.setFont("Helvetica", 14)
        c.drawString(margin, y, "Candidate Name: _________________________________________")
        y -= 40
        c.showPage()
        y = height - 80

    for i, item in enumerate(cart):
        image_heights = []
        paths = item.get('ms_paths', []) if is_ms else item.get('question_paths', [])
        for img_path in sorted(list(set(paths)), key=natural_key):
            if os.path.exists(img_path):
                with Image.open(img_path) as img:
                    w, h = img.size
                    image_heights.append((img_path, draw_w * (h / w)))

        if not image_heights: continue

        if y - (25 + image_heights[0][1] + 40) < margin and y != (height - 80):
            c.showPage()
            y = height - 80

        c.setFont("Helvetica-Bold", 16)
        c.drawString(margin, y, f"Question {i + 1} {'(Mark Scheme)' if is_ms else ''}")
        y -= 25

        for img_path, draw_h in image_heights:
            if y - draw_h < margin:
                c.showPage()
                y = height - 100
            c.drawImage(img_path, margin, y - draw_h, width=draw_w, height=draw_h)
            y -= (draw_h + 40)
    c.save()


# --- 7. EXPORT UI ---
st.sidebar.divider()
st.sidebar.subheader("📤 Export Exam")

is_cart_empty = len(st.session_state.test_cart) == 0
custom_exam_title = st.sidebar.text_input("Custom Exam Title", value="Custom Exam Paper")
export_mode = st.sidebar.radio("Rendering Engine:", ["LaTeX (Native Code)", "ReportLab (Images)"])

if st.sidebar.button("🔨 Build Final Files", disabled=is_cart_empty):
    with st.spinner(f"Compiling via {export_mode}..."):
        EXPORT_DIR = "exports"
        os.makedirs(EXPORT_DIR, exist_ok=True)
        safe_prefix = custom_exam_title.strip().replace(' ', '-')

        base_pdf = os.path.join(EXPORT_DIR, f"{safe_prefix}_Paper.pdf")
        ms_pdf = os.path.join(EXPORT_DIR, f"{safe_prefix}_MS.pdf")

        if export_mode == "ReportLab (Images)":
            generate_exam_pdf_images(st.session_state.test_cart, base_pdf, is_ms=False, custom_title=custom_exam_title)
            generate_exam_pdf_images(st.session_state.test_cart, ms_pdf, is_ms=True, custom_title=custom_exam_title)
        else:
            base_temp = "latex_temp_q"
            ms_temp = "latex_temp_ms"
            compile_full_latex_exam(st.session_state.test_cart, base_temp, is_markscheme=False,
                                    custom_title=custom_exam_title)
            compile_full_latex_exam(st.session_state.test_cart, ms_temp, is_markscheme=True,
                                    custom_title=custom_exam_title + " (Mark Scheme)")

            if os.path.exists(f"{base_temp}.pdf"): os.rename(f"{base_temp}.pdf", base_pdf)
            if os.path.exists(f"{ms_temp}.pdf"): os.rename(f"{ms_temp}.pdf", ms_pdf)

        st.session_state.export_paths = {"pdf": base_pdf, "ms": ms_pdf}
        st.session_state.files_ready = True
        st.rerun()

if not is_cart_empty and st.session_state.get("files_ready"):
    st.sidebar.success("✅ Files Ready!")
    st.sidebar.info(f"📂 Saved to: `{os.path.abspath('exports')}`")


# --- 8. TABS (BROWSE, ORGANISE, ANALYTICS) ---
@st.fragment
def render_cart_button(item):
    q_base = item.get('question_base')
    if any(c.get('question_base') == q_base for c in st.session_state.test_cart):
        if st.button("❌ Remove", key=f"rem_{q_base}", use_container_width=True):
            st.session_state.test_cart = [c for c in st.session_state.test_cart if c.get('question_base') != q_base]
            st.rerun(scope="fragment")
    else:
        if st.button("➕ Add", key=f"add_{q_base}", use_container_width=True):
            st.session_state.test_cart.append(item)
            st.rerun(scope="fragment")


tab_explore, tab_preview, tab_analytics = st.tabs(["🔎 Browse", "📝 Organise Exam", "📊 Analytics"])

with tab_explore:
    st.write(f"Showing **{len(filtered_items)}** questions matching filters.")
    BATCH_SIZE = 30
    if "display_limit" not in st.session_state: st.session_state.display_limit = BATCH_SIZE

    display_items = list(filtered_items)
    random.Random(st.session_state.shuffle_seed).shuffle(display_items)

    cols = st.columns(3)
    for idx, item in enumerate(display_items[:st.session_state.display_limit]):
        with cols[idx % 3]:
            with st.container(border=True):
                for q_img_path in item.get('question_paths', []):
                    if os.path.exists(q_img_path): st.image(q_img_path, use_container_width=True)

                meta = item.get('metadata', {})
                st.markdown(f"**Topics:** {', '.join(meta.get('topics', []))}")
                st.caption(f"📖 {item.get('paper')} | 📊 {meta.get('difficulty')} | 📝 {meta.get('marks')} Marks")
                render_cart_button(item)

    if st.session_state.display_limit < len(filtered_items):
        if st.button("🔽 Load More", use_container_width=True, type="primary"):
            st.session_state.display_limit += BATCH_SIZE
            st.rerun()

with tab_preview:
    if not st.session_state.test_cart:
        st.info("Your exam cart is empty.")
    else:
        st.subheader(f"Exam Preview ({len(st.session_state.test_cart)} Questions)")
        for i, item in enumerate(st.session_state.test_cart):
            with st.container(border=True):
                col_info, col_img, col_actions = st.columns([3, 5, 2])

                q_topics = item.get('metadata', {}).get('topics', [])
                q_paper = item.get('paper')

                with col_info:
                    st.markdown(f"### Q{i + 1}")
                    st.markdown(f"**📖 Paper:** {q_paper}")
                    st.markdown(f"**🏷️ Topics:** {', '.join(q_topics)}")
                    st.caption(
                        f"📊 Current Difficulty: {item.get('metadata', {}).get('difficulty', 'Medium')} | 📝 {item.get('metadata', {}).get('marks', 0)} Marks")

                with col_img:
                    paths = item.get('question_paths', [])
                    if paths and os.path.exists(paths[0]):
                        st.image(paths[0], width=400)

                with col_actions:
                    if st.button("🗑️ Remove", key=f"del_prev_{i}", use_container_width=True):
                        st.session_state.test_cart.pop(i)
                        st.rerun()

                    st.divider()

                    diff_options = ["Easy", "Medium", "Hard"]
                    curr_diff = item.get('metadata', {}).get('difficulty', 'Medium')
                    default_idx = diff_options.index(curr_diff) if curr_diff in diff_options else 1

                    target_diff = st.selectbox(
                        "Target Difficulty",
                        diff_options,
                        index=default_idx,
                        key=f"diff_sel_{i}"
                    )

                    current_cart_bases = {c.get('question_base') for c in st.session_state.test_cart}
                    same_topic_pool = [
                        candidate for candidate in db
                        if candidate.get('question_base') not in current_cart_bases
                           and candidate.get('paper') == q_paper
                           and any(t in q_topics for t in candidate.get('metadata', {}).get('topics', []))
                           and candidate.get('metadata', {}).get('difficulty') == target_diff
                    ]

                    st.caption(f"({len(same_topic_pool)} unique options available)")

                    if st.button(
                            "🎲 Randomise",
                            key=f"rand_prev_{i}",
                            use_container_width=True,
                            disabled=not same_topic_pool,
                            help="Swap this question with a random variant from the same paper and topic matching the target difficulty."
                    ):
                        new_q = random.choice(same_topic_pool)
                        st.session_state.test_cart[i] = new_q
                        st.rerun()

                    if st.button(
                            "➕ Insert Next",
                            key=f"insert_prev_{i}",
                            use_container_width=True,
                            disabled=not same_topic_pool,
                            help="Add another question from this topic and paper configuration directly underneath."
                    ):
                        new_q = random.choice(same_topic_pool)
                        st.session_state.test_cart.insert(i + 1, new_q)
                        st.rerun()

with tab_analytics:
    if not st.session_state.test_cart:
        st.info("Add questions to see analytics!")
    else:
        st.subheader("📊 Paper Balance")
        diff_marks, topic_marks = {}, {}
        for item in st.session_state.test_cart:
            meta = item.get('metadata', {})
            d, m = meta.get('difficulty', 'Medium'), meta.get('marks', 0)
            diff_marks[d] = diff_marks.get(d, 0) + m
            for t in meta.get('topics', ['Uncategorized']):
                topic_marks[t] = topic_marks.get(t, 0) + m

        c1, c2 = st.columns(2)
        with c1:
            st.write("**Marks by Difficulty**")
            st.bar_chart(
                pd.DataFrame(list(diff_marks.items()), columns=["Difficulty", "Marks"]).set_index("Difficulty"))
        with c2:
            st.write("**Marks by Topic**")
            st.bar_chart(pd.DataFrame(list(topic_marks.items()), columns=["Topic", "Marks"]).set_index("Topic"))