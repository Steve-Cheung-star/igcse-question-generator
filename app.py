import streamlit as st
from PIL import Image, ImageChops
import io
import base64
import os
import subprocess
import re
import json
from pdf2image import convert_from_path
import shutil

# ------------------------------------------------------------------
# SYSTEM ENVIRONMENT PATH OVERRIDE FOR MACOS COMPILATION STEPS
# ------------------------------------------------------------------
current_path = os.environ.get("PATH", "")
homebrew_paths = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
if homebrew_paths not in current_path:
    os.environ["PATH"] = f"{homebrew_paths}:{current_path}"

# Global Page Configuration Setup
st.set_page_config(page_title="Pristine Exam Bank Builder", layout="wide")
st.title("⚡ Dynamic Batch Exam Generator & Picker Loop")

IMAGE_FOLDER = "images"
if not os.path.exists(IMAGE_FOLDER):
    os.makedirs(IMAGE_FOLDER)


# ------------------------------------------------------------------
# GLOBAL FLAT FILE MAXIMUM INCREMENT DETECTOR
# ------------------------------------------------------------------
def get_next_global_id(folder_path=IMAGE_FOLDER):
    """Scans the flat images folder, finds the maximum integer X among
    existing 'q_X.png' files, and returns X + 1. Default starts at 1.
    """
    if not os.path.exists(folder_path):
        return 1

    highest_id = 0
    pattern = re.compile(r"^q_(\d+)\.png$")

    for filename in os.listdir(folder_path):
        match = pattern.match(filename)
        if match:
            current_num = int(match.group(1))
            if current_num > highest_id:
                highest_id = current_num

    return highest_id + 1


# ------------------------------------------------------------------
# FIXED VERTICAL-ONLY PILLOW IMAGE CROPPER
# ------------------------------------------------------------------
def autocrop_image_margins(image_path, padding=25):
    """Opens the compiled canvas sheet, calculates the absolute vertical
    pixel boundaries, and trims exclusively above and below while
    leaving the horizontal page format completely intact.
    """
    img = Image.open(image_path).convert("RGB")
    bg = Image.new("RGB", img.size, (255, 255, 255))
    diff = ImageChops.difference(img, bg)
    bbox = diff.getbbox()

    if bbox:
        _, top, _, bottom = bbox
        w, h = img.size

        left = 0
        right = w

        top = max(0, top - padding)
        bottom = min(h, bottom + padding)

        cropped_img = img.crop((left, top, right, bottom))
        cropped_img.save(image_path, "PNG")
    return image_path


