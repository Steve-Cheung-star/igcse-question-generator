import streamlit as st
import json
import os
import re
import random
import pandas as pd
import datetime
import zipfile
from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from docx import Document
from docx.shared import Inches
import textwrap

# --- 1. CONFIGURATION & ACCESS CONTROL ---
st.set_page_config(layout="wide", page_title="Exam Maker", page_icon="📝")

IS_LOCAL = os.path.exists(".local_mode")
ACCESS_CODE = "6767"

if "authenticated" not in st.session_state:
    st.session_state.authenticated = IS_LOCAL

if not st.session_state.authenticated:
    with st.sidebar:
        st.title("🔐 Access Control")
        user_input = st.text_input("Enter Access Code", type="password")
        if user_input == ACCESS_CODE:
            st.session_state.authenticated = True
            st.rerun()
        elif user_input != "":
            st.error("Incorrect Code")
    st.title("📚 Exam Maker")
    st.info("Please enter the access code in the sidebar.")
    st.stop()

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

# --- 3. DATA LOADING (JSON Optimized) ---
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
        # 1. Grab the nested metadata block first
        meta = item.get("metadata", {})
        
        # 2. Extract topics from inside the metadata block
        topics_raw = meta.get("topics", ["Uncategorized"])
        if isinstance(topics_raw, str):
            topics_raw = [topics_raw]

        q_paths = item.get("question_paths", [])
        if isinstance(q_paths, str): q_paths = [q_paths]

        ms_paths = item.get("ms_paths", [])
        if isinstance(ms_paths, str): ms_paths = [ms_paths]

        db.append({
            # Use the actual exact keys from your JSON
            "question_base": str(item.get("question_base", f"q_{i}")),
            "paper": str(item.get("paper", "General Paper")),
            "question_paths": q_paths,
            "ms_paths": ms_paths,
            "metadata": {
                "topics": topics_raw,
                "difficulty": str(meta.get("difficulty", "Medium")),
                "marks": int(meta.get("marks", 0)),
                "year": str(meta.get("year", "N/A"))
            }
        })
    return db

db = load_db()

@st.cache_data
def get_all_papers(database):
    return sorted(list(set([item.get('paper') for item in database if item.get('paper')])))

@st.cache_data
def get_all_topics(database):
    return sorted(list(set([t for item in database for t in item.get('metadata', {}).get('topics', [])])))

@st.cache_data
def get_all_difficulties(database):
    return sorted(list(set([item.get('metadata', {}).get('difficulty') for item in database if item.get('metadata', {}).get('difficulty')])))

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
    paper_match = not sel_papers or item.get('paper') in sel_papers
    topic_match = not sel_topics or any(t in sel_topics for t in meta.get('topics', []))
    diff_match = not sel_diffs or meta.get('difficulty') in sel_diffs

    if paper_match and topic_match and diff_match:
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

st.sidebar.divider()
total_marks = sum([item.get('metadata', {}).get('marks', 0) for item in st.session_state.test_cart])
est_time = int(total_marks * 1.2)

col_metric, col_sync = st.sidebar.columns([2, 1])
with col_metric:
    st.metric("Questions in Cart", len(st.session_state.test_cart))
with col_sync:
    st.write("")
    if st.button("Sync", use_container_width=True, type="secondary"):
        st.rerun()

st.sidebar.metric("Total Marks", total_marks)
st.sidebar.caption(f"⏱️ Estimated Time: ~{est_time} mins")

# --- 5. EXPORT HELPERS ---
def draw_bold_page_number(c, width, height):
    page_num = c.getPageNumber()
    c.saveState()
    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(width / 2.0, height - 55, str(page_num))
    c.restoreState()

def get_exam_title(cart):
    papers = sorted(list({item.get('paper') for item in cart if item.get('paper')}))
    if not papers: return "Custom Exam Paper"
    return f"{', '.join(papers)} Exam Paper"

def natural_key(string_):
    return [int(s) if s.isdigit() else s.lower() for s in re.split(r'(\d+)', string_)]

