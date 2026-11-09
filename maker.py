import hashlib
import io
import json
import os
import re
import subprocess
import tempfile
import urllib.parse
import urllib.request
import zipfile
from datetime import date
import streamlit as st
import streamlit.components.v1 as components
from PIL import Image

# System PATH override to support Mac/Linux pdflatex
current_path = os.environ.get("PATH", "")
homebrew_paths = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
if homebrew_paths not in current_path:
    os.environ["PATH"] = f"{homebrew_paths}:{current_path}"

try:
    from pdf2image import convert_from_path
    HAS_PDF2IMAGE = True
except ImportError:
    HAS_PDF2IMAGE = False

TIKZ_CACHE_DIR = ".tikz_cache"
os.makedirs(TIKZ_CACHE_DIR, exist_ok=True)

# =============================================================================
# Streamlit Page Configuration
# =============================================================================
st.set_page_config(
    page_title="Math Exam Paper Builder",
    page_icon="📝",
    layout="wide",
    initial_sidebar_state="expanded",
)

# =============================================================================
# Cached Helpers & Data Loading Functions
# =============================================================================

@st.cache_data(show_spinner=False)
def clean_latex_for_katex(text: str) -> str:
    """Cached LaTeX cleaner to avoid regex overhead on every rerun."""
    if not text:
        return ""
    
    cleaned = text.replace("\\n", "\n")
    cleaned = re.sub(r"\\\((.*?)\\\)", r"$\1$", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"\\\[(.*?)\\\]", r"$$\1$$", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"\\\\\s*\[[^\]]+\]", "\n\n", cleaned)
    cleaned = cleaned.replace(r"\\", "\n\n")
    cleaned = re.sub(r"\\hspace\*?\{[^}]*\}", " ", cleaned)

    paragraphs = [p.strip() for p in cleaned.split("\n") if p.strip()]
    return "\n\n".join(paragraphs)


def load_default_master_bank() -> list:
    """Loads master_bank.json and disambiguates duplicate IDs in background memory."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    candidate_paths = [
        os.path.join(base_dir, "master_bank.json"),
        "master_bank.json",
    ]
    
    for path in candidate_paths:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw_data = json.load(f)

                seen_ids = set()
                sanitized_bank = []

                for idx, q in enumerate(raw_data):
                    base_id = str(q.get("id") or f"q_{idx}")
                    q_id = base_id
                    
                    counter = 1
                    while q_id in seen_ids:
                        q_id = f"{base_id}_dup{counter}"
                        counter += 1

                    q["id"] = q_id
                    seen_ids.add(q_id)
                    sanitized_bank.append(q)

                return sanitized_bank
            except Exception as e:
                st.sidebar.error(f"Failed to parse {path}: {e}")
    return []


def load_syllabus_data() -> dict:
    """Loads syllabus.json data from the app directory."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    candidate_paths = [
        os.path.join(base_dir, "syllabus.json"),
        "syllabus.json",
    ]
    for path in candidate_paths:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    return {}


def build_subtopic_map(syllabus_data: dict) -> dict:
    """Maps subtopic codes (e.g. 'E1.1') to their syllabus metadata dictionary."""
    s_map = {}
    topics = syllabus_data.get("topics", [])
    for topic in topics:
        for sub in topic.get("subtopics", []):
            code = sub.get("code")
            if code:
                s_map[code.strip()] = sub
    return s_map


def get_selected_subtopic_titles(selected_questions: list, subtopic_map: dict) -> str:
    """Dynamically builds a subtitle string from subtopics of selected questions only."""
    if not selected_questions:
        return "Paper 2 (Extended) - Non-Calculator"

    seen_codes = set()
    formatted_titles = []

    for q in selected_questions:
        code = str(q.get("subtopic", "")).strip()
        if code and code not in seen_codes:
            seen_codes.add(code)
            sub_info = subtopic_map.get(code)
            if sub_info and sub_info.get("title"):
                title = sub_info["title"].strip()
                formatted_titles.append(f"{code} {title}")
            elif q.get("subtopic_title"):
                formatted_titles.append(f"{code} {q['subtopic_title'].strip()}")
            else:
                formatted_titles.append(code)

    return ", ".join(formatted_titles) if formatted_titles else "Paper 2 (Extended) - Non-Calculator"


