import os
import json
import requests

def process_with_gemini(title, summary):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    prompt = (
        "Return valid JSON with keys title_fa, summary_fa, category, importance, tags. "
        "Do not wrap the JSON in markdown fences. "
        "Translate and summarize this news in natural Persian. "
        "category must be one of political,economy,technology,science,sports,culture,world,general. "
        "importance is 1-5. title=" + title + "\nsummary=" + summary
    )
    response = requests.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
        params={"key": key},
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=45,
    )
    response.raise_for_status()
    raw = response.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    return json.loads(raw)
