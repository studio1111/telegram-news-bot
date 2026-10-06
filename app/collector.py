from dataclasses import dataclass
from hashlib import sha256
import feedparser
from .core import normalize_text

@dataclass(frozen=True)
class NewsItem:
    item_id: str
    title: str
    url: str
    summary: str
    source: str

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
        items.append(NewsItem(sha256(stable.encode()).hexdigest(), title, link, summary, source_name))
    return items
