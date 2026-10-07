import pytest

import app.telegram as telegram
from app.telegram import build_rich_message_payload


def test_rich_message_embeds_photo_and_text_in_one_message():
    payload = build_rich_message_payload(
        "<b>تیتر خبر</b><details><summary>مشاهده متن کامل خبر</summary><p>متن کامل</p></details>",
        "https://example.com/hero.jpg",
    )
    rich = payload["rich_message"]
    assert rich["html"].startswith('<img src="tg://photo?id=hero"/>')
    assert rich["media"] == [
        {
            "id": "hero",
            "media": {"type": "photo", "media": "https://example.com/hero.jpg"},
        }
    ]
    assert "تیتر خبر" in rich["html"]
    assert "متن کامل" in rich["html"]


def test_rich_message_without_image_keeps_text():
    payload = build_rich_message_payload("<b>تیتر خبر</b>", "")
    assert payload["rich_message"]["html"] == "<b>تیتر خبر</b>"
    assert "media" not in payload["rich_message"]


def test_post_raises_when_telegram_returns_ok_false(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": False, "description": "chat not found"}

    monkeypatch.setattr(telegram.requests, "post", lambda *args, **kwargs: Response())

    with pytest.raises(RuntimeError, match="chat not found"):
        telegram._post("token", "sendMessage", {"chat_id": "chat", "text": "hello"})


def test_rich_message_falls_back_to_send_message_when_rich_api_rejects(monkeypatch):
    calls = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    def fake_post(url, json, timeout):
        calls.append((url, json))
        if url.endswith("/sendRichMessage"):
            return Response({"ok": False, "description": "rich messages are unavailable"})
        return Response({"ok": True, "result": {"message_id": 123}})

    monkeypatch.setattr(telegram.requests, "post", fake_post)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "@channel")

    result = telegram.publish_rich_message("<b>تیتر خبر</b><details><p>متن کامل</p></details>", "")

    assert result["ok"] is True
    assert calls[0][0].endswith("/sendRichMessage")
    assert calls[1][0].endswith("/sendMessage")
    assert calls[1][1]["chat_id"] == "@channel"
    assert "parse_mode" not in calls[1][1]
    assert "تیتر خبر" in calls[1][1]["text"]
    assert "<details>" not in calls[1][1]["text"]
