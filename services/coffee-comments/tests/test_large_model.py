import json
import os
import random
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from generator import LARGE_WEIGHTS, WEIGHTS, CoffeeModel, CommentGenerator, joke_key, text_fields
from write_large_weights import build

from app import create_app


class LargeModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = CoffeeModel(LARGE_WEIGHTS)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "history.db"

    def test_shipped_counts_and_authored_source(self):
        self.assertEqual(self.model.data, build())
        info = self.model.summary()
        self.assertEqual(info["size_bytes"], LARGE_WEIGHTS.stat().st_size)
        for key, expected in {
            "complete_punchlines": 1000,
            "openers": 60,
            "sentence_patterns": 400,
            "pattern_families": 100,
            "fragments": 2000,
            "sampling_weights": 3460,
            "learned_parameters": 0,
        }.items():
            self.assertEqual(info[key], expected, key)
        full = self.model.data["punchlines"]
        self.assertEqual(len({item["text"].casefold() for item in full}), 1000)
        self.assertEqual(sum(item.get("coffees") == ["other"] for item in full), 250)
        # The original model's stable joke and opener IDs survive unchanged.
        classic = CoffeeModel().data
        self.assertEqual(full[:420], classic["punchlines"])
        self.assertEqual(self.model.data["openers"][:20], classic["openers"])

    def test_weekdays_use_local_date_and_never_leak_into_other_days(self):
        moment = datetime.fromisoformat("2026-11-08T23:30:00Z")  # Sunday UTC, Monday in Copenhagen.
        coffee, local, period = self.model.context("espresso", moment)
        self.assertEqual(local.weekday(), 0)
        for weekday in range(7):
            candidates = self.model.candidates(coffee, period, weekday)
            dated = [item for item in candidates if "weekdays" in item]
            self.assertEqual(len(dated), 10)
            self.assertTrue(all(item["weekdays"] == [weekday] for item in dated))
        opener = next(item for item in self.model.openers(coffee) if "{weekday}" in item["text"])
        text = self.model.compose(coffee, local, self.model.candidates(coffee, period, 0)[0], opener)
        self.assertIn("Monday", text)
        self.assertIn("12:30 a.m.", text)

    def test_all_possible_comments_have_a_short_length_bound(self):
        # Longest fragment for each slot bounds every lexical combination without expanding millions of strings.
        longest = {
            slot: max(choices, key=lambda item: len(item["text"]))["text"]
            for slot, choices in self.model.data["slots"].items()
        }
        for coffee in self.model.data["coffee_types"]:
            for weekday in range(7):
                moment = datetime(2026, 11, 2 + weekday, 12, 59, tzinfo=self.model.timezone)
                opener = max(
                    self.model.openers(coffee),
                    key=lambda item: len(self.model.compose(coffee, moment, {"id": "empty", "text": ""}, item)),
                )
                for period in self.model.data["periods"]:
                    for joke in self.model.candidates(coffee, period["id"], weekday):
                        text = self.model.compose(coffee, moment, joke, opener, longest)
                        self.assertLessEqual(len(text), 220, text)
                        self.assertNotIn("{", text)
                        self.assertNotIn("}", text)
                        self.assertNotIn("..", text)
                        self.assertTrue(text.endswith("."), text)

    def test_mystery_pool_and_grammar_vocabulary_are_isolated(self):
        coffee_slots = {"black_identity", "foam_identity", "espresso_identity"}
        for period in self.model.data["periods"]:
            for day in range(7):
                other = self.model.candidates("other", period["id"], day)
                self.assertTrue(all(item["coffees"] == ["other"] for item in other))
                self.assertTrue(all(not text_fields(item["text"]) & coffee_slots for item in other))
                other_ids = {item["id"] for item in other}
                for coffee in ("black", "cappuccino", "espresso"):
                    known = self.model.candidates(coffee, period["id"], day)
                    self.assertTrue(other_ids.isdisjoint(item["id"] for item in known))
        referenced = set().union(*(text_fields(item["text"]) for item in self.model.data["patterns"]))
        self.assertEqual(referenced, self.model.data["slots"].keys())

    def test_pattern_variants_share_repetition_history_across_restart(self):
        generator = CommentGenerator(self.model, self.path, rng=random.Random(8))
        moment = datetime(2026, 11, 5, 14, 30, tzinfo=self.model.timezone)
        keys, openers, texts = [], [], []
        for index in range(180):
            if index == 90:
                generator = CommentGenerator(self.model, self.path, rng=random.Random(9))
            coffee = ("black", "cappuccino", "espresso", "other")[index % 4]
            texts.append(generator.comment(coffee, moment))
            with sqlite3.connect(self.path) as connection:
                key, opener = connection.execute(
                    "SELECT punchline, opener FROM history ORDER BY id DESC LIMIT 1"
                ).fetchone()
            self.assertNotIn(key, keys[-60:])
            self.assertNotIn(opener, openers[-2:])
            keys.append(key)
            openers.append(opener)
        self.assertGreater(sum(key.startswith("grammar_") for key in keys), 20)
        self.assertEqual(len(set(texts)), len(texts))
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM history").fetchone()[0], 100)
            self.assertTrue(connection.execute("SELECT COUNT(*) FROM fragment_usage").fetchone()[0] > 0)

    def test_fragments_rotate_and_their_counts_survive_restart(self):
        data = build()
        data["punchlines"] = [item for item in data["punchlines"] if item["id"] in ("office_01", "other_01")]
        data["patterns"] = [item for item in data["patterns"] if item["family"] == "grammar_office_001"]
        for slot in data["slots"]:
            data["slots"][slot] = data["slots"][slot][:3]
        path = Path(self.temp.name) / "small-grammar.json"
        path.write_text(json.dumps(data))
        model = CoffeeModel(path)
        moment = datetime(2026, 11, 5, 9, tzinfo=model.timezone)
        for run in range(2):
            generator = CommentGenerator(model, self.path, history_size=0, rng=random.Random(run))
            for _ in range(6):
                generator.comment("black", moment)
            with sqlite3.connect(self.path) as connection:
                counts = list(connection.execute("SELECT slot, fragment, uses FROM fragment_usage"))
            self.assertEqual(len(counts), 6)
            self.assertEqual({count for _, _, count in counts}, {run + 1})

    def test_switching_between_models_preserves_classic_history(self):
        moment = datetime(2026, 11, 5, 9, tzinfo=self.model.timezone)
        classic = CommentGenerator(CoffeeModel(), self.path, rng=random.Random(4))
        classic.comment("espresso", moment)
        with sqlite3.connect(self.path) as connection:
            first = connection.execute("SELECT punchline FROM history").fetchone()[0]
        large = CommentGenerator(self.model, self.path, rng=random.Random(5))
        for _ in range(10):
            large.comment("espresso", moment)
        classic = CommentGenerator(CoffeeModel(), self.path, rng=random.Random(6))
        classic.comment("espresso", moment)
        with sqlite3.connect(self.path) as connection:
            history = [row[0] for row in connection.execute("SELECT punchline FROM history ORDER BY id")]
        self.assertEqual(history[0], first)
        self.assertEqual(len(history), 12)
        self.assertEqual(len(set(history)), 12)

    def test_concurrent_large_generators_share_the_repeat_window(self):
        generators = [CommentGenerator(self.model, self.path, rng=random.Random(i)) for i in range(2)]
        moment = datetime(2026, 11, 5, 9, tzinfo=self.model.timezone)
        with ThreadPoolExecutor(max_workers=6) as executor:
            outputs = list(executor.map(lambda i: generators[i % 2].comment("other", moment), range(24)))
        self.assertEqual(len(set(outputs)), 24)
        with sqlite3.connect(self.path) as connection:
            total, unique = connection.execute("SELECT COUNT(*), COUNT(DISTINCT punchline) FROM history").fetchone()
        self.assertEqual((total, unique), (24, 24))

    def test_invalid_grammar_is_rejected(self):
        mutations = [
            lambda d: d["patterns"][0].update(text="The {missing_slot} has arrived."),
            lambda d: d["patterns"][0].update(family=""),
            lambda d: d["slots"]["task"][0].update(weight=0),
            lambda d: d["slots"]["task"][0].update(text="{nested}"),
            lambda d: d["slots"].update(task=[]),
            lambda d: d["punchlines"][0].update(weekdays=[7]),
            lambda d: d.update(format_version=1),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                data = build()
                mutate(data)
                path = Path(self.temp.name) / "invalid.json"
                path.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    CoffeeModel(path)

    def test_same_context_cycles_through_premises_not_only_wording_variants(self):
        moment = datetime(2026, 11, 5, 9, tzinfo=self.model.timezone)
        families = {joke_key(joke) for joke in self.model.candidates("other", "morning", moment.weekday())}
        generator = CommentGenerator(self.model, self.path, rng=random.Random(10))
        for _ in families:
            generator.comment("other", moment)
        with sqlite3.connect(self.path) as connection:
            counts = dict(connection.execute("SELECT punchline, uses FROM punchline_usage"))
        self.assertEqual(set(counts), families)
        self.assertEqual(set(counts.values()), {1})


class LargeAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "history.db"

    def test_large_api_contract_aliases_timezone_and_read_only_metadata(self):
        with TestClient(create_app(history_db=self.path, model_variant="large")) as client:
            self.assertEqual(client.get("/model").json()["complete_punchlines"], 1000)
            self.assertEqual(client.get("/model").json()["variant"], "large")
            self.assertEqual(client.get("/health").json(), {"status": "ok"})
            manifest = client.get("/manifest").json()
            self.assertEqual(manifest["name"], "coffee-comments")
            self.assertEqual(manifest["version"], "2.0.0")
            with sqlite3.connect(self.path) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM history").fetchone()[0], 0)
            for coffee in ("black", "cappuccino", "espresso", "other", "unknown"):
                for route, coffee_key, time_key, output in [
                    ("/comment", "coffee_type", "datetime", "comment"),
                    ("/generate", "coffee_label", "timestamp", "response"),
                ]:
                    response = client.post(route, json={coffee_key: coffee, time_key: "2026-11-05T08:30:00Z"})
                    self.assertEqual(response.status_code, 200)
                    result = response.json()
                    self.assertEqual(set(result), {"comment"} if route == "/comment" else {"response", "elapsed_s"})
                    self.assertIn("9:30 a.m.", result[output])
                    self.assertLessEqual(len(result[output]), 220)
                    if coffee not in ("other", "unknown"):
                        self.assertIn(coffee, result[output].lower())
            response = client.post("/generate", json={"coffee_label": "espresso", "timestamp": "yesterday"})
            self.assertEqual(response.status_code, 422)
            with sqlite3.connect(self.path) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM history").fetchone()[0], 10)

    def test_default_large_classic_environment_and_custom_file_override(self):
        with patch.dict(os.environ, {}, clear=True):
            with TestClient(create_app(history_db=self.path)) as client:
                self.assertEqual(client.get("/model").json()["variant"], "large")
        with patch.dict(os.environ, {"COFFEE_MODEL_VARIANT": "classic"}):
            with TestClient(create_app(history_db=self.path)) as client:
                self.assertEqual(client.get("/model").json()["complete_punchlines"], 420)
        with patch.dict(os.environ, {"COFFEE_MODEL_VARIANT": "invalid"}):
            with self.assertRaises(ValueError):
                create_app(history_db=self.path)
            with TestClient(create_app(history_db=self.path, weights_path=WEIGHTS)) as client:
                self.assertEqual(client.get("/model").json()["variant"], "classic")


if __name__ == "__main__":
    unittest.main()
