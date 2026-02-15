"""Inspect the extracted document structure."""
import json
from pathlib import Path

doc = json.loads(Path("pipeline_output/02_structured_document.json").read_text(encoding="utf-8"))

print("=== TOP-LEVEL SECTIONS ===")
print(f"Total sections: {doc['total_sections']}, Total paragraphs: {doc['total_paragraphs']}")
print()

for s in doc["sections"]:
    sub_count = len(s.get("subsections", []))
    para_count = len(s.get("paragraphs", []))
    title = s["title"]
    print(f"  [{s.get('level', '?')}] {title}  (subs={sub_count}, paras={para_count})")
    for sub in s.get("subsections", []):
        sub2_count = len(sub.get("subsections", []))
        sub_para = len(sub.get("paragraphs", []))
        sub_title = sub["title"]
        print(f"      [{sub.get('level', '?')}] {sub_title}  (subs={sub2_count}, paras={sub_para})")
        for sub2 in sub.get("subsections", []):
            sub3_count = len(sub2.get("subsections", []))
            sub2_para = len(sub2.get("paragraphs", []))
            print(f"          [{sub2.get('level', '?')}] {sub2['title']}  (subs={sub3_count}, paras={sub2_para})")

print()
print("=== RULES SECTION DISTRIBUTION ===")
rules = json.loads(Path("pipeline_output/05_extracted_rules.json").read_text(encoding="utf-8"))
from collections import Counter
section_counts = Counter()
for r in rules["rules"]:
    section = r.get("source_section", "unknown")
    # Take first 2 levels of section path
    parts = section.split(" > ")
    key = " > ".join(parts[:2]) if len(parts) >= 2 else section
    section_counts[key] += 1

print(f"Total rules: {rules['total_rules']}")
print()
for section, count in sorted(section_counts.items()):
    print(f"  {count:4d}  {section}")
