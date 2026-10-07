from datetime import datetime, timezone

from app.collector import NewsItem
import app.main as news_main


def test_main_uses_thirty_minute_window():
    from datetime import timedelta

    now = datetime(2026, 10, 6, 18, 10, tzinfo=timezone.utc)
    assert news_main.is_recent_news(now - timedelta(minutes=30), now)
    assert not news_main.is_recent_news(now - timedelta(minutes=30, seconds=1), now)


def test_item_without_image_is_not_marked_seen(monkeypatch):
    class FakeStore:
        last_seen = None

        def __init__(self):
            self.seen = set()
            self.records = []

        def load(self):
            return self.seen

        def load_records(self):
            return self.records

        def save(self, seen, records=None):
            FakeStore.last_seen = set(seen)

    item = NewsItem(
        item_id="item-1",
        title="New technology story",
        url="https://example.com/story",
        summary="A technology story.",
        source="TechCrunch",
        image_url="",
        published_at=datetime.now(timezone.utc),
    )

    monkeypatch.setattr(news_main, "StateStore", FakeStore)
    monkeypatch.setattr(news_main.Path, "read_text", lambda *args, **kwargs: '[{"name":"TechCrunch","url":"feed"}]')
    monkeypatch.setattr(news_main, "collect_feed", lambda *args, **kwargs: [item])
    monkeypatch.setattr(news_main, "is_recent_news", lambda *args, **kwargs: True)
    monkeypatch.setattr(news_main, "fetch_article_text", lambda *args, **kwargs: "")
    monkeypatch.setattr(news_main, "fetch_article_image_url", lambda *args, **kwargs: "")

    news_main.main()

    assert FakeStore.last_seen == set()


def test_non_technology_gemini_result_is_not_published(monkeypatch):
    now = datetime.now(timezone.utc)
    item = NewsItem(
        "world-1",
        "World event",
        "https://example.com/world",
        "A non technology event",
        "BBC Technology",
        "",
        now,
    )

    monkeypatch.setattr(
        news_main,
        "_collect_recent_items",
        lambda *args, **kwargs: [item],
    )
    monkeypatch.setattr(news_main, "fetch_article_text", lambda *args, **kwargs: "article")
    monkeypatch.setattr(news_main, "fetch_article_image_url", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        news_main,
        "process_with_gemini",
        lambda *args, **kwargs: {
            "title_fa": "رویداد جهانی",
            "summary_fa": "خلاصه",
            "article_fa": "متن",
            "category": "world",
        },
    )
    monkeypatch.setattr(news_main, "publish_rich_message", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not publish")))

    class FakeStore:
        def load(self): return set()
        def load_records(self): return []
        def save(self, seen, records): pass

    monkeypatch.setattr(news_main, "StateStore", FakeStore)
    news_main.main()
