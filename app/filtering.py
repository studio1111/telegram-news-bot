"""Final technology gate, applied after Gemini has classified a story.

Only stories about technology / IT / AI / software / hardware are published.
"""
from .core import is_technology_news, normalize_text

# Feeds that are technology-only by construction. Mixed feeds (WIRED, The Verge,
# Engadget, Ars Technica) also carry politics, culture and shopping deals, so an
# item from them must carry a technology tag or be classified as technology.
TRUSTED_TECHNOLOGY_SOURCES = {
    "TechCrunch",
    "BBC Technology",
    "The Guardian Technology",
    "Digiato",
    "Vigiato",
}


def has_technology_tag(categories) -> bool:
    return any(is_technology_news(category) for category in (categories or ()))


def is_publishable_technology(categories, source: str, ai_category) -> bool:
    """True when the story is technology news.

    A story passes when Gemini classified it as technology, when the feed itself
    tagged it as technology, or when it comes from a technology-only feed.
    """
    if normalize_text(str(ai_category or "")).lower() == "technology":
        return True
    if has_technology_tag(categories):
        return True
    return normalize_text(source) in TRUSTED_TECHNOLOGY_SOURCES
