import streamlit as st
import base64
import os
import subprocess
import re
import json

# ------------------------------------------------------------------
# SYSTEM ENVIRONMENT PATH OVERRIDE FOR MACOS
# ------------------------------------------------------------------
current_path = os.environ.get("PATH", "")
homebrew_paths = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
if homebrew_paths not in current_path:
    os.environ["PATH"] = f"{homebrew_paths}:{current_path}"

st.set_page_config(page_title="Master Exam Assembly System", layout="wide")
st.title("📚 Full-Test Multi-Topic Chunked Assembler (App2)")


# ------------------------------------------------------------------
# RIGOROUS APP.PY FORMATTING LINKER ENGINE
# ------------------------------------------------------------------
def assemble_and_compile_full_test(sections_dict):
    """
    Loops through parsed question bodies and compiles them using the exact
    line-by-line normalization and structural rules from app.py.
    """
    master_body_lines = []
    question_counter = 1

    # Process every isolated chunk section using app.py layout logic
    for sec_name, latex_content in sections_dict.items():
        # Prevent layout leaking by forcing a safe clearance barrier per question block
        master_body_lines.append(f"\n% --- START OF QUESTION {question_counter}: {sec_name} ---")
        master_body_lines.append(r"\needspace{7cm}")
        master_body_lines.append(f"\\noindent\\textbf{{Question {question_counter}}} \\par\\vspace{{0.2cm}}")

        raw_lines = latex_content.splitlines()
        in_enum = False
        active_environments = []

        for line in raw_lines:
            # Clean up residual AI markdown backticks or structural debris
            if "```" in line or "[TOPIC_" in line or "[VARIANT_" in line:
                continue

            # Protect percentage values
            line = re.sub(r'(\d+)%\s*', r'\1\\% ', line)

            # Universal cleanup for malformed double backslashes
            line = line.replace(r'\\\\', r'\\')
            stripped = line.strip()

            if not stripped:
                continue

            # Monitor LaTeX environments
            start_match = re.search(r"\\begin\{([A-Za-z0-9\*]+)\}", stripped)
            if start_match:
                active_environments.append(start_match.group(1))
            end_match = re.search(r"\\end\{([A-Za-z0-9\*]+)\}", stripped)
            if end_match and active_environments:
                if active_environments[-1] == end_match.group(1):
                    active_environments.pop()

            # Custom token conversions from app.py
            if stripped.startswith("[ENUM_START]"):
                master_body_lines.append(r"\begin{enumerate}[label=\textbf{(\alph*)}]")
                in_enum = True
                continue

            elif stripped.startswith("[ENUM_END]"):
                master_body_lines.append(r"\end{enumerate}")
                in_enum = False
                continue

            elif stripped.startswith("[NEWPAGE]") or "[NEWPAGE]" in stripped:
                clean_item_text = stripped.replace("[NEWPAGE]", "").replace("[ITEM]", "").strip()
                if in_enum:
                    master_body_lines.append(r"\end{enumerate}")
                    master_body_lines.append(r"\newpage")
                    master_body_lines.append(r"\begin{enumerate}[label=\textbf{(\alph*)}, resume]")
                    if clean_item_text:
                        master_body_lines.append(f"\\item {clean_item_text}")
                else:
                    master_body_lines.append(r"\newpage")
                    if clean_item_text:
                        master_body_lines.append(f"{clean_item_text} \\\\")
                continue

            elif stripped.startswith("[ITEM]"):
                item_text = stripped.replace("[ITEM]", "").strip()
                if in_enum:
                    master_body_lines.append(f"\\item {item_text}")
                else:
                    master_body_lines.append(f"\\noindent {item_text} \\\\")
                continue

            elif stripped.startswith("[MATH_START]"):
                math_text = stripped.replace("[MATH_START]", "").replace("[MATH_END]", "").strip()
                master_body_lines.append(
                    f"\n\\par\\vspace{{0.15cm}}\\hspace*{{0.6cm}}${math_text}$\\par\\vspace{{0.15cm}}")
                continue

            # Fallback handling for regular prose lines outside equations/TikZ environments
            if len(active_environments) == 0 and not stripped.startswith("\\") and not stripped.endswith(r"\\"):
                line = line + r" \\"

            master_body_lines.append(line)

        # Add a clean paragraph space separation after closing the active question block
        master_body_lines.append(r"\par\vspace{1cm}")
        question_counter += 1

    processed_latex_body = "\n".join(master_body_lines)

    macro_definition = r"""
\newcommand{\examanswerslot}[3]{%
  \par\nopagebreak\vspace*{#1}%
  \par\nopagebreak\vspace*{0.2cm}%
  \phantom{.} \hfill \makebox[#2\linewidth]{\dotfill}\hspace{4pt}\makebox[15pt][r]{{[#3]}}\par
}
"""

    # Precise app.py document preamble configurations
    full_document = f"""\\documentclass[12pt]{{{'article'}}}
\\usepackage[utf8]{{inputenc}}
\\usepackage{{amsmath,amssymb,amsfonts}}
\\usepackage{{tikz}}
\\usetikzlibrary{{arrows.meta, angles, quotes}}
\\usepackage[a4paper, margin=0.8in]{{geometry}}
\\usepackage{{enumitem}}
\\usepackage{{mathptmx}}
\\usepackage{{needspace}}

\\setlength{{\\parindent}}{{0pt}}
\\setlist[enumerate]{{leftmargin=*, align=left}}

{macro_definition}

\\pagestyle{{empty}}

\\begin{{document}}
\\setlength{{\\parindent}}{{0pt}}
{processed_latex_body}
\\end{{document}}
"""

    with open("master_test.tex", "w", encoding="utf-8") as f:
        f.write(full_document)

    try:
        result = subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", "master_test.tex"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        if os.path.exists("master_test.pdf"):
            return "master_test.pdf"
        else:
            st.error("❌ PDF engine generation crash. Check console output logs.")
    except Exception as e:
        st.error(f"Compilation pipeline broken: {e}")
    return None


