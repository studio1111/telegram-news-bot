from datetime import datetime, timedelta, timezone
from functools import lru_cache
from html import escape
import os
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TOKEN_ALIASES = {
    "raises": "raise", "raised": "raise", "raising": "raise",
    "secures": "secure", "secured": "secure", "securing": "secure",
    "funding": "fund", "financing": "fund", "investment": "fund", "investments": "fund",
    "unveils": "launch", "unveiled": "launch", "unveiling": "launch",
    "introduces": "launch", "introduced": "launch", "introducing": "launch",
    "launches": "launch", "launched": "launch", "launching": "launch",
    "reveals": "launch", "revealed": "launch", "reveal": "launch",
    "chips": "chip", "processors": "chip", "processor": "chip",
    "announces": "announce", "announced": "announce", "announcing": "announce",
    "detects": "detect", "detected": "detect", "detection": "detect", "detector": "detect",
    "identifies": "detect", "identify": "detect", "identified": "detect", "identifying": "detect",
    "verification": "detect", "verify": "detect", "verified": "detect",
    "checks": "check", "checked": "check", "checking": "check",
    "generated": "generate", "generates": "generate", "generation": "generate",
    "created": "create", "creating": "create", "creates": "create",
    "produced": "create", "produces": "create", "producing": "create",
    "detecting": "detect", "identify": "detect", "identified": "detect",
    "websites": "website", "site": "website", "sites": "website", "portal": "website",
    "شناسایی": "detect", "تشخیص": "detect", "شناسایی‌کردن": "detect", "تشخیص‌دادن": "detect",
    "عرضه": "launch", "عرضه‌شده": "launch", "منتشر": "launch", "منتشرشد": "launch",
    "انتشار": "launch", "راه‌اندازی": "launch", "راهاندازی": "launch", "معرفی": "launch",
    "ارائه": "launch", "ارائه‌شده": "launch", "خبر": "announce",
}

_EVENT_TEXT_ALIASES = {
    "synth id": "synthid",
    "synth-id": "synthid",
    "synthid detector": "synthid",
    "synthid-detector": "synthid",
    "سینث آی دی": "synthid",
    "سینث‌آی‌دی": "synthid",
    "سینت اید": "synthid",
    "سینت‌اید": "synthid",
    "سینث آی‌دی": "synthid",
    "سینث‌آی دی": "synthid",
}


def _canonicalize_event_text(value: str) -> str:
    text = normalize_text(value).lower()
    for source, target in sorted(_EVENT_TEXT_ALIASES.items(), key=lambda pair: len(pair[0]), reverse=True):
        text = text.replace(source, target)
    return text

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

# The token helpers are cached: every candidate is compared with hundreds of
# history stories, so the same texts were being re-tokenized thousands of times
# per run. Results are frozensets so cached values can never be mutated.
@lru_cache(maxsize=16384)
def _story_tokens(value: str) -> frozenset[str]:
    text = _canonicalize_event_text(value).replace("$", " ").replace(",", "")
    text = re.sub(r"[’']s\b", "", text)
    text = text.replace("۲۰۰", "200")
    text = re.sub(r"\b(million|millions)\b", "million", text)
    text = re.sub(r"[^\w\u0600-\u06ff]+", " ", text)
    return frozenset({
        _TOKEN_ALIASES.get(t, t)
        for t in text.split()
        if len(t) > 2 and t not in _STOP_WORDS
    })

@lru_cache(maxsize=16384)
def _numbers(value: str) -> frozenset[str]:
    return frozenset(re.findall(r"\d+(?:\.\d+)?", normalize_text(value).replace(",", "")))

def _story_variants(record: dict) -> list[tuple[str, str]]:
    variants = [(record.get("title", ""), record.get("summary", ""))]
    display_title = record.get("display_title", "")
    display_summary = record.get("display_summary", "")
    if display_title or display_summary:
        variants.append((display_title, display_summary))
    return [
        (normalize_text(title), normalize_text(summary))
        for title, summary in variants
        if normalize_text(title) or normalize_text(summary)
    ]