def draw_topic_checklist(c, cart, width, height, margin):
    top_margin = 80
    c.showPage()
    draw_bold_page_number(c, width, height)
    y = height - top_margin
    c.setFont("Helvetica-Bold", 18)
    c.drawString(margin, y, "Topic Checklist & Self-Reflection")
    y -= 30
    c.setFont("Helvetica", 11)
    c.drawString(margin, y, "Review the topics below and circle your confidence level.")
    y -= 40

    c.setFont("Helvetica-Bold", 12)
    c.drawString(margin, y, "Question")
    c.drawString(margin + 80, y, "Topic Details")
    c.drawString(margin + 400, y, "Confidence")
    y -= 10
    c.line(margin, y, width - margin, y)
    y -= 25

    for i, item in enumerate(cart):
        if y < 150:
            c.showPage()
            draw_bold_page_number(c, width, height)
            y = height - top_margin
            c.setFont("Helvetica-Bold", 12)
            c.drawString(margin, y, "Question")
            c.drawString(margin + 80, y, "Topic Details")
            c.drawString(margin + 400, y, "Confidence")
            y -= 10
            c.line(margin, y, width - margin, y)
            y -= 25

        topics = item.get('metadata', {}).get('topics', [])
        c.setFont("Helvetica", 10)
        c.drawString(margin, y, f"Question {i + 1}")
        c.drawString(margin + 400, y, "[ Low ]  [ Med ]  [ High ]")

        topic_text = ", ".join(topics) if topics else "General"
        t_chunks = textwrap.wrap(topic_text, width=60)
        
        current_y = y
        c.setFont("Helvetica-Bold", 10)
        for j, chunk in enumerate(t_chunks):
            c.drawString(margin + 80, current_y, chunk)
            if j < len(t_chunks) - 1: current_y -= 12

        y = current_y - 20
        c.setDash(1, 2)
        c.setStrokeColorRGB(0.7, 0.7, 0.7)
        c.line(margin, y + 10, width - margin, y + 10)
        c.setDash(1, 0)
        c.setStrokeColorRGB(0, 0, 0)

def generate_combined_pdf(cart, filename, custom_title="", include_checklist=False, include_formula=False):
    c = canvas.Canvas(filename, pagesize=A4)
    width, height = A4
    margin = 50
    top_margin = 80
    y = height - top_margin
    draw_w = width - (2 * margin)

    title_to_use = custom_title if custom_title.strip() else get_exam_title(cart)

    if len(cart) > 0:
        draw_bold_page_number(c, width, height)
        c.setFont("Helvetica-Bold", 24)
        c.drawString(margin, y, title_to_use)
        y -= 25
        c.drawString(margin, y, "(With Mark Schemes)")
        y -= 50
        c.setFont("Helvetica", 14)
        c.drawString(margin, y, "Candidate Name: _________________________________________")
        y -= 40
        total_m = sum([item.get('metadata', {}).get('marks', 0) for item in cart])
        c.drawString(margin, y, f"Time Allowed: {int(total_m * 1.2)} minutes")
        y -= 25
        c.drawString(margin, y, f"Total Marks: {total_m}")
        y -= 60
        c.setFont("Helvetica-Bold", 14)
        c.drawString(margin, y, "Instructions:")
        y -= 25
        c.setFont("Helvetica", 12)
        for inst in ["• Answer all questions.", "• Click the blue '🔗 View Mark Scheme' link to jump to answers."]:
            c.drawString(margin, y, inst)
            y -= 25

    if include_formula and os.path.exists("formula_sheet.png"):
        c.showPage()
        draw_bold_page_number(c, width, height)
        c.drawImage("formula_sheet.png", margin, margin, width=draw_w, height=height - 150, preserveAspectRatio=True, anchor='n')
    
    c.showPage()
    draw_bold_page_number(c, width, height)
    y = height - top_margin

    for i, item in enumerate(cart):
        image_heights = []
        images_to_draw = sorted(list(set(item.get('question_paths', []))), key=natural_key)

        for img_path in images_to_draw:
            if os.path.exists(img_path):
                with Image.open(img_path) as img:
                    w, h = img.size
                    image_heights.append((img_path, draw_w * (h / w)))

        if not image_heights: continue

        if y - (25 + image_heights[0][1] + 40) < margin and y != (height - top_margin):
            c.showPage()
            draw_bold_page_number(c, width, height)
            y = height - top_margin

        q_key, ms_key = f"Q_{i}", f"MS_{i}"
        c.bookmarkPage(q_key, fit='FitH', top=y + 20)
        c.addOutlineEntry(f"Question {i + 1}", q_key, level=0)

        c.setFont("Helvetica-Bold", 16)
        c.drawString(margin, y, f"Question {i + 1}")
        y -= 25

        for img_path, draw_h in image_heights:
            if y - draw_h < margin:
                c.showPage()
                draw_bold_page_number(c, width, height)
                y = height - top_margin - 20
            c.drawImage(img_path, margin, y - draw_h, width=draw_w, height=draw_h)
            y -= (draw_h + 10)

        c.setFont("Helvetica", 10)
        c.setFillColorRGB(0, 0, 1)
        tw = c.stringWidth("🔗 View Mark Scheme", "Helvetica", 10)
        if y - 15 < margin:
            c.showPage()
            draw_bold_page_number(c, width, height)
            y = height - top_margin
        c.drawString(width - margin - tw, y, "🔗 View Mark Scheme")
        c.linkAbsolute("", ms_key, (width - margin - tw, y, width - margin, y + 10), Border='[0 0 0]')
        c.setFillColorRGB(0, 0, 0)
        y -= 30

    if include_checklist:
        draw_topic_checklist(c, cart, width, height, margin)

    c.showPage()
    draw_bold_page_number(c, width, height)
    y = height - top_margin
    c.setFont("Helvetica-Bold", 24)
    c.drawString(margin, y, "Mark Schemes")
    y -= 50

    for i, item in enumerate(cart):
        image_heights = []
        images_to_draw = sorted(list(set(item.get('ms_paths', []))), key=natural_key)

        for img_path in images_to_draw:
            if os.path.exists(img_path):
                with Image.open(img_path) as img:
                    w, h = img.size
                    image_heights.append((img_path, draw_w * (h / w)))

        if not image_heights: continue

        if y - (25 + image_heights[0][1] + 40) < margin and y != (height - top_margin):
            c.showPage()
            draw_bold_page_number(c, width, height)
            y = height - top_margin

        c.bookmarkPage(f"MS_{i}", fit='FitH', top=y + 20)
        c.addOutlineEntry(f"Mark Scheme {i + 1}", f"MS_{i}", level=0)

        c.setFont("Helvetica-Bold", 16)
        c.drawString(margin, y, f"Question {i + 1} (Mark Scheme)")
        y -= 25

        for img_path, draw_h in image_heights:
            if y - draw_h < margin:
                c.showPage()
                draw_bold_page_number(c, width, height)
                y = height - top_margin - 20
            c.drawImage(img_path, margin, y - draw_h, width=draw_w, height=draw_h)
            y -= (draw_h + 10)

        c.setFont("Helvetica", 10)
        c.setFillColorRGB(0, 0, 1)
        tw = c.stringWidth("🔙 Back to Question", "Helvetica", 10)
        if y - 15 < margin:
            c.showPage()
            draw_bold_page_number(c, width, height)
            y = height - top_margin
        c.drawString(width - margin - tw, y, "🔙 Back to Question")
        c.linkAbsolute("", f"Q_{i}", (width - margin - tw, y, width - margin, y + 10), Border='[0 0 0]')
        c.setFillColorRGB(0, 0, 0)
        y -= 30

    c.save()

