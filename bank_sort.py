import json
import re
from typing import List, Dict, Any, Tuple

DIFFICULTY_RANK = {
    "Easy": 1,
    "Medium": 2,
    "Hard": 3
}

def parse_subtopic(subtopic_str: str) -> Tuple[int, ...]:
    """
    Extracts numerical segments from subtopic strings.
    Example: "E1.10" -> (1, 10), ensuring E1.2 sorts before E1.10.
    """
    if not isinstance(subtopic_str, str):
        return (0, 0)
    matches = re.findall(r'\d+', subtopic_str)
    return tuple(int(n) for n in matches) if matches else (0, 0)

def sort_question_bank(data: List[Dict[str, Any]], deduplicate: bool = False) -> List[Dict[str, Any]]:
    """
    Sorts master bank by Subtopic (natural) -> Paper Type -> Topic -> Difficulty -> Total Marks -> ID.
    """
    items_to_sort = data

    if deduplicate:
        seen = set()
        unique_data = []
        for item in data:
            item_id = item.get("id")
            if item_id not in seen:
                seen.add(item_id)
                unique_data.append(item)
        items_to_sort = unique_data

    return sorted(
        items_to_sort,
        key=lambda item: (
            parse_subtopic(item.get("subtopic", "")),
            item.get("paper_type", ""),
            item.get("topic", ""),
            DIFFICULTY_RANK.get(item.get("difficulty", ""), 99),
            item.get("total_marks", 0),
            item.get("id", "")
        )
    )

if __name__ == "__main__":
    filepath = "master_bank.json"

    # Read existing question bank
    with open(filepath, "r", encoding="utf-8") as f:
        bank_data = json.load(f)

    # Sort question bank
    sorted_bank = sort_question_bank(bank_data, deduplicate=False)

    # Overwrite original file directly
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(sorted_bank, f, indent=2)

    print(f"Successfully sorted {len(sorted_bank)} items and updated {filepath}")