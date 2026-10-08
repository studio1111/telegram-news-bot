import html as html_lib
import os
import re
import time

import requests


TELEGRAM_MESSAGE_LIMIT = 4096
CAPTION_LIMIT = 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024
FOOTER_MARKER = "آخرین اخبار تکنولوژی | @MyNewsTechnology"


class TelegramAPIError(RuntimeError):
    """Telegram request failed; fallback is safe only for explicit client rejection."""

    def __init__(self, message, *, fallback_safe=False):
        super().__init__(message)
        self.fallback_safe = fallback_safe


def _credentials():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("Telegram credentials are required")
    return token, chat_id


def _post(token, method, payload, max_rate_limit_retries=5):
    for attempt in range(max_rate_limit_retries + 1):
        response = requests.post(
            f"https://api.telegram.org/bot{token}/{method}",
            json=payload,
            timeout=30,
        )
        # Telegram answers 4xx with a JSON body ({"ok": false, ...}). Read the
        # body first; calling raise_for_status() first turned every rejection
        # into requests.HTTPError, which bypassed the fallback entirely.
        try:
            data = response.json()
        except ValueError:
            data = None

        if not isinstance(data, dict):
            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                raise TelegramAPIError(f"Telegram HTTP error in {method}: {exc}", fallback_safe=False) from exc
            raise TelegramAPIError(f"Telegram returned a non-JSON response in {method}", fallback_safe=False)

        if data.get("ok"):
            return data

        retry_after = (data.get("parameters") or {}).get("retry_after")
        if data.get("error_code") == 429 and retry_after and attempt < max_rate_limit_retries:
            time.sleep(min(int(retry_after), 60) + 1)
            continue

        error_code = data.get("error_code")
        fallback_safe = method == "sendRichMessage" and isinstance(error_code, int) and 400 <= error_code < 500 and error_code != 429
        raise TelegramAPIError(data.get("description") or f"Telegram API error in {method}", fallback_safe=fallback_safe)

    raise TelegramAPIError(f"Telegram rate limit persisted in {method}", fallback_safe=False)


def _post_multipart(token, method, data, files):
    """Upload bytes (used when Telegram cannot fetch an image URL itself)."""
    response = requests.post(
        f"https://api.telegram.org/bot{token}/{method}",
        data=data,
        files=files,
        timeout=60,
    )
    try:
        body = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict) or not body.get("ok"):
        description = body.get("description") if isinstance(body, dict) else "non-JSON response"
        raise TelegramAPIError(f"Telegram upload failed in {method}: {description}")
    return body


def _rich_html_to_plain_text(value: str) -> str:
    text = re.sub(r"<img[^>]*>", "", value or "", flags=re.IGNORECASE)
    text = re.sub(r"</?(?:details|summary|p|br|div|section|article)[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_lib.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n\n", text)
    return text.strip()


