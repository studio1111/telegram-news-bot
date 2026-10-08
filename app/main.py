import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import time

import requests

from .ai import process_with_gemini
from .collector import collect_feed, fetch_article_image_url, fetch_article_text  # noqa: F401
from .core import build_rich_message_html, is_duplicate_story, is_new_item
from .storage import MAX_PUBLISHED_STORIES, StateStore, StateStoreError
from .telegram import publish_rich_message


GEMINI_WORKERS = 8
IMAGE_WORKERS = 8
# Safety deadline only (workflow timeout is 30 min). State is saved after every
# published item, so reaching the deadline never loses sent stories.
RUN_DEADLINE_SECONDS = 1500
TECHNOLOGY_CATEGORY = "technology"
PERSIAN_FALLBACK_SOURCES = {"Digiato", "Vigiato"}
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
    # Image availability first: a duplicate with a picture beats one without.
    # Then English sources beat Persian fallbacks, then the newest story wins.
    return (
        bool(item.image_url),
        item.source not in PERSIAN_FALLBACK_SOURCES,
        _published_key(item),
    )


def _group_duplicate_candidates(candidates):
    """Collapse duplicate stories inside this batch and keep the best copy of each."""
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
            if is_duplicate_story(_story_record(candidates[left]), [_story_record(candidates[right])]):
                union(left, right)

    groups = {}
    for index, item in enumerate(candidates):
        groups.setdefault(find(index), []).append(item)

    selected, dropped = [], []
    for group in groups.values():
        winner = max(group, key=_image_candidate_score)
        selected.append(winner)
        dropped.extend(item for item in group if item is not winner)
        if len(group) > 1:
            print(f"[DUPLICATE_GROUP] size={len(group)} selected={winner.source} image={'yes' if winner.image_url else 'no'}")
    return sorted(selected, key=_published_key), dropped


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


def _collect_new_items(sources, seen):
    """Return every unseen item from every enabled feed. No age or count limit."""
    candidates = []
    batch_keys = set()
    for source in sources:
        if source.get("fallback_only"):
            # Persian fallback feeds mostly repeat English stories; they are skipped.
            print(f"[SKIP_FALLBACK] {source['name']}")
            continue
        try:
            items = collect_feed(source["url"], source["name"], source.get("limit", 100))
        except Exception as exc:
            print(f"[SOURCE_ERROR] {source['name']}: {exc}")
            continue
        new_count = 0
        for item in items:
            if not is_new_item(item.item_id, item.url, seen) or item.item_id in batch_keys or item.url in batch_keys:
                continue
            candidates.append(item)
            batch_keys.update((item.item_id, item.url))
            new_count += 1
        print(f"[SOURCE] {source['name']}: fetched={len(items)} new={new_count}")
    print(f"[COLLECT] new_total={len(candidates)}")
    return sorted(candidates, key=_published_key)


def _process_candidate(item):
    # Gemini receives only feed text; it classifies the story and translates it.
    return item, item.image_url, process_with_gemini(item.title, item.summary, item.summary)


def _process_all(candidates, deadline):
    """Run Gemini on every candidate. Returns (results, failed_count)."""
    results = []
    if not candidates or time.monotonic() >= deadline:
        return results, 0
    failed = 0
    executor = ThreadPoolExecutor(max_workers=min(GEMINI_WORKERS, len(candidates)))
    future_map = {executor.submit(_process_candidate, item): item for item in candidates}
    try:
        while future_map and time.monotonic() < deadline:
            remaining = max(0.01, deadline - time.monotonic())
            try:
                for future in as_completed(list(future_map), timeout=remaining):
                    item = future_map.pop(future)
                    try:
                        results.append(future.result())
                    except Exception as exc:
                        failed += 1
                        print(f"[GEMINI_ERROR] source={item.source} url={item.url}: {exc}")
                    if time.monotonic() >= deadline:
                        break
            except FuturesTimeout:
                print("[DEADLINE] AI processing deadline reached")
    finally:
        for future in future_map:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
    return results, failed


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


def _mark_sent(record, seen, published_stories):
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
        except requests.Timeout as exc:
            # Telegram may already have accepted the post; retrying could duplicate it.
            print(f"[OUTBOX_TIMEOUT_ASSUMED_SENT] key={record['key']}: {exc}")
            _mark_sent(record, seen, published_stories)
            _save_state(store, seen, published_stories, outbox)
            continue
        except Exception as exc:
            print(f"[OUTBOX_ERROR] key={record['key']}: {exc}")
            continue
        _mark_sent(record, seen, published_stories)
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

    deadline = time.monotonic() + RUN_DEADLINE_SECONDS
    published_count = ai_failed = duplicates = telegram_failed = publish_failed = not_technology = 0
    candidates = []

    def persist():
        _save_state(store, seen, published_stories, outbox)

    try:
        _retry_outbox(store, seen, published_stories, outbox, deadline)
        candidates = _collect_new_items(sources, seen)
        candidates = _hydrate_missing_images(candidates, deadline)
        candidates, grouped_out = _group_duplicate_candidates(candidates)
        for item in grouped_out:
            seen.update((item.item_id, item.url))
        print(f"[RUN] candidates={len(candidates)} deadline_seconds={RUN_DEADLINE_SECONDS}")

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

        processed_results, ai_failed = _process_all(ai_candidates, deadline)
        processed_results.sort(key=lambda result: _published_key(result[0]))
        for item, image_url, processed in processed_results:
            if time.monotonic() >= deadline:
                print("[DEADLINE] reached before Telegram publishing")
                break
            # Only technology stories are published; the rest are marked seen.
            if processed.get("category") != TECHNOLOGY_CATEGORY:
                not_technology += 1
                seen.update((item.item_id, item.url))
                print(f"[SKIP_NOT_TECH] category={processed.get('category')} source={item.source} url={item.url}")
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
            except requests.Timeout as exc:
                # Ambiguous: Telegram may have posted it. Treat as sent to avoid duplicates.
                print(f"[TELEGRAM_TIMEOUT_ASSUMED_SENT] source={item.source} url={item.url}: {exc}")
            except Exception as exc:
                telegram_failed += 1
                print(f"[TELEGRAM_ERROR] source={item.source} url={item.url}: {exc}")
                continue

            for record in outbox:
                if record.get("key") == key:
                    _mark_sent(record, seen, published_stories)
            seen.update((item.item_id, item.url))
            published_stories.append(rendered_story)
            published_count += 1
            persist()
            print(f"[PUBLISHED] source={item.source} image={'yes' if image_url else 'no'} url={item.url}")
    finally:
        persist()
        print(
            f"[SUMMARY] candidates={len(candidates)} published={published_count} "
            f"not_technology={not_technology} gemini_failed={ai_failed} "
            f"duplicates={duplicates} telegram_failed={telegram_failed} process_failed={publish_failed}"
        )


if __name__ == "__main__":
    main()
