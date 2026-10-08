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
        categories=("Technology",),
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
        NewsItem("story-0", "Technology story 0", "https://example.com/0", "technology", "TechCrunch", "https://example.com/0.jpg", now, ("Technology",)),
        NewsItem("story-1", "Technology story 1", "https://example.com/1", "technology", "The Verge", "https://example.com/1.jpg", now, ("Technology",)),
        NewsItem("story-2", "Technology story 2", "https://example.com/2", "technology", "Engadget", "https://example.com/2.jpg", now, ("Technology",)),
    ]

    def feed(url, source, limit):
        return [next(item for item in items if item.source == source)]

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
            '[{"name":"TechCrunch","url":"feed0","limit":100},'
            '{"name":"The Verge","url":"feed1","limit":100},'
            '{"name":"Engadget","url":"feed2","limit":100}]'
        ),
    )

    main_module.main()

    assert len(published) == 3

def test_logs_publish_summary(monkeypatch, capsys):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    item = NewsItem(
        "summary-story", "Technology story", "https://example.com/summary",
        "summary", "TechCrunch", "", now, ("Technology",)
    )
    monkeypatch.setattr(main_module, "collect_feed", lambda *args: [item])
    monkeypatch.setattr(main_module, "fetch_article_text", lambda url: "article")
    monkeypatch.setattr(main_module, "fetch_article_image_url", lambda url: "")
    monkeypatch.setattr(main_module, "process_with_gemini", lambda *args: {
        "title_fa": "خبر", "summary_fa": "خلاصه", "article_fa": "متن",
        "category": "technology"
    })
    monkeypatch.setattr(main_module, "is_duplicate_story", lambda *args: False)
    monkeypatch.setattr(main_module, "publish_rich_message", lambda *args: None)

    class FakeStore:
        def load(self): return set()
        def load_records(self): return []
        def save(self, seen, records): pass

    monkeypatch.setattr(main_module, "StateStore", FakeStore)
    monkeypatch.setattr(
        main_module.Path, "read_text",
        lambda self, encoding="utf-8": '[{"name":"Test Source","url":"feed","limit":100}]'
    )

    main_module.main()
    output = capsys.readouterr().out
    assert "[PUBLISHED] source=Test Source" in output
    assert "[SUMMARY] candidates=1 published=1" in output


def test_publishes_technology_story_when_gemini_mislabels_it_as_world(monkeypatch):
    now = datetime.now(timezone.utc)
    item = NewsItem(
        "recovered-story",
        "OpenAI announces a new AI model",
        "https://example.com/recovered",
        "OpenAI says the new artificial intelligence model improves ChatGPT.",
        "The Verge",
        "",
        now,
        ("Technology",),
    )

    monkeypatch.setattr(main_module, "collect_feed", lambda *args: [item])
    monkeypatch.setattr(main_module, "fetch_article_text", lambda url: (
        "OpenAI described the new artificial intelligence model and its ChatGPT system."
    ))
    monkeypatch.setattr(main_module, "fetch_article_image_url", lambda url: "")
    monkeypatch.setattr(
        main_module,
        "process_with_gemini",
        lambda *args: {
            "title_fa": "مدل هوش مصنوعی جدید اوپن‌ای‌آی",
            "summary_fa": "اوپن‌ای‌آی از یک مدل هوش مصنوعی جدید خبر داد.",
            "article_fa": "این مدل برای سیستم چت‌جی‌پی‌تی توسعه یافته است.",
            "category": "world",
        },
    )
    monkeypatch.setattr(main_module, "is_duplicate_story", lambda *args: False)

    published = []
    monkeypatch.setattr(
        main_module,
        "publish_rich_message",
        lambda message, image_url: published.append(message),
    )

    class FakeStore:
        def load(self): return set()
        def load_records(self): return []
        def save(self, seen, records): pass

    monkeypatch.setattr(main_module, "StateStore", FakeStore)
    monkeypatch.setattr(
        main_module.Path,
        "read_text",
        lambda self, encoding="utf-8": (
            '[{"name":"The Verge","url":"feed","limit":100}]'
        ),
    )

    main_module.main()

    assert len(published) == 1
