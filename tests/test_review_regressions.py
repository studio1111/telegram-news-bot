import json

import pytest
import requests

import app.main as news_main
from app.telegram import TelegramDeliveryUnknown

import app.rubika as rubika
import app.telegram as telegram
import app.storage as storage
from app.storage import StateStore


def test_outbox_retains_all_retryable_deliveries_over_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "MAX_OUTBOX", 2)
    store = StateStore(tmp_path / "state.json")
    outbox = [
        {"key": "pending-1", "message": "one", "status": "pending"},
        {"key": "pending-2", "message": "two", "status": "pending"},
        {"key": "rubika-pending", "message": "three", "status": "sent", "rubika_status": "pending"},
        {"key": "completed", "message": "four", "status": "sent", "rubika_status": "sent"},
    ]

    store.save(set(), [], outbox)

    saved = store.load_outbox()
    assert {record["key"] for record in saved} == {
        "pending-1",
        "pending-2",
        "rubika-pending",
    }


def test_outbox_caps_only_fully_delivered_history(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "MAX_OUTBOX", 2)
    store = StateStore(tmp_path / "state.json")
    outbox = [
        {"key": "old", "message": "old", "status": "sent", "rubika_status": "sent"},
        {"key": "newer", "message": "newer", "status": "sent", "rubika_status": "sent"},
        {"key": "newest", "message": "newest", "status": "sent", "rubika_status": "sent"},
    ]

    store.save(set(), [], outbox)

    assert [record["key"] for record in store.load_outbox()] == ["newer", "newest"]


class _ImageResponse:
    status_code = 200
    headers = {"Content-Type": "image/jpeg"}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        yield b"image-bytes"


class _UploadResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"file_id": "uploaded-file"}


def test_rubika_image_download_uses_validated_redirect_safe_fetch(monkeypatch):
    fetched = []
    monkeypatch.setattr(rubika, "_safe_get", lambda url, timeout: (fetched.append((url, timeout)) or _ImageResponse()))
    monkeypatch.setattr(rubika, "_api", lambda *args, **kwargs: {"data": {"upload_url": "https://upload.example/upload"}})
    monkeypatch.setattr(rubika.requests, "post", lambda *args, **kwargs: _UploadResponse())
    monkeypatch.setattr(
        rubika.requests,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unsafe requests.get must not be used")),
    )

    result = rubika._upload_image("token", "https://images.example/news.jpg")

    assert result == "uploaded-file"
    assert fetched == [("https://images.example/news.jpg", rubika.REQUEST_TIMEOUT)]



def test_media_read_timeout_is_not_mistaken_for_telegram_delivery():
    # Image downloads happen before the Telegram API call. Their read timeout
    # must leave the outbox pending rather than falsely marking the story sent.
    assert not news_main._delivery_unknown(requests.exceptions.ReadTimeout("image download timed out"))


def test_telegram_api_read_timeout_is_treated_as_ambiguous_delivery():
    assert news_main._delivery_unknown(TelegramDeliveryUnknown("Telegram response timed out"))



def test_state_migration_preserves_pending_rubika_delivery_status(tmp_path):
    store = StateStore(tmp_path / "state.json")
    pending_rubika = {
        "key": "story-1",
        "url": "https://example.com/story-1",
        "message": "خبر",
        "image_url": "https://example.com/story.jpg",
        "status": "sent",
        "rubika_status": "pending",
    }

    store.save(set(), [], [pending_rubika])

    assert store.load_outbox()[0]["rubika_status"] == "pending"



def test_telegram_transport_error_does_not_leak_bot_token(monkeypatch):
    from app import telegram

    token = "super-secret-bot-token"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "@channel")
    monkeypatch.setattr(
        telegram.requests,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            requests.exceptions.ConnectionError(f"failed https://api.telegram.org/bot{token}/sendRichMessage")
        ),
    )

    with pytest.raises(telegram.TelegramAPIError) as caught:
        telegram._post(token, "sendRichMessage", {"chat_id": "@channel"})

    assert token not in str(caught.value)


def test_rubika_upload_error_does_not_leak_signed_upload_url(monkeypatch):
    signed_url = "https://upload.example/upload?signature=private-secret"
    monkeypatch.setattr(rubika, "_safe_get", lambda url, timeout: _ImageResponse())
    monkeypatch.setattr(rubika, "_api", lambda *args, **kwargs: {"data": {"upload_url": signed_url}})
    monkeypatch.setattr(
        rubika.requests,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            requests.exceptions.ConnectionError(f"failed {signed_url}")
        ),
    )

    with pytest.raises(RuntimeError) as caught:
        rubika._upload_image("token", "https://images.example/news.jpg")

    assert "private-secret" not in str(caught.value)
    assert signed_url not in str(caught.value)



def test_telegram_retries_unfetchable_image_with_multipart_upload(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "@channel")
    calls = []

    def fake_post(token, method, payload, max_rate_limit_retries=2, files=None):
        calls.append((method, payload, files))
        if len(calls) == 1:
            raise telegram.TelegramAPIError("Bad Request: failed to get HTTP URL content")
        return {"ok": True, "result": {"message_id": 123}}

    monkeypatch.setattr(telegram, "_post", fake_post)
    monkeypatch.setattr(
        telegram,
        "_download_image_for_upload",
        lambda url: (b"image-bytes", "image/jpeg", "hero.jpg"),
    )

    result = telegram.publish_rich_message("<b>خبر</b>", "https://images.example/story.jpg")

    assert result["ok"] is True
    assert len(calls) == 2
    assert calls[1][0] == "sendRichMessage"
    assert calls[1][2] == {"hero": ("hero.jpg", b"image-bytes", "image/jpeg")}
    uploaded_payload = json.loads(calls[1][1]["rich_message"])
    assert uploaded_payload["media"][0]["media"]["media"] == "attach://hero"


def test_image_download_timeout_does_not_mark_telegram_delivery_unknown(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "@channel")

    def fake_post(token, method, payload, max_rate_limit_retries=2, files=None):
        raise telegram.TelegramAPIError("Bad Request: failed to get HTTP URL content")

    monkeypatch.setattr(telegram, "_post", fake_post)
    monkeypatch.setattr(
        telegram,
        "_download_image_for_upload",
        lambda url: (_ for _ in ()).throw(requests.exceptions.ReadTimeout("image CDN timeout")),
    )

    with pytest.raises(telegram.TelegramAPIError) as caught:
        telegram.publish_rich_message("<b>خبر</b>", "https://images.example/story.jpg")

    assert not caught.value.delivery_unknown
    assert not news_main._delivery_unknown(caught.value)
