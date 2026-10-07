import json
from datetime import datetime, timezone
from pathlib import Path

from .ai import process_with_gemini
from .collector import collect_feed, fetch_article_image_url, fetch_article_text
from .core import (
    build_rich_message_html,
    is_duplicate_story,
    is_new_item,
    is_recent_news,
    is_technology_news,
)
from .storage import StateStore
from .telegram import publish_rich_message


def _collect_recent_items(sources, seen, now):
    candidates = []
    for source in sources:
        items = collect_feed(
            source["url"],
            source["name"],
            source.get("limit", 100),
        )
        for item in items:
            if not is_new_item(item.item_id, item.url, seen):
                continue
            if not is_recent_news(item.published_at, now):
                continue
            candidates.append(item)

    # Process the complete 30-minute window globally, newest first.
    # This prevents source order from influencing which stories are handled first.
    return sorted(
        candidates,
        key=lambda item: item.published_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )


def main():
    sources = json.loads(Path("data/sources.json").read_text(encoding="utf-8"))
    store = StateStore()
    seen = store.load()
    published_stories = store.load_records()
    now = datetime.now(timezone.utc)

    candidates = _collect_recent_items(sources, seen, now)
    published_count = 0
    ai_failed = 0
    non_technology = 0
    duplicates = 0
    telegram_failed = 0

    print(
        f"[RUN] now={now.isoformat()} window_minutes=30 candidates={len(candidates)}"
    )

    for item in candidates:
        article_text = fetch_article_text(item.url)
        image_url = item.image_url or fetch_article_image_url(item.url)

        # Image is optional. A valid technology story must not be discarded
        # merely because its RSS entry/article page has no recoverable image.
        try:
            processed = process_with_gemini(
                item.title,
                item.summary,
                article_text,
            )
        except Exception as exc:
            # One broken article/AI response must not prevent other sources
            # in the same 30-minute window from being published.
            ai_failed += 1
            print(f"[GEMINI_ERROR] source={item.source} url={item.url}: {exc}")
            continue

        if not is_technology_news(processed.get("category", "")):
            non_technology += 1
            print(
                f"[FILTERED] non_technology source={item.source} "
                f"category={processed.get('category', '')} url={item.url}"
            )
            continue

        story = {"title": item.title, "summary": item.summary}
        if is_duplicate_story(story, published_stories):
            duplicates += 1
            print(f"[DUPLICATE] source={item.source} url={item.url}")
            continue

        message = build_rich_message_html(
            processed["title_fa"],
            processed["summary_fa"],
            processed.get("article_fa") or processed["summary_fa"],
            item.source,
        )

        try:
            publish_rich_message(message, image_url)
        except Exception as exc:
            # Do not mark failed publications as seen.
            telegram_failed += 1
            print(f"[TELEGRAM_ERROR] source={item.source} url={item.url}: {exc}")
            continue

        published_count += 1
        print(f"[PUBLISHED] source={item.source} url={item.url}")
        seen.update((item.item_id, item.url))
        published_stories.append(story)
        published_stories = published_stories[-500:]

    store.save(seen, published_stories)
    print(
        f"[SUMMARY] candidates={len(candidates)} published={published_count} "
        f"gemini_failed={ai_failed} non_technology={non_technology} "
        f"duplicates={duplicates} telegram_failed={telegram_failed}"
    )


if __name__ == "__main__":
    main()
