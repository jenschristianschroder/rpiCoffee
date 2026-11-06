"""Check the production container over HTTP with real Piper synthesis; stdlib only."""

import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from array import array

base = os.environ.get("BASE_URL", "http://127.0.0.1:5050").rstrip("/")
for attempt in range(60):
    try:
        with urllib.request.urlopen(base + "/health", timeout=2) as response:
            health = json.load(response)
        assert health == {
            "status": "healthy",
            "engine": "piper-tts",
            "engine_version": "1.8.0",
            "loaded_voice": "en_US-lessac-medium",
        }, health
        break
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        if attempt == 59:
            raise
        time.sleep(0.5)

with urllib.request.urlopen(base + "/manifest", timeout=5) as response:
    manifest = json.load(response)
    assert manifest["name"] == "tts"
    assert manifest["endpoints"]["execute"] == {"method": "POST", "path": "/synthesize"}
    assert any(output["name"] == "audio" and output["type"] == "binary" for output in manifest["outputs"])

with urllib.request.urlopen(base + "/voices", timeout=5) as response:
    assert any(voice["name"] == health["loaded_voice"] for voice in json.load(response))

text = "An espresso at three in the afternoon. A bold strategy for someone who enjoys sleeping."
requests = [
    urllib.request.Request(
        base + "/synthesize",
        data=json.dumps({"text": text}).encode(),
        headers={"Content-Type": "application/json"},
    ),
    urllib.request.Request(base + "/synthesize?" + urllib.parse.urlencode({"text": text, "speed": 1.5})),
]
for request in requests:
    with urllib.request.urlopen(request, timeout=30) as response:
        assert response.status == 200
        assert response.headers.get_content_type() == "audio/wav"
        assert float(response.headers["X-Processing-Time"]) >= 0
        audio = response.read()
    with wave.open(io.BytesIO(audio), "rb") as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 22050)
        assert wav.getnframes() > 1000
        frames = wav.readframes(wav.getnframes())
        assert len(frames) == wav.getnframes() * 2
        assert max(abs(sample) for sample in array("h", frames)) > 128
        print(request.get_method(), "generated", round(wav.getnframes() / wav.getframerate(), 2), "seconds of PCM WAV")
print("Piper 1.8.0 production HTTP checks passed.")
