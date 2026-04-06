"""
Remove the stale 3-Way PO content block from templates/agents/reference.html.
The old block runs from the '{# Four focused...' comment through to the
line containing '#metrics'>Metrics</a>, plus the blank line that follows.
Everything after that (the new procurement content) is kept.
"""
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
fp = os.path.join(BASE, "templates", "agents", "reference.html")

with open(fp, encoding="utf-8") as f:
    lines = f.readlines()

print(f"Total lines before: {len(lines)}")

start_idx = None
end_idx = None

for i, line in enumerate(lines):
    if "{# Four focused, tabbed flow diagrams" in line and start_idx is None:
        start_idx = i
    if "#metrics" in line and "Metrics" in line:
        end_idx = i + 1  # include the blank line after

print(f"Old block lines (1-based): {start_idx + 1} .. {end_idx + 1}")

if start_idx is not None and end_idx is not None:
    new_lines = lines[:start_idx] + lines[end_idx + 1:]
    with open(fp, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
    print(f"Total lines after: {len(new_lines)}")
    print("Done.")
else:
    print("ERROR: anchor(s) not found")
    print(f"  start_idx={start_idx}, end_idx={end_idx}")
