"""Real Piper inference and HTTP contract checks with the bundled voice model."""

import io
import math
import os
import tempfile
import unittest
import wave
from array import array
from importlib.metadata import version
from pathlib import Path
from unittest.mock import patch

from app.tts_engine import TTSEngine, TTSEngineError
from fastapi.testclient import TestClient

from app import server

MODELS = Path(os.environ.get("TTS_TEST_MODELS_DIR", "/app/models"))
VOICE = "en_US-lessac-medium"
TEXT = "An espresso at three in the afternoon. A bold strategy for someone who enjoys sleeping."


def wav_duration(test, data):
    """Require a complete, non-silent PCM WAV that the existing player can consume."""
    test.assertEqual(data[:4], b"RIFF")
    test.assertEqual(data[8:12], b"WAVE")
    with wave.open(io.BytesIO(data), "rb") as wav:
        test.assertEqual(wav.getnchannels(), 1)
        test.assertEqual(wav.getsampwidth(), 2)
        test.assertEqual(wav.getframerate(), 22050)
        test.assertEqual(wav.getcomptype(), "NONE")
        test.assertGreater(wav.getnframes(), 1000)
        frames = wav.readframes(wav.getnframes())
        test.assertEqual(len(frames), wav.getnframes() * 2)
        test.assertGreater(max(abs(sample) for sample in array("h", frames)), 128)
        return wav.getnframes() / wav.getframerate()


class PiperEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = TTSEngine(MODELS)
        cls.engine.load_voice(VOICE)

    def test_piper_version_and_cpu_provider(self):
        self.assertEqual(version("piper-tts"), "1.8.0")
        self.assertEqual(self.engine._voice_instance.session.get_providers(), ["CPUExecutionProvider"])

    def test_real_multi_sentence_synthesis_produces_playable_audio(self):
        wav_duration(self, self.engine.synthesize(TEXT))

    def test_speed_multiplier_changes_actual_audio_duration(self):
        slow = wav_duration(self, self.engine.synthesize(TEXT, speed=0.5))
        fast = wav_duration(self, self.engine.synthesize(TEXT, speed=2.0))
        self.assertGreater(slow, fast * 2, "The speed multiplier must still affect the generated speech.")

    def test_empty_text_is_rejected(self):
        with self.assertRaisesRegex(TTSEngineError, "Text is empty"):
            self.engine.synthesize("   ")

    def test_missing_voice_does_not_discard_the_loaded_model(self):
        with self.assertRaisesRegex(TTSEngineError, "Voice model not found"):
            self.engine.load_voice("does-not-exist")
        self.assertEqual(self.engine.current_voice, VOICE)
        wav_duration(self, self.engine.synthesize("The coffee is ready."))

    def test_synthesis_requires_a_loaded_voice(self):
        engine = TTSEngine(MODELS)
        with self.assertRaisesRegex(TTSEngineError, "No voice model loaded"):
            engine.synthesize(TEXT)


class TTSAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        models = patch.object(server, "MODELS_DIR", MODELS)
        models.start()
        cls.addClassCleanup(models.stop)
        settings = patch.object(server, "SETTINGS_PATH", Path(cls.temp.name) / "settings.json")
        settings.start()
        cls.addClassCleanup(settings.stop)
        cls.client = TestClient(server.app)
        cls.client.__enter__()
        cls.addClassCleanup(cls.client.__exit__, None, None, None)

    def test_health_reports_installed_engine_and_loaded_voice(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "healthy",
                "engine": "piper-tts",
                "engine_version": "1.8.0",
                "loaded_voice": VOICE,
            },
        )

    def test_manifest_drives_real_coffee_comment_synthesis(self):
        manifest = self.client.get("/manifest").json()
        self.assertEqual(manifest["name"], "tts")
        self.assertEqual(manifest["endpoints"]["execute"], {"method": "POST", "path": "/synthesize"})
        self.assertEqual(
            manifest["outputs"],
            [
                {"name": "audio", "type": "binary", "description": "Synthesized WAV audio bytes"},
            ],
        )
        for text in (
            "Black coffee at 6 a.m. Your optimism is still loading.",
            "A cappuccino at 9:30 a.m. The foam is doing most of the work.",
            "An espresso at 3 p.m. Friday has officially become a speed run.",
            "Unidentified machine activity. Either a cleaning cycle, or the office ghost wants a latte.",
        ):
            with self.subTest(text=text):
                response = self.client.post(manifest["endpoints"]["execute"]["path"], json={"text": text})
                self.assertEqual(response.status_code, 200, response.text[:200] if response.status_code != 200 else "")
                self.assertEqual(response.headers["content-type"], "audio/wav")
                elapsed = float(response.headers["X-Processing-Time"])
                self.assertTrue(math.isfinite(elapsed) and elapsed >= 0)
                wav_duration(self, response.content)

    def test_get_route_and_explicit_voice_and_speed(self):
        response = self.client.get("/synthesize", params={"text": TEXT, "voice": VOICE, "speed": 1.5})
        self.assertEqual(response.status_code, 200)
        wav_duration(self, response.content)

    def test_voices_lists_the_existing_default(self):
        response = self.client.get("/voices")
        self.assertEqual(response.status_code, 200)
        self.assertIn({"name": VOICE, "language": "en_US", "quality": "medium"}, response.json())

    def test_invalid_requests_do_not_break_subsequent_synthesis(self):
        for body in ({}, {"text": ""}, {"text": TEXT, "speed": 0}, {"text": TEXT, "speed": 5}):
            with self.subTest(body=body):
                self.assertEqual(self.client.post("/synthesize", json=body).status_code, 422)
        self.assertEqual(
            self.client.post("/synthesize", json={"text": TEXT, "voice": "does-not-exist"}).status_code, 400
        )
        response = self.client.post("/synthesize", json={"text": "Still ready for coffee."})
        self.assertEqual(response.status_code, 200)
        wav_duration(self, response.content)


if __name__ == "__main__":
    unittest.main()