def calculate_question_marks(question: dict) -> int:
    """Calculates total marks for a question item."""
    if "total_marks" in question and question["total_marks"] > 0:
        return question["total_marks"]
    
    marks = 0
    for part in question.get("parts", []):
        part_marks = part.get("marks", 0)
        subparts = part.get("subparts", [])
        if subparts:
            part_marks += sum(sp.get("marks", 0) for sp in subparts)
        marks += part_marks
    return marks


# =============================================================================
# Cached TikZ Diagram Compiler & Renderer (Memory + Disk Cache)
# =============================================================================

def fetch_quicklatex_fallback(tikz_code: str):
    """Fallback QuickLaTeX web API fetcher if local pdf2image is unavailable."""
    if not tikz_code or not tikz_code.strip():
        return None

    code = tikz_code.strip()
    if not code.startswith(r"\begin{tikzpicture}"):
        code = f"\\begin{{tikzpicture}}\n{code}\n\\end{{tikzpicture}}"

    preamble = (
        r"\usepackage{amsmath}\usepackage{amsfonts}\usepackage{amssymb}"
        r"\usepackage{tikz}\usetikzlibrary{arrows.meta, angles, quotes, calc, positioning, patterns}"
    )
    payload = {
        "formula": code,
        "fsize": "16px",
        "fcolor": "000000",
        "mode": "0",
        "out": "0",
        "preamble": preamble,
    }

    try:
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request("https://www.quicklatex.com/gim.php", data=data)
        with urllib.request.urlopen(req, timeout=8) as response:
            res_text = response.read().decode("utf-8")
            lines = [line.strip() for line in res_text.splitlines() if line.strip()]
            if lines and lines[0].startswith("0") and len(lines) >= 2:
                img_url = lines[1].split()[0]
                if img_url.startswith("http://"):
                    img_url = "https://" + img_url[7:]
                img_req = urllib.request.Request(img_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(img_req, timeout=8) as img_resp:
                    return img_resp.read()
    except Exception:
        pass
    return None


@st.cache_data(show_spinner=False)
def compile_tikz_to_png_bytes(tikz_code: str) -> bytes:
    """Compiles TikZ to PNG bytes, caching results in memory & disk to avoid re-running pdflatex."""
    if not tikz_code or not tikz_code.strip():
        return None

    code = tikz_code.strip()
    if "\\begin{tikzpicture}" not in code:
        code = "\\begin{tikzpicture}\n" + code + "\n\\end{tikzpicture}"

    code_hash = hashlib.md5(code.encode("utf-8")).hexdigest()
    cache_path = os.path.join(TIKZ_CACHE_DIR, f"{code_hash}.png")

    if os.path.exists(cache_path):
        with open(cache_path, "rb") as f:
            return f.read()

    full_tex = (
        "\\documentclass[border=4pt]{standalone}\n"
        "\\usepackage{tikz}\n"
        "\\usetikzlibrary{arrows.meta, angles, quotes, calc, positioning, patterns}\n"
        "\\usepackage{amsmath,amssymb,amsfonts}\n"
        "\\begin{document}\n"
        + code +
        "\n\\end{document}\n"
    )

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
                timeout=15,
            )

            if os.path.exists(pdf_path) and HAS_PDF2IMAGE:
                images = convert_from_path(pdf_path, dpi=300)
                if images:
                    img = images[0]
                    min_width = 800
                    if img.width < min_width:
                        new_height = int(img.height * (min_width / img.width))
                        img = img.resize((min_width, new_height), Image.Resampling.LANCZOS)

                    buffer = io.BytesIO()
                    img.save(buffer, format="PNG")
                    img_bytes = buffer.getvalue()

                    with open(cache_path, "wb") as f:
                        f.write(img_bytes)

                    return img_bytes
        except Exception:
            pass

    return fetch_quicklatex_fallback(tikz_code)


def render_tikz(tikz_code: str):
    """Wrapper to render cached TikZ diagram."""
    img_bytes = compile_tikz_to_png_bytes(tikz_code)
    if img_bytes:
        st.image(img_bytes, caption="Diagram", use_container_width=True)
    else:
        st.info("🎨 *[TikZ Diagram Included - Rendered in PDF output]*")


