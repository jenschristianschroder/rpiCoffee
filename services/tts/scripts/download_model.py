"""
Download Piper TTS voice models from the official GitHub releases.

Usage:
    python download_model.py --voice en_US-lessac-medium --output-dir ./models
"""

import argparse
import json
import logging
import sys
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Piper voices metadata URL
VOICES_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/voices.json"

# Base URL for downloading voice files
BASE_DOWNLOAD_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


def download_file(url: str, dest: Path) -> None:
    """Download a file from a URL to a local path."""
    logger.info(f"Downloading: {url}")
    logger.info(f"       To: {dest}")
    urllib.request.urlretrieve(url, str(dest))
    logger.info(f"  Complete: {dest.stat().st_size:,} bytes")


def get_voice_info(voice_name: str) -> dict:
    """Fetch the voices.json and find info for the requested voice."""
    logger.info(f"Fetching voice catalog from {VOICES_URL}")
    with urllib.request.urlopen(VOICES_URL) as response:
        voices = json.loads(response.read().decode())

    # Voice name format: en_US-lessac-medium
    # Key in voices.json: en_US-lessac-medium
    if voice_name in voices:
        return voices[voice_name]

    # Try searching by partial match
    matches = [k for k in voices if voice_name in k]
    if matches:
        logger.info(f"Found partial matches: {matches}")
        return voices[matches[0]]

    available = list(voices.keys())[:20]
    logger.error(f"Voice '{voice_name}' not found. Some available voices: {available}")
    sys.exit(1)


def download_voice(voice_name: str, output_dir: Path) -> None:
    """Download a Piper voice model and its config file."""
    output_dir.mkdir(parents=True, exist_ok=True)

    voice_info = get_voice_info(voice_name)

    # Get the files to download from the voice info
    files = voice_info.get("files", {})
    if not files:
        logger.error(f"No files found for voice '{voice_name}'")
        sys.exit(1)

    for relative_path, file_info in files.items():
        url = f"{BASE_DOWNLOAD_URL}/{relative_path}"
        # Extract just the filename
        filename = Path(relative_path).name
        dest = output_dir / filename
        if dest.exists():
            logger.info(f"Already exists, skipping: {dest}")
            continue
        download_file(url, dest)

    logger.info(f"Voice '{voice_name}' downloaded to {output_dir}")


def list_common_voices():
    """Print a list of commonly used voices."""
    common = [
        ("en_US-lessac-medium", "English (US) - Lessac - Medium quality"),
        ("en_US-lessac-high", "English (US) - Lessac - High quality"),
        ("en_US-amy-medium", "English (US) - Amy - Medium quality"),
        ("en_US-ryan-medium", "English (US) - Ryan - Medium quality"),
        ("en_GB-alan-medium", "English (UK) - Alan - Medium quality"),
        ("de_DE-thorsten-medium", "German - Thorsten - Medium quality"),
        ("fr_FR-siwis-medium", "French - Siwis - Medium quality"),
        ("es_ES-davefx-medium", "Spanish - Davefx - Medium quality"),
    ]
    print("\nCommon Piper voices:")
    print("-" * 60)
    for name, desc in common:
        print(f"  {name:30s} {desc}")
    print(f"\nFull list: {VOICES_URL}")


def main():
    parser = argparse.ArgumentParser(description="Download Piper TTS voice models")
    parser.add_argument(
        "--voice", "-v",
        required=True,
        help="Voice model name (e.g., en_US-lessac-medium)",
    )
    parser.add_argument(
        "--output-dir", "-o",
        type=Path,
        default=Path("./models"),
        help="Directory to save the model files (default: ./models)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List common voices and exit",
    )
    args = parser.parse_args()

    if args.list:
        list_common_voices()
        return

    download_voice(args.voice, args.output_dir)


if __name__ == "__main__":
    main()
