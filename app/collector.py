from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import calendar
import ipaddress
import json
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
        attrs = dict(attrs)
        tag = tag.lower()
        if tag == "img":
            values = [
                attrs.get("src", ""),
                attrs.get("data-src", ""),
                attrs.get("data-lazy-src", ""),
                attrs.get("data-original", ""),
            ]
            srcset = attrs.get("srcset") or attrs.get("data-srcset") or ""
            values.extend(part.strip().split()[0] for part in srcset.split(",") if part.strip())
            for value in values:
                if value.startswith(("http://", "https://")):
                    self.images.append(value)
        elif tag == "link" and "image" in (attrs.get("rel", "") or "").lower():
            href = attrs.get("href", "")
            if href.startswith(("http://", "https://")):
                self.images.append(href)


def _article_image_urls(html: str) -> list[str]:
    found = []

    def add(value):
        if isinstance(value, str) and value.startswith(("http://", "https://")) and value not in found:
            found.append(value)

    patterns = (
        r'(?is)<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
        r'(?is)<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'(?is)<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)',
        r'(?is)<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']',
    )
    for pattern in patterns:
        for match in re.finditer(pattern, html or ""):
            add(match.group(1).strip())

    scripts = re.findall(r'(?is)<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html or "")
    for script in scripts:
        try:
            data = json.loads(script)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                for key in ("image", "thumbnailUrl", "contentUrl"):
                    image = value.get(key)
                    if isinstance(image, str):
                        add(image)
                    elif isinstance(image, list):
                        for entry in image:
                            if isinstance(entry, str):
                                add(entry)
                            elif isinstance(entry, dict):
                                add(entry.get("url") or entry.get("contentUrl"))
                    elif isinstance(image, dict):
                        add(image.get("url") or image.get("contentUrl"))
                stack.extend(v for v in value.values() if isinstance(v, (dict, list)))
            elif isinstance(value, list):
                stack.extend(value)

    parser = _ImageParser()
    try:
        parser.feed(html or "")
    except Exception:
        pass
    for image in parser.images:
        add(image)

    return found


def _article_image_url(html: str) -> str:
    urls = _article_image_urls(html)
    return urls[0] if urls else ""


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


def validate_image_url(url: str, timeout: int = 8) -> bool:
    if not url:
        return False
    try:
        _validate_url(url)
        response = requests.get(
            url,
            timeout=timeout,
            headers=_NO_CACHE_HEADERS,
            allow_redirects=True,
            stream=True,
        )
        response.raise_for_status()
        return (response.headers.get("Content-Type") or "").lower().startswith("image/")
    except (requests.RequestException, ValueError):
        return False


def resolve_article_image_url(article_url: str, preferred_image_url: str = "") -> str:
    _validate_url(article_url)
    if preferred_image_url and validate_image_url(preferred_image_url):
        return preferred_image_url
    html = _fetch_article_html(article_url)
    for candidate in _article_image_urls(html):
        if validate_image_url(candidate):
            return candidate
    return ""


def collect_feed(url: str, source_name: str, limit: int | None = None):
    try:
        response = _safe_get(url)
        parsed = feedparser.parse(_bounded_response(response))
    except (requests.RequestException, ValueError) as exc:
        print(f"[FEED_ERROR] {source_name} url={url}: {exc}")
        return []
    items=[]
    entries = parsed.entries if limit is None or limit <= 0 else parsed.entries[:limit]
    for entry in entries:
        raw_categories = entry.get("tags", []) or []
        categories = tuple(normalize_text(tag.get("term") or tag.get("label") or "") for tag in raw_categories if normalize_text(tag.get("term") or tag.get("label") or ""))
        title=normalize_text(entry.get("title", "")); link=normalize_text(entry.get("link", "")); summary=normalize_text(entry.get("summary", ""))
        if not title or not link: continue
        try: _validate_url(link)
        except ValueError: continue
        stable=entry.get("id") or link
        items.append(NewsItem(sha256(stable.encode()).hexdigest(),title,link,summary,source_name,_image_url(entry),_entry_published_at(entry),categories))
    print(f"[FEED_FETCH] {source_name}: status={response.status_code} entries={len(parsed.entries)} parsed_items={len(items)} limit={limit or "unlimited"}")
    for sample in items[:3]: print(f"[FEED_ITEM] {source_name}: published={sample.published_at} title={sample.title[:100]}")
    return items
