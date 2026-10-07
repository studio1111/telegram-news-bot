from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import calendar
import re
import threading
import time

import feedparser
import requests

from .core import normalize_text
from .security import MAX_ARTICLE_BYTES, validate_public_url

_USER_AGENT = "Mozilla/5.0 (compatible; MyNewsTechnology/1.0)"
_HEADERS = {"User-Agent": _USER_AGENT, "Cache-Control": "no-cache, no-store, max-age=0", "Pragma": "no-cache"}

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
    def __init__(self): super().__init__(); self.images = []
    def handle_starttag(self, tag, attrs):
        if tag.lower() == "img":
            src = dict(attrs).get("src") or dict(attrs).get("data-src") or ""
            if src.startswith(("http://", "https://")): self.images.append(src)

def _article_image_url(html):
    patterns = (r'(?is)<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', r'(?is)<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', r'(?is)<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)')
    for pattern in patterns:
        match = re.search(pattern, html or "")
        if match and match.group(1).startswith(("http://", "https://")): return match.group(1).strip()
    return ""

def _image_url(entry):
    for key in ("media_content", "media_thumbnail"):
        for media in entry.get(key, []) or []:
            if (url := media.get("url", "")).startswith(("http://", "https://")): return url
    for enclosure in entry.get("enclosures", []) or []:
        url = enclosure.get("href") or enclosure.get("url") or ""
        if url.startswith(("http://", "https://")) and str(enclosure.get("type", "")).startswith("image/"): return url
    for value in [entry.get("summary", ""), entry.get("description", "")] + [c.get("value", "") for c in entry.get("content", []) or []]:
        parser = _ImageParser(); parser.feed(value or "")
        if parser.images: return parser.images[0]
        if image := _article_image_url(value): return image
    return ""

def _entry_published_at(entry):
    value = entry.get("published_parsed") or entry.get("updated_parsed")
    if not value: return None
    try: return datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError): return None

_ARTICLE_CACHE = {}; _ARTICLE_CACHE_LOCK = threading.Lock(); _ARTICLE_CACHE_LIMIT = 512

def _bounded_response_text(response):
    content_length = response.headers.get("Content-Length")
    if content_length and int(content_length) > MAX_ARTICLE_BYTES: raise ValueError("article response too large")
    chunks = []; total = 0
    for chunk in response.iter_content(65536):
        total += len(chunk)
        if total > MAX_ARTICLE_BYTES: raise ValueError("article response too large")
        chunks.append(chunk)
    return b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")

def _fetch_article_html(url):
    with _ARTICLE_CACHE_LOCK:
        if url in _ARTICLE_CACHE: return _ARTICLE_CACHE[url]
    try:
        validate_public_url(url)
        response = requests.get(url, params={"_": str(int(time.time()))}, timeout=20, headers=_HEADERS, stream=True, allow_redirects=False)
        response.raise_for_status()
        if response.headers.get("Content-Type", "").split(";", 1)[0].lower() not in {"", "text/html", "application/xhtml+xml"}: raise ValueError("article is not HTML")
        html = _bounded_response_text(response)
    except (requests.RequestException, ValueError): html = ""
    with _ARTICLE_CACHE_LOCK:
        if len(_ARTICLE_CACHE) >= _ARTICLE_CACHE_LIMIT: _ARTICLE_CACHE.clear()
        _ARTICLE_CACHE[url] = html
    return html

def fetch_article_text(url, max_chars=18000):
    html = _fetch_article_html(url)
    text = re.sub(r"(?is)<(script|style|noscript|svg).*?>.*?</\\1>", " ", html)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    return normalize_text(re.sub(r"\\s+", " ", text))[:max_chars] if html else ""

def fetch_article_image_url(url): return _article_image_url(_fetch_article_html(url))

def collect_feed(url, source_name, limit=100):
    try:
        response = requests.get(url, params={"_": str(int(time.time()))}, timeout=20, headers=_HEADERS, stream=True)
        response.raise_for_status()
        body = _bounded_response_text(response)
    except (requests.RequestException, ValueError) as exc:
        print(f"[FEED_ERROR] {source_name} url={url}: {exc}"); return []
    parsed = feedparser.parse(body.encode())
    items = []
    for entry in parsed.entries[:limit]:
        title, link, summary = normalize_text(entry.get("title", "")), normalize_text(entry.get("link", "")), normalize_text(entry.get("summary", ""))
        if not title or not link: continue
        stable = f"{source_name}:{entry.get('id') or link}"
        items.append(NewsItem(sha256(stable.encode()).hexdigest(), title, link, summary, source_name, _image_url(entry), _entry_published_at(entry)))
    print(f"[FEED_FETCH] {source_name}: status={response.status_code} entries={len(parsed.entries)} parsed_items={len(items)}")
    return items
