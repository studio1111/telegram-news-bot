import app.collector as collector
from app.collector import NewsItem
from datetime import datetime, timezone
import app.main as main


def test_hydrate_missing_images_drops_stories_without_a_recoverable_image(monkeypatch):
    item_with_image = NewsItem("1", "Has image", "https://example.com/1", "summary", "TechCrunch", "")
    item_without_image = NewsItem("2", "No image", "https://example.com/2", "summary", "TechCrunch", "")

    def fake_fetch(url):
        return "https://cdn.example.com/story.jpg" if url.endswith("/1") else ""

    monkeypatch.setattr(main, "resolve_article_image_url", lambda url, preferred="": fake_fetch(url))
    result = main._hydrate_missing_images([item_with_image, item_without_image], main.time.monotonic() + 5)

    assert [item.url for item in result] == ["https://example.com/1"]
    assert result[0].image_url == "https://cdn.example.com/story.jpg"


def test_collect_feed_with_no_limit_does_not_truncate_entries(monkeypatch):
    class Response:
        status_code = 200

    entries = []
    for index in range(125):
        entries.append({
            "id": f"id-{index}",
            "title": f"Story {index}",
            "link": f"https://example.com/{index}",
            "summary": "summary",
            "published_parsed": (2026, 10, 8, 12, 0, 0, 3, 281, 0),
        })

    monkeypatch.setattr(collector, "_safe_get", lambda url: Response())
    monkeypatch.setattr(collector, "_bounded_response", lambda response: b"unused")

    class Parsed:
        pass

    parsed = Parsed()
    parsed.entries = entries
    monkeypatch.setattr(collector.feedparser, "parse", lambda payload: parsed)

    result = collector.collect_feed("https://example.com/feed.xml", "TechCrunch", None)

    assert len(result) == 125
