from datetime import datetime, timezone

import app.main as news_main
import app.telegram as telegram
from app.collector import NewsItem


def test_process_candidate_fetches_full_article_before_gemini(monkeypatch):
    item = NewsItem(
        "id",
        "Title",
        "https://example.com/story",
        "RSS summary",
        "TechCrunch",
        "https://example.com/image.jpg",
        datetime.now(timezone.utc),
        (),
    )
    seen = {}

    monkeypatch.setattr(news_main, "fetch_article_text", lambda url: (seen.__setitem__("url", url) or "Full article"))
    monkeypatch.setattr(
        news_main,
        "process_with_gemini",
        lambda title, summary, article: (seen.__setitem__("article", article) or {"title_fa": "x"}),
    )

    news_main._process_candidate(item)

    assert seen["url"] == item.url
    assert seen["article"] == "Full article"


def test_rich_message_with_image_never_falls_back_to_text_only(monkeypatch):
    calls = []

    class Response:
        def json(self):
            return {
                "ok": False,
                "error_code": 400,
                "description": "invalid rich media",
            }

        def raise_for_status(self):
            pass

    def fake_post(url, json, timeout):
        calls.append((url, json))
        return Response()

    monkeypatch.setattr(telegram.requests, "post", fake_post)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "@channel")

    try:
        telegram.publish_rich_message("<b>Title</b>", "https://example.com/image.jpg")
    except telegram.TelegramAPIError:
        pass
    else:
        raise AssertionError("image publication must not silently degrade to text-only")

    assert len(calls) == 1


def test_invalid_existing_rss_image_is_replaced_or_removed(monkeypatch):
    item = NewsItem(
        "id",
        "Title",
        "https://example.com/story",
        "Summary",
        "TechCrunch",
        "https://example.com/bad-image",
        datetime.now(timezone.utc),
        (),
    )

    monkeypatch.setattr(news_main, "validate_image_url", lambda url: False)
    monkeypatch.setattr(
        news_main,
        "fetch_article_image_url",
        lambda url: "https://example.com/recovered.jpg",
    )

    result = news_main._hydrate_missing_images([item], 10**9)

    assert result[0].image_url == "https://example.com/recovered.jpg"
