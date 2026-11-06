import json
import os
import random
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from generator import WEIGHTS

from app import create_app


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.history = Path(self.temp.name) / "history.sqlite3"
        self.settings_file = Path(self.temp.name) / "settings.json"
        self.app = create_app(history_db=self.history, model_variant="large")
        self.client = self.make_client(self.app)
        self.brew = {"coffee_label": "espresso", "timestamp": "2026-11-06T15:00:00+01:00"}

    def make_client(self, application):
        client = TestClient(application)
        self.addCleanup(client.close)
        return client

    def snapshot(self):
        with sqlite3.connect(self.history) as connection:
            return {
                table: connection.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall()
                for table in ("history", "punchline_usage", "fragment_usage")
            }

    def select(self, variant):
        return self.client.patch("/settings", json={"settings": {"COFFEE_MODEL_VARIANT": variant}})

    def test_manifest_and_settings_are_read_only_inspection(self):
        before = self.snapshot()
        manifest = self.client.get("/manifest").json()
        self.assertEqual(manifest["endpoints"]["settings"], {"method": "GET", "path": "/settings"})
        self.assertEqual(manifest["endpoints"]["update_settings"], {"method": "PATCH", "path": "/settings"})
        settings = self.client.get("/settings").json()
        self.assertEqual(len(settings), 1)
        self.assertEqual(settings[0]["key"], "COFFEE_MODEL_VARIANT")
        self.assertEqual(settings[0]["value"], "large")
        self.assertEqual(settings[0]["options"], ["classic", "large"])
        self.assertFalse(settings[0]["read_only"])
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.settings_file.exists())

    def test_nested_and_flat_updates_switch_both_generation_routes_immediately(self):
        for variant, body in [
            ("classic", {"settings": {"COFFEE_MODEL_VARIANT": "classic"}}),
            ("large", {"COFFEE_MODEL_VARIANT": "large"}),
        ]:
            with self.subTest(variant=variant):
                result = self.client.patch("/settings", json=body)
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.json(), {"ok": True, "settings": {"COFFEE_MODEL_VARIANT": variant}})
                self.assertEqual(self.client.get("/model").json()["variant"], variant)
                self.assertEqual(self.client.get("/settings").json()[0]["value"], variant)
                self.assertIn(variant, self.client.get("/manifest").json()["description"])
                self.assertEqual(json.loads(self.settings_file.read_text()), {"COFFEE_MODEL_VARIANT": variant})
                self.assertEqual(self.app.state.generator.model.summary()["variant"], variant)
                with patch.object(self.app.state.generator, "comment", return_value=variant) as comment:
                    self.assertEqual(self.client.post("/generate", json=self.brew).json()["response"], variant)
                    self.assertEqual(
                        self.client.post(
                            "/comment",
                            json={
                                "coffee_type": "espresso",
                                "datetime": self.brew["timestamp"],
                            },
                        ).json()["comment"],
                        variant,
                    )
                    self.assertEqual(comment.call_count, 2)
                self.assertEqual(self.client.post("/generate", json=self.brew).status_code, 200)
                self.assertEqual(
                    self.client.get("/coffee-types").json()["coffee_types"],
                    ["black", "cappuccino", "espresso", "other"],
                )

    def test_selection_survives_restart_and_takes_precedence_over_environment(self):
        self.assertEqual(self.select("classic").status_code, 200)
        with patch.dict(os.environ, {"COFFEE_MODEL_VARIANT": "large"}):
            restarted = self.make_client(create_app(history_db=self.history))
        self.assertEqual(restarted.get("/model").json()["variant"], "classic")
        self.assertEqual(restarted.get("/settings").json()[0]["value"], "classic")
        self.assertEqual(restarted.post("/generate", json=self.brew).status_code, 200)

    def test_switching_preserves_history_usage_and_fragments(self):
        self.app.state.generator.rng = random.Random(8)
        for _ in range(30):
            self.assertEqual(self.client.post("/generate", json=self.brew).status_code, 200)
        before = self.snapshot()
        self.assertTrue(before["fragment_usage"])
        for variant in ("classic", "large"):
            self.assertEqual(self.select(variant).status_code, 200)
            self.assertEqual(self.snapshot(), before)
        restarted = self.make_client(create_app(history_db=self.history))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(restarted.post("/generate", json=self.brew).status_code, 200)
        after = self.snapshot()
        self.assertEqual(len(after["history"]), len(before["history"]) + 1)
        self.assertEqual(after["history"][:-1], before["history"])

    def test_invalid_updates_do_not_change_model_settings_or_history(self):
        self.select("classic")
        before = self.snapshot()
        saved = self.settings_file.read_bytes()
        invalid = [
            {},
            [],
            None,
            {"settings": {}},
            {"coffee_model_variant": "large"},
            {"settings": {"COFFEE_MODEL_VARIANT": "large"}, "extra": True},
            {"COFFEE_MODEL_VARIANT": "large", "extra": True},
        ]
        invalid += [
            {"settings": {"COFFEE_MODEL_VARIANT": value}} for value in ("huge", "LARGE", "", None, 1, True, [], {})
        ]
        for body in invalid:
            with self.subTest(body=body):
                self.assertEqual(self.client.patch("/settings", json=body).status_code, 422)
        self.assertEqual(
            self.client.patch("/settings", content="{", headers={"Content-Type": "application/json"}).status_code, 422
        )
        self.assertEqual(self.client.get("/model").json()["variant"], "classic")
        self.assertEqual(self.settings_file.read_bytes(), saved)
        self.assertEqual(self.snapshot(), before)

    def test_failed_save_keeps_the_previous_file_and_running_model(self):
        self.select("classic")
        previous = self.settings_file.read_bytes()
        before = self.snapshot()
        for operation in ("os.fsync", "os.replace"):
            with self.subTest(operation=operation), patch("app." + operation, side_effect=OSError("disk failure")):
                with self.assertLogs("app", level="ERROR"):
                    result = self.select("large")
                self.assertEqual(result.status_code, 500)
            self.assertEqual(self.settings_file.read_bytes(), previous)
            self.assertEqual(self.client.get("/model").json()["variant"], "classic")
            self.assertEqual(self.client.get("/settings").json()[0]["value"], "classic")
            self.assertEqual(self.snapshot(), before)
            self.assertEqual({p.name for p in self.history.parent.iterdir()}, {"settings.json", "history.sqlite3"})
        self.assertEqual(self.client.post("/generate", json=self.brew).status_code, 200)

    def test_failed_model_load_leaves_the_previous_selection_running(self):
        self.select("classic")
        saved = self.settings_file.read_bytes()
        with patch("app.CoffeeModel", side_effect=ValueError("invalid model")):
            with self.assertLogs("app", level="ERROR"):
                self.assertEqual(self.select("large").status_code, 500)
        self.assertEqual(self.client.get("/model").json()["variant"], "classic")
        self.assertEqual(self.settings_file.read_bytes(), saved)
        self.assertEqual(self.client.post("/generate", json=self.brew).status_code, 200)

    def test_custom_weights_remain_pinned_and_do_not_overwrite_the_saved_selection(self):
        self.select("large")
        saved = self.settings_file.read_bytes()
        with patch.dict(os.environ, {"WEIGHTS_PATH": str(WEIGHTS)}):
            custom = self.make_client(create_app(history_db=self.history))
        setting = custom.get("/settings").json()[0]
        self.assertEqual(setting["value"], "custom")
        self.assertEqual(setting["options"], ["custom"])
        self.assertTrue(setting["read_only"])
        self.assertEqual(custom.get("/model").json()["variant"], "classic")
        result = custom.patch("/settings", json={"settings": {"COFFEE_MODEL_VARIANT": "large"}})
        self.assertEqual(result.status_code, 409)
        self.assertEqual(self.settings_file.read_bytes(), saved)
        unpinned = self.make_client(create_app(history_db=self.history))
        self.assertEqual(unpinned.get("/model").json()["variant"], "large")

    def test_invalid_persisted_settings_fail_startup_clearly(self):
        for content in ('{"COFFEE_MODEL_VARIANT":"huge"}', "{", "{}"):
            with self.subTest(content=content):
                self.settings_file.write_text(content)
                with self.assertRaises(ValueError):
                    create_app(history_db=self.history)

    def test_switches_and_brews_can_run_concurrently(self):
        def request(index):
            if index % 3 == 0:
                variant = "classic" if index % 2 == 0 else "large"
                return self.select(variant)
            return self.client.post("/generate", json=self.brew)

        with ThreadPoolExecutor(max_workers=6) as pool:
            responses = list(pool.map(request, range(24)))
        for response in responses:
            self.assertEqual(response.status_code, 200)
            if "response" in response.json():
                text = response.json()["response"]
                self.assertIn("3 p.m.", text)
                self.assertLessEqual(len(text), 220)
                self.assertNotIn("{", text)
        self.assertEqual(len(self.snapshot()["history"]), 16)
        saved = json.loads(self.settings_file.read_text())["COFFEE_MODEL_VARIANT"]
        self.assertEqual(self.client.get("/model").json()["variant"], saved)


if __name__ == "__main__":
    unittest.main()