# ------------------------------------------------------------------
# MULTI-PAGE EXAM-FORMATTED LATEX COMPILER ENGINE
# ------------------------------------------------------------------
def compile_exam_page(latex_body, output_filename="temp_layout", is_markscheme=False):
    """Wraps either a question block or a markscheme table structure into an
    article template, executes pdflatex, and returns list of cropped PNG paths.
    """
    if os.path.exists("/opt/homebrew/bin") and "/opt/homebrew/bin" not in os.environ["PATH"]:
        os.environ["PATH"] = f"/opt/homebrew/bin:{os.environ['PATH']}"
    elif os.path.exists("/usr/local/bin") and "/usr/local/bin" not in os.environ["PATH"]:
        os.environ["PATH"] = f"/usr/local/bin:{os.environ['PATH']}"

    # Pre-parse: Catch independent $$ math $$ blocks and rewrite them as indented text fragments
    latex_body = re.sub(
        r"\$\$(.*?)\$\$",
        r"\n\\par\\vspace{0.1cm}\\hspace*{0.6cm}$\1$\\par\\vspace{0.15cm}\n",
        latex_body,
        flags=re.DOTALL
    )

    raw_lines = latex_body.splitlines()

    # COUNT MAIN-LEVEL ITEMS ONLY
    main_item_count = 0
    subenum_depth = 0
    has_subenum_internal = False

    for line in raw_lines:
        stripped_line = line.strip()
        if stripped_line.startswith("[SUBENUM_START]"):
            subenum_depth += 1
            has_subenum_internal = True
        elif stripped_line.startswith("[SUBENUM_END]"):
            subenum_depth -= 1
        elif stripped_line.startswith("[ITEM]") and subenum_depth == 0:
            main_item_count += 1

    # If a question contains subparts, do NOT drop the itemize mechanics
    is_single_part_question = (main_item_count <= 1) and not has_subenum_internal and not is_markscheme

    assembled_lines = []
    active_environments = []

    for line in raw_lines:
        line = re.sub(r'(\d+)%\s*', r'\1\\% ', line)
        stripped = line.strip()
        if not stripped:
            continue

        if is_markscheme and "Part" in stripped and "Answer" in stripped and "Marks" in stripped:
            continue

        start_match = re.search(r"\\begin\{([A-Za-z0-9\*]+)\}", stripped)
        if start_match:
            active_environments.append(start_match.group(1))
        end_match = re.search(r"\\end\{([A-Za-z0-9\*]+)\}", stripped)
        if end_match and active_environments:
            if active_environments[-1] == end_match.group(1):
                active_environments.pop()

        # Structural Token Routing Logic
        if stripped.startswith("[ENUM_START]"):
            if not is_single_part_question:
                assembled_lines.append(r"\begin{enumerate}[label=\textbf{(\alph*)}, leftmargin=*]")
            continue
        elif stripped.startswith("[ENUM_END]"):
            if not is_single_part_question:
                assembled_lines.append(r"\end{enumerate}")
            continue
        elif stripped.startswith("[SUBENUM_START]"):
            assembled_lines.append(r"\begin{enumerate}[label=\textbf{(\roman*)}, leftmargin=*]")
            continue
        elif stripped.startswith("[SUBENUM_END]"):
            assembled_lines.append(r"\end{enumerate}")
            continue

        elif stripped.startswith("[NEWPAGE]") or "[NEWPAGE]" in stripped:
            clean_item_text = stripped.replace("[NEWPAGE]", "").replace("[ITEM]", "").strip()
            assembled_lines.append(r"\newpage")
            if clean_item_text:
                if "[ITEM]" in stripped:
                    assembled_lines.append(
                        f"\\item {clean_item_text}" if not is_single_part_question else f"\\noindent {clean_item_text} \\\\")
                else:
                    assembled_lines.append(f"{clean_item_text} \\\\")
            continue

        elif stripped.startswith("[ITEM]"):
            item_text = stripped.replace("[ITEM]", "").strip()
            if is_single_part_question:
                assembled_lines.append(f"\\noindent {item_text} \\\\")
            else:
                assembled_lines.append(f"\\item {item_text}")
            continue

        # Safeguard: Do not append line endings inside active markscheme tables
        if len(active_environments) == 0 and not is_markscheme:
            if not stripped.startswith("\\") and not stripped.endswith(r"\\") and not stripped.startswith(
                    "[") and not stripped.startswith(r"\par"):
                line = line + r" \\"

        assembled_lines.append(line)

    processed_latex = "\n".join(assembled_lines)

    if "\\begin{tikzpicture}" in processed_latex or "\\begin{tabular}" in processed_latex:
        processed_latex = "\\needspace{10cm}\n" + processed_latex

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
    \renewcommand{\arraystretch}{1.5}
    \begin{tabular}{|p{1.5cm}|p{4.8cm}|p{1.2cm}|p{7.7cm}|}
    \hline
    \textbf{Part} & \textbf{Answer} & \textbf{Marks} & \textbf{Partial Marks / Guidance} \\ \hline
}{
    \hline
    \end{tabular}
}
"""

    full_latex_document = f"""\\documentclass[12pt]{{{'article'}}}
\\usepackage[utf8]{{inputenc}}
\\usepackage{{amsmath,amssymb,amsfonts}}
\\usepackage{{tikz}}
\\usetikzlibrary{{arrows.meta, angles, quotes}}
\\usepackage[a4paper, margin=0.8in]{{geometry}}
\\usepackage{{enumitem}}
\\usepackage{{mathptmx}}
\\usepackage{{needspace}}
\\usepackage{{array}}

\\setlength{{\\parindent}}{{0pt}}

{macro_definition}

\\pagestyle{{empty}}