@lru_cache(maxsize=16384)
def _named_entities(value: str) -> frozenset[str]:
    entities = set()
    for raw in re.findall(r"\(([^()]{1,100})\)", value or ""):
        normalized = re.sub(r"[^\w]+", " ", raw.lower(), flags=re.UNICODE).strip()
        if not normalized:
            continue
        parts = normalized.split()
        if parts:
            entities.add(parts[0])
        if len(parts) <= 4:
            entities.add(normalized.replace(" ", ""))
    return frozenset(entities)


def _pair_similarity(left_title: str, left_summary: str, right_title: str, right_summary: str) -> bool:
    left_full = f"{left_title} {left_summary}"
    right_full = f"{right_title} {right_summary}"
    left_tokens = _story_tokens(left_full)
    right_tokens = _story_tokens(right_full)
    if not left_tokens or not right_tokens:
        return False

    title_tokens_left = _story_tokens(left_title)
    title_tokens_right = _story_tokens(right_title)
    overlap, overlap_coefficient = _title_overlap(title_tokens_left, title_tokens_right)
    title_jaccard = (
        len(title_tokens_left & title_tokens_right) / len(title_tokens_left | title_tokens_right)
        if title_tokens_left and title_tokens_right else 0.0
    )
    combined_similarity = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    shared_numbers = _numbers(left_full) & _numbers(right_full)
    shared_anchors = _event_anchor_tokens(left_full) & _event_anchor_tokens(right_full)
    shared_support = (left_tokens & right_tokens) - shared_anchors
    shared_actions = _event_action_tokens(left_full) & _event_action_tokens(right_full)
    significant_numbers = {
        number for number in shared_numbers
        if not (len(number.split(".")[0]) == 4 and number.split(".")[0].isdigit()
                and 1900 <= int(number.split(".")[0]) <= 2100)
    }

    # SequenceMatcher is the slowest check, so it only runs when the cheap
    # token conditions already hold (same result as computing it up front).
    if title_jaccard >= 0.65 and _title_similarity(left_title, right_title) >= 0.72:
        return True
    if combined_similarity >= 0.55 and _title_similarity(left_title, right_title) >= 0.82:
        return True
    if overlap >= 3 and overlap_coefficient >= 0.60 and combined_similarity >= 0.40:
        return True
    if significant_numbers and overlap >= 2 and overlap_coefficient >= 0.40 and combined_similarity >= 0.38:
        return True
    if overlap >= 2 and overlap_coefficient >= 0.50 and combined_similarity >= 0.50:
        return True

    # Cross-source paraphrases can have very different titles while still
    # describing the same event. Require two distinctive anchors, supporting
    # context, and a strong shared action. Generic launch/announcement wording
    # alone is not enough, which protects same-company different-event stories.
    strong_actions = shared_actions & {
        "detect", "check", "fund", "secure", "raise", "acquire", "partner",
        "restrict", "reduce", "increase", "create", "watermark", "ban",
        "block", "buy", "sell",
    }
    if len(shared_anchors) >= 2 and strong_actions:
        return True
    if len(shared_anchors) >= 2 and len(shared_support) >= 2 and shared_actions:
        return True

    # High-confidence product/event anchors handle major cross-source rewrites
    # where the titles share little surface wording. SynthID has several Persian
    # spellings, all canonicalized above to the same event key.
    if "synthid" in shared_anchors and "google" in shared_anchors and "detect" in shared_actions:
        return True

    shared_entities = _named_entities(left_full) & _named_entities(right_full)
    if len(shared_entities) >= 2:
        return True

    return False


