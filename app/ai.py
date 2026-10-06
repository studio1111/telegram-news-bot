import json
import os

import requests


DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def _extract_json(text):
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "
".join(lines).strip()
    return json.loads(text)


def process_with_gemini(title, summary):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    model = os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    prompt = (
        "Return valid JSON with keys title_fa, summary_fa, category, importance, tags. "
        "Do not wrap the JSON in markdown fences. "
        "Translate and summarize this news in natural Persian. "
        "category must be one of political,economy,technology,science,sports,culture,world,general. "
        "importance is 1-5. title=" + title + "
summary=" + summary
    )
    response = requests.post(
        f"{GEMINI_API_BASE}/{model}:generateContent",
        params={"key": key},
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=45,
    )
    if not response.ok:
        detail = response.text[:1000]
        raise RuntimeError(f"Gemini API error {response.status_code}: {detail}")

    try:
        raw = response.json()["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("Gemini API returned an unexpected response") from exc

    return _extract_json(raw)