\\begin{{document}}
\\setlength{{\\parindent}}{{0pt}}
{processed_latex}
\\end{{document}}
"""
    tex_path = f"{output_filename}.tex"
    pdf_path = f"{output_filename}.pdf"
    log_path = f"{output_filename}.log"

    with open(tex_path, "w", encoding="utf-8") as f:
        f.write(full_latex_document)

    try:
        subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", tex_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        if not os.path.exists(pdf_path):
            st.error(f"❌ LaTeX Engine Compilation Crash ({'Markscheme' if is_markscheme else 'Question'})!")
            if os.path.exists(log_path):
                with open(log_path, "r", encoding="utf-8", errors="ignore") as log_file:
                    log_lines = log_file.readlines()
                    error_snippet = "".join(log_lines[-25:])
                st.subheader("📋 Engine Log Tail Diagnoses:")
                st.code(error_snippet, language="text")
            return None

        poppler_env_path = "/opt/homebrew/bin" if os.path.exists("/opt/homebrew/bin") else (
            "/usr/local/bin" if os.path.exists("/usr/local/bin") else None)

        if poppler_env_path:
            images = convert_from_path(pdf_path, dpi=200, poppler_path=poppler_env_path)
        else:
            images = convert_from_path(pdf_path, dpi=200)

        generated_png_paths = []
        if images:
            for page_idx, img_obj in enumerate(images):
                suffix = "" if page_idx == 0 else f"_part{page_idx + 1}"
                png_path = f"{output_filename}{suffix}.png"

                img_obj.save(png_path, "PNG")
                autocrop_image_margins(png_path)
                generated_png_paths.append(png_path)
            return generated_png_paths

    except Exception as e:
        st.error(f"❌ Image Transformation Subprocess Failed!")
        st.exception(e)
    finally:
        for ext in [".tex", ".pdf", ".aux", ".log"]:
            file_to_del = f"{output_filename}{ext}"
            if os.path.exists(file_to_del):
                os.remove(file_to_del)
    return None


if "selected_latex_code" not in st.session_state:
    st.session_state["selected_latex_code"] = ""
if "selected_ms_code" not in st.session_state:
    st.session_state["selected_ms_code"] = ""
if "variants_dict" not in st.session_state:
    st.session_state["variants_dict"] = {}
if "ms_dict" not in st.session_state:
    st.session_state["ms_dict"] = {}

# ------------------------------------------------------------------
# TWO-COLUMN WORKFLOW LAYOUT
# ------------------------------------------------------------------
col_left, col_right = st.columns(2)

with col_left:
    st.header("📤 Step 1: Request")

    if not os.path.exists("registry.json"):
        with open("registry.json", "w") as f:
            json.dump({}, f)

    try:
        with open("registry.json", "r") as f:
            registry_db = json.load(f)
    except Exception:
        registry_db = {}

    registry_constraints_prompt = ""

    if registry_db:
        selected_niche = st.selectbox(
            "Select Target Syllabus Niche Profile:",
            list(registry_db.keys()),
            key="selected_niche_profile"
        )
        niche_data = registry_db[selected_niche]
        topic = niche_data.get("skill_name", "Unknown Skill")
        syllabus_ref = niche_data.get("syllabus_ref", "")

        constraints_list = niche_data.get("technical_constraints", [])
        if constraints_list:
            registry_constraints_prompt = "\nREGISTRY PROFILE TECHNICAL CONSTRAINTS:\n"
            for c_idx, constraint_text in enumerate(constraints_list, 1):
                registry_constraints_prompt += f" {c_idx}. {constraint_text}\n"
    else:
        st.warning("⚠️ registry.json is empty. Using baseline fallback fields.")
        topic = st.text_input("Topic Focus", "Sets and Venn Diagrams")
        syllabus_ref = "E1.2"

    selected_paper_target = st.radio(
        "🎯 Choose Target Exam Assessment Component:",
        ["Paper 2", "Paper 4"],
        index=0,
        help="Paper 2 enforces NON-CALCULATOR parameters. Paper 4 assumes a full CALCULATOR ecosystem framework."
    )

    st.write("**Target Difficulty Levels Selection:**")
    c1, c2, c3 = st.columns(3)
    with c1:
        diff_easy = st.checkbox("Easy", value=True)
    with c2:
        diff_med = st.checkbox("Medium", value=False)
    with c3:
        diff_hard = st.checkbox("Hard", value=False)

    selected_difficulties = []
    if diff_easy: selected_difficulties.append("Easy")
    if diff_med: selected_difficulties.append("Medium")
    if diff_hard: selected_difficulties.append("Hard")

    if not selected_difficulties:
        selected_difficulties = ["Easy"]

    num_variants = st.slider("Variants to Generate per checked difficulty", min_value=1, max_value=10, value=5)
    include_tikz = st.checkbox("Include TikZ Diagram", value=True)

    tikz_instruction_block = ""
    if include_tikz:
        tikz_instruction_block = """