def _truncate_plain(plain: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> str:
    # sendMessage has a 4096-character limit (captions 1024). Preserve the end
    # of the post so the source and channel footer remain visible after truncation.
    if len(plain) <= limit:
        return plain
    if FOOTER_MARKER in plain:
        body, _tail = plain.split(FOOTER_MARKER, 1)
        body = body.rstrip()
        # Keep the "source" line that sits just above the footer.
        source_line = ""
        if "\n" in body:
            head, last_line = body.rsplit("\n", 1)
            if last_line.startswith("📡"):
                body, source_line = head.rstrip(), last_line
        suffix = "\n…\n" + (source_line + "\n" if source_line else "") + FOOTER_MARKER
        available = max(0, limit - len(suffix))
        return body[:available].rstrip() + suffix
    return plain[: limit - 1].rstrip() + "…"


def publish_message(text):
    token, chat_id = _credentials()
    return _post(
        token,
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
    )


def publish_photo(image_url, caption):
    if not image_url:
        return None
    token, chat_id = _credentials()
    return _post(
        token,
        "sendPhoto",
        {
            "chat_id": chat_id,
            "photo": image_url,
            "caption": caption,
            "parse_mode": "HTML",
        },
    )


def _download_image(image_url):
    """Fetch an image ourselves; some CDNs block Telegram's servers (hotlink protection)."""
    response = requests.get(
        image_url,
        timeout=20,
        stream=True,
        headers={"User-Agent": "Mozilla/5.0 (compatible; MyNewsTechnology/1.0)"},
    )
    response.raise_for_status()
    content_type = str(response.headers.get("Content-Type", "")).split(";")[0].strip().lower()
    if not content_type.startswith("image/"):
        raise ValueError(f"not an image: {content_type or 'unknown content type'}")
    chunks, total = [], 0
    for chunk in response.iter_content(chunk_size=65536):
        if not chunk:
            continue
        total += len(chunk)
        if total > MAX_IMAGE_BYTES:
            raise ValueError("image too large")
        chunks.append(chunk)
    return b"".join(chunks), content_type


def _send_photo_plain(token, chat_id, image_url, caption):
    """Send a photo with a plain-text caption: by URL first, then by uploading the bytes."""
    try:
        return _post(token, "sendPhoto", {"chat_id": chat_id, "photo": image_url, "caption": caption})
    except TelegramAPIError as url_error:
        print(f"[TELEGRAM_PHOTO_URL_REJECTED] {url_error}")
    data, content_type = _download_image(image_url)
    extension = content_type.split("/", 1)[-1].replace("jpeg", "jpg") or "jpg"
    return _post_multipart(
        token,
        "sendPhoto",
        {"chat_id": chat_id, "caption": caption},
        {"photo": (f"image.{extension}", data, content_type)},
    )


def build_rich_message_payload(html, image_url=""):
    rich_message = {
        "html": html,
        "is_rtl": True,
    }

    if image_url:
        # Telegram Rich Messages require media to be declared in the
        # InputRichMessage.media field and referenced from the HTML by tg://.
        rich_message["html"] = f'<img src="tg://photo?id=hero"/>\n\n{html}'
        rich_message["media"] = [
            {
                "id": "hero",
                "media": {
                    "type": "photo",
                    "media": image_url,
                },
            }
        ]

    return {
        "chat_id": os.environ.get("TELEGRAM_CHAT_ID"),
        "rich_message": rich_message,
    }


def publish_rich_message(html, image_url=""):
    """Publish a rich news post, with a plain Telegram fallback if needed."""
    token, chat_id = _credentials()
    payload = build_rich_message_payload(html, image_url)
    try:
        return _post(token, "sendRichMessage", payload)
    except TelegramAPIError as rich_error:
        if not rich_error.fallback_safe:
            raise
        plain = _rich_html_to_plain_text(html)
        if not plain:
            raise
        print(f"[TELEGRAM_FALLBACK] sendRichMessage rejected: {rich_error}")
        photo_result = None
        if image_url:
            # Keep the picture: the old fallback silently dropped it.
            first_line = plain.split("\n", 1)[0]
            caption = _truncate_plain(plain, CAPTION_LIMIT) if len(plain) <= CAPTION_LIMIT else _truncate_plain(first_line, CAPTION_LIMIT)
            try:
                photo_result = _send_photo_plain(token, chat_id, image_url, caption)
            except Exception as photo_error:
                print(f"[TELEGRAM_PHOTO_ERROR] image dropped, sending text only: {photo_error}")
            if photo_result is not None and len(plain) <= CAPTION_LIMIT:
                return photo_result
        try:
            return _post(
                token,
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": _truncate_plain(plain),
                    "disable_web_page_preview": True,
                },
            )
        except Exception as fallback_error:
            if photo_result is not None:
                # The photo (with the headline) is already in the channel; do not
                # fail the story, or the retry would post the photo a second time.
                print(f"[TELEGRAM_TEXT_AFTER_PHOTO_ERROR] {fallback_error}")
                return photo_result
            raise RuntimeError(
                f"Telegram rich message failed: {rich_error}; "
                f"plain message fallback failed: {fallback_error}"
            ) from fallback_error
