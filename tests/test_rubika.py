import html
import requests

from app import rubika


def test_rubika_disabled_without_both_secrets(monkeypatch):
    monkeypatch.delenv("RUBIKA_BOT_TOKEN", raising=False)
    monkeypatch.setenv("RUBIKA_CHANNEL_ID", "MyNewsTechnology")
    assert not rubika.rubika_enabled()


def test_rubika_enabled_with_both_secrets(monkeypatch):
    monkeypatch.setenv("RUBIKA_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("RUBIKA_CHANNEL_ID", "MyNewsTechnology")
    assert rubika.rubika_enabled()


def test_plain_text_removes_html_and_decodes_entities():
    value = rubika._plain_text("<b>📰 عنوان</b><br>متن &amp; توضیح")
    assert "<b>" not in value
    assert "عنوان" in value
    assert "متن & توضیح" in value


def test_rubika_requires_image(monkeypatch):
    monkeypatch.setenv("RUBIKA_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("RUBIKA_CHANNEL_ID", "MyNewsTechnology")
    try:
        rubika.publish_rubika_message("<b>خبر</b>", "")
    except RuntimeError as exc:
        assert "requires an image" in str(exc)
    else:
        raise AssertionError("text-only Rubika publication should be rejected")



def test_api_http_errors_never_expose_bot_token(monkeypatch):
    token = "private-rubika-token"
    monkeypatch.setattr(
        rubika.requests,
        "post",
        lambda *args, **kwargs: _HttpErrorResponse(token),
    )

    try:
        rubika._api(token, "sendFile", {"chat_id": "channel", "file_id": "image"})
    except RuntimeError as exc:
        assert token not in str(exc)
        assert "HTTP" in str(exc)
    else:
        raise AssertionError("Rubika HTTP failures must be surfaced safely")


class _HttpErrorResponse:
    def __init__(self, token):
        self.token = token

    def raise_for_status(self):
        raise requests.HTTPError(
            f"403 Client Error: Forbidden for url: https://botapi.rubika.ir/v3/{self.token}/sendFile"
        )



def test_rubika_conversion_preserves_full_text_label_and_article():
    from app.core import build_rich_message_html

    original = build_rich_message_html(
        "عنوان خبر",
        "خلاصه خبر",
        "این متن کامل خبر است و نباید حذف شود.",
        "TechCrunch",
    )
    converted = rubika._plain_text(original)

    assert "✨ مشاهده متن کامل خبر ✨" in converted
    assert "این متن کامل خبر است و نباید حذف شود." in converted
    assert "📡 منبع: TechCrunch" in converted
    assert "آخرین اخبار تکنولوژی | @MyNewsTechnology" in converted


def test_rubika_publishes_image_with_converted_news_text(monkeypatch):
    monkeypatch.setenv("RUBIKA_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("RUBIKA_CHANNEL_ID", "channel-id")
    monkeypatch.setattr(rubika, "_upload_image", lambda token, url: "uploaded-image-id")
    calls = []
    monkeypatch.setattr(
        rubika,
        "_api",
        lambda token, method, payload: calls.append((token, method, payload)) or {"status": "OK"},
    )
    message = "<b>📰 خبر</b><br>✨ مشاهده متن کامل خبر ✨<br>متن کامل<br>📡 منبع: Engadget"

    result = rubika.publish_rubika_message(message, "https://example.com/news.jpg")

    assert result == {"status": "OK"}
    assert calls == [(
        "fake-token",
        "sendFile",
        {
            "chat_id": "channel-id",
            "file_id": "uploaded-image-id",
            "text": "📰 خبر\n✨ مشاهده متن کامل خبر ✨\nمتن کامل\n📡 منبع: Engadget",
        },
    )]


def test_rubika_falls_back_to_text_only_when_media_is_rejected(monkeypatch):
    monkeypatch.setenv("RUBIKA_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("RUBIKA_CHANNEL_ID", "channel-id")
    monkeypatch.setattr(rubika, "_upload_image", lambda token, url: "uploaded-image-id")
    calls = []

    def fake_api(token, method, payload):
        calls.append((method, payload))
        if method == "sendFile":
            raise RuntimeError("Rubika sendFile failed: INVALID_INPUT")
        return {"status": "OK", "data": {"message_id": "123"}}

    monkeypatch.setattr(rubika, "_api", fake_api)
    result = rubika.publish_rubika_message(
        "<b>خبر</b><br>متن کامل",
        "https://example.com/news.jpg",
    )

    assert result["status"] == "OK"
    assert result["_delivery_mode"] == "text_fallback"
    assert calls == [
        ("sendFile", {"chat_id": "channel-id", "file_id": "uploaded-image-id", "text": "خبر\\nمتن کامل"}),
        ("sendMessage", {"chat_id": "channel-id", "text": "خبر\\nمتن کامل"}),
    ]


def test_rubika_does_not_fallback_for_ambiguous_or_other_errors(monkeypatch):
    monkeypatch.setenv("RUBIKA_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("RUBIKA_CHANNEL_ID", "channel-id")
    monkeypatch.setattr(rubika, "_upload_image", lambda token, url: "uploaded-image-id")
    calls = []

    def fake_api(token, method, payload):
        calls.append(method)
        raise RuntimeError("Rubika sendFile failed: TIMEOUT")

    monkeypatch.setattr(rubika, "_api", fake_api)
    try:
        rubika.publish_rubika_message("خبر", "https://example.com/news.jpg")
    except RuntimeError as exc:
        assert "TIMEOUT" in str(exc)
    else:
        raise AssertionError("non-INVALID_INPUT failures must remain retryable")
    assert calls == ["sendFile"]