# ------------------------------------------------------------------
# REGISTRY DATA PROVIDER
# ------------------------------------------------------------------
if os.path.exists("registry.json"):
    try:
        with open("registry.json", "r") as f:
            registry_db = json.load(f)
    except:
        registry_db = {}
else:
    registry_db = {
        f"Topic {i}: Sample Skill Metric": {"skill_name": f"Sample Skill Set {i}", "syllabus_ref": f"E{i}.1"}
        for i in range(1, 7)
    }

# Session tracker configurations
if "master_test_builder" not in st.session_state:
    st.session_state["master_test_builder"] = {}
if "current_chunk_idx" not in st.session_state:
    st.session_state["current_chunk_idx"] = 0

# ------------------------------------------------------------------
# TWO-COLUMN WORKFLOW LAYOUT
# ------------------------------------------------------------------
col_left, col_right = st.columns([1, 1])

with col_left:
    st.header("📝 Step 1: Topics & Chunk Selection")

    all_keys = list(registry_db.keys())
    selected_keys = st.multiselect(
        "Select syllabus entries to include in this exam:",
        options=all_keys,
        default=all_keys
    )

    chunk_size = st.slider("Number of topics to group per chunk generation prompt:", min_value=1, max_value=5, value=2)

    if selected_keys:
        chunks = [selected_keys[i:i + chunk_size] for i in range(0, len(selected_keys), chunk_size)]
        total_chunks = len(chunks)

        if st.session_state["current_chunk_idx"] >= total_chunks:
            st.session_state["current_chunk_idx"] = 0

        curr_idx = st.session_state["current_chunk_idx"]
        current_chunk_topics = chunks[curr_idx]

        st.warning(f"Displaying Generation Prompt Group **{curr_idx + 1} of {total_chunks}**")

        # Uses standard baseline instructions from app.py
        topic_instructions = ""
        for index, t_key in enumerate(current_chunk_topics, 1):
            s_name = registry_db[t_key].get("skill_name")
            s_ref = registry_db[t_key].get("syllabus_ref")
            topic_instructions += f"{index}. TOPIC AREA: '{s_name}' (Syllabus Ref: {s_ref})\n"
            topic_instructions += f"   - MUST START DIRECTLY WITH `[ENUM_START]` token.\n"
            topic_instructions += f"   - Use answer slots: `\\\\examanswerslot{{2.5cm}}{{0.2}}{{3}}`\n"
            topic_instructions += f"   - Wrap this specific question exactly inside [TOPIC_START_{s_name}] and [TOPIC_END_{s_name}] tags.\n\n"

        massive_prompt = f"""You are an elite test setter specializing in Cambridge IGCSE 0607 Extended International Mathematics. Generate exactly 1 pristine exam question variant for each of the following distinct topic items listed below:

{topic_instructions}
CRITICAL STRUCTURING RULES (MATCHING APP.PY):
1. NO HEADERS/TITLES & NO FORCED INTRO SENTENCES: Do not output any section names, question headers, or labels like \"Question 1\". Start completely and directly with the `[ENUM_START]` token.
2. ZERO LEFT INDENTATION: Ensure all lines print completely flush against the left margin. 
3. MANDATORY SUBPART ENUMERATION: Every single distinct problem part or active command must be structured as an item within an enumeration list starting with an `[ITEM]` tag.
4. UNIFORM WORKSPACE METHOD: Follow item texts immediately with the `\\examanswerslot{{workspace_height}}{{line_width_ratio}}{{marks}}` macro.
5. STANDALONE EXPRESSIONS & DISPLAY FRACTIONS: Complex math expressions must be isolated on their own lines using `[MATH_START]` and `[MATH_END]`.

- Use British English spelling conventions throughout.
- Make sure each question is closed out correctly with its corresponding topic text tracking tags. Do not print markdown backtick blocks inside or outside the tracking tags.
"""
        encoded_prompt_b64 = base64.b64encode(massive_prompt.encode("utf-8")).decode("utf-8")

        js_text = f"""
        <script>
        async function copyChunkPrompt() {{
            const rawPrompt = atob("{encoded_prompt_b64}");
            const textBlob = new Blob([rawPrompt], {{ type: 'text/plain' }});
            await navigator.clipboard.write([new ClipboardItem({{"text/plain": textBlob}})]);
            alert("Prompt group {curr_idx + 1} successfully copied to clipboard!");
        }}
        </script>
        <button onclick="copyChunkPrompt()" style="background:linear-gradient(135deg, #4b6cb7 0%, #182848 100%);color:white;padding:12px;border:none;border-radius:6px;cursor:pointer;width:100%;font-weight:bold;font-size:14px;">
            📋 Copy Chunk Prompt Group ({len(current_chunk_topics)} Topics)
        </button>
        """
        st.components.v1.html(js_text, height=50)

        nb_c1, nb_c2 = st.columns(2)
        with nb_c1:
            if st.button("⬅️ Previous Chunk Group") and curr_idx > 0:
                st.session_state["current_chunk_idx"] -= 1
                st.rerun()
        with nb_c2:
            if st.button("Next Chunk Group ➡️") and curr_idx < total_chunks - 1:
                st.session_state["current_chunk_idx"] += 1
                st.rerun()

    st.divider()
    st.subheader("📦 Paper Composition Inventory")
    if st.session_state["master_test_builder"]:
        for k in st.session_state["master_test_builder"].keys():
            st.write(f"✅ Loaded Section: **{k}**")
        if st.button("🗑️ Clear Paper Inventory"):
            st.session_state["master_test_builder"] = {}
            st.rerun()
    else:
        st.warning("Paper inventory is currently empty.")