# =============================================================================
# Session State Initialization
# =============================================================================
if "master_bank" not in st.session_state or not st.session_state.master_bank:
    st.session_state.master_bank = load_default_master_bank()

if "visible_count" not in st.session_state:
    st.session_state.visible_count = 9

syllabus_data = load_syllabus_data()
subtopic_map = build_subtopic_map(syllabus_data)

defaults = {
    "selected_ids": [],
    "doc_title": "IGCSE 0607 Practice Questions",
    "doc_subtitle": "Paper 2 (Extended) - Non-Calculator",
    "override_subtitle": False,
    "doc_author": "",
    "doc_date": r"\today",
    "override_marks": False,
    "custom_total_marks": 0,
    "remove_nav": True,
    "clean_header": True,
    "use_needspace": False,
    "needspace_val": 4.0,
    "export_qp": True,
    "export_ms": True,
    "export_combined": False,
    "export_config": False,
    "show_preview_ms": False,
    "filter_topics": [],
    "filter_subtopics": [],
    "filter_diff": [],
    "filter_papers": [],
    "compiled_files": {},
}

for key, val in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = val

# =============================================================================
# Isolated Question Card Fragment
# =============================================================================

@st.fragment
def render_question_card(q: dict, subtopic_map: dict):
    """Renders a single question card as an isolated fragment to prevent full-page fading on selection."""
    qid = q["id"]
    is_selected = qid in st.session_state.selected_ids
    q_marks = calculate_question_marks(q)
    q_topic = q.get("topic", "-")
    q_subtopic = str(q.get("subtopic", "")).strip()

    with st.container(border=True):
        top_l, top_r = st.columns([3, 1])
        with top_l:
            st.markdown(f"📌 **{q_topic}**")
        with top_r:
            st.caption(f"**[{q_marks} Marks]**")

        if q_subtopic:
            sub_info = subtopic_map.get(q_subtopic)
            if sub_info:
                sub_title = sub_info.get("title", "")
                st.caption(f"🏷️ **Subtopic:** `{q_subtopic}` - **{sub_title}**")
                with st.popover("📖 Syllabus Info", use_container_width=False):
                    st.markdown(f"**Code:** `{q_subtopic}`")
                    st.markdown(f"**Title:** {sub_title}")
                    if sub_info.get("objectives"):
                        st.markdown("**Objectives:**")
                        for obj in sub_info["objectives"]:
                            st.markdown(f"- {obj}")
                    if notes_text := sub_info.get("notes_and_examples", {}).get("text"):
                        st.markdown("**Notes & Examples:**")
                        st.markdown(notes_text)
            else:
                st.caption(f"🏷️ **Subtopic:** {q_subtopic}")

        st.caption(f"⚡ Diff: {q.get('difficulty', '-')} | Paper: {q.get('paper_type', '-')}")
        st.markdown("---")

        if q.get("intro_text"):
            st.markdown(clean_latex_for_katex(q["intro_text"]))
        
        if q.get("intro_diagram_tikz"):
            render_tikz(q["intro_diagram_tikz"])

        for part in q.get("parts", []):
            p_label = f"**{part.get('label', '')}** " if part.get("label") else ""
            p_prompt = clean_latex_for_katex(part.get("prompt", ""))
            p_marks = f" *[{part.get('marks')}m]*" if part.get("marks", 0) > 0 else ""
            st.markdown(f"{p_label}{p_prompt}{p_marks}")

            if part.get("diagram_tikz"):
                render_tikz(part["diagram_tikz"])

            for subpart in part.get("subparts", []):
                sp_label = f"&nbsp;&nbsp;**{subpart.get('label', '')}** " if subpart.get("label") else ""
                sp_prompt = clean_latex_for_katex(subpart.get("prompt", ""))
                sp_marks = f" *[{subpart.get('marks')}m]*" if subpart.get("marks", 0) > 0 else ""
                st.markdown(f"{sp_label}{sp_prompt}{sp_marks}")

                if subpart.get("diagram_tikz"):
                    render_tikz(subpart["diagram_tikz"])

                if st.session_state.show_preview_ms and subpart.get("markscheme"):
                    with st.expander("Markscheme", expanded=False):
                        for ms in subpart["markscheme"]:
                            st.markdown(f"- {clean_latex_for_katex(ms)}")

            if st.session_state.show_preview_ms and part.get("markscheme") and not part.get("subparts"):
                with st.expander("Markscheme", expanded=False):
                    for ms in part["markscheme"]:
                        st.markdown(f"- {clean_latex_for_katex(ms)}")

        st.markdown("---")

        if is_selected:
            if st.button("➖ Remove from Test", key=f"card_rem_{qid}", type="secondary", use_container_width=True):
                st.session_state.selected_ids.remove(qid)
                st.rerun(scope="app")
        else:
            if st.button("➕ Add to Test", key=f"card_add_{qid}", type="primary", use_container_width=True):
                st.session_state.selected_ids.append(qid)
                st.rerun(scope="app")


