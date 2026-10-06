import json
import os
from pathlib import Path

from .ai import process_with_gemini
from .collector import collect_feed, fetch_article_text
from .core import (
    build_expanded_message,
    is_duplicate_story,
    is_new_item,
    is_technology_news,
)
from .storage import StateStore
from .telegram import publish_message, publish_photo


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

            article_text = fetch_article_text(item.url)
            processed = process_with_gemini(item.title, item.summary, article_text)
            seen.update((item.item_id, item.url))

            if not is_technology_news(processed.get("category", "")):
                continue

            story = {"title": item.title, "summary": item.summary}
            if is_duplicate_story(story, published_stories):
                continue

            message = build_expanded_message(
                processed["title_fa"],
                processed.get("article_fa") or processed["summary_fa"],
                item.source,
            )

            # Telegram can fetch the public hero image directly. If a feed does not
            # expose one, the article is still published as a clean text post.
            publish_photo(
                item.image_url,
                f"📰 <b>{processed['title_fa']}</b>",
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
