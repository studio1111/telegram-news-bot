import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from .ai import process_with_gemini
from .collector import collect_feed, fetch_article_image_url, fetch_article_text
from .core import (
    build_rich_message_html,
    is_duplicate_story,
    is_new_item,
    is_recent_news,
)
from .storage import StateStore
from .telegram import publish_rich_message


GEMINI_WORKERS = 6


def _collect_recent_items(sources, seen, now):
    candidates = []
    stats = {
        "sources": len(sources),
        "feed_items": 0,
        "unseen_items": 0,
        "recent_items": 0,
        "missing_dates": 0,
        "source_errors": 0,
    }

    for source in sources:
        try:
            items = collect_feed(
                source["url"],
                source["name"],
                source.get("limit", 100),
            )
        except Exception as exc:
            stats["source_errors"] += 1
            print(f"[SOURCE_ERROR] {source['name']}: {exc}")
            continue

        stats["feed_items"] += len(items)
        unseen = 0
        recent = 0
        missing_dates = 0
        for item in items:
            if not is_new_item(item.item_id, item.url, seen):
                continue
            unseen += 1
            if item.published_at is None:
                missing_dates += 1
                continue
            if is_recent_news(item.published_at, now):
                candidates.append(item)
                recent += 1

        stats["unseen_items"] += unseen
        stats["recent_items"] += recent
        stats["missing_dates"] += missing_dates
        print(
            f"[SOURCE] {source['name']}: fetched={len(items)} "
            f"unseen={unseen} recent={recent} missing_date={missing_dates}"
        )

    print(
        f"[COLLECT] sources={stats['sources']} feeds={stats['feed_items']} "
        f"unseen={stats['unseen_items']} recent={stats['recent_items']} "
        f"missing_dates={stats['missing_dates']} errors={stats['source_errors']}"
    )

    return sorted(
        candidates,
        key=lambda item: item.published_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )


def _process_candidate(item):
    article_text = fetch_article_text(item.url)
    image_url = item.image_url or fetch_article_image_url(item.url)
    processed = process_with_gemini(
        item.title,
        item.summary,
        article_text,
    )
    return item, image_url, processed


def main():
    sources = json.loads(Path("data/sources.json").read_text(encoding="utf-8"))
    store = StateStore()
    seen = store.load()
    published_stories = store.load_records()
    now = datetime.now(timezone.utc)

    candidates = _collect_recent_items(sources, seen, now)
    published_count = 0
    ai_failed = 0
    duplicates = 0
    telegram_failed = 0

    print(
        f"[RUN] now={now.isoformat()} window_minutes=30 candidates={len(candidates)}"
    )

    # Remove already-published stories before spending network/API time on
    # article extraction and Gemini. This keeps the duplicate-only policy while
    # allowing every genuinely new story through.
    ai_candidates = []
    for item in candidates:
        story = {"title": item.title, "summary": item.summary}
        if is_duplicate_story(story, published_stories):
            duplicates += 1
            print(f"[DUPLICATE] source={item.source} url={item.url}")
            continue
        ai_candidates.append(item)

    # Gemini is the slowest external step. Process independent stories in
    # parallel so a busy 30-minute window cannot exceed the GitHub Actions
    # timeout merely because many eligible stories need translation.
    processed_results = []
    if ai_candidates:
        workers = min(GEMINI_WORKERS, len(ai_candidates))
        print(f"[AI_BATCH] candidates={len(ai_candidates)} workers={workers}")
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {
                executor.submit(_process_candidate, item): item
                for item in ai_candidates
            }
            for future in as_completed(future_map):
                item = future_map[future]
                try:
                    processed_results.append(future.result())
                except Exception as exc:
                    ai_failed += 1
                    print(
                        f"[GEMINI_ERROR] source={item.source} url={item.url}: {exc}"
                    )

    # Publish after AI processing. Keep Telegram calls in the main thread and
    # update the in-memory duplicate set after every successful publication.
    processed_results.sort(
        key=lambda result: result[0].published_at
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    for item, image_url, processed in processed_results:
        story = {"title": item.title, "summary": item.summary}
        if is_duplicate_story(story, published_stories):
            duplicates += 1
            print(f"[DUPLICATE_AFTER_AI] source={item.source} url={item.url}")
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
        f"gemini_failed={ai_failed} duplicates={duplicates} "
        f"telegram_failed={telegram_failed}"
    )


if __name__ == "__main__":
    main()
