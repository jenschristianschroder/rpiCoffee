"""A small, no-training, context-conditioned phrase generator."""

import json
import math
import random
import sqlite3
import threading
import unicodedata
from contextlib import closing
from pathlib import Path
from string import Formatter
from zoneinfo import ZoneInfo

WEIGHTS = Path(__file__).resolve().parent / "weights.json"
LARGE_WEIGHTS = WEIGHTS.with_name("weights-large.json")
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def text_fields(text):
    return {field for _, field, _, _ in Formatter().parse(text) if field}


def joke_key(joke):
    # Variations of a grammar structure share a repeat window and usage counter.
    return joke.get("family", joke["id"])


def normalize(value):
    value = "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))
    return " ".join(value.casefold().replace("_", " ").replace("-", " ").split())


def clock_text(moment):
    hour = moment.hour % 12 or 12
    suffix = "a.m." if moment.hour < 12 else "p.m."
    if moment.minute:
        return f"{hour}:{moment.minute:02d} {suffix}"
    return f"{hour} {suffix}"


def opener_key(opener):
    # Older custom models have no opener IDs; their text is a stable fallback.
    return opener.get("id", opener["text"])


def avoid_recent(items, recent, key):
    """Exclude recent choices, or use the oldest if a custom pool is too small."""
    fresh = [item for item in items if key(item) not in recent]
    if fresh:
        return fresh
    age = {}
    for index, identifier in enumerate(recent):
        age.setdefault(identifier, index)  # Most recent occurrence of each item.
    oldest = max(age[key(item)] for item in items)
    return [item for item in items if age[key(item)] == oldest]


