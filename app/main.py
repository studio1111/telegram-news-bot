import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from .ai import process_with_gemini
from .collector import collect_feed, fetch_article_image_url, fetch_article_text
from .core import (
    NEWS_WINDOW_MINUTES,
    build_rich_message_html,
    is_duplicate_story,
    is_new_item,
    is_recent_news,
    is_technology_story,
)
from .storage import MAX_PUBLISHED_STORIES, StateStore
from .telegram import publish_rich_message


GEMINI_WORKERS = 6
_OLDEST = datetime.min.replace(tzinfo=timezone.utc)


def _published_key(item):
    return item.published_at or _OLDEST


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
    batch_keys = set()

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
            # The same entry can appear twice in one feed or across feeds.
            if item.item_id in batch_keys or item.url in batch_keys:
                continue
            unseen += 1
            if item.published_at is None:
                missing_dates += 1
                continue
            if is_recent_news(item.published_at, now):
                candidates.append(item)
                batch_keys.update((item.item_id, item.url))
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

    # Oldest first, so the channel reads chronologically (newest at bottom).
    return sorted(candidates, key=_published_key)


def _process_candidate(item):
    article_text = fetch_article_text(item.url)
    image_url = item.image_url or fetch_article_image_url(item.url)
    processed = process_with_gemini(
        item.title,
        item.summary,
        article_text,
    )
    return item, image_url, processed


def _story_record(item):
    return {"title": item.title, "summary": item.summary, "url": item.url}


def main():
    sources = json.loads(Path("data/sources.json").read_text(encoding="utf-8"))
    store = StateStore()
    seen = store.load()
    published_stories = store.load_records()
    now = datetime.now(timezone.utc)

    published_count = 0
    ai_failed = 0
    duplicates = 0
    telegram_failed = 0
    non_technology = 0
    publish_failed = 0
    candidates = []

    def persist():
        store.save(seen, published_stories[-MAX_PUBLISHED_STORIES:])

    try:
        candidates = _collect_recent_items(sources, seen, now)
        print(
            f"[RUN] now={now.isoformat()} window_minutes={NEWS_WINDOW_MINUTES} "
            f"candidates={len(candidates)}"
        )

        # Remove already-published stories before spending network/API time on
        # article extraction and Gemini. Duplicates are marked as seen so the
        # wider look-back window does not re-check them on every run.
        ai_candidates = []
        for item in candidates:
            if is_duplicate_story(_story_record(item), published_stories):
                duplicates += 1
                seen.update((item.item_id, item.url))
                print(f"[DUPLICATE] source={item.source} url={item.url}")
                continue
            ai_candidates.append(item)

        # Gemini is the slowest external step. Process independent stories in
        # parallel so a busy window cannot exceed the GitHub Actions timeout.
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
                        # Not marked as seen: retried on the next run while the
                        # story is still inside the look-back window.
                        ai_failed += 1
                        print(
                            f"[GEMINI_ERROR] source={item.source} url={item.url}: {exc}"
                        )

        # Publish oldest first. Telegram calls stay in the main thread and the
        # duplicate set is updated after every successful publication.
        processed_results.sort(key=lambda result: _published_key(result[0]))

        for item, image_url, processed in processed_results:
            story = _story_record(item)
            if is_duplicate_story(story, published_stories):
                duplicates += 1
                seen.update((item.item_id, item.url))
                print(f"[DUPLICATE_AFTER_AI] source={item.source} url={item.url}")
                continue

            try:
                category = str(processed.get("category", "")).strip().lower()
                tech_story = is_technology_story(
                    category,
                    item.title,
                    item.summary,
                    processed.get("article_fa", "") or "",
                )
                if not tech_story:
                    non_technology += 1
                    # Remember the rejection so Gemini is not paid again for
                    # the same story on the next run.
                    seen.update((item.item_id, item.url))
                    print(
                        f"[NON_TECHNOLOGY] source={item.source} "
                        f"category={processed.get('category', '')} url={item.url}"
                    )
                    continue

                message = build_rich_message_html(
                    processed["title_fa"],
                    processed["summary_fa"],
                    processed.get("article_fa") or processed["summary_fa"],
                    item.source,
                )
            except Exception as exc:
                publish_failed += 1
                print(f"[PROCESS_ERROR] source={item.source} url={item.url}: {exc}")
                continue

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
            del published_stories[:-MAX_PUBLISHED_STORIES]
            # Persist after every publication so a crash or cancellation
            # later in the run cannot cause already-sent stories to repeat.
            persist()
    finally:
        persist()
        print(
            f"[SUMMARY] candidates={len(candidates)} published={published_count} "
            f"gemini_failed={ai_failed} duplicates={duplicates} "
            f"telegram_failed={telegram_failed} non_technology={non_technology} "
            f"process_failed={publish_failed}"
        )


if __name__ == "__main__":
    main()
