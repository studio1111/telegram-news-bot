import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import time

import requests

from .ai import find_duplicate_groups, process_with_gemini
from .collector import collect_feed, fetch_article_image_url, fetch_article_text
from .core import NEWS_WINDOW_MINUTES, build_rich_message_html, is_advertisement, is_allowed_news_source, is_duplicate_story, is_new_item, is_recent_news
from .storage import MAX_PUBLISHED_STORIES, StateStore, StateStoreError
from .telegram import publish_rich_message


GEMINI_WORKERS = 8
IMAGE_WORKERS = 8
# Safety net: leave enough time for feed collection, image recovery, Gemini and Telegram.
RUN_DEADLINE_SECONDS = 1200
NEWS_WINDOW = max(NEWS_WINDOW_MINUTES, 90)
# Gemini duplicate grouping: new stories per request and published stories shown as context.
SEMANTIC_CHUNK = 80
SEMANTIC_HISTORY = 150
_OLDEST = datetime.min.replace(tzinfo=timezone.utc)


def _published_key(item):
    return item.published_at or _OLDEST


def _outbox_key(item):
    return "url:" + hashlib.sha256(item.url.encode("utf-8")).hexdigest()



def _story_record(item, processed=None):
    record = {"title": item.title, "summary": item.summary, "url": item.url}
    if processed:
        record["display_title"] = processed.get("title_fa", "")
        record["display_summary"] = processed.get("summary_fa", "")
    return record


def _image_candidate_score(item):
    # All three configured sources have equal source priority.
    # Prefer an available image, then the earliest publication.
    return (bool(item.image_url), _published_key(item))



def _delivery_unknown(exc):
    """A read timeout means Telegram may already have delivered the message.

    Retrying it would post the story twice, so it is treated as delivered.
    Connection errors and API rejections are NOT ambiguous and stay retryable.
    """
    timeout = requests.exceptions.ReadTimeout
    return isinstance(exc, timeout) or isinstance(exc.__cause__, timeout)


def _prioritize_duplicate_candidates(candidates):
    """Collapse transitive duplicate groups and choose the best source once."""
    candidates = list(candidates)
    parent = list(range(len(candidates)))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left, right):
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left in range(len(candidates)):
        for right in range(left + 1, len(candidates)):
            if is_duplicate_story(
                _story_record(candidates[left]),
                [_story_record(candidates[right])],
            ):
                union(left, right)

    groups = {}
    for index, item in enumerate(candidates):
        groups.setdefault(find(index), []).append(item)

    selected = []
    for group in groups.values():
        winner = max(group, key=_image_candidate_score)
        selected.append(winner)
        if len(group) > 1:
            print(
                f"[DUPLICATE_PRIORITY] group={len(group)} selected={winner.source} "
                f"image={'yes' if winner.image_url else 'no'}"
            )
    return sorted(selected, key=_published_key)


def _semantic_dedup(candidates, history, seen, deadline):
    """Drop candidates that report the same event as a published story or as a better candidate.

    The word-overlap filter cannot match paraphrases or Persian vs English
    rewrites of the same event, so Gemini groups them. If Gemini fails the
    candidates are kept (the word-overlap filters still apply).
    """
    if not candidates or (len(candidates) < 2 and not history):
        return candidates, 0
    if time.monotonic() >= deadline:
        return candidates, 0
    recent = list(history)[-SEMANTIC_HISTORY:]
    kept = []
    removed = 0
    for start in range(0, len(candidates), SEMANTIC_CHUNK):
        chunk = candidates[start:start + SEMANTIC_CHUNK]
        if time.monotonic() >= deadline:
            kept.extend(candidates[start:])
            break
        context = recent + [_story_record(item) for item in kept][-SEMANTIC_CHUNK:]
        records = [dict(_story_record(item), source=item.source) for item in chunk]
        try:
            groups = find_duplicate_groups(records, context)
        except Exception as exc:
            print(f"[SEMANTIC_DEDUP_ERROR] {exc}")
            kept.extend(chunk)
            continue
        drop = set()
        for group in groups:
            members = [int(ident[1:]) - 1 for ident in group if ident[0] == "C"]
            if not members:
                continue
            if any(ident[0] == "H" for ident in group):
                drop.update(members)
            else:
                winner = max(members, key=lambda index: _image_candidate_score(chunk[index]))
                drop.update(index for index in members if index != winner)
        for index, item in enumerate(chunk):
            if index in drop:
                removed += 1
                seen.update((item.item_id, item.url))
                print(f"[SEMANTIC_DUPLICATE] source={item.source} url={item.url}")
            else:
                kept.append(item)
    return kept, removed