with col_right:
    st.header("📥 Step 2: Chunk Payload Collector")

    pasted_payload = st.text_area(
        "Paste the AI response block for the current chunk group below:",
        height=250,
        placeholder="Expecting multiple [TOPIC_START_...] blocks inside this text zone..."
    )

    if st.button("➕ Parse and Append Chunk Items"):
        if pasted_payload and selected_keys:
            appended_count = 0
            for t_key in selected_keys:
                s_name = registry_db[t_key].get("skill_name")
                start_tag = f"[TOPIC_START_{s_name}]"
                end_tag = f"[TOPIC_END_{s_name}]"

                if start_tag in pasted_payload and end_tag in pasted_payload:
                    extracted_snippet = pasted_payload.split(start_tag)[1].split(end_tag)[0].strip()
                    # Sanitize any accidental inner code fencing if generated by the model
                    if extracted_snippet.startswith("```latex"):
                        extracted_snippet = extracted_snippet[8:]
                    if extracted_snippet.startswith("```"):
                        extracted_snippet = extracted_snippet[3:]
                    if extracted_snippet.endswith("```"):
                        extracted_snippet = extracted_snippet[:-3]

                    st.session_state["master_test_builder"][t_key] = extracted_snippet.strip()
                    appended_count += 1

            if appended_count > 0:
                st.success(f"Successfully processed and appended {appended_count} topic entries!")
                st.rerun()
            else:
                st.error("No valid topic tags found in the pasted content. Check your boundary flags.")

    st.divider()
    st.subheader("🚀 Step 3: Produce Consolidated Document")

    if st.session_state["master_test_builder"]:
        if st.button("⚙️ Compile Full Composite Paper PDF"):
            with st.spinner("Linking segments and executing pdflatex engine..."):
                pdf_output_path = assemble_and_compile_full_test(st.session_state["master_test_builder"])

                if pdf_output_path and os.path.exists(pdf_output_path):
                    st.success("🎉 Full Test Document Master Compiled Successfully!")
                    with open(pdf_output_path, "rb") as f:
                        st.download_button(
                            label="📥 Download Consolidated Question Paper PDF",
                            data=f.read(),
                            file_name="completed_examination_paper.pdf",
                            mime="application/pdf"
                        )
    else:
        st.info("Load chunk questions via the payload collector to enable full PDF rendering options.")