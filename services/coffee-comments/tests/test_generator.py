import json
import random
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from generator import WEIGHTS, CoffeeModel, CommentGenerator, clock_text
from write_weights import build


class GeneratorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.model = CoffeeModel()
        self.path = Path(self.temp.name) / "history.db"
        self.generator = CommentGenerator(self.model, self.path, rng=random.Random(7))

    def history(self):
        with sqlite3.connect(self.path) as connection:
            return [row[0] for row in connection.execute("SELECT punchline FROM history ORDER BY id")]

    def test_aliases_and_unknown_drink(self):
        for raw, expected in [
            (" Black-Coffee ", "black"),
            ("BLACK_COFFEE", "black"),
            ("coffee", "black"),
            ("CAPPUCCINO", "cappuccino"),
            ("expresso", "espresso"),
            ("OTHER", "other"),
            ("unknown", "other"),
            ("unrecognised", "other"),
        ]:
            self.assertEqual(self.model.canonical_coffee(raw), expected)
        for unsupported in ["tomato soup", "latte", "decaf", "double_espresso", "americano", "filter"]:
            with self.assertRaises(ValueError):
                self.model.canonical_coffee(unsupported)

    def test_local_time_boundaries(self):
        cases = [
            (0, "early"),
            (5, "early"),
            (6, "morning"),
            (9, "morning"),
            (10, "midday"),
            (13, "midday"),
            (14, "afternoon"),
            (16, "afternoon"),
            (17, "evening"),
            (23, "evening"),
        ]
        for hour, expected in cases:
            moment = datetime.fromisoformat(f"2026-11-04T{hour:02d}:00:00+01:00")
            self.assertEqual(self.model.context("espresso", moment)[2], expected)

    def test_utc_and_daylight_saving_conversion(self):
        cases = [
            ("2026-11-04T15:30:00+00:00", 16, 1),
            ("2026-03-29T01:30:00+00:00", 3, 2),
            ("2026-10-25T00:30:00+00:00", 2, 2),
            ("2026-10-25T01:30:00+00:00", 2, 1),
        ]
        for stamp, hour, offset_hours in cases:
            local = self.model.context("espresso", datetime.fromisoformat(stamp))[1]
            self.assertEqual(local.hour, hour)
            self.assertEqual(local.utcoffset().total_seconds(), offset_hours * 3600)
        with self.assertRaises(ValueError):
            self.model.context("espresso", datetime(2026, 11, 4, 16))

    def test_clock_is_spoken_friendly(self):
        self.assertEqual(clock_text(datetime(2026, 11, 4, 0)), "12 a.m.")
        self.assertEqual(clock_text(datetime(2026, 11, 4, 12)), "12 p.m.")
        self.assertEqual(clock_text(datetime(2026, 11, 4, 16, 5)), "4:05 p.m.")

    def test_time_and_drink_specific_jokes_are_restricted(self):
        morning_ids = {j["id"] for j in self.model.candidates("espresso", "morning")}
        evening_ids = {j["id"] for j in self.model.candidates("espresso", "evening")}
        self.assertNotIn("espresso_evening_01", morning_ids)
        self.assertIn("espresso_evening_01", evening_ids)
        self.assertFalse(any(j.startswith("black_") or j.startswith("cappuccino_") for j in evening_ids))

    def test_all_contexts_have_variety_and_short_complete_comments(self):
        for coffee in self.model.data["coffee_types"]:
            for period in self.model.data["periods"]:
                jokes = self.model.candidates(coffee, period["id"])
                self.assertGreaterEqual(len(jokes), 100)
                moment = datetime(2026, 11, 4, period["start_hour"], 59)
                for joke in jokes:
                    for opener in self.model.openers(coffee):
                        text = self.model.compose(coffee, moment, joke, opener)
                        self.assertLessEqual(len(text), 180, text)
                        self.assertNotIn("{", text)
                        self.assertNotIn("..", text)
                        self.assertTrue(text.endswith("."))

    def test_recent_punchlines_do_not_repeat_and_survive_restart(self):
        moment = datetime.fromisoformat("2026-11-04T16:30:00+01:00")
        for _ in range(60):
            self.generator.comment("espresso", moment)
        previous = self.history()
        self.assertEqual(len(set(previous)), 60)
        restarted = CommentGenerator(self.model, self.path, rng=random.Random(8))
        restarted.comment("espresso", moment)
        self.assertNotIn(self.history()[-1], previous)

    def test_concurrent_generators_share_history_without_repeats(self):
        second = CommentGenerator(self.model, self.path, rng=random.Random(9))
        moment = datetime.fromisoformat("2026-11-04T16:30:00+01:00")
        with ThreadPoolExecutor(max_workers=6) as executor:
            outputs = list(
                executor.map(lambda i: (self.generator if i % 2 else second).comment("cappuccino", moment), range(12))
            )
        self.assertEqual(len(outputs), 12)
        self.assertEqual(len(set(self.history())), 12)

    def test_history_is_bounded(self):
        moment = datetime.fromisoformat("2026-11-04T16:30:00+01:00")
        for _ in range(105):
            self.generator.comment("espresso", moment)
        self.assertEqual(len(self.history()), 100)

    def test_same_context_uses_every_available_joke_before_repeating(self):
        moment = datetime.fromisoformat("2026-11-04T16:30:00+01:00")
        for coffee in ["espresso", "other"]:
            path = Path(self.temp.name) / f"rotation-{coffee}.db"
            generator = CommentGenerator(self.model, path, rng=random.Random(7))
            candidates = self.model.candidates(coffee, "afternoon")
            for _ in candidates:
                generator.comment(coffee, moment)
            with sqlite3.connect(path) as connection:
                usage = dict(connection.execute("SELECT punchline, uses FROM punchline_usage"))
            self.assertEqual(set(usage), {j["id"] for j in candidates})
            self.assertEqual(set(usage.values()), {1})
            generator.comment(coffee, moment)
            with sqlite3.connect(path) as connection:
                self.assertEqual(connection.execute("SELECT MAX(uses) FROM punchline_usage").fetchone()[0], 2)

    def test_mixed_drinks_and_times_preserve_joke_and_opener_windows(self):
        jokes, openers = [], []
        for index in range(180):
            coffee = ["black", "cappuccino", "espresso", "other"][index % 4]
            hour = [7, 11, 15, 19, 4][index % 5]
            moment = datetime(2026, 11, 4, hour, tzinfo=self.model.timezone)
            self.generator.comment(coffee, moment)
            with sqlite3.connect(self.path) as connection:
                joke, opener = connection.execute(
                    "SELECT punchline, opener FROM history ORDER BY id DESC LIMIT 1"
                ).fetchone()
            self.assertNotIn(joke, jokes[-60:])
            self.assertNotIn(opener, openers[-2:])
            jokes.append(joke)
            openers.append(opener)

    def test_legacy_history_is_migrated_once_without_losing_records(self):
        legacy = Path(self.temp.name) / "legacy.db"
        with sqlite3.connect(legacy) as connection:
            connection.execute("CREATE TABLE history (id INTEGER PRIMARY KEY, punchline TEXT NOT NULL)")
            connection.executemany("INSERT INTO history(punchline) VALUES (?)", [("office_01",), ("espresso_01",)])
        generator = CommentGenerator(self.model, legacy, rng=random.Random(3))
        generator.comment("espresso", datetime.fromisoformat("2026-11-04T16:30:00+01:00"))
        with sqlite3.connect(legacy) as connection:
            history = list(connection.execute("SELECT punchline, opener FROM history ORDER BY id"))
            usage = dict(connection.execute("SELECT punchline, uses FROM punchline_usage"))
        self.assertEqual(history[:2], [("office_01", None), ("espresso_01", None)])
        self.assertNotIn(history[-1][0], ["office_01", "espresso_01"])
        self.assertIsNotNone(history[-1][1])
        self.assertEqual(usage["office_01"], 1)
        self.assertEqual(usage["espresso_01"], 1)
        self.assertEqual(sum(usage.values()), 3)
        restarted = CommentGenerator(self.model, legacy, rng=random.Random(4))
        with sqlite3.connect(legacy) as connection:
            self.assertEqual(dict(connection.execute("SELECT punchline, uses FROM punchline_usage")), usage)
        restarted.comment("espresso", datetime.fromisoformat("2026-11-04T16:30:00+01:00"))
        with sqlite3.connect(legacy) as connection:
            self.assertEqual(connection.execute("SELECT SUM(uses) FROM punchline_usage").fetchone()[0], 4)

    def test_small_custom_model_reuses_the_oldest_available_choice(self):
        payload = json.loads(WEIGHTS.read_text())
        payload["punchlines"] = payload["punchlines"][:2]
        payload["openers"] = payload["openers"][:2]
        # Legacy custom models use an unrestricted pool for every category.
        for item in payload["punchlines"] + payload["openers"]:
            item.pop("coffees", None)
        small = Path(self.temp.name) / "small.json"
        small.write_text(json.dumps(payload))
        generator = CommentGenerator(CoffeeModel(small), self.path, rng=random.Random(2))
        moment = datetime.fromisoformat("2026-11-04T16:30:00+01:00")
        for _ in range(10):
            generator.comment("black", moment)
        with sqlite3.connect(self.path) as connection:
            rows = list(connection.execute("SELECT punchline, opener FROM history ORDER BY id"))
        for before, after in zip(rows, rows[1:]):
            self.assertNotEqual(before[0], after[0])
            self.assertNotEqual(before[1], after[1])

    def test_shipped_model_matches_authored_source(self):
        self.assertEqual(json.loads(WEIGHTS.read_text()), build())

    def test_other_never_uses_confirmed_coffee_jokes_or_openers(self):
        other_openers = {item["id"] for item in self.model.openers("other")}
        self.assertEqual(len(other_openers), 10)
        self.assertIsNone(self.model.data["coffee_types"]["other"]["caffeine"])
        for period in self.model.data["periods"]:
            other = self.model.candidates("other", period["id"])
            self.assertTrue(all(joke.get("coffees") == ["other"] for joke in other))
            other_ids = {joke["id"] for joke in other}
            self.assertGreater(len(other_ids), self.generator.history_size)
            for coffee in ["black", "cappuccino", "espresso"]:
                known_ids = {joke["id"] for joke in self.model.candidates(coffee, period["id"])}
                self.assertTrue(other_ids.isdisjoint(known_ids))
                self.assertEqual(len(known_ids), 118)
                self.assertTrue(other_openers.isdisjoint({item["id"] for item in self.model.openers(coffee)}))

    def test_missing_or_invalid_opener_coverage_is_rejected(self):
        for invalid_restriction in [[], ["nonexistent"], ["black", "cappuccino", "espresso"]]:
            payload = json.loads(WEIGHTS.read_text())
            for opener in payload["openers"]:
                opener["coffees"] = invalid_restriction
            invalid = Path(self.temp.name) / "openers.json"
            invalid.write_text(json.dumps(payload))
            with self.assertRaises(ValueError):
                CoffeeModel(invalid)

    def test_duplicate_punchline_text_is_rejected(self):
        payload = json.loads(WEIGHTS.read_text())
        payload["punchlines"][1]["text"] = payload["punchlines"][0]["text"]
        invalid = Path(self.temp.name) / "duplicate.json"
        invalid.write_text(json.dumps(payload))
        with self.assertRaises(ValueError):
            CoffeeModel(invalid)

    def test_invalid_model_is_rejected(self):
        payload = json.loads(WEIGHTS.read_text())
        payload["punchlines"][0]["weight"] = -1
        invalid = Path(self.temp.name) / "invalid.json"
        invalid.write_text(json.dumps(payload))
        with self.assertRaises(ValueError):
            CoffeeModel(invalid)


if __name__ == "__main__":
    unittest.main()
