import json
import os
from pathlib import Path
from .ai import process_with_gemini
from .collector import collect_feed
from .core import build_telegram_message, is_new_item
from .storage import StateStore
from .telegram import publish_message

def max_new_items(env=None):
    env = os.environ if env is None else env
    try:
        value = int(env.get("MAX_NEW_ITEMS", "3"))
        return value if value > 0 else 3
    except (TypeError, ValueError):
        return 3

def main():
    sources = json.loads(Path("data/sources.json").read_text(encoding="utf-8"))
    store = StateStore()
    seen = store.load()
    published = 0
    limit = max_new_items()
    for source in sources:
        for item in collect_feed(source["url"], source["name"], source.get("limit", 10)):
            if published >= limit:
                break
            if not is_new_item(item.item_id, item.url, seen):
                continue
            processed = process_with_gemini(item.title, item.summary)
            message = build_telegram_message(
                processed["title_fa"], processed["summary_fa"],
                processed["category"], item.source, item.url
            )
            publish_message(message)
            seen.update((item.item_id, item.url))
            published += 1
        if published >= limit:
            break
    store.save(seen)

if __name__ == "__main__":
    main()
