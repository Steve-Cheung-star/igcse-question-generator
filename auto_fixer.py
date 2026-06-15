import json
import re
import os
import shutil

def get_expected_labels(q_code):
    tags = re.findall(r'\[ITEM\]|\[SUBENUM_START\]|\[SUBENUM_END\]', q_code)
    
    if not tags or '[ITEM]' not in tags:
        return []
        
    # Smart check: Count only top-level items
    main_item_count = 0
    temp_in_sub = False
    for tag in tags:
        if tag == '[SUBENUM_START]': temp_in_sub = True
        elif tag == '[SUBENUM_END]': temp_in_sub = False
        elif tag == '[ITEM]' and not temp_in_sub: main_item_count += 1
        
    is_single_main = (main_item_count <= 1)
    
    labels = []
    level1 = 0 
    level2 = 0 
    in_sub = False
    roman_map = {1: 'i', 2: 'ii', 3: 'iii', 4: 'iv', 5: 'v', 6: 'vi', 7: 'vii', 8: 'viii'}
    
    for tag in tags:
        if tag == '[SUBENUM_START]':
            in_sub = True
            level2 = 0
        elif tag == '[SUBENUM_END]':
            in_sub = False
        elif tag == '[ITEM]':
            if in_sub:
                level2 += 1
                if is_single_main:
                    # PROMOTED: Label becomes (a), (b)
                    char1 = chr(96 + level2)
                    labels.append(f"({char1})")
                else:
                    # REGULAR: Label is (a)(i), (a)(ii)
                    char1 = chr(96 + level1) if level1 > 0 else 'a'
                    char2 = roman_map.get(level2, 'x')
                    labels.append(f"({char1})({char2})")
            else:
                level1 += 1
                if not is_single_main:
                    # Only assign main labels if there is more than 1 main item
                    char1 = chr(96 + level1)
                    labels.append(f"({char1})")
                
    return labels

def fix_tabular_newlines(latex_code):
    """
    Parses the code to find tabular and officialmarkscheme environments.
    Ensures any row containing '&' correctly ends with standard ' \\'.
    """
    lines = latex_code.split('\n')
    new_lines = []
    active_envs = []
    
    for line in lines:
        stripped = line.strip()
        
        # Track active environments
        start_match = re.search(r"\\begin\{([A-Za-z0-9\*]+)\}", stripped)
        if start_match:
            active_envs.append(start_match.group(1))
        end_match = re.search(r"\\end\{([A-Za-z0-9\*]+)\}", stripped)
        if end_match and active_envs:
            if active_envs[-1] == end_match.group(1):
                active_envs.pop()
                
        # If we are inside a table and looking at a data row
        if ('tabular' in active_envs or 'officialmarkscheme' in active_envs):
            if '&' in stripped and not stripped.endswith(r'\hline'):
                # Strip trailing backslashes/whitespace and enforce a clean double backslash
                clean_line = re.sub(r"\\+$", "", line.rstrip())
                new_lines.append(clean_line + r" \\")
                continue
                
        new_lines.append(line)
        
    return '\n'.join(new_lines)

def fix_ms_code(ms_code, expected_labels):
    lines = ms_code.split('\n')
    new_lines = []
    label_idx = 0

    for line in lines:
        # Detect an answer row (contains '&', isn't a LaTeX command like \hline, and isn't the header)
        if '&' in line and not line.strip().startswith('\\') and 'Part & Answer' not in line:
            if label_idx < len(expected_labels):
                parts = line.split('&', 1)
                new_line = f"{expected_labels[label_idx]} &{parts[1]}"
                new_lines.append(new_line)
                label_idx += 1
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    return '\n'.join(new_lines)

def run_autofixer():
    input_file = "master_bank.json"
    backup_file = "master_bank_BACKUP.json"

    if not os.path.exists(input_file):
        print(f"Error: Could not find {input_file}")
        return

    # 1. Create a safe backup before we modify anything
    shutil.copy2(input_file, backup_file)

    with open(input_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    fixed_count = 0
    manual_review = []

    for item in data:
        q_id = item.get("question_base", "Unknown ID")
        q_code = item.get("question_code", "")
        ms_code = item.get("ms_code", "")

        # A: Enforce correct tabular new lines on both question and markscheme logic blocks
        fixed_q_code = fix_tabular_newlines(q_code)
        fixed_ms_code = fix_tabular_newlines(ms_code)
        
        changed = (fixed_q_code != q_code) or (fixed_ms_code != ms_code)

        expected_labels = get_expected_labels(fixed_q_code)

        if expected_labels:
            # Count the data rows in the newly formatted mark scheme
            ms_rows = [l for l in fixed_ms_code.split('\n') if
                       '&' in l and not l.strip().startswith('\\') and 'Part & Answer' not in l]

            # B: Apply label fix if rows and predicted labels match perfectly
            if len(expected_labels) == len(ms_rows):
                new_ms_code = fix_ms_code(fixed_ms_code, expected_labels)
                if new_ms_code != fixed_ms_code:
                    fixed_ms_code = new_ms_code
                    changed = True
            else:
                manual_review.append((q_id, len(expected_labels), len(ms_rows)))

        # 3. Only overwrite if something actually changed
        if changed:
            item["question_code"] = fixed_q_code
            item["ms_code"] = fixed_ms_code
            fixed_count += 1

    # Overwrite original file
    with open(input_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

    print(f"✅ Successfully checked/updated {fixed_count} questions!")
    print(f"📂 Updated {input_file} directly. (A backup was saved as {backup_file})\n")

    if manual_review:
        print(f"⚠️ Skipped label sync for {len(manual_review)} questions due to mismatched rows (Requires Manual Edit):")
        for q_id, q_len, ms_len in manual_review:
            print(f" - ID: {q_id} (Question code wants {q_len} parts, Mark Scheme table has {ms_len} rows)")

if __name__ == "__main__":
    run_autofixer()