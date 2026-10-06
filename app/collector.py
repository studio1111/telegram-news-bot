from dataclasses import dataclass
from hashlib import sha256
from html.parser import HTMLParser
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

    parser = _ImageParser()
    parser.feed(entry.get("summary", ""))
    return parser.images[0] if parser.images else ""


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

    parser = HTMLParser()
    # Strip script/style tags and collect visible text without adding a parser dependency.
    text = re.sub(r"(?is)<(script|style|noscript|svg).*?>.*?</\1>", " ", response.text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return normalize_text(text)[:max_chars]


def collect_feed(url: str, source_name: str, limit: int = 10):
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
            )
        )
    return items
