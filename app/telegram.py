import os

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
    return response.json()


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
    """Publish image, headline, summary and expandable article as one post."""
    token, _ = _credentials()
    payload = build_rich_message_payload(html, image_url)
    return _post(token, "sendRichMessage", payload)