# =============================================================================
# LEFT SIDEBAR: CONTROLS, FILTERS & COMPILER
# =============================================================================
st.sidebar.title("⚙️ Controls & Filters")

# Direct Navigation Link to Standalone Viewer Page
st.sidebar.page_link("pages/viewer.py", label="📚 Open PDF Viewer Page", icon="📄")
st.sidebar.markdown("---")

if st.session_state.master_bank:
    st.sidebar.caption(f"🟢 **{len(st.session_state.master_bank)}** questions loaded from bank.")
else:
    st.sidebar.error("🔴 `master_bank.json` not found in current directory.")

# --- 1. Question Bank Filters ---
st.sidebar.subheader("🔍 Question Bank Filters")

all_topics = sorted(list({q.get("topic") for q in st.session_state.master_bank if q.get("topic")}))
sel_topics = st.sidebar.multiselect("Topic", options=all_topics, key="filter_topics")

def natural_sort_key(s: str):
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r"(\d+)", str(s))]

all_subtopics = sorted(
    list({q.get("subtopic") for q in st.session_state.master_bank if q.get("subtopic")}),
    key=natural_sort_key
)

def format_subtopic(code: str) -> str:
    sub_info = subtopic_map.get(str(code).strip())
    if sub_info and sub_info.get("title"):
        return f"{code} - {sub_info['title']}"
    return str(code)

sel_subtopics = st.sidebar.multiselect(
    "Subtopic",
    options=all_subtopics,
    format_func=format_subtopic,
    key="filter_subtopics"
)

all_diff = sorted(list({q.get("difficulty") for q in st.session_state.master_bank if q.get("difficulty")}))
sel_diff = st.sidebar.multiselect("Difficulty", options=all_diff, key="filter_diff")

all_papers = sorted(list({q.get("paper_type") for q in st.session_state.master_bank if q.get("paper_type")}))
sel_papers = st.sidebar.multiselect("Paper Type", options=all_papers, key="filter_papers")

filter_state_key = (tuple(sel_topics), tuple(sel_subtopics), tuple(sel_diff), tuple(sel_papers))
if "prev_filter_state" not in st.session_state or st.session_state.prev_filter_state != filter_state_key:
    st.session_state.visible_count = 9
    st.session_state.prev_filter_state = filter_state_key

filtered_questions = [
    q for q in st.session_state.master_bank
    if (not sel_topics or q.get("topic") in sel_topics)
    and (not sel_subtopics or q.get("subtopic") in sel_subtopics)
    and (not sel_diff or q.get("difficulty") in sel_diff)
    and (not sel_papers or q.get("paper_type") in sel_papers)
]

filtered_ids = [q["id"] for q in filtered_questions]

current_selected_objs = [
    q for q in st.session_state.master_bank if q.get("id") in st.session_state.selected_ids
]
auto_marks = sum(calculate_question_marks(q) for q in current_selected_objs)
final_total_marks = st.session_state.custom_total_marks if st.session_state.override_marks else auto_marks

auto_subtopics_subtitle = get_selected_subtopic_titles(current_selected_objs, subtopic_map)

# --- 2. Quick Actions & Summary Metrics ---
st.sidebar.markdown("---")
st.sidebar.subheader("📊 Summary & Actions")

