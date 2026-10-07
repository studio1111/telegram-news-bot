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

def _title_similarity(left: str, right: str) -> float:
    from difflib import SequenceMatcher

    return SequenceMatcher(
        None,
        normalize_text(left).lower(),
        normalize_text(right).lower(),
    ).ratio()


def is_duplicate_story(item: dict, previous: list[dict], threshold: float = 0.65) -> bool:
    item_url = normalize_text(item.get("url", ""))
    item_title = normalize_text(item.get("title", ""))
    for story in previous:
        story_url = normalize_text(story.get("url", ""))
        if item_url and story_url and item_url == story_url:
            return True

        title_jaccard = 0.0
        item_tokens = _story_tokens(item_title)
        story_tokens = _story_tokens(story.get("title", ""))
        if item_tokens and story_tokens:
            title_jaccard = len(item_tokens & story_tokens) / len(item_tokens | story_tokens)

        combined_similarity = story_similarity(item, story)
        title_similarity = _title_similarity(item_title, story.get("title", ""))

        # Duplicate detection must be conservative. Generic words such as
        # "company", "new", "technology", and "launches" are not enough.
        if title_jaccard >= threshold and title_similarity >= 0.72:
            return True
        if title_similarity >= 0.82 and combined_similarity >= 0.55:
            return True
        if (
            _numbers(item.get("title", "") + " " + item.get("summary", ""))
            & _numbers(story.get("title", "") + " " + story.get("summary", ""))
            and title_jaccard >= 0.45
            and combined_similarity >= 0.55
        ):
            return True

    return False

def is_new_item(item_id: str, url: str, seen: set[str]) -> bool:
    return item_id not in seen and url not in seen

NEWS_WINDOW_MINUTES = 30

def is_recent_news(
    published_at: datetime | None,
    now: datetime | None = None,
    window_minutes: int = NEWS_WINDOW_MINUTES,
) -> bool:
    if published_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    published_at = published_at.astimezone(timezone.utc)
    now = now.astimezone(timezone.utc)
    age = now - published_at
    return timedelta(0) <= age <= timedelta(minutes=window_minutes)

_TECHNOLOGY_CATEGORIES = {
    "technology",
    "tech",
    "artificial intelligence",
    "ai",
    "cybersecurity",
    "cyber security",
    "software",
    "hardware",
    "gadgets",
    "mobile",
    "cloud",
    "semiconductors",
    "consumer technology",
}

_TECHNOLOGY_SIGNALS = (
    "technology", "tech", "artificial intelligence", "machine learning",
    "generative ai", "ai model", "ai system", "chatgpt", "openai",
    "anthropic", "google gemini", "microsoft copilot", "nvidia",
    "semiconductor", "chip", "processor", "gpu", "software",
    "cybersecurity", "cyber security", "malware", "ransomware",
    "smartphone", "iphone", "android", "robotics", "robot",
    "quantum computing", "data center", "cloud computing",
    "operating system", "browser", "app store", "social platform",
    "tesla", "apple", "meta platforms", "amazon web services",
)

_NON_TECH_SPORTS_SIGNALS = (
    "football", "soccer", "basketball", "baseball", "cricket",
    "tennis", "golf", "rugby", "concacaf", "nations league",
    "match", "head-to-head", "league standings", "goal", "goals",
    "player statistics", "sports", "tournament",
)

def is_technology_news(category: str) -> bool:
    return normalize_text(category).lower() in _TECHNOLOGY_CATEGORIES

def is_technology_story(
    category: str,
    title: str = "",
    summary: str = "",
    article: str = "",
) -> bool:
    """Accept technology stories even when the AI category is slightly wrong.

    The feed source is never treated as proof by itself. Content must contain
    multiple technology signals, while strong sports evidence blocks fallback.
    """
    if is_technology_news(category):
        return True

    text = normalize_text(f"{title} {summary} {article}").lower()
    if not text:
        return False

    sports_hits = sum(signal in text for signal in _NON_TECH_SPORTS_SIGNALS)
    tech_hits = sum(signal in text for signal in _TECHNOLOGY_SIGNALS)

    if sports_hits >= 2 and tech_hits < 4:
        return False

    return tech_hits >= 2

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
        f"📡 منبع: {clean_source}<br>\n"
        f"{CHANNEL_FOOTER}"
    )

def build_expanded_message(title, article, source):
    return build_rich_message_html(title, "", article, source).replace(
        "\n\n</details>", "</details>"
    )