class CoffeeModel:
    def __init__(self, weights_path=WEIGHTS, timezone="Europe/Copenhagen"):
        self.data = json.loads(Path(weights_path).read_text(encoding="utf-8"))
        self.size_bytes = Path(weights_path).stat().st_size
        self.timezone = ZoneInfo(timezone)
        self._validate()
        self.aliases = {}
        for name, details in self.data["coffee_types"].items():
            for alias in [name, *details["aliases"]]:
                key = normalize(alias)
                if key in self.aliases and self.aliases[key] != name:
                    raise ValueError(f"Ambiguous coffee alias: {alias}")
                self.aliases[key] = name

    def _validate(self):
        if self.data.get("format_version") not in (1, 2):
            raise ValueError("Unsupported model format.")
        coffees = self.data["coffee_types"]
        periods = self.data["periods"]
        if not coffees or not periods or not self.data["openers"] or not self.data["punchlines"]:
            raise ValueError("The model must contain coffee types, periods, openers, and punchlines.")
        hours = []
        for period in periods:
            if (
                type(period["start_hour"]) is not int
                or type(period["end_hour"]) is not int
                or not 0 <= period["start_hour"] < period["end_hour"] <= 24
            ):
                raise ValueError("Invalid period hours.")
            hours.extend(range(period["start_hour"], period["end_hour"]))
        if sorted(hours) != list(range(24)):
            raise ValueError("Periods must cover every hour exactly once.")
        period_ids = {p["id"] for p in periods}
        if len(period_ids) != len(periods):
            raise ValueError("Period identifiers must be unique.")
        for details in coffees.values():
            if (
                not isinstance(details["label"], str)
                or not details["label"]
                or (details["caffeine"] is not None and type(details["caffeine"]) is not bool)
            ):
                raise ValueError("Invalid coffee definition.")
            if not isinstance(details["aliases"], list) or any(not isinstance(a, str) for a in details["aliases"]):
                raise ValueError("Invalid coffee aliases.")
        slots = self.data.get("slots", {})
        patterns = self.data.get("patterns", [])
        if self.data["format_version"] == 1 and (slots or patterns):
            raise ValueError("Sentence grammars require model format 2.")
        context_fields = {"coffee", "coffee_lower", "clock", "weekday"}
        for name, choices in slots.items():
            if not name.isidentifier() or name in context_fields or not choices:
                raise ValueError("Invalid or empty grammar slot.")
            ids, texts = set(), set()
            for choice in choices:
                identifier, text = choice["id"], choice["text"]
                if not isinstance(identifier, str) or not identifier or identifier in ids:
                    raise ValueError("Fragment identifiers must be unique within each slot.")
                if not isinstance(text, str) or not text.strip() or text.casefold() in texts:
                    raise ValueError("Fragments must be distinct nonempty strings.")
                if "{" in text or "}" in text:
                    raise ValueError("Fragments must be literal text.")
                ids.add(identifier)
                texts.add(text.casefold())
        jokes = self.data["punchlines"] + patterns
        weighted_items = self.data["openers"] + jokes + [choice for choices in slots.values() for choice in choices]
        for item in weighted_items:
            weight = item["weight"]
            if type(weight) not in (int, float) or not math.isfinite(weight) or not 0 < weight <= 1000:
                raise ValueError("Weights must be finite positive numbers no greater than 1000.")
            if not isinstance(item["text"], str) or not item["text"].strip():
                raise ValueError("Model text must be nonempty.")
        opener_ids = set()
        for opener in self.data["openers"]:
            identifier = opener_key(opener)
            if not isinstance(identifier, str) or not identifier or identifier in opener_ids:
                raise ValueError("Opener identifiers must be unique nonempty strings.")
            opener_ids.add(identifier)
            allowed = context_fields if self.data["format_version"] == 2 else context_fields - {"weekday"}
            if not text_fields(opener["text"]) <= allowed:
                raise ValueError("Unsupported opener placeholder.")
            if "coffees" in opener and (not opener["coffees"] or not set(opener["coffees"]) <= coffees.keys()):
                raise ValueError("Unknown or empty opener coffee restriction.")
        identifiers, texts = set(), set()
        for item in jokes:
            if not isinstance(item["id"], str) or item["id"] in identifiers:
                raise ValueError("Punchline identifiers must be unique strings.")
            identifiers.add(item["id"])
            text = item["text"].strip().casefold()
            if text in texts:
                raise ValueError("Punchlines must have distinct text.")
            texts.add(text)
            if "coffees" in item and (not item["coffees"] or not set(item["coffees"]) <= coffees.keys()):
                raise ValueError("Unknown or empty coffee restriction.")
            if "periods" in item and (not item["periods"] or not set(item["periods"]) <= period_ids):
                raise ValueError("Unknown or empty period restriction.")
            if "caffeine" in item and type(item["caffeine"]) is not bool:
                raise ValueError("Invalid caffeine restriction.")
            if "weekdays" in item and (
                not item["weekdays"] or any(type(day) is not int or not 0 <= day <= 6 for day in item["weekdays"])
            ):
                raise ValueError("Weekday restrictions must contain integers from 0 to 6.")
        for item in patterns:
            fields = text_fields(item["text"])
            if not fields or not fields <= slots.keys():
                raise ValueError("Sentence patterns must reference known grammar slots.")
            if not isinstance(item.get("family"), str) or not item["family"]:
                raise ValueError("Sentence patterns require a stable family identifier.")
        if self.data["format_version"] == 2:
            for item in self.data["punchlines"]:
                if "{" in item["text"] or "}" in item["text"]:
                    raise ValueError("Complete punchlines must be literal text.")
        for coffee in coffees:
            if not self.openers(coffee):
                raise ValueError(f"No compatible openers for {coffee}.")
            for period in periods:
                for weekday in range(7):
                    if not self.candidates(coffee, period["id"], weekday):
                        raise ValueError(f"No compatible punchlines for {coffee}/{period['id']}/{weekday}.")

    def canonical_coffee(self, value):
        key = normalize(value)
        if key not in self.aliases:
            options = ", ".join(self.data["coffee_types"])
            raise ValueError(f"Unknown coffee_type. Supported types: {options}.")
        return self.aliases[key]

    def context(self, coffee_type, moment):
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError("datetime must include a UTC offset or Z.")
        coffee = self.canonical_coffee(coffee_type)
        local = moment.astimezone(self.timezone)
        period = next(p["id"] for p in self.data["periods"] if p["start_hour"] <= local.hour < p["end_hour"])
        return coffee, local, period

    def candidates(self, coffee, period, weekday=None):
        caffeinated = self.data["coffee_types"][coffee]["caffeine"]
        return [
            joke
            for joke in self.data["punchlines"] + self.data.get("patterns", [])
            if ("coffees" not in joke or coffee in joke["coffees"])
            and ("periods" not in joke or period in joke["periods"])
            and ("caffeine" not in joke or joke["caffeine"] == caffeinated)
            and ("weekdays" not in joke or weekday in joke["weekdays"])
        ]

    def openers(self, coffee):
        return [opener for opener in self.data["openers"] if "coffees" not in opener or coffee in opener["coffees"]]

    def compose(self, coffee, moment, punchline, opener, fragments=None):
        label = self.data["coffee_types"][coffee]["label"]
        # Only change the first letter when inserting the label inside a sentence.
        lower_label = label[0].lower() + label[1:]
        setup = opener["text"].format(
            coffee=label, coffee_lower=lower_label, clock=clock_text(moment), weekday=WEEKDAYS[moment.weekday()]
        )
        # Clock abbreviations already end in a period.
        setup = setup.replace("..", ".")
        body = punchline["text"]
        if "family" in punchline:
            body = body.format(**(fragments or {}))
        return f"{setup} {body}"

    def summary(self):
        slots = self.data.get("slots", {})
        patterns = self.data.get("patterns", [])
        fragments = sum(len(choices) for choices in slots.values())
        combinations = []
        for coffee in self.data["coffee_types"]:
            for period in self.data["periods"]:
                for weekday in range(7):
                    count = sum(
                        math.prod(len(slots[field]) for field in text_fields(joke["text"])) if "family" in joke else 1
                        for joke in self.candidates(coffee, period["id"], weekday)
                    )
                    combinations.append(count * len(self.openers(coffee)))
        return {
            "variant": self.data.get("variant", "classic"),
            "model_type": self.data["model_type"],
            "size_bytes": self.size_bytes,
            "learned_parameters": 0,
            "sampling_weights": len(self.data["openers"]) + len(self.data["punchlines"]) + len(patterns) + fragments,
            "complete_punchlines": len(self.data["punchlines"]),
            "openers": len(self.data["openers"]),
            "sentence_patterns": len(patterns),
            "pattern_families": len({item["family"] for item in patterns}),
            "fragments": fragments,
            "combinations_per_context": {"min": min(combinations), "max": max(combinations)},
            "combination_note": (
                "Selectable combinations for a fixed coffee, weekday and time period; not distinct jokes."
            ),
        }