sb_m1, sb_m2, sb_m3 = st.sidebar.columns(3)
sb_m1.metric("Filtered", len(filtered_questions))
sb_m2.metric("Selected", len(st.session_state.selected_ids))
sb_m3.metric("Marks", final_total_marks)

sb_btn_col1, sb_btn_col2 = st.sidebar.columns(2)
with sb_btn_col1:
    if st.button("Select All", key="sb_select_all", use_container_width=True):
        st.session_state.selected_ids = list(set(st.session_state.selected_ids + filtered_ids))
        st.rerun()

with sb_btn_col2:
    if st.button("Clear All", key="sb_clear_all", use_container_width=True):
        st.session_state.selected_ids = []
        st.rerun()

st.sidebar.toggle("Show Markschemes in Cards", key="show_preview_ms")

# --- 3. Paper Metadata ---
st.sidebar.markdown("---")
st.sidebar.subheader("📝 Paper Header Info")
st.session_state.doc_title = st.sidebar.text_input("Title", st.session_state.doc_title)

override_sub = st.sidebar.checkbox("Override Subtitle", value=st.session_state.override_subtitle, key="override_subtitle")

if not override_sub:
    st.session_state.doc_subtitle = auto_subtopics_subtitle
    st.sidebar.text_input(
        "Subtitle (Auto-populated from selected subtopics)",
        value=st.session_state.doc_subtitle,
        disabled=True,
        help="Check 'Override Subtitle' above to manually edit this string."
    )
else:
    st.session_state.doc_subtitle = st.sidebar.text_input("Subtitle (Custom Override)", st.session_state.doc_subtitle)

st.session_state.doc_author = st.sidebar.text_input("Author", st.session_state.doc_author)

use_calendar = st.sidebar.checkbox("📅 Select Date from Calendar", value=False)
if use_calendar:
    picked_date = st.sidebar.date_input("Exam Date", value=date.today())
    st.session_state.doc_date = picked_date.strftime("%d %B %Y")
else:
    st.session_state.doc_date = st.sidebar.text_input("Date String / LaTeX", st.session_state.doc_date)

st.session_state.override_marks = st.sidebar.checkbox("Override Total Marks", value=st.session_state.override_marks)
if st.session_state.override_marks:
    st.session_state.custom_total_marks = st.sidebar.number_input("Custom Total Marks", value=auto_marks, min_value=0)
    final_total_marks = st.session_state.custom_total_marks

# --- 4. Presets & JSON Export ---
st.sidebar.markdown("---")
st.sidebar.subheader("📂 Paper Presets")

preset_file = st.sidebar.file_uploader(
    "Load Saved Config", 
    type=["json"], 
    help="Upload a saved paper configuration JSON."
)

if preset_file is not None:
    try:
        config = json.load(preset_file)
        if isinstance(config, dict):
            st.session_state.doc_title = config.get("title", st.session_state.doc_title)
            st.session_state.doc_subtitle = config.get("subtitle", st.session_state.doc_subtitle)
            st.session_state.override_subtitle = config.get("override_subtitle", True)
            st.session_state.doc_author = config.get("author", st.session_state.doc_author)
            st.session_state.doc_date = config.get("date", st.session_state.doc_date)
            st.session_state.selected_ids = config.get("selected_ids", st.session_state.selected_ids)
            st.session_state.remove_nav = config.get("remove_nav", st.session_state.remove_nav)
            st.session_state.clean_header = config.get("clean_header", st.session_state.clean_header)
            st.session_state.use_needspace = config.get("use_needspace", st.session_state.use_needspace)
            st.session_state.needspace_val = config.get("needspace_val", st.session_state.needspace_val)
            st.session_state.export_qp = config.get("export_qp", st.session_state.export_qp)
            st.session_state.export_ms = config.get("export_ms", st.session_state.export_ms)
            st.session_state.export_combined = config.get("export_combined", st.session_state.export_combined)
            st.session_state.export_config = config.get("export_config", st.session_state.export_config)
            st.sidebar.success("Config loaded!")
    except Exception as err:
        st.sidebar.error(f"Error loading preset: {err}")

