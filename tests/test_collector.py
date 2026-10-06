from app.collector import _image_url, _article_image_url, collect_feed


def test_image_url_reads_og_image_from_entry_content():
    entry = {
        "summary": '<p>خبر</p>',
        "content": [{"value": '<meta property="og:image" content="https://example.com/hero.jpg">'}],
    }
    assert _image_url(entry) == "https://example.com/hero.jpg"


def test_article_image_url_reads_open_graph_image():
    html = '<html><head><meta property="og:image" content="https://example.com/hero.webp"></head></html>'
    assert _article_image_url(html) == "https://example.com/hero.webp"


def test_collect_feed_preserves_rss_publication_timestamp(monkeypatch):
    class Parsed:
        entries = [
            {
                "id": "news-1",
                "title": "خبر فناوری",
                "link": "https://example.com/news-1",
                "summary": "خلاصه",
                "published_parsed": (2026, 10, 6, 18, 0, 0, 0, 279, 0),
            }
        ]

    monkeypatch.setattr("app.collector.feedparser.parse", lambda _: Parsed())
    items = collect_feed("https://example.com/feed", "Example", 10)

    assert len(items) == 1
    assert items[0].published_at is not None
    assert items[0].published_at.timestamp() == 1791309600.0
