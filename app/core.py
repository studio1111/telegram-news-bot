from datetime import datetime, timedelta, timezone
from html import escape
import re

_STOP_WORDS = {
    "the", "a", "an", "to", "of", "and", "for", "in", "on", "by", "with",
    "is", "are", "was", "were", "has", "have", "had", "its", "this", "that",
    "company", "startup", "news", "says", "said", "new",
    "میلیون", "میلیارد", "شرکت", "برای", "با", "از", "به", "در", "و", "یک",
}

CHANNEL_HANDLE = "@MyNewsTechnology"
CHANNEL_FOOTER = f"آخرین اخبار تکنولوژی | {CHANNEL_HANDLE}"


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def _story_tokens(value: str) -> set[str]:
    text = normalize_text(value).lower()
    text = text.replace("$", " ").replace(",", "")
    text = text.replace("۲۰۰", "200")
    text = re.sub(r"\b(million|millions)\b", "million", text)
    text = re.sub(r"[^\w\u0600-\u06ff]+", " ", text)
    return {t for t in text.split() if len(t) > 2 and t not in _STOP_WORDS}


def _numbers(value: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", normalize_text(value).replace(",", "")))


def story_similarity(left: dict, right: dict) -> float:
    left_tokens = _story_tokens(f"{left.get('title', '')} {left.get('summary', '')}")
    right_tokens = _story_tokens(f"{right.get('title', '')} {right.get('summary', '')}")
    if not left_tokens or not right_tokens:
        return 0.0
    jaccard = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    numbers_match = bool(
        _numbers(left.get("title", "") + " " + left.get("summary", ""))
        & _numbers(right.get("title", "") + " " + right.get("summary", ""))
    )
    return min(1.0, jaccard + (0.20 if numbers_match else 0.0))


def is_duplicate_story(item: dict, previous: list[dict], threshold: float = 0.48) -> bool:
    return any(story_similarity(item, story) >= threshold for story in previous)


def is_new_item(item_id: str, url: str, seen: set[str]) -> bool:
    return item_id not in seen and url not in seen


NEWS_WINDOW_MINUTES = 10\n\n\ndef is_recent_news(published_at: datetime | None, now: datetime | None = None, window_minutes: int = NEWS_WINDOW_MINUTES) -> bool:
    if published_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    published_at = published_at.astimezone(timezone.utc)
    now = now.astimezone(timezone.utc)
    age = now - published_at
    return timedelta(0) <= age <= timedelta(minutes=window_minutes)


def is_technology_news(category: str) -> bool:
    return normalize_text(category).lower() == "technology"


def build_telegram_message(title, summary, category, source, url=None):
    return (
        f"📰 <b>{escape(normalize_text(title))}</b>\n\n"
        f"{escape(normalize_text(summary))}\n\n"
        f"🏷 {escape(normalize_text(category))}\n"
        f"📡 منبع: {escape(normalize_text(source))}"
    )


def build_rich_message_html(title, summary, article, source):
    clean_title = escape(normalize_text(title))
    clean_summary = escape(normalize_text(summary))
    clean_article = escape(normalize_text(article))
    clean_source = escape(normalize_text(source))

    return (
        f"<b>📰 {clean_title}</b>\n\n"
        f"{clean_summary}\n\n"
        "<details><summary>&nbsp;&nbsp;✨ مشاهده متن کامل خبر ✨&nbsp;&nbsp;</summary>"
        f"<p>{clean_article}</p>"
        "</details>\n\n"
        f"📡 منبع: {clean_source}\n"
        f"{CHANNEL_FOOTER}"
    )


def build_expanded_message(title, article, source):
    return build_rich_message_html(title, "", article, source).replace(
        "\n\n</details>", "</details>"
    )
