import json
import os
from pathlib import Path

from .ai import process_with_gemini
from .collector import collect_feed, fetch_article_image_url, fetch_article_text
from .core import (
    build_rich_message_html,
    is_duplicate_story,
    is_new_item,
    is_technology_news,
)
from .storage import StateStore
from .telegram import publish_rich_message


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
            image_url = item.image_url or fetch_article_image_url(item.url)
            seen.update((item.item_id, item.url))

            # The channel format requires a real source image. Do not publish
            # a text-only post when the feed omitted the image and the article
            # page does not expose a recoverable Open Graph/Twitter image.
            if not image_url:
                continue

            processed = process_with_gemini(item.title, item.summary, article_text)

            if not is_technology_news(processed.get("category", "")):
                continue

            story = {"title": item.title, "summary": item.summary}
            if is_duplicate_story(story, published_stories):
                continue

            message = build_rich_message_html(
                processed["title_fa"],
                processed["summary_fa"],
                processed.get("article_fa") or processed["summary_fa"],
                item.source,
            )

            publish_rich_message(message, image_url)

            published_stories.append(story)
            published_stories = published_stories[-500:]
            published += 1

        if published >= limit:
            break

    store.save(seen, published_stories)


if __name__ == "__main__":
    main()