7. ADVANCED TIKZ LAYOUT & STABLE FOREACH EVALUATION: Wrap background lines and graphs strictly within this centering template environment:
     \\begin{center}
     \\begin{tikzpicture}
     ...
     \\end{tikzpicture}
     \\end{center}
   - NO \\pgfmathparse LABELS: Do NOT use \\pgfmathparse inside any \\foreach loops. Calculate tick labels directly statement via the 'evaluate' syntax key:
     \\foreach \\x [evaluate=\\x as \\val using int(12 + \\x*2)] in {0,2,4,6,8,10} {
        \\draw (\\x,0) -- (\\x,-0.1) node[below] {\\small $\\val$};
     }
   - GRID DESIGN PARAMETERS: Set explicit xstep/ystep metrics matching distributions using faint lines: [color=gray!40, xstep=0.5cm, ystep=0.5cm].
"""
    else:
        tikz_instruction_block = "7. DO NOT include any TikZ diagrams. Text and math strings only."

    total_requested_count = len(selected_difficulties) * num_variants

    tag_list = []
    ms_tag_list = []
    for idx in range(1, total_requested_count + 1):
        tag_list.append(f"[VARIANT_{idx}_START] and [VARIANT_{idx}_END]")
        ms_tag_list.append(f"[MS_{idx}_START] and [MS_{idx}_END]")
    tag_instructions = ", ".join(tag_list)
    ms_tag_instructions = ", ".join(ms_tag_list)

    strategy_distribution_directives = ""
    current_variant_counter = 1
    for diff in selected_difficulties:
        strategy_distribution_directives += f"- For variants {current_variant_counter} through {current_variant_counter + num_variants - 1}: Tuning explicitly to an '{diff}' complexity matrix profile.\n"
        current_variant_counter += num_variants

    if selected_paper_target == "Paper 2":
        paper_specific_prompt_rules = """- ENFORCE STRICT PAPER 2 NON-CALCULATOR RESTRICTIONS:
  * All constants, dimensions, coordinates, and indices must evaluate cleanly through written fractions or simple integers.
  * No complex multi-digit decimal division or irrational arithmetic that requires computing accessories.
  * Focus on exact answers, fractional indexing rules, clean expressions, algebraic cancellation parameters, and explicit analytical proofs."""
    else:
        paper_specific_prompt_rules = """- ENFORCE CALCULATOR EXPECTATIONS FOR PAPER 4:
  * Questions should contain advanced calculations, multi-step geometric problem layouts, continuous statistics tracking, or multi-stage formulas where digital calculators are typical.
  * Answers can require 3 significant figure approximations where necessary, but parameters should remain mathematically clean inside variables."""

    massive_prompt = fr"""You are an elite math test setter specializing in Cambridge IGCSE 0607 Extended International Mathematics. Generate exactly {total_requested_count} distinct question variations regarding '{topic}' (Syllabus Reference Context: {syllabus_ref}). Use British spelling throughout.