def generate_exam_pdf(cart, filename, is_ms=False, custom_title="", include_checklist=False, include_formula=False):
    c = canvas.Canvas(filename, pagesize=A4)
    width, height = A4
    margin = 50
    top_margin = 80
    y = height - top_margin
    draw_w = width - (2 * margin)

    if not is_ms and len(cart) > 0:
        draw_bold_page_number(c, width, height)
        c.setFont("Helvetica-Bold", 24)
        c.drawString(margin, y, custom_title if custom_title.strip() else get_exam_title(cart))
        y -= 50
        c.setFont("Helvetica", 14)
        c.drawString(margin, y, "Candidate Name: _________________________________________")
        y -= 40
        c.drawString(margin, y, f"Total Marks: {sum([i.get('metadata', {}).get('marks', 0) for i in cart])}")
        y -= 60
        if include_formula and os.path.exists("formula_sheet.png"):
            c.showPage()
            c.drawImage("formula_sheet.png", margin, margin, width=draw_w, height=height - 150, preserveAspectRatio=True, anchor='n')
        c.showPage()
        draw_bold_page_number(c, width, height)
        y = height - top_margin

    for i, item in enumerate(cart):
        image_heights = []
        paths = item.get('ms_paths', []) if is_ms else item.get('question_paths', [])
        for img_path in sorted(list(set(paths)), key=natural_key):
            if os.path.exists(img_path):
                with Image.open(img_path) as img:
                    w, h = img.size
                    image_heights.append((img_path, draw_w * (h / w)))

        if not image_heights: continue

        if y - (25 + image_heights[0][1] + 40) < margin and y != (height - top_margin):
            c.showPage()
            draw_bold_page_number(c, width, height)
            y = height - top_margin

        c.setFont("Helvetica-Bold", 16)
        c.drawString(margin, y, f"Question {i + 1} {'(Mark Scheme)' if is_ms else ''}")
        y -= 25

        for img_path, draw_h in image_heights:
            if y - draw_h < margin:
                c.showPage()
                y = height - top_margin - 20
            c.drawImage(img_path, margin, y - draw_h, width=draw_w, height=draw_h)
            y -= (draw_h + 40)

    if not is_ms and include_checklist:
        draw_topic_checklist(c, cart, width, height, margin)

    c.save()

