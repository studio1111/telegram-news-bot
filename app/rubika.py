"""Rubika Bot API publisher. Telegram remains the primary delivery target."""
import html
import os
import re
from urllib.parse import urlparse

import requests

API_BASE = "https://botapi.rubika.ir/v3"
REQUEST_TIMEOUT = 30
MAX_IMAGE_BYTES = 12 * 1024 * 1024


def rubika_enabled():
    return bool(os.environ.get("RUBIKA_BOT_TOKEN") and os.environ.get("RUBIKA_CHANNEL_ID"))


def _credentials():
    token = os.environ.get("RUBIKA_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("RUBIKA_CHANNEL_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("Rubika credentials are required")
    return token, chat_id


def _plain_text(message):
    value = re.sub(r"<br\s*/?>", "\n", message or "", flags=re.IGNORECASE)
    value = re.sub(r"</(?:p|div|section|article|details|summary)>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n\n", value)
    return value.strip()


def _api(token, method, payload=None):
    # The token is part of the API URL. Never allow requests' exceptions to
    # print that URL into GitHub Actions logs.
    try:
        response = requests.post(
            f"{API_BASE}/{token}/{method}",
            json=payload or {},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        status = f" (HTTP {status_code})" if status_code else ""
        raise RuntimeError(
            f"Rubika {method} request failed{status} ({type(exc).__name__})"
        ) from None

    try:
        body = response.json()
    except ValueError as exc:
        raise RuntimeError(f"Rubika {method} returned non-JSON response") from exc
    if not isinstance(body, dict):
        raise RuntimeError(f"Rubika {method} returned an invalid response")
    status = str(body.get("status", "")).upper()
    if status and status not in {"OK", "SUCCESS"}:
        detail = body.get("description") or body.get("error_message") or body.get("error")
        suffix = f": {str(detail)[:240]}" if isinstance(detail, (str, int, float)) and detail else ""
        raise RuntimeError(f"Rubika {method} failed: {status}{suffix}")
    if body.get("ok") is False:
        detail = body.get("description", "unknown error")
        raise RuntimeError(f"Rubika {method} failed: {str(detail)[:240]}")
    return body

def _data(body):
    value = body.get("data", body)
    return value if isinstance(value, dict) else {}


def _upload_image(token, image_url):
    parsed = urlparse(image_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError("Rubika image URL must be HTTPS")
    with requests.get(image_url, stream=True, timeout=REQUEST_TIMEOUT) as response:
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
        if not content_type.startswith("image/"):
            raise RuntimeError("News image URL did not return an image")
        chunks = []
        total = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_IMAGE_BYTES:
                raise RuntimeError("News image exceeds Rubika upload limit")
            chunks.append(chunk)
    image_bytes = b"".join(chunks)
    if not image_bytes:
        raise RuntimeError("News image download was empty")

    upload_info = _data(_api(token, "requestSendFile", {"type": "Image"}))
    upload_url = upload_info.get("upload_url")
    if not isinstance(upload_url, str) or not upload_url.startswith("https://"):
        raise RuntimeError("Rubika did not provide a valid upload URL")
    filename = os.path.basename(parsed.path) or "news-image.jpg"
    upload_response = requests.post(
        upload_url,
        files={"file": (filename, image_bytes, content_type)},
        timeout=REQUEST_TIMEOUT,
    )
    upload_response.raise_for_status()
    try:
        uploaded = upload_response.json()
    except ValueError:
        uploaded = {}
    file_info = _data(uploaded) if isinstance(uploaded, dict) else {}
    file_id = file_info.get("file_id") or (uploaded.get("file_id") if isinstance(uploaded, dict) else None)
    if not file_id:
        raise RuntimeError("Rubika image upload did not return a file_id")
    return str(file_id)


def publish_rubika_message(message, image_url=""):
    """Publish a news item to Rubika, preserving delivery if its media endpoint rejects it.

    Telegram is handled separately by the caller. A definite INVALID_INPUT rejection
    from Rubika's media endpoint falls back to a normal text message, so the story can
    still reach Rubika while image delivery is diagnosed. Other errors remain visible
    and retryable rather than being mistaken for success.
    """
    token, chat_id = _credentials()
    text = _plain_text(message)
    if not text:
        raise RuntimeError("Cannot publish an empty Rubika news post")
    if not image_url:
        raise RuntimeError("Rubika news publication requires an image")

    file_id = _upload_image(token, image_url)
    try:
        return _api(token, "sendFile", {"chat_id": chat_id, "file_id": file_id, "text": text})
    except RuntimeError as exc:
        # INVALID_INPUT is a definite API rejection, not an ambiguous timeout.
        # Try text-only delivery so Rubika is not completely silent. Telegram
        # has already been sent and is never called from this fallback.
        if "INVALID_INPUT" not in str(exc).upper():
            raise
        print("[RUBIKA_MEDIA_REJECTED] sendFile returned INVALID_INPUT; trying sendMessage text fallback")
        result = _api(token, "sendMessage", {"chat_id": chat_id, "text": text})
        if isinstance(result, dict):
            result = dict(result)
            result["_delivery_mode"] = "text_fallback"
        return result
