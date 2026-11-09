import base64
import io
import os
import zipfile
import streamlit as st

st.set_page_config(
    page_title="PDF Viewer & Manager",
    page_icon="📚",
    layout="wide"
)

st.title("📚 Shared PDF Exam Documents & Subtopics")

SHARED_DIR = "shared"


def get_pdf_base64_iframe(file_path: str, height: int = 650) -> str:
    """Encodes a PDF file to base64 and returns an HTML iframe container with an interactive Fullscreen overlay button."""
    with open(file_path, "rb") as f:
        base64_pdf = base64.b64encode(f.read()).decode("utf-8")

    return f"""
    <div id="pdf-wrapper" style="position: relative; width: 100%; height: {height}px; background-color: #525659; border-radius: 8px; overflow: hidden;">
        <button onclick="toggleFullscreen()" style="
            position: absolute; 
            top: 10px; 
            right: 25px; 
            z-index: 1000; 
            padding: 6px 12px; 
            background-color: #0E1117; 
            color: #FFFFFF; 
            border: 1px solid #4B5563; 
            border-radius: 6px; 
            cursor: pointer; 
            font-family: system-ui, -apple-system, sans-serif; 
            font-size: 13px;
            font-weight: 500;
            box-shadow: 0px 2px 6px rgba(0,0,0,0.3);
        ">
            ⛶ Fullscreen
        </button>
        <iframe id="pdf-frame" 
                src="data:application/pdf;base64,{base64_pdf}#navpanes=0" 
                width="100%" 
                height="100%" 
                type="application/pdf" 
                allowfullscreen="true"
                style="border: none;">
        </iframe>
    </div>

    <script>
    function toggleFullscreen() {{
        var container = document.getElementById("pdf-wrapper");
        if (!document.fullscreenElement) {{
            if (container.requestFullscreen) {{
                container.requestFullscreen();
            }} else if (container.webkitRequestFullscreen) {{
                container.webkitRequestFullscreen();
            }} else if (container.msRequestFullscreen) {{
                container.msRequestFullscreen();
            }}
        }} else {{
            if (document.exitFullscreen) {{
                document.exitFullscreen();
            }}
        }}
    }}
    </script>
    """


def create_zip_buffer(file_dict: dict, selected_keys: list) -> io.BytesIO:
    """Compresses selected files into an in-memory ZIP buffer."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for key in selected_keys:
            file_path = file_dict[key]
            zf.write(file_path, arcname=os.path.basename(file_path))
    buffer.seek(0)
    return buffer


def get_file_size_str(filepath: str) -> str:
    """Returns formatted file size string."""
    size_bytes = os.path.getsize(filepath)
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def render_file_manager(file_dict: dict, group_prefix: str):
    """Renders split layout: left scrollable checkbox checklist, right iframe PDF viewer."""
    if not file_dict:
        st.info("No PDF files found in this category.")
        return

    filenames = list(file_dict.keys())

    def toggle_all():
        select_all_val = st.session_state[f"{group_prefix}_select_all"]
        for idx in range(len(filenames)):
            st.session_state[f"{group_prefix}_cb_{idx}"] = select_all_val

    for idx in range(len(filenames)):
        cb_key = f"{group_prefix}_cb_{idx}"
        if cb_key not in st.session_state:
            st.session_state[cb_key] = False

    col_left, col_right = st.columns([1, 1.2], gap="large")

    with col_left:
        st.subheader("📋 Select Files")
        st.checkbox("☑️ Select All Files", key=f"{group_prefix}_select_all", on_change=toggle_all)
        st.divider()

        selected_filenames = []
        with st.container(height=480):
            for idx, name in enumerate(filenames):
                f_path = file_dict[name]
                f_size = get_file_size_str(f_path)

                c_box, c_info, c_view = st.columns([0.1, 0.65, 0.25])
                is_checked = c_box.checkbox("", key=f"{group_prefix}_cb_{idx}", label_visibility="collapsed")
                if is_checked:
                    selected_filenames.append(name)

                c_info.markdown(
                    f"**{name}**  \n<small style='color: #6c757d;'>{f_size}</small>",
                    unsafe_allow_html=True,
                )

                if c_view.button("👁️ View", key=f"{group_prefix}_view_{idx}"):
                    st.session_state[f"{group_prefix}_preview_file"] = f_path

        st.divider()

        if selected_filenames:
            zip_buffer = create_zip_buffer(file_dict, selected_filenames)
            st.download_button(
                label=f"📦 Download Selected ({len(selected_filenames)} Files as ZIP)",
                data=zip_buffer,
                file_name=f"{group_prefix}_selected_files.zip",
                mime="application/zip",
                use_container_width=True,
                type="primary",
            )
        else:
            st.warning("Select at least one checkbox to download.")

    with col_right:
        st.subheader("👁️ PDF Viewer")
        active_preview = st.session_state.get(f"{group_prefix}_preview_file")

        if active_preview and os.path.exists(active_preview):
            filename = os.path.basename(active_preview)
            col_caption, col_save = st.columns([0.7, 0.3])
            col_caption.caption(f"Currently viewing: `{filename}`")

            with open(active_preview, "rb") as f:
                pdf_bytes = f.read()

            col_save.download_button(
                label="⬇️ Save PDF",
                data=pdf_bytes,
                file_name=filename,
                mime="application/pdf",
                use_container_width=True,
            )

            iframe_html = get_pdf_base64_iframe(active_preview, height=650)
            st.components.v1.html(iframe_html, height=670, scrolling=False)
        else:
            st.info("Click the **👁️ View** button next to any file to inspect it here.")


# --- MAIN DIRECTORY SCAN & UI ---
if not os.path.exists(SHARED_DIR):
    st.warning(f"Directory `{SHARED_DIR}` not found. Run compilation script first.")
    st.stop()

main_pdfs = {}
bytopic_pdfs = {}

for root, _, files in os.walk(SHARED_DIR):
    for file in sorted(files):
        if file.endswith(".pdf"):
            full_path = os.path.join(root, file)
            display_name = os.path.basename(file)
            if "bytopic" in root:
                bytopic_pdfs[display_name] = full_path
            else:
                main_pdfs[display_name] = full_path

tab1, tab2 = st.tabs(["📄 Full Papers (QP / MS / Combined)", "📌 By-Topic PDFs"])

with tab1:
    render_file_manager(main_pdfs, "main_papers")

with tab2:
    render_file_manager(bytopic_pdfs, "bytopic_papers")