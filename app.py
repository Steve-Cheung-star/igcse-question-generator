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
import uuid

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
    if os.path.exists("/opt/homebrew/bin") and "/opt/homebrew/bin" not in os.environ["PATH"]:
        os.environ["PATH"] = f"/opt/homebrew/bin:{os.environ['PATH']}"
    elif os.path.exists("/usr/local/bin") and "/usr/local/bin" not in os.environ["PATH"]:
        os.environ["PATH"] = f"/usr/local/bin:{os.environ['PATH']}"

    latex_body = re.sub(
        r"\$\$(.*?)\$\$",
        r"\n\\par\\vspace{0.1cm}\\hspace*{0.6cm}$\1$\\par\\vspace{0.15cm}\n",
        latex_body,
        flags=re.DOTALL
    )

    raw_lines = latex_body.splitlines()

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

        # ------------------------------------------------------------------
        # NEW LINE-BREAK NORMALIZATION LOGIC
        # ------------------------------------------------------------------
        # Rip off ALL trailing backslashes to give us a clean baseline (strips 1, 2, or 3+ slashes)
        clean_stripped = re.sub(r"\\+$", "", stripped).strip()

        if len(active_environments) > 0 and (
                "tabular" in active_environments or "officialmarkscheme" in active_environments):
            # TABULAR SAFETY NET: Any row with '&' MUST end with exactly '\\'
            if "&" in stripped and not stripped.endswith(r"\hline"):
                # Bypass if the line is "hanging" (ends with &) and waiting for the next line
                if clean_stripped.endswith("&"):
                    line = clean_stripped
                else:
                    line = clean_stripped + r" \\"
            else:
                line = stripped

        elif len(active_environments) == 0:
            # Check if it already has a valid LaTeX line break
            if not stripped.endswith(r"\\") and not stripped.endswith(r"\par"):
                # Skip structural/spacing commands that handle their own layout
                skip_prefixes = (
                    r"\begin", r"\end", r"\item", r"\par",
                    r"\examanswerslot", r"\newpage", r"\vspace",
                    r"\hspace", r"\[", r"\]", r"\\", "["
                )
                if not stripped.startswith(skip_prefixes):
                    # Rebuild the line using the clean baseline
                    line = clean_stripped + r" \\"
                else:
                    line = stripped
            else:
                line = stripped
        else:
            # If we are inside an environment (like TikZ), trust the line as-is
            line = stripped

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
        # Detect presence of display fractions to choose the proper row stretch height metric dynamically
        stretch_factor = "2.5" if r"\dfrac" in latex_body else "1.5"
        macro_definition += f"""
\\newenvironment{{officialmarkscheme}}{{
    \\renewcommand{{\\arraystretch}}{{{stretch_factor}}}
    \\begin{{tabular}}{{|p{{1.5cm}}|p{{4.8cm}}|p{{1.2cm}}|p{{7.7cm}}|}}
    \\hline
    \\textbf{{Part}} & \\textbf{{Answer}} & \\textbf{{Marks}} & \\textbf{{Partial Marks / Guidance}} \\\\ \\hline
}}{{
    \\hline
    \\end{{tabular}}
}}
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


# State Initialization
if "selected_latex_code" not in st.session_state:
    st.session_state["selected_latex_code"] = ""
if "selected_ms_code" not in st.session_state:
    st.session_state["selected_ms_code"] = ""
if "variants_dict" not in st.session_state:
    st.session_state["variants_dict"] = {}
if "ms_dict" not in st.session_state:
    st.session_state["ms_dict"] = {}
if "batch_id" not in st.session_state:
    st.session_state["batch_id"] = str(uuid.uuid4())
if "last_gemini_output" not in st.session_state:
    st.session_state["last_gemini_output"] = ""

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

    # NEW: High Variance Toggle
    st.write("**Generation Style Settings:**")
    high_variance_mode = st.toggle("🔀 Enable High Variance & Crossover Topics", value=False,
                                   help="Forces the AI to change structural layouts, mix topics, and avoid simple number-swapping.")

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
  * Figures and values should be less nice, they don't need to end with 0 or 5.
  * Answers can require 3 significant figure approximations where necessary, but parameters should remain mathematically clean inside variables."""

    # NEW: Variance and Crossover Injection Logic
    variance_directive = ""
    if high_variance_mode:
        variance_directive = """
CRITICAL VARIANCE AND CROSSOVER DIRECTIVE:
- STRICT PROHIBITION ON NUMBER-SWAPPING: You are strictly forbidden from reusing the same question structure and merely changing the values. Each variant MUST have a completely different real-world context, structural layout, and questioning angle.
- CROSSOVER TOPICS REQUIRED: You must seamlessly integrate at least one secondary syllabus topic into the sub-parts of each variant to test multi-disciplinary knowledge.
- SUB-PART DIVERSITY: Radically vary the number and nesting of sub-parts. For example, Variant 1 might have (a), (b)(i), (b)(ii); Variant 2 might have (a), (b), (c). 
- DELIVERABLE VARIETY: Ask for different deliverables across variants (e.g., mix "Show that", "Calculate", "Explain why", "Find the ratio").
"""
    else:
        variance_directive = "- Standard variant generation permitted. Maintain focus purely on the primary topic."

    massive_prompt = fr"""You are an elite math test setter specializing in Cambridge IGCSE 0607 Extended International Mathematics. Generate exactly {total_requested_count} distinct question variations regarding '{topic}' (Syllabus Reference Context: {syllabus_ref}). Use British spelling throughout.

CRITICAL ASSESSMENT COMPONENT RESTRICTIONS:
{paper_specific_prompt_rules}
{registry_constraints_prompt}

{variance_directive}

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
   $$ \\dfrac{{3}}{{\\sqrt{{5}}}} $$
   \\examanswerslot{{2cm}}{{0.25}}{{2}}

5. THE MANDATORY CORRESPONDING MARKSCHEME:
For each variant question generated, you MUST construct its precise official matching markscheme structured entry row inside an `\\begin{{officialmarkscheme}}` environment. Match nested subparts (such as (b)(i), (b)(ii)) exactly in the Part descriptor column. Use standard LaTeX table columns matching: Part & Answer & Marks & Partial Marks / Guidance.\

6. CURRENCY SYMBOLS: Use 3 letter codes like HKD, AUD, GBP, USD, EUR (312 HKD, 58 GBP)  . Do not use dollar signs, pound signs, euro signs, etc.\

7. NUMBER FORMAT: Use a space instead of comma for separating thousands and millions.\

CRITICAL PARSING CONSTRAINT:
You MUST use valid LaTeX row line breaks inside the `officialmarkscheme` environment. Every data entry line must end with a full double backslash `\\\\`. Do NOT output single backslashes `\\` under any circumstances. Ensure no trailing space follows the double backslash token.

MATHEMATICAL TYPOGRAPHY FRACTION RULES:
- ALWAYS use display-style fractions (`\\dfrac{{num}}{{den}}`) for all standard equations, fractions inside sentences, fractions inside tables, and display math blocks to maximize clarity and alignment metrics.
- DO NOT use standard `\\frac` except when formatting components embedded within an exponent/superscript profile (e.g., $x^{{\\frac{{1}}{{2}}}}$). For all other scenarios, `\\dfrac` is strictly required.
- CRITICAL MARKSCHEME CELL MATH MODE: Inside the tabular `officialmarkscheme` environments, any cell string containing fractions (`\\dfrac`), operators (`\\times`, `\\div`), or math characters MUST be wrapped explicitly in standard inline math delimiters (`$...$`). Example: `$1\\dfrac{{2}}{{3}}$` or `M1 for $\\dfrac{{8}}{{3}} \\times \\dfrac{{5}}{{8}}$`.

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

    if st.query_params.get("copied") == "true":
        st.toast("Requirements copied to clipboard successfully!", icon="✅")
        st.query_params.clear()

    btn_col1, btn_col2 = st.columns(2)

    with btn_col1:
        js_loop_text = f"""
        <script>
        async function runTextLoop() {{
            const rawPrompt = atob("{encoded_prompt_b64}");
            const textBlob = new Blob([rawPrompt], {{ type: 'text/plain' }});
            await navigator.clipboard.write([new ClipboardItem({{"text/plain": textBlob}})]);
            window.open("{clean_gemini_url}", "_blank");
        }}
        </script>
        <button onclick="runTextLoop()" style="background:linear-gradient(135deg, #4b6cb7 0%, #182848 100%);color:white;padding:12px;border:none;border-radius:6px;cursor:pointer;width:100%;font-weight:bold;font-size:14px;box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
            🚀 Copy Requirements & Open Gemini Loop
        </button>
        """
        st.components.v1.html(js_loop_text, height=55)

    with btn_col2:
        js_silent_text = f"""
        <script>
        async function runTextSilent(btn) {{
            try {{
                const rawPrompt = atob("{encoded_prompt_b64}");
                const textBlob = new Blob([rawPrompt], {{ type: 'text/plain' }});
                await navigator.clipboard.write([new ClipboardItem({{"text/plain": textBlob}})]);

                btn.style.background = "linear-gradient(135deg, #2ecc71 0%, #27ae60 100%)";
                btn.innerHTML = "✓ Copied Requirements!";

                setTimeout(() => {{
                    const url = new URL(window.parent.location.href);
                    url.searchParams.set("copied", "true");
                    window.parent.location.href = url.toString();
                }}, 400);
            }} catch(e) {{
                btn.innerHTML = "❌ Copy Failed";
            }}
        }}
        </script>
        <button onclick="runTextSilent(this)" style="background:linear-gradient(135deg, #2c3e50 0%, #3498db 100%);color:white;padding:12px;border:none;border-radius:6px;cursor:pointer;width:100%;font-weight:bold;font-size:14px;box-shadow: 0 2px 4px rgba(0,0,0,0.1);transition: all 0.3s ease;">
            📋 Copy Requirements (Silent)
        </button>
        """
        st.components.v1.html(js_silent_text, height=55)

with col_right:
    st.header("📥 Step 2: Paste & Pick")

    gemini_output = st.text_area(
        "Paste Gemini payload block here:",
        height=160,
        placeholder="Expecting payload block containing [VARIANT_x_START] and [MS_x_START] tags..."
    )

    # Detect new payloads to prevent Streamlit from carrying over old edits into fresh batches
    if gemini_output and gemini_output != st.session_state["last_gemini_output"]:
        st.session_state["last_gemini_output"] = gemini_output
        st.session_state["batch_id"] = str(uuid.uuid4())

    if gemini_output:
        # ----------------------------------------------------------
        # STEP A: UNIFY ALL ROW BREAKS INSIDE MARKSCHEME BLOCKS
        # ----------------------------------------------------------
        def normalize_ms_row_breaks(match):
            block = match.group(0)
            # Convert any trailing backslash mess (\, \\, \\\, etc.) at line-ends cleanly to \\
            block = re.sub(r"\\+\s*$", r" \\\\", block, flags=re.MULTILINE)

            # Tabular safety fallback: Strip accidental markdown artifacts or manual tabular wrappings
            block = re.sub(r"\\begin\{tabular\}\{[^\}]*\}", "", block)
            block = re.sub(r"\\end\{tabular\}", "", block)
            return block


        sanitized_output = re.sub(
            r"\\begin\{officialmarkscheme\}\s*(?=\[MS_\d+_END\])",
            r"\\end{officialmarkscheme}\n",
            gemini_output
        )


        # ----------------------------------------------------------
        # TARGETED FIX: Strip tabular ONLY inside markscheme blocks
        # ----------------------------------------------------------
        def strip_tabular_inside_ms(match):
            block = match.group(0)
            block = re.sub(r"\\begin\{tabular\}\{[^\}]*\}", "", block)
            block = re.sub(r"\\end\{tabular\}", "", block)
            return block


        sanitized_output = re.sub(
            r"\[MS_\d+_START\].*?\[MS_\d+_END\]",
            strip_tabular_inside_ms,
            sanitized_output,
            flags=re.DOTALL
        )

        row_count = len(re.findall(r"&", sanitized_output))
        if row_count <= 3:
            sanitized_output = re.sub(
                r"^\s*\(?[a-zA-e]\)?(?:\([i-v]+\))?\s*(&)",
                r" \1",
                sanitized_output,
                count=0,
                flags=re.MULTILINE
            )


        # ----------------------------------------------------------
        # STEP B: STRUCTURAL 4-COLUMN ENFORCER & LINE INJECTOR
        # ----------------------------------------------------------
        def inject_hlines_per_row(match):
            inner_content = match.group(1)

            # 1. Isolate and shield all TikZ blocks from structural split adjustments
            tikz_blocks = []

            def save_tikz(m_tikz):
                tikz_blocks.append(m_tikz.group(0))
                return f" __TIKZ_BLOCK_{len(tikz_blocks) - 1}__ "

            protected_content = re.sub(
                r"\\begin\{tikzpicture\}.*?\\end\{tikzpicture\}",
                save_tikz,
                inner_content,
                flags=re.DOTALL
            )

            # 2. Process rows systematically line-by-line based on row intents
            raw_lines = protected_content.split('\n')
            cleaned_rows = []

            for line in raw_lines:
                line_stripped = line.strip()
                if not line_stripped or "Part & Answer" in line_stripped:
                    continue

                # If the line contains an ampersand, it's an intended table data row
                if "&" in line_stripped:
                    row_content = re.sub(r"\\hline", "", line_stripped)
                    row_content = re.sub(r"\\+$", "", row_content).strip()

                    # Strict 4-column normalization to protect LaTeX layout metrics from crashing
                    parts = row_content.split('&')
                    if len(parts) > 4:
                        main_parts = parts[:3]
                        guidance_parts = [p.strip() for p in parts[3:] if p.strip()]
                        guidance_text = " \\newline ".join(guidance_parts)
                        main_parts.append(guidance_text)
                        row_content = " & ".join(main_parts)
                    elif len(parts) < 4:
                        while len(parts) < 4:
                            parts.append("")
                        row_content = " & ".join(parts)

                    cleaned_rows.append(f"{row_content} \\\\ \\hline")
                else:
                    # Append hanging/unstructured text lines safely to the previous row's guidance cell
                    if cleaned_rows:
                        last_row = cleaned_rows.pop()
                        last_row_clean = last_row.replace(r" \\ \hline", "").strip()
                        cleaned_rows.append(f"{last_row_clean} \\newline {line_stripped} \\\\ \\hline")
                    else:
                        cleaned_rows.append(f" & & & {line_stripped} \\\\ \\hline")

            # 3. Restore protected TikZ diagrams back to their locations
            final_content = "\n".join(cleaned_rows)
            for idx, tikz_code in enumerate(tikz_blocks):
                final_content = final_content.replace(f"__TIKZ_BLOCK_{idx}__", f"\n{tikz_code}\n")

            return "\n" + final_content + "\n"


        # Apply the layout injector over the text blocks
        sanitized_output = re.sub(
            r"\\begin\{officialmarkscheme\}(.*?)\\end\{officialmarkscheme\}",
            lambda m: f"\\begin{{officialmarkscheme}}{inject_hlines_per_row(m)}\\end{{officialmarkscheme}}",
            sanitized_output,
            flags=re.DOTALL
        )

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

        # ----------------------------------------------------------
        # EDITABLE SOURCE SYNTAX (LIVE RECOMPILE INJECTOR)
        # ----------------------------------------------------------
        with st.expander("✍️ Edit Source Syntax (Live Recompile)", expanded=False):
            st.info(
                "Any changes made here will automatically recompile the previews below and be saved when you commit.")

            edited_q_code = st.text_area(
                "Question LaTeX Source:",
                value=variants[chosen_variant_key],
                height=250,
                key=f"edit_q_{st.session_state['batch_id']}_{chosen_variant_key}"
            )

            edited_ms_code = st.text_area(
                "Markscheme LaTeX Source:",
                value=st.session_state["ms_dict"].get(chosen_variant_key, ""),
                height=250,
                key=f"edit_ms_{st.session_state['batch_id']}_{chosen_variant_key}"
            )

        # Push edits back into dictionaries so they survive switching tabs or variants
        st.session_state["variants_dict"][chosen_variant_key] = edited_q_code
        st.session_state["ms_dict"][chosen_variant_key] = edited_ms_code

        # Override the selected codes with the newly edited strings for compilation
        st.session_state["selected_latex_code"] = edited_q_code
        st.session_state["selected_ms_code"] = edited_ms_code

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

                # This correctly pulls the actively edited code from earlier
                raw_code = st.session_state["selected_latex_code"]
                slot_marks = re.findall(r"\\examanswerslot\{.*?\}\{.*?\}\{(\d+)\}", raw_code)
                manual_marks = re.findall(r"\\makebox\[\d+pt\]\[r\]\{\s*\[?(\d+)\]?\s*\}", raw_code)

                all_mark_strings = slot_marks + manual_marks
                calculated_marks = sum(int(m) for m in all_mark_strings) if all_mark_strings else 3

                active_topic = topic
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