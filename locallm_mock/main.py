"""
locallm mock – a fake Coffee LLM API.

Returns canned witty coffee statements based on the coffee type
parsed from the prompt.  Matches the real locallm API spec.
"""

from __future__ import annotations

import random
import re
import time
from datetime import datetime

from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="locallm-mock", version="1.0.0")

# ── Canned responses per coffee type ─────────────────────────────

_STATEMENTS: dict[str, list[str]] = {
    "black": [
        "Black coffee is proof that sometimes the simplest things in life are the most powerful.",
        "A cup of black coffee is a hug from the inside that tells you to get moving.",
        "There is nothing more honest than a cup of black coffee, no pretense, no frills, just pure energy.",
        "Black coffee drinkers do not need cream or sugar, they need results.",
        "The beauty of black coffee is its stubborn refusal to be anything other than itself.",
        "Black coffee at this hour means business, not pleasure.",
        "Nothing says I am ready for whatever today throws at me like a straight black coffee.",
        "Black coffee is the opening line of every great story told before noon.",
        "A true minimalist starts the morning with black coffee and a clear head.",
        "If mornings had a spirit animal it would be a strong black coffee.",
    ],
    "espresso": [
        "Espresso is a small cup carrying enormous ambitions.",
        "An espresso is the exclamation point at the end of a slow morning.",
        "Espresso is survival juice for people who question their career choices daily.",
        "Life is too short for weak coffee, that is why espresso was invented.",
        "An espresso is not a drink, it is a strategic decision.",
        "Espresso: because adulting was not in the original plan.",
        "The best ideas arrive right after the first espresso of the day.",
        "Espresso is the audible gasp your brain makes when it finally wakes up.",
        "One shot of espresso can turn a Monday into a Friday kind of mood.",
        "Espresso is the shortest distance between asleep and unstoppable.",
    ],
    "cappuccino": [
        "A cappuccino is the art gallery of the coffee world, pretty and powerful.",
        "Cappuccino is proof that foam can fix almost any existential crisis.",
        "A good cappuccino is a warm blanket in a cup, on a cold morning.",
        "Cappuccino drinkers understand that life needs a little froth on top.",
        "There is a certain elegance to ordering a cappuccino that says I have got my life together.",
        "Cappuccino is the coffee equivalent of adding a soundtrack to your morning routine.",
        "A cappuccino is not just a drink, it is a tiny ceremony of self care.",
        "Foam, espresso, and steamed milk walk into a cup, and a cappuccino is born.",
        "The swirl of a cappuccino is a reminder that chaos can be beautiful.",
        "A cappuccino in hand means the morning stands a fighting chance.",
    ],
    "other": [
        "Sometimes the best coffee is the one you did not plan on ordering.",
        "An unexpected coffee choice can change the trajectory of your entire day.",
        "Mystery coffee is the plot twist your morning routine needed.",
        "When in doubt, just pick something caffeinated and keep moving.",
        "The coffee you cannot name might just be the best one you ever had.",
    ],
}

# ── Number-to-words for TTS expansion ────────────────────────────

_HOUR_WORDS = {
    0: "twelve", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
    6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven",
    12: "twelve", 13: "one", 14: "two", 15: "three", 16: "four", 17: "five",
    18: "six", 19: "seven", 20: "eight", 21: "nine", 22: "ten", 23: "eleven",
}

_MINUTE_WORDS = {
    0: "", 1: "oh one", 2: "oh two", 3: "oh three", 4: "oh four", 5: "oh five",
    6: "oh six", 7: "oh seven", 8: "oh eight", 9: "oh nine",
    10: "ten", 11: "eleven", 12: "twelve", 13: "thirteen", 14: "fourteen",
    15: "fifteen", 16: "sixteen", 17: "seventeen", 18: "eighteen", 19: "nineteen",
    20: "twenty", 21: "twenty one", 22: "twenty two", 23: "twenty three",
    24: "twenty four", 25: "twenty five", 26: "twenty six", 27: "twenty seven",
    28: "twenty eight", 29: "twenty nine", 30: "thirty",
    31: "thirty one", 32: "thirty two", 33: "thirty three", 34: "thirty four",
    35: "thirty five", 36: "thirty six", 37: "thirty seven", 38: "thirty eight",
    39: "thirty nine", 40: "forty", 41: "forty one", 42: "forty two",
    43: "forty three", 44: "forty four", 45: "forty five", 46: "forty six",
    47: "forty seven", 48: "forty eight", 49: "forty nine", 50: "fifty",
    51: "fifty one", 52: "fifty two", 53: "fifty three", 54: "fifty four",
    55: "fifty five", 56: "fifty six", 57: "fifty seven", 58: "fifty eight",
    59: "fifty nine",
}


def _time_to_spoken(hour: int, minute: int) -> str:
    """Convert hour:minute to spoken English form."""
    period = "ay em" if hour < 12 else "pee em"
    h_word = _HOUR_WORDS.get(hour, str(hour))
    m_word = _MINUTE_WORDS.get(minute, str(minute))
    if minute == 0:
        return f"{h_word} {period}"
    return f"{h_word} {m_word} {period}"


def _tts_transform(text: str, hour: int, minute: int) -> str:
    """Make text more TTS-friendly: expand times, strip quotes/punctuation."""
    spoken_time = _time_to_spoken(hour, minute)
    # Replace ISO-like time references with spoken form
    text = re.sub(r'\d{1,2}:\d{2}', spoken_time, text)
    # Strip quotes
    text = text.replace('"', '').replace("'", '')
    # Strip some punctuation that causes TTS pauses
    text = text.replace(';', ',').replace('—', ', ').replace('–', ', ')
    return text


# ── Request / response models ───────────────────────────────────

class GenerateRequest(BaseModel):
    prompt: str
    max_tokens: int = Field(default=256, ge=1, le=1024)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    top_p: float = Field(default=0.9, ge=0.0, le=1.0)
    tts: bool = False


class GenerateResponse(BaseModel):
    response: str
    tokens: int
    elapsed_s: float
    tokens_per_s: float


# ── Endpoints ────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest):
    start = time.monotonic()

    # Parse coffee type from prompt
    prompt_lower = req.prompt.lower()
    coffee_type = "other"
    for ct in ("black", "espresso", "cappuccino"):
        if ct in prompt_lower:
            coffee_type = ct
            break

    # Parse timestamp from prompt (ISO format)
    hour, minute = 8, 0  # defaults
    ts_match = re.search(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})', req.prompt)
    if ts_match:
        try:
            dt = datetime.fromisoformat(ts_match.group(1))
            hour, minute = dt.hour, dt.minute
        except ValueError:
            pass

    # Pick a statement
    statements = _STATEMENTS.get(coffee_type, _STATEMENTS["other"])
    text = random.choice(statements)

    # TTS transform if requested
    if req.tts:
        text = _tts_transform(text, hour, minute)

    elapsed = time.monotonic() - start
    tokens = len(text.split())

    return GenerateResponse(
        response=text,
        tokens=tokens,
        elapsed_s=round(elapsed, 4),
        tokens_per_s=round(tokens / max(elapsed, 0.001), 1),
    )
