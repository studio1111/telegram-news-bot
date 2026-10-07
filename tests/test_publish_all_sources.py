from datetime import datetime, timezone
import app.main as main_module
from app.collector import NewsItem


def test_publishes_technology_story_without_image(monkeypatch):
    now = datetime.now(timezone.utc)
    item = NewsItem(
        item_id="story-1",
        title="New technology launch",
        url="https://example.com/story-1",
        summary="A technology story.",
        source="The Verge",
        image_url="",
        published_at=now,
    )

    monkeypatch.setattr(
        main_module,
        "collect_feed",
        lambda url, source, limit: [item],
    )
    monkeypatch.setattr(main_module, "fetch_article_text", lambda url: "article")
    monkeypatch.setattr(main_module, "fetch_article_image_url", lambda url: "")
    monkeypatch.setattr(
        main_module,
        "process_with_gemini",
        lambda title, summary, article: {
            "title_fa": "خبر فناوری",
            "summary_fa": "خلاصه خبر",
            "article_fa": "متن خبر",
            "category": "technology",
        },
    )
    monkeypatch.setattr(main_module, "is_duplicate_story", lambda story, previous: False)

    published = []
    monkeypatch.setattr(
        main_module,
        "publish_rich_message",
        lambda message, image_url: published.append((message, image_url)),
    )

    class FakeStore:
        def load(self):
            return set()

        def load_records(self):
            return []

        def save(self, seen, records):
            self.saved = (seen, records)

    monkeypatch.setattr(main_module, "StateStore", FakeStore)
    monkeypatch.setattr(
        main_module.Path,
        "read_text",
        lambda self, encoding="utf-8": '[{"name":"The Verge","url":"feed","limit":100}]',
    )
    main_module.main()

    assert len(published) == 1
    assert published[0][1] == ""


def test_publishes_all_eligible_stories_across_sources(monkeypatch):
    now = datetime.now(timezone.utc)
    items = [
        NewsItem(f"story-{i}", f"Technology story {i}", f"https://example.com/{i}",
                 "technology", f"Source {i}", f"https://example.com/{i}.jpg", now)
        for i in range(5)
    ]

    def feed(url, source, limit):
        index = int(source.rsplit(" ", 1)[-1])
        return [items[index]]

    monkeypatch.setattr(main_module, "collect_feed", feed)
    monkeypatch.setattr(main_module, "fetch_article_text", lambda url: "article")
    monkeypatch.setattr(main_module, "fetch_article_image_url", lambda url: "")
    monkeypatch.setattr(
        main_module,
        "process_with_gemini",
        lambda title, summary, article: {
            "title_fa": title,
            "summary_fa": summary,
            "article_fa": article,
            "category": "technology",
        },
    )
    monkeypatch.setattr(main_module, "is_duplicate_story", lambda story, previous: False)

    published = []
    monkeypatch.setattr(
        main_module,
        "publish_rich_message",
        lambda message, image_url: published.append(message),
    )

    class FakeStore:
        def load(self):
            return set()

        def load_records(self):
            return []

        def save(self, seen, records):
            pass

    monkeypatch.setattr(main_module, "StateStore", FakeStore)
    monkeypatch.setattr(
        main_module.Path,
        "read_text",
        lambda self, encoding="utf-8": (
            '[{"name":"Source 0","url":"feed0","limit":100},'
            '{"name":"Source 1","url":"feed1","limit":100},'
            '{"name":"Source 2","url":"feed2","limit":100},'
            '{"name":"Source 3","url":"feed3","limit":100},'
            '{"name":"Source 4","url":"feed4","limit":100}]'
        ),
    )
    monkeypatch.setattr(main_module.datetime, "now", lambda tz=None: now)

    main_module.main()

    assert len(published) == 5