def story_similarity(left: dict, right: dict) -> float:
    left_tokens = _story_tokens(" ".join(
        f"{title} {summary}" for title, summary in _story_variants(left)
    ))
    right_tokens = _story_tokens(" ".join(
        f"{title} {summary}" for title, summary in _story_variants(right)
    ))
    if not left_tokens or not right_tokens:
        return 0.0
    jaccard = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    numbers_match = bool(
        _numbers(" ".join(f"{title} {summary}" for title, summary in _story_variants(left)))
        & _numbers(" ".join(f"{title} {summary}" for title, summary in _story_variants(right)))
    )
    return min(1.0, jaccard + (0.20 if numbers_match else 0.0))


def _title_similarity(left: str, right: str) -> float:
    from difflib import SequenceMatcher
    return SequenceMatcher(None, normalize_text(left).lower(), normalize_text(right).lower()).ratio()


_TRACKING_QUERY_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "gclid", "fbclid", "mc_cid", "mc_eid",
}


def _canonical_story_url(value: str) -> str:
    raw = normalize_text(value)
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw.rstrip("/")
    if not parts.scheme or not parts.netloc:
        return raw.rstrip("/")
    query = [(key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
             if key.lower() not in _TRACKING_QUERY_KEYS]
    hostname = (parts.hostname or "").lower()
    netloc = hostname
    if parts.port and parts.port not in {80, 443}:
        netloc = f"{hostname}:{parts.port}"
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), netloc, path, urlencode(query), ""))



_EVENT_GENERIC_TERMS = {
    "company", "companies", "story", "stories", "news", "report", "reports",
    "says", "said", "new", "latest", "today", "now", "available", "launch",
    "announce", "release", "released", "product", "products", "service",
    "services", "system", "systems", "tool", "tools", "model", "models",
    "technology", "technologies", "tech", "software", "hardware", "device",
    "devices", "content", "media", "website", "ai", "artificial", "intelligence",
    "people", "users", "user", "using", "use", "uses", "can", "lets", "let",
    "هوش", "مصنوعی", "رسانه", "محتوا", "ابزار", "جدید", "شرکت", "وبسایت", "وب‌سایت",
}

def _event_anchor_tokens(value: str) -> set[str]:
    return {
        token for token in _story_tokens(value)
        if len(token) >= 5 and token not in _EVENT_GENERIC_TERMS
    }

def _event_action_tokens(value: str) -> set[str]:
    return _story_tokens(value) & {
        "launch", "announce", "detect", "check", "fund", "secure", "raise",
        "acquire", "partner", "restrict", "reduce", "increase", "create",
        "watermark", "expand", "ban", "block", "buy", "sell",
    }

def _title_overlap(left: set[str], right: set[str]) -> tuple[int, float]:
    if not left or not right:
        return 0, 0.0
    overlap = len(left & right)
    return overlap, overlap / min(len(left), len(right))


def is_duplicate_story(item: dict, previous: list[dict], threshold: float = 0.65) -> bool:
    item_url = _canonical_story_url(item.get("url", ""))
    for story in previous:
        story_url = _canonical_story_url(story.get("url", ""))
        if item_url and story_url and item_url == story_url:
            return True
        for item_title, item_summary in _story_variants(item):
            for story_title, story_summary in _story_variants(story):
                if _pair_similarity(item_title, item_summary, story_title, story_summary):
                    return True
    return False

def is_new_item(item_id: str, url: str, seen: set[str]) -> bool:
    return item_id not in seen and url not in seen

# GitHub Actions can delay a scheduled run, so the workflow sets a wide window
# (NEWS_WINDOW_MINUTES). "seen" and the duplicate filter prevent repeats; the
# window only stops a fresh/empty state from replaying days of old news.
NEWS_WINDOW_MINUTES = int(os.environ.get("NEWS_WINDOW_MINUTES", "90"))
_FUTURE_TOLERANCE = timedelta(minutes=5)

