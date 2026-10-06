import re
from html import escape

_STOP_WORDS = {
    "the", "a", "an", "to", "of", "and", "for", "in", "on", "by", "with",
    "is", "are", "was", "were", "has", "have", "had", "its", "this", "that",
    "company", "startup", "news", "says", "said", "new",
    "میلیون", "میلیارد", "شرکت", "برای", "با", "از", "به", "در", "و", "یک",
}

def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()

def _story_tokens(value: str) -> set[str]:
    text = normalize_text(value).lower()
    text = text.replace("$", " ").replace(",", "")
    text = text.replace("۲۰۰", "200")
    text = re.sub(r"\b(million|millions)\b", "million", text)
    text = re.sub(r"[^\w\u0600-\u06ff]+", " ", text)
    tokens = {t for t in text.split() if len(t) > 2 and t not in _STOP_WORDS}
    return tokens

def _numbers(value: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", normalize_text(value).replace(",", "")))

def story_similarity(left: dict, right: dict) -> float:
    left_tokens = _story_tokens(f"{left.get('title', '')} {left.get('summary', '')}")
    right_tokens = _story_tokens(f"{right.get('title', '')} {right.get('summary', '')}")
    if not left_tokens or not right_tokens:
        return 0.0
    jaccard = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    numbers_match = bool(_numbers(left.get("title", "") + " " + left.get("summary", "")) &
                         _numbers(right.get("title", "") + " " + right.get("summary", "")))
    return min(1.0, jaccard + (0.20 if numbers_match else 0.0))

def is_duplicate_story(item: dict, previous: list[dict], threshold: float = 0.48) -> bool:
    for story in previous:
        if story_similarity(item, story) >= threshold:
            return True
    return False

def is_new_item(item_id: str, url: str, seen: set[str]) -> bool:
    return item_id not in seen and url not in seen

def is_technology_news(category: str) -> bool:
    return normalize_text(category).lower() == "technology"

def build_telegram_message(title, summary, category, source, url):
    return (
      f"📰 <b>{escape(normalize_text(title))}</b>\n\n"
      f"{escape(normalize_text(summary))}\n\n"
      f"🏷 {escape(normalize_text(category))}\n"
      f"📡 منبع: {escape(normalize_text(source))}\n\n"
      f"🔗 <a href=\"{escape(url,quote=True)}\">مشاهده منبع</a>"
    )
