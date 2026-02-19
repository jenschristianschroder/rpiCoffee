"""
Piper TTS Engine Wrapper

Handles model loading, voice management, and speech synthesis
using the piper-tts library with ONNX runtime inference.
"""

import io
import wave
import logging
from pathlib import Path
from typing import Optional

import piper

logger = logging.getLogger(__name__)


class TTSEngineError(Exception):
    """Custom exception for TTS engine errors."""
    pass


class TTSEngine:
    """
    Wrapper around Piper TTS for local speech synthesis.

    Manages voice models and provides a simple synthesis API.
    """

    def __init__(self, models_dir: Path):
        self.models_dir = models_dir
        self.current_voice: Optional[str] = None
        self._voice_instance: Optional[piper.PiperVoice] = None

        if not models_dir.exists():
            raise TTSEngineError(f"Models directory not found: {models_dir}")

    def list_voices(self) -> list[str]:
        """List available voice model names."""
        voices = []
        for onnx_file in sorted(self.models_dir.glob("*.onnx")):
            # Check that the corresponding .json config exists
            config_file = onnx_file.with_suffix(".onnx.json")
            if config_file.exists():
                voices.append(onnx_file.stem)
        return voices

    def load_voice(self, voice_name: str) -> None:
        """Load a voice model by name."""
        onnx_path = self.models_dir / f"{voice_name}.onnx"
        config_path = self.models_dir / f"{voice_name}.onnx.json"

        if not onnx_path.exists():
            available = self.list_voices()
            raise TTSEngineError(
                f"Voice model not found: {voice_name}. "
                f"Available voices: {available}"
            )

        if not config_path.exists():
            raise TTSEngineError(
                f"Voice config not found: {config_path}"
            )

        try:
            logger.info(f"Loading voice model: {voice_name}")
            self._voice_instance = piper.PiperVoice.load(
                str(onnx_path),
                config_path=str(config_path),
                use_cuda=False,  # Raspberry Pi = CPU only
            )
            self.current_voice = voice_name
            logger.info(f"Voice loaded: {voice_name}")
        except Exception as e:
            raise TTSEngineError(f"Failed to load voice '{voice_name}': {e}")

    def synthesize(
        self,
        text: str,
        speed: float = 1.0,
    ) -> bytes:
        """
        Synthesize text to WAV audio bytes.

        Args:
            text: The text to speak.
            speed: Speech speed multiplier (1.0 = normal).

        Returns:
            WAV file content as bytes.
        """
        if not self._voice_instance:
            raise TTSEngineError("No voice model loaded")

        if not text.strip():
            raise TTSEngineError("Text is empty")

        try:
            # Piper synthesize_ids_to_wav expects a file-like object
            wav_buffer = io.BytesIO()

            with wave.open(wav_buffer, "wb") as wav_file:
                self._voice_instance.synthesize(
                    text,
                    wav_file,
                    length_scale=1.0 / speed,  # Inverse: higher speed = lower scale
                )

            wav_buffer.seek(0)
            return wav_buffer.read()

        except Exception as e:
            raise TTSEngineError(f"Synthesis failed: {e}")