export_config_data = {
    "title": st.session_state.doc_title,
    "subtitle": st.session_state.doc_subtitle,
    "override_subtitle": st.session_state.override_subtitle,
    "author": st.session_state.doc_author,
    "date": st.session_state.doc_date,
    "total_marks": final_total_marks,
    "selected_ids": st.session_state.selected_ids,
    "remove_nav": st.session_state.remove_nav,
    "clean_header": st.session_state.clean_header,
    "use_needspace": st.session_state.use_needspace,
    "needspace_val": st.session_state.needspace_val,
    "export_qp": st.session_state.export_qp,
    "export_ms": st.session_state.export_ms,
    "export_combined": st.session_state.export_combined,
    "export_config": st.session_state.export_config,
}

if st.session_state.selected_ids:
    selected_questions_json = [
        q for q in st.session_state.master_bank if q.get("id") in st.session_state.selected_ids
    ]
    st.sidebar.download_button(
        "📄 Export Selected Questions (JSON)",
        data=json.dumps(selected_questions_json, indent=2).encode("utf-8"),
        file_name="selected_questions.json",
        mime="application/json",
        use_container_width=True,
    )

# --- 5. COMPILE PDF SECTION ---
st.sidebar.markdown("---")
st.sidebar.subheader("🚀 Compile & Export PDF")

st.sidebar.markdown("**Select Output Files:**")
st.sidebar.checkbox("📄 Question Paper (QP)", key="export_qp")
st.sidebar.checkbox("📑 Markscheme (MS)", key="export_ms")
st.sidebar.checkbox("📚 Combined File (QP + MS)", key="export_combined")
st.sidebar.checkbox("💾 Paper Config (JSON)", key="export_config")

st.sidebar.markdown("---")
st.session_state.remove_nav = st.sidebar.checkbox("Remove Cover Navigation Page (--remove-nav)", value=st.session_state.remove_nav)
st.session_state.clean_header = st.sidebar.checkbox("Clean Running Header (Page # Only)", value=st.session_state.clean_header)

st.session_state.use_needspace = st.sidebar.checkbox("Continuous Layout (--needspace)", value=st.session_state.use_needspace)
if st.session_state.use_needspace:
    st.session_state.needspace_val = st.sidebar.number_input(
        "Needspace (cm)", 
        value=float(st.session_state.needspace_val), 
        min_value=1.0, 
        max_value=15.0, 
        step=0.5
    )

extra_cli_args = st.sidebar.text_input("Extra CLI Arguments", "")

compile_btn_sb = st.sidebar.button(
    "🚀 Compile PDF(s)",
    type="primary",
    key="sb_compile_btn",
    use_container_width=True,
)

if compile_btn_sb:
    if not st.session_state.selected_ids:
        st.sidebar.error("Please select at least one question to compile.")
    elif not (st.session_state.export_qp or st.session_state.export_ms or st.session_state.export_combined or st.session_state.export_config):
        st.sidebar.error("Please select at least one output file option (QP, MS, Combined, or Config).")
    else:
        selected_questions_data = [
            q for q in st.session_state.master_bank if q.get("id") in st.session_state.selected_ids
        ]
        
        base_filename = st.session_state.doc_title.replace(" ", "_")
        targets = []
        if st.session_state.export_qp:
            targets.append(("qp", f"{base_filename}_qp.pdf", "📄 Download Question Paper (QP)"))
        if st.session_state.export_ms:
            targets.append(("ms", f"{base_filename}_ms.pdf", "📑 Download Markscheme (MS)"))
        if st.session_state.export_combined:
            targets.append(("combined", f"{base_filename}_combined.pdf", "📚 Download Combined PDF"))

        compiled_results = {}

        if st.session_state.export_config:
            compiled_results["config"] = {
                "data": json.dumps(export_config_data, indent=2).encode("utf-8"),
                "filename": f"{base_filename}_config.json",
                "label": "💾 Download Paper Config (JSON)",
                "mime": "application/json",
            }

        if targets:
            with st.spinner("Compiling requested PDF file(s)..."):
                with tempfile.TemporaryDirectory() as tmpdirname:
                    json_temp_path = os.path.join(tmpdirname, "paper_input.json")
                    with open(json_temp_path, "w", encoding="utf-8") as f:
                        json.dump(selected_questions_data, f, indent=2)

                    for mode, filename, label in targets:
                        pdf_output_path = os.path.join(tmpdirname, filename)
                        cmd = [
                            "python", "compile_pdf.py",
                            "--input", json_temp_path,
                            "--output", pdf_output_path,
                            "--mode", mode,
                            "--title", st.session_state.doc_title,
                            "--subtitle", st.session_state.doc_subtitle,
                            "--author", st.session_state.doc_author,
                            "--date", st.session_state.doc_date,
                            "--total-marks", str(final_total_marks),
                        ]
                        if st.session_state.remove_nav:
                            cmd.append("--remove-nav")
                        if st.session_state.clean_header:
                            cmd.append("--clean-header")
                        if st.session_state.use_needspace:
                            cmd.extend(["--needspace", str(st.session_state.needspace_val)])
                        if extra_cli_args.strip():
                            cmd.extend(extra_cli_args.strip().split())

                        try:
                            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
                            if res.returncode == 0 and os.path.exists(pdf_output_path):
                                with open(pdf_output_path, "rb") as pdf_file:
                                    compiled_results[mode] = {
                                        "data": pdf_file.read(),
                                        "filename": filename,
                                        "label": label,
                                        "mime": "application/pdf",
                                    }
                            else:
                                st.sidebar.error(f"Compilation error for {mode.upper()}:")
                                st.sidebar.code(res.stderr or res.stdout)
                        except Exception as e:
                            st.sidebar.error(f"Failed to execute compile script: {e}")

        st.session_state.compiled_files = compiled_results