CRITICAL ASSESSMENT COMPONENT RESTRICTIONS:
{paper_specific_prompt_rules}
{registry_constraints_prompt}
CRITICAL STRUCTURING RULES:
1. NO HEADERS/TITLES: Do not output any titles, question headers, or labels like "Question 1". Start directly with the raw context text or the `[ENUM_START]` token.
2. ZERO LEFT INDENTATION: Ensure all lines print completely flush against the left margin. 
3. NESTED SUBPART ENUMERATION & SYNTAX ROUTING: Main alphabetical lists must start with `[ENUM_START]` and close with `[ENUM_END]`. If an item contains sub-properties (like part b containing several distinct questions), wrap those properties inside a nested sub-list starting with `[SUBENUM_START]` and closing with `[SUBENUM_END]`. Use `[ITEM]` for every element line.
   Follow these condition-aware layout formats strictly:

   ❌ CASE A: PLOTTING / ACTION COMMANDS ("Complete the table", "Draw", "Sketch", "Graph", "Plot")
   Do NOT output the `\\examanswerslot` macro. Instead, insert the mark count completely flush against the right margin right below your context/table/TikZ diagram using this exact syntax row:
   [ITEM] On the grid, draw the cumulative frequency graph.
   \\begin{{center}}
   \\begin{{tikzpicture}} ... \\end{{tikzpicture}}
   \\end{{center}}
   \\phantom{{.}} \\hfill \\makebox[15pt][r]{{[3]}}

   🔍 CASE B: PROOFS / DERIVATIONS ("Show that...")
   You MUST write the `\\examanswerslot` macro but force the dotted line width ratio argument parameter to 0 so no dots render:
   [ITEM] Show that the equation reduces to $x^2 - 5x + 6 = 0$.
   \\examanswerslot{{3.5cm}}{{0}}{{2}}

   ✅ CASE C: STANDARD CALCULATION / VALUE RETRIEVAL
   Follow item texts immediately on a new line with the standard slot macro format:
   [ITEM] Calculate the median value.
   \\examanswerslot{{2cm}}{{0.25}}{{1}}

4. STANDALONE EXPRESSIONS & DISPLAY MATH BLOCKS: Isolate standalone complex fractions or complex mathematical equations completely onto their own independent lines using standard double dollar signs ($$).
   Example:
   [ITEM] Rationalise the denominator.
   $$ \\frac{{3}}{{\\sqrt{{5}}}} $$
   \\examanswerslot{{2cm}}{{0.25}}{{2}}

5. THE MANDATORY CORRESPONDING MARKSCHEME:
For each variant question generated, you MUST construct its precise official matching markscheme structured entry row inside an `\\begin{{officialmarkscheme}}` environment. Match nested subparts (such as (b)(i), (b)(ii)) exactly in the Part descriptor column. Use standard LaTeX table columns matching: Part & Answer & Marks & Partial Marks / Guidance.

BATCH DISTRIBUTION DIRECTIVES:
{strategy_distribution_directives}
{tikz_instruction_block}

PACKAGING EXPORT PAIRS:
Wrap each individual question variant inside these exact token pairs sequentially:
{tag_instructions}

Directly underneath each question variant block, wrap its matching tabular markscheme inside these token pairs:
{ms_tag_instructions}

