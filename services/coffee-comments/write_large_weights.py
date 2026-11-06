"""Build the large, no-training model from its reviewable authored sources."""

import json
from pathlib import Path

import large_grammar as grammar
import large_phrases as phrases
from write_weights import build as build_classic


def build():
    data = build_classic()
    data.update(
        {
            "format_version": 2,
            "variant": "large",
            "name": "Coffee Snark Large",
            "model_type": "conditional weighted sentence grammar",
            "description": (
                "1,000 complete jokes, 100 sentence families with four introductions each, "
                "and 2,000 typed lexical variations. Directly authored; no learned or pretrained weights."
            ),
        }
    )
    known = ["black", "cappuccino", "espresso"]

    def add(prefix, texts, coffees, *, expected, periods=None, weekdays=None, weight=2.0):
        if len(texts) != expected:
            raise ValueError(f"{prefix}: expected {expected} complete punchlines, got {len(texts)}")
        for index, text in enumerate(texts, 1):
            item = {"id": f"large_{prefix}_{index:03d}", "text": text, "weight": weight, "coffees": coffees}
            if periods is not None:
                item["periods"] = periods
            if weekdays is not None:
                item["weekdays"] = weekdays
            data["punchlines"].append(item)

    add("office", phrases.GENERAL, known, expected=80)
    for coffee in known:
        add(coffee, getattr(phrases, coffee.upper()), [coffee], expected=80, weight=3.0)
    add("other", phrases.OTHER, ["other"], expected=120)
    for period, texts in phrases.PERIODS.items():
        add(period, texts, known, expected=12, periods=[period], weight=3.0)
    for weekday, texts in phrases.WEEKDAYS.items():
        add(f"weekday_{weekday}", texts, known, expected=10, weekdays=[weekday], weight=3.0)
    for period, texts in phrases.OTHER_PERIODS.items():
        add(f"other_{period}", texts, ["other"], expected=2, periods=[period])

    for prefix, texts, coffees in [
        ("coffee", grammar.COFFEE_OPENERS, known),
        ("other", grammar.OTHER_OPENERS, ["other"]),
    ]:
        if len(texts) != 20:
            raise ValueError(f"Expected 20 additional {prefix} openers")
        data["openers"].extend(
            {"id": f"large_{prefix}_opener_{index:02d}", "text": text, "weight": 1.0, "coffees": coffees}
            for index, text in enumerate(texts, 1)
        )

    data["slots"] = {}
    for name, (modifiers, bases) in grammar.SLOT_BANKS.items():
        modifiers, bases = modifiers.split("|"), bases.split("|")
        if len(modifiers) != 5 or len(bases) != 20:
            raise ValueError(f"{name}: expected five modifiers and twenty base phrases")
        data["slots"][name] = [
            {"id": f"{name}_{i:02d}_{j:02d}", "text": f"{modifier} {base}", "weight": 1.0}
            for i, modifier in enumerate(modifiers, 1)
            for j, base in enumerate(bases, 1)
        ]

    data["patterns"] = []
    for group, texts, coffees, expected in [
        ("office", grammar.OFFICE, known, 40),
        ("black", grammar.BLACK, ["black"], 12),
        ("cappuccino", grammar.CAPPUCCINO, ["cappuccino"], 12),
        ("espresso", grammar.ESPRESSO, ["espresso"], 12),
        ("other", grammar.OTHER, ["other"], 24),
    ]:
        if len(texts) != expected:
            raise ValueError(f"{group}: expected {expected} sentence families, got {len(texts)}")
        for index, text in enumerate(texts, 1):
            family = f"grammar_{group}_{index:03d}"
            for suffix, prefix in [
                ("plain", ""),
                ("update", "Official update: "),
                ("record", "For the record, "),
                ("finding", "Today's finding: "),
            ]:
                body = text if not prefix else text[0].lower() + text[1:]
                data["patterns"].append(
                    {
                        "id": f"{family}_{suffix}",
                        "family": family,
                        "text": prefix + body,
                        "weight": 3.0,
                        "coffees": coffees,
                    }
                )
    return data


if __name__ == "__main__":
    path = Path(__file__).resolve().parent / "weights-large.json"
    path.write_text(json.dumps(build(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {path.name}: {path.stat().st_size:,} bytes. Training steps: 0.")