if st.session_state.compiled_files:
    st.sidebar.markdown("### 📥 Download Output Files")
    
    for mode, f_info in st.session_state.compiled_files.items():
        st.sidebar.download_button(
            label=f_info["label"],
            data=f_info["data"],
            file_name=f_info["filename"],
            mime=f_info.get("mime", "application/pdf"),
            use_container_width=True,
            key=f"download_{mode}",
        )

    if len(st.session_state.compiled_files) > 1:
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for mode, f_info in st.session_state.compiled_files.items():
                zip_file.writestr(f_info["filename"], f_info["data"])
        zip_buffer.seek(0)
        
        st.sidebar.download_button(
            label="📦 Download All as ZIP (.zip)",
            data=zip_buffer,
            file_name=f"{st.session_state.doc_title.replace(' ', '_')}_all_files.zip",
            mime="application/zip",
            type="primary",
            use_container_width=True,
            key="download_zip_all",
        )

# =============================================================================
# MAIN CONTENT AREA
# =============================================================================
st.title("📝 Math Question Bank & Paper Builder")

if not st.session_state.master_bank:
    st.error("⚠️ Question bank empty. Ensure `master_bank.json` is located in your app directory.")
    st.stop()

total_items = len(filtered_questions)
visible_count = st.session_state.visible_count
display_questions = filtered_questions[:visible_count]

st.subheader(f"Questions ({total_items} Found | Showing {min(visible_count, total_items)} of {total_items})")

if not display_questions:
    st.info("No questions match the current sidebar filters.")
else:
    cols = st.columns(3)
    for idx, q in enumerate(display_questions):
        with cols[idx % 3]:
            render_question_card(q, subtopic_map)

    # Auto-populate / Infinite Scroll section
    if visible_count < total_items:
        st.markdown("---")
        lm_col1, lm_col2, lm_col3 = st.columns([1, 2, 1])
        with lm_col2:
            if st.button("🔽 Load More Questions", key="btn_load_more", use_container_width=True, type="secondary"):
                st.session_state.visible_count += 9
                st.rerun()

        components.html(
            """
            <div id="scroll-sentinel" style="height: 10px;"></div>
            <script>
            const observer = new IntersectionObserver((entries) => {
                entries.forEach(entry => {
                    if (entry.isIntersecting) {
                        const buttons = window.parent.document.querySelectorAll('button');
                        for (let btn of buttons) {
                            if (btn.innerText.includes('Load More Questions')) {
                                btn.click();
                                break;
                            }
                        }
                    }
                });
            }, { threshold: 0.1 });

            const target = document.getElementById('scroll-sentinel');
            if (target) {
                observer.observe(target);
            }
            </script>
            """,
            height=20,
        )