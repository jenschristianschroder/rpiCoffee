"""
Convert the coffee JSONL dataset into the Qwen2.5 chat template format
for fine-tuning. Outputs a JSONL file with 'messages' field.
"""

import json
from pathlib import Path

INPUT = Path("dataset-coffee.json")
OUTPUT = Path("dataset-chat.jsonl")

SYSTEM_PROMPT = (
    "You are a witty coffee commentator. Given a coffee type and time, "
    "write a short, humorous observation about drinking that coffee at that time."
)


def convert():
    rows = []
    with INPUT.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            rows.append(
                {
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": rec["prompt"]},
                        {"role": "assistant", "content": rec["response"]},
                    ]
                }
            )

    with OUTPUT.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"Wrote {len(rows)} examples to {OUTPUT}")


if __name__ == "__main__":
    convert()
