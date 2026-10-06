import json
import os
import time

import requests


DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
TRANSIENT_GEMINI_STATUS_CODES = {429, 500, 502, 503, 504}


def _extract_json(text):
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return json.loads(text)


def process_with_gemini(title, summary, article_text=""):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    model = os.environ.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    source_text = article_text or summary
    prompt = (
        "Return valid JSON with keys title_fa, summary_fa, article_fa, category, importance, tags. "
        "Do not wrap the JSON in markdown fences. "
        "Translate into natural, professional Persian and write an original, clean, detailed news report. "
        "Do not copy the source article verbatim and do not invent facts. "
        "article_fa should be a coherent standalone Persian report with a clear lead, key facts, "
        "important context, and a short conclusion. Keep it suitable for Telegram and under 3500 Persian words. "
        "summary_fa should be a concise 2-3 sentence lead. "
        "category must be one of political,economy,technology,science,sports,culture,world,general. "
        "importance is 1-5. "
        "tags should contain useful Persian topic labels. "
        "CRITICAL NAMING RULE: Whenever the report mentions a person, company, organization, product, "
        "service, platform, model, device, or other named entity, write its natural Persian name first "
        "and immediately put the original English name in parentheses. "
        "Examples: «سم آلتمن (Sam Altman)»، «اوپن‌ای‌آی (OpenAI)»، "
        "«آیفون (iPhone)»، «چت‌جی‌پی‌تی (ChatGPT)». "
        "Use the English spelling in parentheses exactly enough to identify the entity. "
        "Apply this consistently in title_fa, summary_fa, and article_fa. "
        "Do not put parentheses around ordinary translated words that are not proper names. "
        "TITLE: " + title + "\n"
        "RSS SUMMARY: " + summary + "\n"
        "ARTICLE TEXT: " + source_text
    )
    last_error = None
    for attempt in range(3):
        try:
            response = requests.post(
                f"{GEMINI_API_BASE}/{model}:generateContent",
                params={"key": key},
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=90,
            )
            if response.ok:
                break
            if response.status_code not in TRANSIENT_GEMINI_STATUS_CODES:
                raise RuntimeError(
                    f"Gemini API error {response.status_code}: {response.text[:1000]}"
                )
            last_error = RuntimeError(
                f"Gemini API transient error {response.status_code}: {response.text[:1000]}"
            )
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            last_error = exc

        if attempt < 2:
            time.sleep(2 * (attempt + 1))
    else:
        raise RuntimeError("Gemini API temporarily unavailable after 3 attempts") from last_error

    try:
        raw = response.json()["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("Gemini API returned an unexpected response") from exc

    return _extract_json(raw)