class CommentGenerator:
    """Prefer less-used jokes and persist both joke and opener repeat avoidance."""

    def __init__(self, model, history_db, history_size=60, rng=None):
        self.model = model
        self.db = Path(history_db)
        if type(history_size) is not int or not 0 <= history_size <= 100:
            raise ValueError("history_size must be an integer from 0 to 100.")
        self.history_size = history_size
        self.rng = rng or random.SystemRandom()
        self.lock = threading.Lock()
        self.db.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db, timeout=10)) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY, punchline TEXT NOT NULL, opener TEXT)"
                )
                columns = {row[1] for row in connection.execute("PRAGMA table_info(history)")}
                if "opener" not in columns:
                    connection.execute("ALTER TABLE history ADD COLUMN opener TEXT")
                has_usage = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='punchline_usage'"
                ).fetchone()
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS punchline_usage "
                    "(punchline TEXT PRIMARY KEY, uses INTEGER NOT NULL CHECK(uses >= 0))"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS fragment_usage "
                    "(slot TEXT NOT NULL, fragment TEXT NOT NULL, uses INTEGER NOT NULL CHECK(uses >= 0), "
                    "PRIMARY KEY(slot, fragment))"
                )
                if not has_usage:
                    # Seed once from retained legacy history without clearing it.
                    connection.execute(
                        "INSERT INTO punchline_usage(punchline, uses) "
                        "SELECT punchline, COUNT(*) FROM history GROUP BY punchline"
                    )

    def comment(self, coffee_type, moment):
        coffee, local, period = self.model.context(coffee_type, moment)
        candidates = self.model.candidates(coffee, period, local.weekday())
        with self.lock, closing(sqlite3.connect(self.db, timeout=10)) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                recent = [
                    row[0]
                    for row in connection.execute(
                        "SELECT punchline FROM history ORDER BY id DESC LIMIT ?", (self.history_size,)
                    )
                ]
                eligible = avoid_recent(candidates, recent, joke_key)
                usage = dict(connection.execute("SELECT punchline, uses FROM punchline_usage"))
                lowest_count = min(usage.get(joke_key(j), 0) for j in eligible)
                eligible = [j for j in eligible if usage.get(joke_key(j), 0) == lowest_count]
                punchline = self.rng.choices(eligible, weights=[j["weight"] for j in eligible], k=1)[0]
                recent_openers = [
                    row[0]
                    for row in connection.execute(
                        "SELECT opener FROM history WHERE opener IS NOT NULL ORDER BY id DESC LIMIT 2"
                    )
                ]
                openers = avoid_recent(self.model.openers(coffee), recent_openers, opener_key)
                opener = self.rng.choices(openers, weights=[o["weight"] for o in openers], k=1)[0]
                fragments = {}
                if "family" in punchline:
                    for slot in sorted(text_fields(punchline["text"])):
                        choices = self.model.data["slots"][slot]
                        counts = dict(
                            connection.execute("SELECT fragment, uses FROM fragment_usage WHERE slot = ?", (slot,))
                        )
                        least = min(counts.get(choice["id"], 0) for choice in choices)
                        choices = [choice for choice in choices if counts.get(choice["id"], 0) == least]
                        choice = self.rng.choices(choices, weights=[item["weight"] for item in choices], k=1)[0]
                        fragments[slot] = choice["text"]
                        connection.execute(
                            "INSERT INTO fragment_usage(slot, fragment, uses) VALUES (?, ?, 1) "
                            "ON CONFLICT(slot, fragment) DO UPDATE SET uses=uses+1",
                            (slot, choice["id"]),
                        )
                text = self.model.compose(coffee, local, punchline, opener, fragments)
                connection.execute(
                    "INSERT INTO history(punchline, opener) VALUES (?, ?)", (joke_key(punchline), opener_key(opener))
                )
                connection.execute(
                    "INSERT INTO punchline_usage(punchline, uses) VALUES (?, 1) "
                    "ON CONFLICT(punchline) DO UPDATE SET uses=uses+1",
                    (joke_key(punchline),),
                )
                connection.execute(
                    "DELETE FROM history WHERE id NOT IN (SELECT id FROM history ORDER BY id DESC LIMIT ?)",
                    (max(100, self.history_size),),
                )
        return text