def _hydrate_missing_images(candidates, deadline):
    missing = [item for item in candidates if not item.image_url]
    if not missing:
        return candidates
    hydrated = {item.url: item for item in candidates}
    executor = ThreadPoolExecutor(max_workers=min(IMAGE_WORKERS, len(missing)))
    future_map = {executor.submit(fetch_article_image_url, item.url): item for item in missing}
    try:
        while future_map and time.monotonic() < deadline:
            remaining = max(0.01, deadline - time.monotonic())
            try:
                for future in as_completed(list(future_map), timeout=remaining):
                    item = future_map.pop(future)
                    try:
                        image_url = future.result()
                    except Exception as exc:
                        print(f"[IMAGE_ERROR] source={item.source} url={item.url}: {exc}")
                        image_url = ""
                    if image_url:
                        hydrated[item.url] = replace(item, image_url=image_url)
                        print(f"[IMAGE_FOUND] source={item.source} url={item.url}")
                    if time.monotonic() >= deadline:
                        break
            except FuturesTimeout:
                print("[DEADLINE] image recovery deadline reached")
    finally:
        for future in future_map:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
    return [hydrated[item.url] for item in candidates]


def _source_allowed(source):
    return is_allowed_news_source(source)

def _item_is_publishable(item):
    return _source_allowed(item.source) and not is_advertisement(item.title, item.summary, item.categories)

def _collect_recent_items(sources, seen, now):
    """Collect every unseen, recent story from the three allowed sources, excluding advertisements."""
    candidates = []
    stats = {"sources": len(sources), "feed_items": 0, "unseen_items": 0, "recent_items": 0, "missing_dates": 0, "source_errors": 0, "advertisements": 0, "source_filtered": 0}
    batch_keys = set()
    for source in sources:
        try:
            items = collect_feed(source["url"], source["name"], source.get("limit", 100))
        except Exception as exc:
            stats["source_errors"] += 1
            print(f"[SOURCE_ERROR] {source['name']}: {exc}")
            continue
        stats["feed_items"] += len(items)
        unseen = recent = missing_dates = 0
        for item in items:
            if not is_new_item(item.item_id, item.url, seen) or item.item_id in batch_keys or item.url in batch_keys:
                continue
            unseen += 1
            if item.published_at is None:
                missing_dates += 1
                continue
            if not _source_allowed(item.source):
                stats["source_filtered"] += 1
                continue
            if is_advertisement(item.title, item.summary, item.categories):
                stats["advertisements"] += 1
                seen.update((item.item_id, item.url))
                continue
            if is_recent_news(item.published_at, now, NEWS_WINDOW):
                candidates.append(item)
                batch_keys.update((item.item_id, item.url))
                recent += 1
        stats["unseen_items"] += unseen
        stats["recent_items"] += recent
        stats["missing_dates"] += missing_dates
        print(f"[SOURCE] {source['name']}: fetched={len(items)} unseen={unseen} recent={recent} missing_date={missing_dates}")
    print(f"[COLLECT] sources={stats['sources']} feeds={stats['feed_items']} unseen={stats['unseen_items']} recent={stats['recent_items']} missing_dates={stats['missing_dates']} errors={stats['source_errors']}")
    return sorted(candidates, key=_published_key)


def _process_candidate(item):
    # Image recovery happens before AI processing. Gemini receives only feed text.
    article_text = item.summary
    return item, item.image_url, process_with_gemini(item.title, item.summary, article_text)


def _dedup_history(published_stories, outbox):
    history = list(published_stories)
    for record in outbox:
        if record.get("status") != "sent" or not record.get("url"):
            continue
        history.append({
            "title": record.get("title", ""),
            "summary": record.get("summary", ""),
            "url": record.get("url", ""),
            "display_title": record.get("display_title", ""),
            "display_summary": record.get("display_summary", ""),
        })
    return history


def _save_state(store, seen, published_stories, outbox):
    try:
        store.save(seen, published_stories[-MAX_PUBLISHED_STORIES:], outbox)
    except TypeError as exc:
        if "positional" not in str(exc) and "argument" not in str(exc):
            raise
        store.save(seen, published_stories[-MAX_PUBLISHED_STORIES:])


def _retry_outbox(store, seen, published_stories, outbox, deadline):
    recovered = 0
    for record in list(outbox):
        if time.monotonic() >= deadline:
            print("[DEADLINE] reached while retrying outbox")
            break
        if record.get("status") != "pending":
            continue
        try:
            publish_rich_message(record["message"], record.get("image_url", ""))
        except Exception as exc:
            if _delivery_unknown(exc):
                print(f"[OUTBOX_AMBIGUOUS] key={record['key']}: {exc}; treating as delivered to avoid a duplicate")
            else:
                print(f"[OUTBOX_ERROR] key={record['key']}: {exc}")
                continue
        record["status"] = "sent"
        if record.get("url"):
            seen.add(record["url"])
            published_stories.append({
                "title": record.get("title", ""),
                "summary": record.get("summary", ""),
                "url": record["url"],
                "display_title": record.get("display_title", ""),
                "display_summary": record.get("display_summary", ""),
            })
        _save_state(store, seen, published_stories, outbox)
        recovered += 1
        print(f"[OUTBOX_SENT] key={record['key']}")
    return recovered