# --- 6. EXPORT UI ---
st.sidebar.divider()
st.sidebar.subheader("📤 Export Exam")

is_cart_empty = len(st.session_state.test_cart) == 0
custom_exam_title = st.sidebar.text_input("Custom Exam Title (Optional)")
include_formula_opt = st.sidebar.checkbox("Attach Formula Sheet", value=False)
include_checklist_opt = st.sidebar.checkbox("Include Topic Checklist", value=True)
build_combined_pdf = st.sidebar.checkbox("Generate Combined PDF with Links", value=True)

if st.sidebar.button("🔨 Build Final Files", disabled=is_cart_empty):
    with st.spinner("Generating files..."):
        try:
            EXPORT_DIR = "exports"
            os.makedirs(EXPORT_DIR, exist_ok=True)
            datestamp = datetime.datetime.now().strftime("%Y-%m-%d")
            safe_prefix = f"{custom_exam_title.strip().replace(' ', '-')}-" if custom_exam_title.strip() else ""

            base_pdf = os.path.join(EXPORT_DIR, f"{safe_prefix}paper-{datestamp}.pdf")
            ms_pdf = os.path.join(EXPORT_DIR, f"{safe_prefix}ms-{datestamp}.pdf")
            combined_pdf = os.path.join(EXPORT_DIR, f"{safe_prefix}combined-{datestamp}.pdf")
            json_draft = os.path.join(EXPORT_DIR, f"{safe_prefix}draft-{datestamp}.json")

            with open(json_draft, 'w') as f: json.dump(st.session_state.test_cart, f, indent=4)

            generate_exam_pdf(st.session_state.test_cart, base_pdf, is_ms=False, custom_title=custom_exam_title, include_checklist=include_checklist_opt, include_formula=include_formula_opt)
            generate_exam_pdf(st.session_state.test_cart, ms_pdf, is_ms=True, custom_title=custom_exam_title)

            if build_combined_pdf:
                generate_combined_pdf(st.session_state.test_cart, combined_pdf, custom_title=custom_exam_title, include_checklist=include_checklist_opt, include_formula=include_formula_opt)

            st.session_state.export_paths = {
                "pdf": base_pdf, "ms": ms_pdf, "combined": combined_pdf if build_combined_pdf else None,
                "json": json_draft, "prefix": safe_prefix, "datestamp": datestamp
            }
            st.session_state.files_ready = True
            st.rerun()
        except Exception as e:
            st.error(f"Error: {e}")

if not is_cart_empty and st.session_state.get("files_ready"):
    st.sidebar.success("✅ Files Ready!")
    paths = st.session_state.export_paths
    
    if IS_LOCAL:
        st.sidebar.info(f"📂 Saved to: `{os.path.abspath('exports')}`")
    else:
        # Provide direct downloads
        with open(paths["pdf"], "rb") as f: st.sidebar.download_button("📄 Download PDF Paper", f, os.path.basename(paths["pdf"]))
        with open(paths["ms"], "rb") as f: st.sidebar.download_button("✔️ Download Mark Scheme", f, os.path.basename(paths["ms"]))
        if paths["combined"]:
            with open(paths["combined"], "rb") as f: st.sidebar.download_button("🔗 Download Combined Exam", f, os.path.basename(paths["combined"]))

# --- 7. TABS (BROWSE, ORGANISE, ANALYTICS) ---
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
                col_info, col_img, col_actions = st.columns([2, 6, 2])
                with col_info:
                    st.markdown(f"### Q{i + 1}")
                    st.caption(f"Paper: {item.get('paper')}")
                with col_img:
                    paths = item.get('question_paths', [])
                    if paths and os.path.exists(paths[0]): st.image(paths[0], width=400)
                with col_actions:
                    if st.button("🗑️ Remove", key=f"del_prev_{i}"):
                        st.session_state.test_cart.pop(i)
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
            st.bar_chart(pd.DataFrame(list(diff_marks.items()), columns=["Difficulty", "Marks"]).set_index("Difficulty"))
        with c2:
            st.write("**Marks by Topic**")
            st.bar_chart(pd.DataFrame(list(topic_marks.items()), columns=["Topic", "Marks"]).set_index("Topic"))