Do not include markdown code ticks inside packaging tags.
"""
    encoded_prompt_b64 = base64.b64encode(massive_prompt.encode("utf-8")).decode("utf-8")
    clean_gemini_url = "https://gemini.google.com/app"

    js_text = f"""
    <script>
    async function runText() {{
        const rawPrompt = atob("{encoded_prompt_b64}");
        const textBlob = new Blob([rawPrompt], {{ type: 'text/plain' }});
        await navigator.clipboard.write([new ClipboardItem({{"text/plain": textBlob}})]);
        window.open("{clean_gemini_url}", "_blank");
    }}
    </script>
    <button onclick="runText()" style="background:linear-gradient(135deg, #4b6cb7 0%, #182848 100%);color:white;padding:12px;border:none;border-radius:6px;cursor:pointer;width:100%;font-weight:bold;font-size:15px;">
        🚀 Copy Requirements & Open Gemini Loop
    </button>
    """
    st.components.v1.html(js_text, height=55)

with col_right:
    st.header("📥 Step 2: Paste & Pick")

    gemini_output = st.text_area(
        "Paste Gemini payload block here:",
        height=160,
        placeholder="Expecting payload block containing [VARIANT_x_START] and [MS_x_START] tags..."
    )

    if gemini_output:
        sanitized_output = re.sub(
            r"\\begin\{officialmarkscheme\}\s*(?=\[MS_\d+_END\])",
            r"\\end{officialmarkscheme}\n",
            gemini_output
        )


        def advanced_ms_cleaner(text):
            def replace_ms(match):
                content = match.group(1)

                # Sinks any single trailing backslashes at row ends (even followed by spaces or \hline)
                content = re.sub(r'(?<!\\)\Silicon_Code_Patch\\(?!\s*\\)\s*(\\hline)?\s*$', r'\\\\ \1', content,
                                 flags=re.MULTILINE)

                # Catches internal cell formatting breaks where a row step was skipped
                content = re.sub(r'(?<!\\)\Silicon_Code_Patch\\(?!\\)(\s*&)', r'\\\\ \1', content)
                return f"\\begin{{officialmarkscheme}}{content}\\end{{officialmarkscheme}}"

            return re.sub(r'\\begin{{officialmarkscheme}}(.*?)\\end{{officialmarkscheme}}', replace_ms, text,
                          flags=re.DOTALL)


        sanitized_output = advanced_ms_cleaner(sanitized_output)

        # Strip redundant markdown/tabular artifacts cleanly
        sanitized_output = re.sub(r"\\begin\{tabular\}\{[^\}]*\}", "", sanitized_output)
        sanitized_output = re.sub(r"\\end\{tabular\}", "", sanitized_output)

        sanitized_output = re.sub(r"\bPart\s*&\s*Answer\s*&\s*Marks\s*&\s*Partial.*️\\\\(\s*\\hline)?", "",
                                  sanitized_output)

        # ------------------------------------------------------------------
        # FIXED CONDITIONAL LABEL STRIPPING (ONLY STRIPS SINGLE PART SECTIONS)
        # ------------------------------------------------------------------
        # Count total rows matching an alignment delimiter column marker to determine total entries
        row_count = len(re.findall(r"&", sanitized_output))

        # If there are 3 or fewer alignments across the body matrix, it's a single question entry!
        if row_count <= 3:
            sanitized_output = re.sub(
                r"^\s*\(?[a-zA-e]\)?(?:\([i-v]+\))?\s*(&)",
                r" \1",
                sanitized_output,
                count=0,
                flags=re.MULTILINE
            )

        # ------------------------------------------------------------------
        # CLEAN ROW END PIPELINE (PREVENTS \\ \\ DUPLICATES)
        # ------------------------------------------------------------------
        # Wipe out any pre-existing messy double/triple backslashes before hlines first
        sanitized_output = re.sub(r"\\\\+\s*\\hline", r"\\hline", sanitized_output)
        # Re-inject exactly ONE clean pair of backslashes before every hline
        sanitized_output = re.sub(r"(?<!\\)\s*\\hline", r" \\\\ \\hline", sanitized_output)

        # Force a completely clean vertical closure at the end of the environment body
        sanitized_output = re.sub(r"(?<!\\)\s*\\end\{officialmarkscheme\}", r" \\\\ \\hline\n\\end{officialmarkscheme}",
                                  sanitized_output)

        parsed_variants = {}
        parsed_ms = {}
        for idx in range(1, 31):
            start_tag = f"[VARIANT_{idx}_START]"
            end_tag = f"[VARIANT_{idx}_END]"
            ms_start_tag = f"[MS_{idx}_START]"
            ms_end_tag = f"[MS_{idx}_END]"

            if start_tag in sanitized_output and end_tag in sanitized_output:
                parsed_variants[f"{idx}"] = sanitized_output.split(start_tag)[1].split(end_tag)[0].strip()
            if ms_start_tag in sanitized_output and ms_end_tag in sanitized_output:
                parsed_ms[f"{idx}"] = sanitized_output.split(ms_start_tag)[1].split(ms_end_tag)[0].strip()

        st.session_state["variants_dict"] = parsed_variants
        st.session_state["ms_dict"] = parsed_ms

    if st.session_state["variants_dict"]:
        variants = st.session_state["variants_dict"]
        st.success(f"Isolated {len(variants)} matching question/markscheme variants.")

        chosen_variant_key = st.radio(
            "✨ Active Review Variant Select:",
            list(variants.keys()),
            horizontal=True,
            key="variant_selector_widget"
        )

        st.session_state["selected_latex_code"] = variants[chosen_variant_key]
        st.session_state["selected_ms_code"] = st.session_state["ms_dict"].get(chosen_variant_key, "")

        # ------------------------------------------------------------------
        # DUAL-TAB LIVE RENDER ARCHITECTURE
        # ------------------------------------------------------------------
        tab_question, tab_markscheme = st.tabs(["📄 Preview Question Sheet", "📋 Preview Official Markscheme"])

        compiled_q_pngs = []
        compiled_ms_pngs = []

        with tab_question:
            with st.spinner("Typesetting live question canvas layout..."):
                compiled_q_pngs = compile_exam_page(st.session_state["selected_latex_code"], "temp_q",
                                                    is_markscheme=False)
                if compiled_q_pngs:
                    for p_idx, img_path in enumerate(compiled_q_pngs):
                        st.image(img_path, use_container_width=True)

        with tab_markscheme:
            with st.spinner("Typesetting live official markscheme table..."):
                if st.session_state["selected_ms_code"]:
                    compiled_ms_pngs = compile_exam_page(st.session_state["selected_ms_code"], "temp_ms",
                                                         is_markscheme=True)
                    if compiled_ms_pngs:
                        for p_idx, img_path in enumerate(compiled_ms_pngs):
                            st.image(img_path, use_container_width=True)
                else:
                    st.warning("No corresponding markscheme segment block was found for this variant.")

        # ------------------------------------------------------------------
        # FLAT ROOT AUTOMATED INCREMENT ASSET COMMIT & RICH DATA PERSISTENCE
        # ------------------------------------------------------------------
        if compiled_q_pngs and compiled_ms_pngs:
            st.divider()

            next_global_id = get_next_global_id(IMAGE_FOLDER)

            final_q_filename = f"q_{next_global_id}.png"
            final_ms_filename = f"ms_{next_global_id}.png"

            q_target_path = os.path.join(IMAGE_FOLDER, final_q_filename)
            ms_target_path = os.path.join(IMAGE_FOLDER, final_ms_filename)

            st.info(f"💾 Next available global database slot: **{q_target_path}** and **{ms_target_path}**")

            if st.button(f"💾 Commit and Save as Global Entry #{next_global_id}"):
                shutil.move(compiled_q_pngs[0], q_target_path)
                shutil.move(compiled_ms_pngs[0], ms_target_path)

                for extra_path in compiled_q_pngs[1:] + compiled_ms_pngs[1:]:
                    if os.path.exists(extra_path):
                        os.remove(extra_path)

                raw_code = st.session_state["selected_latex_code"]
                slot_marks = re.findall(r"\\examanswerslot\{.*?\}\{.*?\}\{(\d+)\}", raw_code)
                manual_marks = re.findall(r"\\makebox\[\d+pt\]\[r\]\{\s*\[?(\d+)\]?\s*\}", raw_code)

                all_mark_strings = slot_marks + manual_marks
                calculated_marks = sum(int(m) for m in all_mark_strings) if all_mark_strings else 3

                active_topic = topic if 'topic' in locals() else "Sets and Venn Diagrams"
                active_difficulty = selected_difficulties[0] if selected_difficulties else "Medium"

                new_entry = {
                    "paper": selected_paper_target,
                    "question_base": f"{selected_paper_target}_{next_global_id}",
                    "question_paths": [q_target_path],
                    "ms_paths": [ms_target_path],
                    "question_code": st.session_state["selected_latex_code"],
                    "ms_code": st.session_state["selected_ms_code"],
                    "metadata": {
                        "topics": [active_topic],
                        "marks": calculated_marks,
                        "difficulty": active_difficulty
                    }
                }

                bank_filename = "master_bank.json"
                existing_bank = []

                if os.path.exists(bank_filename):
                    try:
                        with open(bank_filename, "r", encoding="utf-8") as f:
                            existing_bank = json.load(f)
                    except Exception:
                        existing_bank = []

                existing_bank.append(new_entry)

                with open(bank_filename, "w", encoding="utf-8") as f:
                    json.dump(existing_bank, f, indent=4, ensure_ascii=False)

                st.balloons()
                st.success(f"Successfully saved as {final_q_filename}/{final_ms_filename}!")
                st.rerun()

if st.session_state["selected_latex_code"]:
    with col_left:
        st.divider()
        st.subheader("Selected Document Source Syntax:")
        st.code(st.session_state["selected_latex_code"], language="latex")
        if st.session_state["selected_ms_code"]:
            st.subheader("Selected Markscheme Source Syntax:")
            st.code(st.session_state["selected_ms_code"], language="latex")