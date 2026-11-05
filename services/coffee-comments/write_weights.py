"""Write the Coffee Snark phrase model directly. No training or external data."""

import json
from pathlib import Path

from phrases import COFFEE_JOKES, CONTEXT_JOKES, GENERAL, OTHER, OTHER_PERIOD_JOKES, PERIOD_JOKES


def build():
    coffees = {
        "black": {"label": "Black coffee", "aliases": ["black coffee", "coffee", "regular coffee"], "caffeine": True},
        "cappuccino": {"label": "A cappuccino", "aliases": [], "caffeine": True},
        "espresso": {"label": "An espresso", "aliases": ["expresso"], "caffeine": True},
        "other": {
            "label": "Unidentified machine activity",
            "aliases": ["unknown", "unrecognized", "unrecognised"],
            "caffeine": None,
        },
    }
    known_coffees = ["black", "cappuccino", "espresso"]
    jokes = []

    def add(prefix, lines, *, drinks=None, periods=None, weight=1.0):
        for i, text in enumerate(lines, 1):
            item = {"id": f"{prefix}_{i:02d}", "text": text, "weight": weight}
            if drinks is not None:
                item["coffees"] = drinks
            if periods is not None:
                item["periods"] = periods
            jokes.append(item)

    # Preserve identifiers for the existing office jokes and coffee/period groups.
    add("office", GENERAL, drinks=known_coffees)
    for coffee, lines in COFFEE_JOKES.items():
        add(coffee, lines, drinks=[coffee], weight=3.0)
    for period, lines in PERIOD_JOKES.items():
        add(period, lines, drinks=known_coffees, periods=[period], weight=2.0)
    for coffee, periods in CONTEXT_JOKES.items():
        for period, lines in periods.items():
            add(f"{coffee}_{period}", lines, drinks=[coffee], periods=[period], weight=4.0)

    add("other", OTHER, drinks=["other"], weight=1.0)
    for period, lines in OTHER_PERIOD_JOKES.items():
        add(f"other_{period}", lines, drinks=["other"], periods=[period], weight=3.0)

    coffee_openers = [
        {"id": "order", "text": "{coffee} at {clock}.", "weight": 1.5},
        {"id": "question", "text": "{coffee} at {clock}?", "weight": 1.0},
        {"id": "ah", "text": "Ah, {coffee_lower} at {clock}.", "weight": 1.0},
        {"id": "it_is", "text": "It's {clock}. {coffee} it is.", "weight": 1.0},
        {"id": "apparently", "text": "{coffee}. At {clock}, apparently.", "weight": 1.0},
        {"id": "reporting", "text": "{coffee}, reporting for duty at {clock}.", "weight": 1.0},
        {"id": "machine", "text": "At {clock}, the machine presents {coffee_lower}.", "weight": 1.0},
        {"id": "strategy", "text": "Today's {clock} strategy: {coffee_lower}.", "weight": 1.0},
        {"id": "chat", "text": "{coffee} has entered the chat at {clock}.", "weight": 1.0},
        {"id": "plan", "text": "It's {clock}, and {coffee_lower} is the plan.", "weight": 1.0},
    ]
    other_openers = [
        {"id": "other_activity", "text": "Unidentified machine activity at {clock}.", "weight": 1.5},
        {
            "id": "other_mystery",
            "text": "Something happened at {clock}. Classification remains a mystery.",
            "weight": 1.0,
        },
        {"id": "other_phase", "text": "It's {clock}. The machine has entered its mysterious phase.", "weight": 1.0},
        {"id": "other_category", "text": "At {clock}, we have a new entry in the unknown category.", "weight": 1.0},
        {"id": "other_identity", "text": "The {clock} event has declined to identify itself.", "weight": 1.0},
        {"id": "other_weird", "text": "Unscheduled weirdness detected at {clock}.", "weight": 1.0},
        {"id": "other_plot", "text": "At {clock}, the plot thickens near the coffee machine.", "weight": 1.0},
        {"id": "other_whatever", "text": "Whatever that was, it happened at {clock}.", "weight": 1.0},
        {"id": "other_script", "text": "It's {clock}, and the machine has gone off-script.", "weight": 1.0},
        {
            "id": "other_questions",
            "text": "Something stirred at {clock}. The machine is not taking questions.",
            "weight": 1.0,
        },
    ]
    for opener in coffee_openers:
        opener["coffees"] = known_coffees
    for opener in other_openers:
        opener["coffees"] = ["other"]

    return {
        "format_version": 1,
        "name": "Coffee Snark",
        "model_type": "conditional weighted phrase grammar",
        "training_steps": 0,
        "description": "Original hand-written phrases and relative sampling weights; no learned or pretrained weights.",
        "coffee_types": coffees,
        "periods": [
            {"id": "early", "start_hour": 0, "end_hour": 6},
            {"id": "morning", "start_hour": 6, "end_hour": 10},
            {"id": "midday", "start_hour": 10, "end_hour": 14},
            {"id": "afternoon", "start_hour": 14, "end_hour": 17},
            {"id": "evening", "start_hour": 17, "end_hour": 24},
        ],
        "openers": coffee_openers + other_openers,
        "punchlines": jokes,
    }


if __name__ == "__main__":
    path = Path(__file__).resolve().parent / "weights.json"
    payload = build()
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(payload['punchlines'])} punchlines and {len(payload['openers'])} openers. Training steps: 0.")
