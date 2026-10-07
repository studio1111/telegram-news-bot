import html as html_lib
import os
import re

import requests


def _credentials():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("Telegram credentials are required")
    return token, chat_id


def _post(token, method, payload):
    response = requests.post(
        f"https://api.telegram.org/bot{token}/{method}",
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description") or f"Telegram API error in {method}")
    return data


def _rich_html_to_plain_text(value: str) -> str:
    text = re.sub(r"<img[^>]*>", "", value or "", flags=re.IGNORECASE)
    text = re.sub(r"</?(?:details|summary|p|br|div|section|article)[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_lib.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n\n", text)
    return text.strip()


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
    except RuntimeError as rich_error:
        plain = _rich_html_to_plain_text(html)
        if not plain:
            raise rich_error
        # sendMessage has a 4096-character limit. Preserve the end of the
        # post so the source and channel footer remain visible after truncation.
        if len(plain) > 4096:
            footer_marker = "آخرین اخبار تکنولوژی | @MyNewsTechnology"
            if footer_marker in plain:
                body = plain.split(footer_marker, 1)[0].rstrip()
                available = max(0, 4096 - len(footer_marker) - 5)
                plain = body[:available].rstrip() + "\n…\n" + footer_marker
            else:
                plain = plain[:4093].rstrip() + "…"
        try:
            return _post(
                token,
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": plain,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
        except Exception as fallback_error:
            raise RuntimeError(
                f"Telegram rich message failed: {rich_error}; "
                f"plain message fallback failed: {fallback_error}"
            ) from fallback_error