def main():
    sources = json.loads(Path("data/sources.json").read_text(encoding="utf-8"))
    store = StateStore()
    try:
        seen = store.load()
        published_stories = store.load_records()
        outbox = store.load_outbox() if hasattr(store, "load_outbox") else []
    except StateStoreError as exc:
        print(f"[STATE_ERROR] refusing to publish with untrusted state: {exc}")
        raise

    now = datetime.now(timezone.utc)
    deadline = time.monotonic() + RUN_DEADLINE_SECONDS
    published_count = ai_failed = duplicates = telegram_failed = publish_failed = 0
    candidates = []

    def persist():
        _save_state(store, seen, published_stories, outbox)

    run_completed = False
    try:
        _retry_outbox(store, seen, published_stories, outbox, deadline)
        candidates = _collect_recent_items(sources, seen, now)
        candidates = _hydrate_missing_images(candidates, deadline)
        candidates = _prioritize_duplicate_candidates(candidates)
        print(f"[RUN] now={now.isoformat()} window_minutes={NEWS_WINDOW} candidates={len(candidates)} deadline_seconds={RUN_DEADLINE_SECONDS}")

        # Layer 1: same URL / word-overlap match against everything already published.
        ai_candidates = []
        for item in candidates:
            if time.monotonic() >= deadline:
                print("[DEADLINE] reached before AI processing")
                break
            if is_duplicate_story(_story_record(item), _dedup_history(published_stories, outbox)):
                duplicates += 1
                seen.update((item.item_id, item.url))
                continue
            ai_candidates.append(item)

        # Layer 2: Gemini groups paraphrases and Persian/English rewrites of one event.
        ai_candidates, semantic_duplicates = _semantic_dedup(
            ai_candidates, _dedup_history(published_stories, outbox), seen, deadline
        )
        duplicates += semantic_duplicates

        processed_results = []
        if ai_candidates and time.monotonic() < deadline:
            workers = min(GEMINI_WORKERS, len(ai_candidates))
            executor = ThreadPoolExecutor(max_workers=workers)
            future_map = {executor.submit(_process_candidate, item): item for item in ai_candidates}
            try:
                while future_map and time.monotonic() < deadline:
                    remaining = max(0.01, deadline - time.monotonic())
                    try:
                        for future in as_completed(list(future_map), timeout=remaining):
                            item = future_map.pop(future)
                            try:
                                processed_results.append(future.result())
                            except Exception as exc:
                                ai_failed += 1
                                print(f"[GEMINI_ERROR] source={item.source} url={item.url}: {exc}")
                            if time.monotonic() >= deadline:
                                break
                    except FuturesTimeout:
                        print("[DEADLINE] AI processing deadline reached")
            finally:
                for future in future_map:
                    future.cancel()
                executor.shutdown(wait=False, cancel_futures=True)

        processed_results.sort(key=lambda result: _published_key(result[0]))
        for item, image_url, processed in processed_results:
            if time.monotonic() >= deadline:
                print("[DEADLINE] reached before Telegram publishing")
                break

            # Layer 3: re-check against history, including stories sent earlier in this run,
            # using both the source text and the Persian text that will actually be posted.
            story = _story_record(item)
            if is_duplicate_story(story, _dedup_history(published_stories, outbox)):
                duplicates += 1
                seen.update((item.item_id, item.url))
                continue
            rendered_story = _story_record(item, processed)
            if is_duplicate_story(rendered_story, _dedup_history(published_stories, outbox)):
                duplicates += 1
                seen.update((item.item_id, item.url))
                continue
            try:
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

            key = _outbox_key(item)
            if not any(record.get("key") == key and record.get("status") == "pending" for record in outbox):
                outbox.append({
                    "key": key,
                    "url": item.url,
                    "title": item.title,
                    "summary": item.summary,
                    "display_title": processed.get("title_fa", ""),
                    "display_summary": processed.get("summary_fa", ""),
                    "message": message,
                    "image_url": image_url or "",
                    "status": "pending",
                })
                persist()

            try:
                publish_rich_message(message, image_url)
            except Exception as exc:
                if _delivery_unknown(exc):
                    print(f"[TELEGRAM_AMBIGUOUS] source={item.source} url={item.url}: {exc}; treating as delivered to avoid a duplicate")
                else:
                    telegram_failed += 1
                    print(f"[TELEGRAM_ERROR] source={item.source} url={item.url}: {exc}")
                    continue

            for record in outbox:
                if record.get("key") == key:
                    record["status"] = "sent"
            published_count += 1
            seen.update((item.item_id, item.url))
            published_stories.append(rendered_story)
            del published_stories[:-MAX_PUBLISHED_STORIES:]
            persist()
            print(f"[PUBLISHED] source={item.source} image={'yes' if image_url else 'no'} url={item.url}")
        run_completed = True
    finally:
        if run_completed and hasattr(store, "set_last_run_at"):
            store.set_last_run_at(now.isoformat())
        persist()
        print(f"[SUMMARY] candidates={len(candidates)} published={published_count} gemini_failed={ai_failed} duplicates={duplicates} telegram_failed={telegram_failed} process_failed={publish_failed} ")


if __name__ == "__main__":
    main()
