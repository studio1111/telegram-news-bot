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


def publish_rich_message(html, image_url=""):
    """Publish image + headline + expandable article as one Telegram post.

    Telegram Bot API 10.3 supports Rich Messages with media and <details>
    blocks, so the article is no longer split into a photo message and a
    separate text message.
    """
    token, chat_id = _credentials()

    payload = {
        "chat_id": chat_id,
        "rich_message": {
            "html": html,
            "is_rtl": True,
        },
    }

    if image_url:
        payload["rich_message"]["html"] = (
            f'<img src="tg://photo?id=hero"/>\n\n{html}'
        )
        payload["rich_message"]["media"] = [
            {
                "id": "hero",
                "media": {
                    "type": "photo",
                    "media": image_url,
                },
            }
        ]

    return _post(token, "sendRichMessage", payload)
