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
