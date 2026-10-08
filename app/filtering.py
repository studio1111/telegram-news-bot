"""Final technology gate: the RSS item itself must carry a technology tag/category."""
from .core import is_technology_feed_item


def has_technology_tag(categories) -> bool:
    return is_technology_feed_item(categories)


def is_publishable_technology(categories, source: str = "", ai_category: str = "") -> bool:
    """Never use source name or Gemini classification as a technology exception."""
    return is_technology_feed_item(categories, source)
