import re
from html import escape

def normalize_text(value: str) -> str:
    return re.sub(r"\s+"," ",(value or "")).strip()

def is_new_item(item_id: str,url: str,seen: set[str]) -> bool:
    return item_id not in seen and url not in seen

def is_technology_news(category: str) -> bool:
    return normalize_text(category).lower() == "technology"

def build_telegram_message(title,summary,category,source,url):
    return (
      f"📰 <b>{escape(normalize_text(title))}</b>\n\n"
      f"{escape(normalize_text(summary))}\n\n"
      f"🏷 {escape(normalize_text(category))}\n"
      f"📡 منبع: {escape(normalize_text(source))}\n\n"
      f"🔗 <a href=\"{escape(url,quote=True)}\">مشاهده منبع</a>"
    )
