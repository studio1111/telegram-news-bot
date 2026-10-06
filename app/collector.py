from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import calendar
import re

import feedparser
import requests

from .core import normalize_text


@dataclass(frozen=True)
class NewsItem:
    item_id: str
    title: str
    url: str
    summary: str
    source: str
    image_url: str = ""
    published_at: datetime | None = None


class _ImageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.images = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "img":
            return
        attrs = dict(attrs)
        src = attrs.get("src") or attrs.get("data-src") or ""
        if src.startswith(("http://", "https://")):
            self.images.append(src)


def _article_image_url(html: str) -> str:
    patterns = (
        r'(?is)<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
        r'(?is)<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'(?is)<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)',
        r'(?is)<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']',
    )
    for pattern in patterns:
        match = re.search(pattern, html or "")
        if match:
            url = match.group(1).strip()
            if url.startswith(("http://", "https://")):
                return url
    return ""


def _image_url(entry) -> str:
    for key in ("media_content", "media_thumbnail"):
        for media in entry.get(key, []) or []:
            url = media.get("url", "")
            if url.startswith(("http://", "https://")):
                return url

    for enclosure in entry.get("enclosures", []) or []:
        url = enclosure.get("href") or enclosure.get("url") or ""
        if url.startswith(("http://", "https://")) and str(enclosure.get("type", "")).startswith("image/"):
            return url

    for key in ("summary", "description"):
        parser = _ImageParser()
        parser.feed(entry.get(key, "") or "")
        if parser.images:
            return parser.images[0]

    for content in entry.get("content", []) or []:
        parser = _ImageParser()
        parser.feed(content.get("value", "") or "")
        if parser.images:
            return parser.images[0]
        image = _article_image_url(content.get("value", "") or "")
        if image:
            return image

    return _article_image_url(entry.get("summary", "") or "")


def _entry_published_at(entry):
    value = entry.get("published_parsed") or entry.get("updated_parsed")
    if not value:
        return None
    try:
        return datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def fetch_article_text(url: str, max_chars: int = 18000) -> str:
    try:
        response = requests.get(
            url,
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0 (compatible; MyNewsTechnology/1.0)"},
        )
        response.raise_for_status()
    except requests.RequestException:
        return ""

    text = re.sub(r"(?is)<(script|style|noscript|svg).*?>.*?</\\1>", " ", response.text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return normalize_text(text)[:max_chars]


def fetch_article_image_url(url: str) -> str:
    try:
        response = requests.get(
            url,
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0 (compatible; MyNewsTechnology/1.0)"},
        )
        response.raise_for_status()
    except requests.RequestException:
        return ""
    return _article_image_url(response.text)


def collect_feed(url: str, source_name: str, limit: int = 100):
    parsed = feedparser.parse(url)
    items = []
    for entry in parsed.entries[:limit]:
        title = normalize_text(entry.get("title", ""))
        link = normalize_text(entry.get("link", ""))
        summary = normalize_text(entry.get("summary", ""))
        if not title or not link:
            continue
        stable = entry.get("id") or link
        items.append(
            NewsItem(
                sha256(stable.encode()).hexdigest(),
                title,
                link,
                summary,
                source_name,
                _image_url(entry),
                _entry_published_at(entry),
            )
        )
    return items
