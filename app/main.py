import json
import os
from pathlib import Path

from .ai import process_with_gemini
from .collector import collect_feed
from .core import (
    build_telegram_message,
    is_duplicate_story,
    is_new_item,
    is_technology_news,
)
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
    published_stories = store.load_records()
    published = 0
    limit = max_new_items()

    for source in sources:
        for item in collect_feed(source["url"], source["name"], source.get("limit", 10)):
            if published >= limit:
                break
            if not is_new_item(item.item_id, item.url, seen):
                continue

            processed = process_with_gemini(item.title, item.summary)
            seen.update((item.item_id, item.url))

            if not is_technology_news(processed.get("category", "")):
                continue

            story = {"title": item.title, "summary": item.summary}
            if is_duplicate_story(story, published_stories):
                continue

            message = build_telegram_message(
                processed["title_fa"],
                processed["summary_fa"],
                processed["category"],
                item.source,
                item.url,
            )
            publish_message(message)
            published_stories.append(story)
            published_stories = published_stories[-500:]
            published += 1

        if published >= limit:
            break

    store.save(seen, published_stories)

if __name__ == "__main__":
    main()
