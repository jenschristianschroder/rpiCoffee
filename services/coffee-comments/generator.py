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
        if self.data.get("format_version") != 1:
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
        for item in self.data["openers"] + self.data["punchlines"]:
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
            fields = {field for _, field, _, _ in Formatter().parse(opener["text"]) if field}
            if not fields <= {"coffee", "coffee_lower", "clock"}:
                raise ValueError("Unsupported opener placeholder.")
            if "coffees" in opener and (not opener["coffees"] or not set(opener["coffees"]) <= coffees.keys()):
                raise ValueError("Unknown or empty opener coffee restriction.")
        identifiers, texts = set(), set()
        for item in self.data["punchlines"]:
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
        for coffee in coffees:
            if not self.openers(coffee):
                raise ValueError(f"No compatible openers for {coffee}.")
            for period in periods:
                if not self.candidates(coffee, period["id"]):
                    raise ValueError(f"No compatible punchlines for {coffee}/{period['id']}.")

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

    def candidates(self, coffee, period):
        caffeinated = self.data["coffee_types"][coffee]["caffeine"]
        return [
            joke
            for joke in self.data["punchlines"]
            if ("coffees" not in joke or coffee in joke["coffees"])
            and ("periods" not in joke or period in joke["periods"])
            and ("caffeine" not in joke or joke["caffeine"] == caffeinated)
        ]

    def openers(self, coffee):
        return [opener for opener in self.data["openers"] if "coffees" not in opener or coffee in opener["coffees"]]

    def compose(self, coffee, moment, punchline, opener):
        label = self.data["coffee_types"][coffee]["label"]
        # Only change the first letter when inserting the label inside a sentence.
        lower_label = label[0].lower() + label[1:]
        setup = opener["text"].format(coffee=label, coffee_lower=lower_label, clock=clock_text(moment))
        # Clock abbreviations already end in a period.
        setup = setup.replace("..", ".")
        return f"{setup} {punchline['text']}"


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
                if not has_usage:
                    # Seed once from retained legacy history without clearing it.
                    connection.execute(
                        "INSERT INTO punchline_usage(punchline, uses) "
                        "SELECT punchline, COUNT(*) FROM history GROUP BY punchline"
                    )

    def comment(self, coffee_type, moment):
        coffee, local, period = self.model.context(coffee_type, moment)
        candidates = self.model.candidates(coffee, period)
        with self.lock, closing(sqlite3.connect(self.db, timeout=10)) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                recent = [
                    row[0]
                    for row in connection.execute(
                        "SELECT punchline FROM history ORDER BY id DESC LIMIT ?", (self.history_size,)
                    )
                ]
                eligible = avoid_recent(candidates, recent, lambda item: item["id"])
                usage = dict(connection.execute("SELECT punchline, uses FROM punchline_usage"))
                lowest_count = min(usage.get(j["id"], 0) for j in eligible)
                eligible = [j for j in eligible if usage.get(j["id"], 0) == lowest_count]
                punchline = self.rng.choices(eligible, weights=[j["weight"] for j in eligible], k=1)[0]
                recent_openers = [
                    row[0]
                    for row in connection.execute(
                        "SELECT opener FROM history WHERE opener IS NOT NULL ORDER BY id DESC LIMIT 2"
                    )
                ]
                openers = avoid_recent(self.model.openers(coffee), recent_openers, opener_key)
                opener = self.rng.choices(openers, weights=[o["weight"] for o in openers], k=1)[0]
                text = self.model.compose(coffee, local, punchline, opener)
                connection.execute(
                    "INSERT INTO history(punchline, opener) VALUES (?, ?)", (punchline["id"], opener_key(opener))
                )
                connection.execute(
                    "INSERT INTO punchline_usage(punchline, uses) VALUES (?, 1) "
                    "ON CONFLICT(punchline) DO UPDATE SET uses=uses+1",
                    (punchline["id"],),
                )
                connection.execute(
                    "DELETE FROM history WHERE id NOT IN (SELECT id FROM history ORDER BY id DESC LIMIT ?)",
                    (max(100, self.history_size),),
                )
        return text
