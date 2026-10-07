from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import calendar
import ipaddress
import re
import socket
import threading
import time
from urllib.parse import urljoin, urlparse

import feedparser
import requests

from .core import normalize_text


_USER_AGENT = "Mozilla/5.0 (compatible; MyNewsTechnology/1.0)"
_NO_CACHE_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Cache-Control": "no-cache, no-store, max-age=0",
    "Pragma": "no-cache",
}
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 4
ALLOWED_SCHEMES = {"http", "https"}


@dataclass(frozen=True)
class NewsItem:
    item_id: str
    title: str
    url: str
    summary: str
    source: str
    image_url: str = ""
    published_at: datetime | None = None
    categories: tuple[str, ...] = ()


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
        r'(?is)<meta[^>]+property=["\\\']og:image["\\\'][^>]+content=["\\\']([^"\\\']+)',
        r'(?is)<meta[^>]+content=["\\\']([^"\\\']+)["\\\'][^>]+property=["\\\']og:image["\\\']',
        r'(?is)<meta[^>]+name=["\\\']twitter:image["\\\'][^>]+content=["\\\']([^"\\\']+)',
        r'(?is)<meta[^>]+content=["\\\']([^"\\\']+)["\\\'][^>]+name=["\\\']twitter:image["\\\']',
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
        parser = _ImageParser(); parser.feed(entry.get(key, "") or "")
        if parser.images:
            return parser.images[0]
    for content in entry.get("content", []) or []:
        parser = _ImageParser(); parser.feed(content.get("value", "") or "")
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


def _is_public_host(hostname: str) -> bool:
    if not hostname:
        return False
    previous_timeout = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(3)
        addresses = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, socket.timeout, TimeoutError) as exc:
        raise ValueError(f"unable to resolve URL host: {hostname}") from exc
    finally:
        socket.setdefaulttimeout(previous_timeout)
    for entry in addresses:
        address = ipaddress.ip_address(entry[4][0])
        if not address.is_global:
            return False
    return True


def _validate_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES or parsed.username or parsed.password:
        raise ValueError("unsafe URL scheme or credentials")
    if not parsed.hostname or parsed.port not in (None, 80, 443):
        raise ValueError("unsafe URL host or port")
    hostname = parsed.hostname.rstrip(".")
    if hostname.lower() in {"localhost", "localhost.localdomain"}:
        raise ValueError("local URL is not allowed")
    try:
        if ipaddress.ip_address(hostname).is_global is False:
            raise ValueError("private or non-global IP is not allowed")
    except ValueError as exc:
        if str(exc) == "private or non-global IP is not allowed":
            raise
        _is_public_host(hostname)
    return url


def _bounded_response(response) -> bytes:
    length = response.headers.get("Content-Length") if hasattr(response, "headers") else None
    if length and int(length) > MAX_RESPONSE_BYTES:
        raise ValueError("response too large")
    if hasattr(response, "iter_content"):
        chunks=[]; total=0
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk: continue
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise ValueError("response too large")
            chunks.append(chunk)
        return b"".join(chunks)
    if hasattr(response, "content"):
        data = response.content
    else:
        data = str(getattr(response, "text", "")).encode("utf-8")
    if len(data) > MAX_RESPONSE_BYTES:
        raise ValueError("response too large")
    return data


def _safe_get(url: str, timeout: int = 20):
    current = _validate_url(url)
    for _ in range(MAX_REDIRECTS + 1):
        response = requests.get(current, params={"_": str(int(time.time()))}, timeout=timeout,
                                headers=_NO_CACHE_HEADERS, allow_redirects=False, stream=True)
        status_code = getattr(response, "status_code", 200)
        headers = getattr(response, "headers", {}) or {}
        if 300 <= status_code < 400:
            location = headers.get("Location")
            if not location:
                raise ValueError("redirect without location")
            current = _validate_url(urljoin(current, location))
            continue
        response.raise_for_status()
        return response
    raise ValueError("too many redirects")


_ARTICLE_CACHE: dict[str, str] = {}
_ARTICLE_CACHE_LOCK = threading.Lock()
_ARTICLE_CACHE_LIMIT = 512


def _fetch_article_html(url: str) -> str:
    with _ARTICLE_CACHE_LOCK:
        if url in _ARTICLE_CACHE:
            return _ARTICLE_CACHE[url]
    try:
        response = _safe_get(url, timeout=10)
        data = _bounded_response(response)
        encoding = getattr(response, "encoding", None) or "utf-8"
        html = data.decode(encoding, errors="replace")
    except (requests.RequestException, UnicodeError):
        html = ""
    with _ARTICLE_CACHE_LOCK:
        if len(_ARTICLE_CACHE) >= _ARTICLE_CACHE_LIMIT:
            _ARTICLE_CACHE.clear()
        _ARTICLE_CACHE[url] = html
    return html


def fetch_article_text(url: str, max_chars: int = 18000) -> str:
    _validate_url(url)
    html = _fetch_article_html(url)
    if not html:
        return ""
    text = re.sub(r"(?is)<(script|style|noscript|svg).*?>.*?</\\1>", " ", html)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = re.sub(r"\\s+", " ", text)
    return normalize_text(text)[:max_chars]


def fetch_article_image_url(url: str) -> str:
    _validate_url(url)
    return _article_image_url(_fetch_article_html(url))


def collect_feed(url: str, source_name: str, limit: int = 100):
    try:
        response = _safe_get(url)
        parsed = feedparser.parse(_bounded_response(response))
    except (requests.RequestException, ValueError) as exc:
        print(f"[FEED_ERROR] {source_name} url={url}: {exc}")
        return []
    items=[]
    for entry in parsed.entries[:limit]:
        raw_categories = entry.get("tags", []) or []
        categories = tuple(normalize_text(tag.get("term") or tag.get("label") or "") for tag in raw_categories if normalize_text(tag.get("term") or tag.get("label") or ""))
        title=normalize_text(entry.get("title", "")); link=normalize_text(entry.get("link", "")); summary=normalize_text(entry.get("summary", ""))
        if not title or not link: continue
        try: _validate_url(link)
        except ValueError: continue
        stable=entry.get("id") or link
        items.append(NewsItem(sha256(stable.encode()).hexdigest(),title,link,summary,source_name,_image_url(entry),_entry_published_at(entry),categories))
    print(f"[FEED_FETCH] {source_name}: status={response.status_code} entries={len(parsed.entries)} parsed_items={len(items)}")
    for sample in items[:3]: print(f"[FEED_ITEM] {source_name}: published={sample.published_at} title={sample.title[:100]}")
    return items
