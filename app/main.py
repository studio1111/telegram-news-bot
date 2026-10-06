import json
from pathlib import Path
from .ai import process_with_gemini
from .collector import collect_feed
from .core import build_telegram_message, is_new_item
from .storage import StateStore
from .telegram import publish_message

def main():
    sources = json.loads(Path("data/sources.json").read_text(encoding="utf-8"))
    store = StateStore()
    seen = store.load()
    for source in sources:
        for item in collect_feed(source["url"], source["name"], source.get("limit", 10)):
            if not is_new_item(item.item_id, item.url, seen):
                continue
            processed = process_with_gemini(item.title, item.summary)
            message = build_telegram_message(
                processed["title_fa"], processed["summary_fa"],
                processed["category"], item.source, item.url
            )
            publish_message(message)
            seen.update((item.item_id, item.url))
    store.save(seen)

if __name__ == "__main__":
    main()
