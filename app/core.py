from datetime import datetime, timedelta, timezone
from html import escape
import os
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
    text = normalize_text(value).lower().replace("$", " ").replace(",", "")
    text = text.replace("۲۰۰", "200")
    text = re.sub(r"\b(million|millions)\b", "million", text)
    text = re.sub(r"[^\w\u0600-\u06ff]+", " ", text)
    return {t for t in text.split() if len(t) > 2 and t not in _STOP_WORDS}

def _numbers(value: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", normalize_text(value).replace(",", "")))

def story_similarity(left: dict, right: dict) -> float:
    left_tokens = _story_tokens(f"{left.get('title', '')} {left.get('summary', '')}")
    right_tokens = _story_tokens(f"{right.get('title', '')} {right.get('summary', '')}")
    if not left_tokens or not right_tokens: return 0.0
    jaccard = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    numbers_match = bool(_numbers(left.get("title", "") + " " + left.get("summary", "")) & _numbers(right.get("title", "") + " " + right.get("summary", "")))
    return min(1.0, jaccard + (0.20 if numbers_match else 0.0))

def _title_similarity(left: str, right: str) -> float:
    from difflib import SequenceMatcher
    return SequenceMatcher(None, normalize_text(left).lower(), normalize_text(right).lower()).ratio()

def is_duplicate_story(item: dict, previous: list[dict], threshold: float = 0.65) -> bool:
    item_url = normalize_text(item.get("url", "")); item_title = normalize_text(item.get("title", ""))
    for story in previous:
        story_url = normalize_text(story.get("url", ""))
        if item_url and story_url and item_url == story_url: return True
        item_tokens = _story_tokens(item_title); story_tokens = _story_tokens(story.get("title", ""))
        title_jaccard = len(item_tokens & story_tokens) / len(item_tokens | story_tokens) if item_tokens and story_tokens else 0.0
        combined_similarity = story_similarity(item, story); title_similarity = _title_similarity(item_title, story.get("title", ""))
        if title_jaccard >= threshold and title_similarity >= 0.72: return True
        if title_similarity >= 0.82 and combined_similarity >= 0.55: return True
        if (_numbers(item.get("title", "") + " " + item.get("summary", "")) & _numbers(story.get("title", "") + " " + story.get("summary", "")) and title_jaccard >= 0.45 and combined_similarity >= 0.55): return True
    return False

def is_new_item(item_id: str, url: str, seen: set[str]) -> bool:
    return item_id not in seen and url not in seen

NEWS_WINDOW_MINUTES = int(os.environ.get("NEWS_WINDOW_MINUTES", "90"))
_FUTURE_TOLERANCE = timedelta(minutes=5)

def is_recent_news(published_at: datetime | None, now: datetime | None = None, window_minutes: int | None = None) -> bool:
    if published_at is None: return False
    if window_minutes is None: window_minutes = NEWS_WINDOW_MINUTES
    now = now or datetime.now(timezone.utc)
    if published_at.tzinfo is None: published_at = published_at.replace(tzinfo=timezone.utc)
    age = now.astimezone(timezone.utc) - published_at.astimezone(timezone.utc)
    return -_FUTURE_TOLERANCE <= age <= timedelta(minutes=window_minutes)

_TECHNOLOGY_CATEGORIES = {"technology", "tech", "artificial intelligence", "ai", "cybersecurity", "cyber security", "software", "hardware", "gadgets", "mobile", "cloud", "semiconductors", "consumer technology"}
# Strong signals describe a technology subject. Generic words such as
# "tech", "startup", "algorithm" and brand names are weak alone.
_STRONG_TECH_SIGNALS = (
    "artificial intelligence", "machine learning", "generative ai", "ai model", "ai models", "chatbot", "chatgpt", "openai", "anthropic", "gemini", "copilot", "nvidia", "semiconductor", "semiconductors", "microchip", "microchips", "processor", "processors", "gpu", "gpus", "software", "cybersecurity", "cyber security", "malware", "ransomware", "hacker", "hackers", "smartphone", "smartphones", "iphone", "android", "robotics", "robot", "robots", "quantum computing", "data center", "data centers", "cloud computing", "operating system", "browser", "app store", "silicon valley", "هوش مصنوعی", "فناوری", "تکنولوژی", "نرم‌افزار", "سخت‌افزار", "تراشه", "پردازنده", "امنیت سایبری", "گوشی هوشمند", "ربات",
)
_WEAK_TECH_SIGNALS = ("technology", "technologies", "tech", "ai", "startup", "algorithm", "algorithms", "apple", "tesla", "meta", "amazon", "google", "microsoft", "samsung", "chip", "chips")
_NON_TECH_SPORTS_SIGNALS = ("football", "soccer", "basketball", "baseball", "cricket", "tennis", "golf", "rugby", "concacaf", "nations league", "match", "matches", "head-to-head", "league standings", "goal", "goals", "player statistics", "sports", "tournament", "fixture", "fixtures")

def _signal_pattern(signals):
    alternatives = sorted((re.escape(s) for s in signals), key=len, reverse=True)
    return re.compile(r"(?<!\w)(?:" + "|".join(alternatives) + r")(?!\w)")
_STRONG_TECH_RE = _signal_pattern(_STRONG_TECH_SIGNALS)
_WEAK_TECH_RE = _signal_pattern(_WEAK_TECH_SIGNALS)
_SPORTS_RE = _signal_pattern(_NON_TECH_SPORTS_SIGNALS)

def is_technology_news(category: str) -> bool:
    return normalize_text(category).lower() in _TECHNOLOGY_CATEGORIES

def is_technology_story(category: str, title: str = "", summary: str = "", article: str = "") -> bool:
    # Source text is authoritative; generated category is only a prior.
    source_text = normalize_text(f"{title} {summary}").lower()
    article_text = normalize_text(article).lower()
    text = normalize_text(f"{source_text} {article_text}").lower()
    if not text: return False
    sports_hits = len(set(_SPORTS_RE.findall(source_text)))
    strong_source = len(set(_STRONG_TECH_RE.findall(source_text)))
    weak_source = len(set(_WEAK_TECH_RE.findall(source_text)))
    strong_all = len(set(_STRONG_TECH_RE.findall(text)))
    weak_all = len(set(_WEAK_TECH_RE.findall(text)))
    if sports_hits >= 2 and strong_source < 2: return False
    # A single generic word or a brand is never enough. Require either two
    # strong source signals, or one strong signal plus a second independent
    # weak/strong signal. Article text can corroborate, but cannot rescue a
    # generic headline with hallucinated/generated content alone.
    source_evidence = strong_source >= 2 or (strong_source >= 1 and weak_source >= 1)
    article_evidence = strong_all >= 2 or (strong_all >= 1 and weak_all >= 2)
    return source_evidence or (is_technology_news(category) and article_evidence)

def build_telegram_message(title, summary, category, source, url=None):
    return f"📰 <b>{escape(normalize_text(title))}</b>\n\n{escape(normalize_text(summary))}\n\n🏷 {escape(normalize_text(category))}\n📡 منبع: {escape(normalize_text(source))}"

def build_rich_message_html(title, summary, article, source):
    return (f"<b>📰 {escape(normalize_text(title))}</b>\n\n{escape(normalize_text(summary))}\n\n" "<details><summary>&nbsp;&nbsp;✨ مشاهده متن کامل خبر ✨&nbsp;&nbsp;</summary>" f"<p>{escape(normalize_text(article))}</p>" "</details>\n\n" f"📡 منبع: {escape(normalize_text(source))}<br>\n" f"{CHANNEL_FOOTER}")

def build_expanded_message(title, article, source):
    return build_rich_message_html(title, "", article, source).replace("\n\n</details>", "</details>")
