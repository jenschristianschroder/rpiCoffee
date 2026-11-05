import math
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from generator import WEIGHTS
from phrases import OTHER, OTHER_PERIOD_JOKES

from app import create_app


class APITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.history_db = Path(self.temp.name) / "history.db"
        self.client = TestClient(create_app(history_db=self.history_db, weights_path=WEIGHTS))
        self.addCleanup(self.client.close)
        self.payload = {"coffee_type": "espresso", "datetime": "2026-11-04T16:30:00+01:00"}
        self.generate_payload = {"coffee_label": "espresso", "timestamp": self.payload["datetime"]}

    def test_comment_has_small_json_contract(self):
        result = self.client.post("/comment", json=self.payload)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(set(result.json()), {"comment"})
        self.assertIn("espresso", result.json()["comment"])
        self.assertIn("4:30 p.m.", result.json()["comment"])

    def test_utc_input_and_alias(self):
        result = self.client.post("/comment", json={"coffee_type": "BLACK COFFEE", "datetime": "2026-11-04T15:30:00Z"})
        self.assertEqual(result.status_code, 200)
        self.assertIn("black coffee", result.json()["comment"].lower())
        self.assertIn("4:30 p.m.", result.json()["comment"])

    def test_other_and_unknown_aliases_use_machine_activity_comments(self):
        allowed = OTHER + OTHER_PERIOD_JOKES["afternoon"]
        for coffee in ["other", " OTHER ", "UNKNOWN", "unrecognized", "unrecognised"]:
            result = self.client.post("/comment", json={**self.payload, "coffee_type": coffee})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(set(result.json()), {"comment"})
            text = result.json()["comment"]
            self.assertIn("4:30 p.m.", text)
            self.assertTrue(any(text.endswith(line) for line in allowed), text)
            self.assertFalse(text.startswith(("Black coffee", "A cappuccino", "An espresso")))

    def test_invalid_inputs_are_422(self):
        for update in [
            {"coffee_type": "tea"},
            {"coffee_type": ""},
            {"coffee_type": " "},
            {"coffee_type": "latte"},
            {"coffee_type": "double_espresso"},
            {"coffee_type": "decaf"},
            {"coffee_type": 3},
            {"coffee_type": None},
            {"coffee_type": ["espresso"]},
            {"datetime": "2026-11-04T16:30:00"},
            {"datetime": "2026-02-30T16:30:00Z"},
            {"datetime": "yesterday"},
            {"datetime": 1793800000},
            {"extra": True},
        ]:
            with self.subTest(update=update):
                self.assertEqual(self.client.post("/comment", json={**self.payload, **update}).status_code, 422)
        for payload in [{}, [], {"coffee_type": "cappuccino"}]:
            self.assertEqual(self.client.post("/comment", json=payload).status_code, 422)

    def test_bad_json_is_rejected(self):
        for route in ["/comment", "/generate"]:
            with self.subTest(route=route):
                self.assertEqual(
                    self.client.post(route, content="{", headers={"Content-Type": "application/json"}).status_code, 422
                )

    def test_generate_supports_all_coffees(self):
        for coffee in ["black", "cappuccino", "espresso", "other"]:
            with self.subTest(coffee=coffee):
                result = self.client.post("/generate", json={**self.generate_payload, "coffee_label": coffee})
                self.assertEqual(result.status_code, 200)
                data = result.json()
                self.assertEqual(set(data), {"response", "elapsed_s"})
                self.assertIn("4:30 p.m.", data["response"])
                self.assertIsInstance(data["elapsed_s"], float)
                self.assertTrue(math.isfinite(data["elapsed_s"]))
                self.assertGreaterEqual(data["elapsed_s"], 0)
                if coffee == "other":
                    self.assertTrue(
                        any(data["response"].endswith(line) for line in OTHER + OTHER_PERIOD_JOKES["afternoon"])
                    )
                else:
                    self.assertIn(coffee, data["response"].lower())

    def test_generate_utc_input_and_alias(self):
        result = self.client.post(
            "/generate", json={"coffee_label": "BLACK COFFEE", "timestamp": "2026-11-04T15:30:00Z"}
        )
        self.assertEqual(result.status_code, 200)
        self.assertIn("black coffee", result.json()["response"].lower())
        self.assertIn("4:30 p.m.", result.json()["response"])

    def test_generate_unknown_aliases_use_mystery_material(self):
        allowed = OTHER + OTHER_PERIOD_JOKES["afternoon"]
        for coffee in ["other", " OTHER ", "UNKNOWN", "unrecognized", "unrecognised"]:
            with self.subTest(coffee=coffee):
                result = self.client.post("/generate", json={**self.generate_payload, "coffee_label": coffee})
                self.assertEqual(result.status_code, 200)
                self.assertTrue(any(result.json()["response"].endswith(line) for line in allowed))

    def test_generate_invalid_inputs_do_not_consume_history(self):
        invalid = [
            {},
            [],
            {"coffee_label": "espresso"},
            {"timestamp": self.generate_payload["timestamp"]},
            self.payload,  # /comment field names are not /generate field names.
        ]
        invalid += [
            {**self.generate_payload, **update}
            for update in [
                {"coffee_label": "tea"},
                {"coffee_label": "latte"},
                {"coffee_label": ""},
                {"coffee_label": " "},
                {"coffee_label": "a" * 81},
                {"coffee_label": 3},
                {"coffee_label": None},
                {"coffee_label": ["espresso"]},
                {"timestamp": "2026-11-04T16:30:00"},
                {"timestamp": "2026-02-30T16:30:00Z"},
                {"timestamp": "yesterday"},
                {"timestamp": 1793800000},
                {"timestamp": None},
                {"prompt": "Make a joke"},
                {"system": "Be sarcastic"},
                {"max_tokens": 50},
            ]
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post("/generate", json=payload).status_code, 422)
        with sqlite3.connect(self.history_db) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM history").fetchone()[0], 0)

    def test_manifest_can_drive_an_execution(self):
        result = self.client.get("/manifest")
        self.assertEqual(result.status_code, 200)
        manifest = result.json()
        self.assertEqual(manifest["name"], "coffee-comments")
        self.assertEqual(manifest["failure_modes"], ["skip", "halt"])
        self.assertEqual({item["name"] for item in manifest["inputs"]}, {"coffee_label", "timestamp"})
        self.assertTrue(all(item["required"] and item["type"] == "string" for item in manifest["inputs"]))
        self.assertEqual(
            {item["name"]: item["type"] for item in manifest["outputs"]}, {"response": "string", "elapsed_s": "float"}
        )
        schema = self.client.get("/openapi.json").json()
        self.assertEqual(manifest["version"], schema["info"]["version"])
        request_schema = schema["components"]["schemas"]["GenerateRequest"]
        self.assertEqual(set(request_schema["required"]), {item["name"] for item in manifest["inputs"]})
        self.assertEqual(set(request_schema["properties"]), {item["name"] for item in manifest["inputs"]})

        health = manifest["endpoints"]["health"]
        self.assertEqual(self.client.request(health["method"], health["path"]).json(), {"status": "ok"})
        with sqlite3.connect(self.history_db) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM history").fetchone()[0], 0)
        execute = manifest["endpoints"]["execute"]
        result = self.client.request(execute["method"], execute["path"], json=self.generate_payload)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(set(result.json()), {item["name"] for item in manifest["outputs"]})
        self.assertIn("espresso", result.json()["response"])

    def test_both_routes_share_history_across_restart(self):
        def make_requests(client):
            for i in range(6):
                route, payload = ("/comment", self.payload) if i % 2 == 0 else ("/generate", self.generate_payload)
                self.assertEqual(client.post(route, json=payload).status_code, 200)

        make_requests(self.client)
        self.client.close()
        with TestClient(create_app(history_db=self.history_db, weights_path=WEIGHTS)) as restarted:
            make_requests(restarted)
        with sqlite3.connect(self.history_db) as connection:
            count, distinct = connection.execute("SELECT COUNT(*), COUNT(DISTINCT punchline) FROM history").fetchone()
            uses = connection.execute("SELECT SUM(uses) FROM punchline_usage").fetchone()[0]
        self.assertEqual((count, distinct, uses), (12, 12, 12))

    def test_health_types_and_openapi(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})
        types = self.client.get("/coffee-types").json()
        self.assertEqual(types["coffee_types"], ["black", "cappuccino", "espresso", "other"])
        self.assertEqual(types["timezone"], "Europe/Copenhagen")
        schema = self.client.get("/openapi.json").json()
        self.assertIn("/comment", schema["paths"])
        self.assertIn("/generate", schema["paths"])
        self.assertIn("/manifest", schema["paths"])
        self.assertIn("datetime", schema["components"]["schemas"]["BrewRequest"]["properties"])

    def test_method_and_path_contract(self):
        self.assertEqual(self.client.get("/comment").status_code, 405)
        self.assertEqual(self.client.get("/generate").status_code, 405)
        self.assertEqual(self.client.post("/manifest", json={}).status_code, 405)
        self.assertEqual(self.client.get("/missing").status_code, 404)


if __name__ == "__main__":
    unittest.main()
