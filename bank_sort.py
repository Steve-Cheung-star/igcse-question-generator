import json
import re


def natural_key(text):
  """Splits text into chunks of digits and non-digits to enable natural numerical sorting

  (e.g., E1.2 comes before E1.10 instead of standard alphabetical order).
  """
  return [
      int(part) if part.isdigit() else part.lower()
      for part in re.split(r'(\d+)', str(text))
  ]


def sort_master_bank(input_file, output_file):
  with open(input_file, 'r', encoding='utf-8') as f:
    data = json.load(f)

  # Sort by paper_type first, then by subtopic in natural order
  sorted_data = sorted(
      data,
      key=lambda x: (
          natural_key(x.get('paper_type', '')),
          natural_key(x.get('subtopic', '')),
      ),
  )

  with open(output_file, 'w', encoding='utf-8') as f:
    json.dump(sorted_data, f, indent=2, ensure_ascii=False)


if __name__ == '__main__':
  sort_master_bank('master_bank.json', 'sorted_master_bank.json')