def is_recent_news(published_at: datetime | None, now: datetime | None = None, window_minutes: int | None = None) -> bool:
    if published_at is None:
        return False
    if window_minutes is None:
        window_minutes = NEWS_WINDOW_MINUTES
    now = now or datetime.now(timezone.utc)
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    published_at = published_at.astimezone(timezone.utc)
    now = now.astimezone(timezone.utc)
    age = now - published_at
    return -_FUTURE_TOLERANCE <= age <= timedelta(minutes=window_minutes)

_TECHNOLOGY_CATEGORIES = {"technology", "technologies", "tech", "artificial intelligence", "ai", "cybersecurity", "cyber security", "software", "hardware", "gadgets", "mobile", "cloud", "semiconductors", "consumer technology"}



_AD_CATEGORY_MARKERS = {
    "sponsored", "advertorial", "advertisement", "paid post",
    "partner content", "promoted content", "sponsor content", "sponsored content",
    "تبلیغ", "تبلیغاتی", "محتوای تبلیغاتی", "محتوای اسپانسری", "اسپانسر",
}
_AD_TITLE_PREFIX = re.compile(
    r"^(?:sponsored|advertorial|advertisement|paid\\s+post|partner\\s+content|"
    r"promoted\\s+content|sponsor(?:ed)?\\s+content|"
    r"تبلیغ|محتوای\\s+تبلیغاتی|محتوای\\s+اسپانسری|اسپانسر)\\b\\s*[:|\\-]",
    re.IGNORECASE,
)


def is_advertisement(title: str = "", summary: str = "", categories=()) -> bool:
    """Reject explicit sponsored/promotional items without filtering ordinary ad news."""
    category_values = {normalize_text(value).lower() for value in (categories or ())}
    if category_values & _AD_CATEGORY_MARKERS:
        return True
    return bool(_AD_TITLE_PREFIX.search(normalize_text(title)))


def is_technology_news(category: str) -> bool:
    """Return True only for an explicit technology category/tag."""
    normalized = normalize_text(category).lower()
    if normalized in {"فناوری", "تکنولوژی", "علم و فناوری", "هوش مصنوعی", "امنیت سایبری", "گجت"}:
        return True
    if not normalized:
        return False
    if normalized in _TECHNOLOGY_CATEGORIES:
        return True
    return bool(re.search(
        r"(?<!\w)(?:technology|technologies|tech|artificial intelligence|ai|software|hardware|gadgets|cybersecurity|cyber security|semiconductor|semiconductors|mobile|cloud)(?!\w)",
        normalized,
    ))


def is_technology_feed_item(categories, source: str = "") -> bool:
    """Require an explicit technology category/tag on the RSS item itself."""
    return any(is_technology_news(category) for category in (categories or ()))


def is_technology_story(category: str, title: str = "", summary: str = "", article: str = "") -> bool:
    """Compatibility helper: technology classification comes only from the tag/category."""
    return is_technology_news(category)

def build_telegram_message(title, summary, category, source, url=None):
    return f"📰 <b>{escape(normalize_text(title))}</b>\n\n{escape(normalize_text(summary))}\n\n🏷 {escape(normalize_text(category))}\n📡 منبع: {escape(normalize_text(source))}"

def build_rich_message_html(title, summary, article, source):
    clean_title = escape(normalize_text(title))
    clean_summary = escape(normalize_text(summary))
    clean_article = escape(normalize_text(article))
    clean_source = escape(normalize_text(source))
    return (f"<b>📰 {clean_title}</b>\n\n{clean_summary}\n\n" "<details><summary>&nbsp;&nbsp;✨ مشاهده متن کامل خبر ✨&nbsp;&nbsp;</summary>" f"<p>{clean_article}</p>" "</details>\n\n" f"📡 منبع: {clean_source}<br>\n" f"{CHANNEL_FOOTER}")

def build_expanded_message(title, article, source):
    return build_rich_message_html(title, "", article, source).replace("\n\n</details>", "</details>")
