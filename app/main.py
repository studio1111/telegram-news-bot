import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import time

import requests

from .ai import find_duplicate_groups, process_with_gemini
from .semantic_dedup import find_semantic_relations
from .collector import NewsItem, collect_feed, fetch_article_image_url, fetch_article_published_at, fetch_article_text, validate_image_url
from .core import MAX_BACKLOG_AGE_HOURS, NEWS_WINDOW_MINUTES, build_rich_message_html, is_advertisement, is_allowed_news_source, is_backlog_eligible_news, is_duplicate_story, is_new_item, is_recent_news
from .storage import MAX_PUBLISHED_STORIES, StateStore, StateStoreError
from .telegram import publish_rich_message


GEMINI_WORKERS = 8
IMAGE_WORKERS = 8
# Safety net: leave enough time for feed collection, image recovery, Gemini and Telegram.
RUN_DEADLINE_SECONDS = 3300
NEWS_WINDOW = max(NEWS_WINDOW_MINUTES, 90)
# Gemini duplicate grouping: new stories per request and published stories shown as context.
SEMANTIC_CHUNK = 80
SEMANTIC_HISTORY = 150
_OLDEST = datetime.min.replace(tzinfo=timezone.utc)


def _published_key(item):
    return item.published_at or _OLDEST


def _sort_newest_first(items):
    """Prioritize dated stories from newest to oldest; undated stories stay last."""
    return sorted(
        items,
        key=lambda item: (item.published_at is not None, _published_key(item)),
        reverse=True,
    )


def _outbox_key(item):
    return "url:" + hashlib.sha256(item.url.encode("utf-8")).hexdigest()


def _pending_news_key(item):
    return item.url.strip() or ("id:" + item.item_id)


def _pending_news_record(item):
    return {
        "item_id": item.item_id,
        "title": item.title,
        "url": item.url,
        "summary": item.summary,
        "source": item.source,
        "image_url": item.image_url or "",
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "categories": list(item.categories or ()),
    }


def _pending_news_item(record):
    url = record.get("url", "") if isinstance(record.get("url", ""), str) else ""
    if not url.strip():
        raise StateStoreError("pending news record has an invalid URL")
    published_at = record.get("published_at")
    parsed_date = None
    if isinstance(published_at, str) and published_at.strip():
        try:
            parsed_date = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            if parsed_date.tzinfo is None:
                parsed_date = parsed_date.replace(tzinfo=timezone.utc)
            parsed_date = parsed_date.astimezone(timezone.utc)
        except ValueError:
            parsed_date = None
    categories = record.get("categories", [])
    if not isinstance(categories, (tuple, list)):
        categories = ()
    return NewsItem(
        item_id=record.get("item_id") if isinstance(record.get("item_id"), str) and record.get("item_id") else url.strip(),
        title=record.get("title") if isinstance(record.get("title"), str) else "",
        url=url.strip(),
        summary=record.get("summary") if isinstance(record.get("summary"), str) else "",
        source=record.get("source") if isinstance(record.get("source"), str) else "",
        image_url=record.get("image_url") if isinstance(record.get("image_url"), str) else "",
        published_at=parsed_date,
        categories=tuple(value for value in categories if isinstance(value, str)),
    )


def _merge_pending_news_item(existing, incoming):
    """Refresh queued metadata without losing an already recovered image or date."""
    return replace(
        existing,
        item_id=existing.item_id or incoming.item_id,
        title=incoming.title or existing.title,
        summary=incoming.summary or existing.summary,
        source=incoming.source or existing.source,
        image_url=incoming.image_url or existing.image_url,
        published_at=existing.published_at or incoming.published_at,
        categories=incoming.categories or existing.categories,
    )


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
    return _sort_newest_first(selected)


def _semantic_dedup(candidates, history, seen, deadline):
    """Use embeddings first and keep the previous Gemini grouping as a safe fallback."""
    if not candidates or time.monotonic() >= deadline:
        return candidates, 0
    recent = list(history)[-SEMANTIC_HISTORY:]
    records = [_story_record(item) for item in candidates]
    try:
        relations = find_semantic_relations(records, recent)
    except Exception as exc:
        print(f"[SEMANTIC_DEDUP_ERROR] {exc}; falling back to legacy Gemini grouping")
        try:
            groups = find_duplicate_groups(records, recent)
        except Exception as fallback_exc:
            print(f"[LEGACY_SEMANTIC_DEDUP_ERROR] {fallback_exc}")
            return candidates, 0
        drop = set()
        for group in groups:
            members = [int(value[1:]) - 1 for value in group if isinstance(value, str) and value.startswith("C")]
            if any(isinstance(value, str) and value.startswith("H") for value in group):
                drop.update(members)
            elif members:
                winner = max(members, key=lambda index: _image_candidate_score(candidates[index]))
                drop.update(index for index in members if index != winner)
        kept = []
        for index, item in enumerate(candidates):
            if index in drop:
                seen.update((item.item_id, item.url))
                print(f"[SEMANTIC_DUPLICATE] source={item.source} url={item.url}")
            else:
                kept.append(item)
        return kept, len(candidates) - len(kept)

    drop = set()
    for relation in relations:
        candidate_indexes = relation.get("candidate_indexes", [])
        relation_type = relation.get("relation_type", "DISTINCT")
        confidence = float(relation.get("confidence", 0.0) or 0.0)
        if relation_type == "DUPLICATE" and confidence >= 0.70:
            if relation.get("history_match"):
                drop.update(candidate_indexes)
            elif candidate_indexes:
                winner = max(
                    candidate_indexes,
                    key=lambda index: _image_candidate_score(candidates[index]),
                )
                drop.update(index for index in candidate_indexes if index != winner)
        elif relation_type == "UPDATE" and confidence >= 0.70:
            print(
                f"[SEMANTIC_UPDATE] candidates={candidate_indexes} "
                f"confidence={confidence:.2f}"
            )

    kept = []
    removed = 0
    for index, item in enumerate(candidates):
        if index in drop:
            removed += 1
            seen.update((item.item_id, item.url))
            print(f"[SEMANTIC_DUPLICATE] source={item.source} url={item.url}")
        else:
            kept.append(item)
    return kept, removed

def _hydrate_missing_images(candidates, deadline):
    """Verify every image and recover missing/invalid images from the article page."""
    if not candidates:
        return candidates
    hydrated = {}
    executor = ThreadPoolExecutor(max_workers=min(IMAGE_WORKERS, len(candidates)))

    def resolve(item):
        if item.image_url and validate_image_url(item.image_url):
            return item
        return replace(item, image_url=fetch_article_image_url(item.url))

    future_map = {executor.submit(resolve, item): item for item in candidates}
    try:
        while future_map and time.monotonic() < deadline:
            remaining = max(0.01, deadline - time.monotonic())
            try:
                for future in as_completed(list(future_map), timeout=remaining):
                    item = future_map.pop(future)
                    try:
                        resolved = future.result()
                    except Exception as exc:
                        print(f"[IMAGE_ERROR] source={item.source} url={item.url}: {exc}")
                        resolved = replace(item, image_url="")
                    if resolved.image_url:
                        hydrated[item.url] = resolved
                        if resolved.image_url != item.image_url:
                            print(f"[IMAGE_FOUND] source={item.source} url={item.url}")
                    else:
                        hydrated[item.url] = resolved
                    if time.monotonic() >= deadline:
                        break
            except FuturesTimeout:
                print("[DEADLINE] image recovery deadline reached")
    finally:
        for future in future_map:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)

    return [
        hydrated[item.url]
        for item in candidates
        if item.url in hydrated and hydrated[item.url].image_url
    ]


def _source_allowed(source):
    return is_allowed_news_source(source)

def _item_is_publishable(item):
    return not is_advertisement(item.title, item.summary, item.categories)

def _collect_recent_items(sources, seen, now):
    """Collect unseen items within the backlog horizon; main defers stale discoveries one run."""
    candidates = []
    stats = {"sources": len(sources), "feed_items": 0, "unseen_items": 0, "eligible_items": 0, "missing_dates": 0, "source_errors": 0, "advertisements": 0, "source_filtered": 0}
    batch_keys = set()
    for source in sources:
        try:
            items = collect_feed(source["url"], source["name"], source.get("limit", 100))
        except Exception as exc:
            stats["source_errors"] += 1
            print(f"[SOURCE_ERROR] {source['name']}: {exc}")
            continue
        stats["feed_items"] += len(items)
        unseen = eligible = missing_dates = 0
        for item in items:
            if not is_new_item(item.item_id, item.url, seen) or item.item_id in batch_keys or item.url in batch_keys:
                continue
            unseen += 1
            if item.published_at is None:
                try:
                    recovered_date = fetch_article_published_at(item.url)
                except Exception as exc:
                    print(f"[DATE_RECOVERY_ERROR] source={item.source} url={item.url}: {exc}")
                    recovered_date = None
                if recovered_date is not None:
                    item = replace(item, published_at=recovered_date)
                    print(f"[DATE_RECOVERED] source={item.source} published={recovered_date.isoformat()} url={item.url}")
                else:
                    missing_dates += 1
                    continue
            if is_advertisement(item.title, item.summary, item.categories):
                stats["advertisements"] += 1
                seen.update((item.item_id, item.url))
                continue
            if is_backlog_eligible_news(item.published_at, now, MAX_BACKLOG_AGE_HOURS):
                candidates.append(item)
                batch_keys.update((item.item_id, item.url))
                eligible += 1
        stats["unseen_items"] += unseen
        stats["eligible_items"] += eligible
        stats["missing_dates"] += missing_dates
        print(f"[SOURCE] {source['name']}: fetched={len(items)} unseen={unseen} eligible={eligible} missing_date={missing_dates}")
    print(f"[COLLECT] sources={stats['sources']} feeds={stats['feed_items']} unseen={stats['unseen_items']} eligible={stats['eligible_items']} missing_dates={stats['missing_dates']} errors={stats['source_errors']}")
    return _sort_newest_first(candidates)


def _process_candidate(item):
    # Image recovery happens before AI processing. Gemini should receive the cleaned
    # article body when available, not only the short RSS summary.
    article_text = fetch_article_text(item.url) or item.summary
    return item, item.image_url, process_with_gemini(item.title, item.summary, article_text)


def _deduplicate_history_records(records):
    """Clean duplicate historical stories before they become dedup context."""
    cleaned = []
    removed = 0
    for record in records:
        if any(is_duplicate_story(record, [existing]) for existing in cleaned):
            removed += 1
            print(f"[HISTORY_DUPLICATE] url={record.get('url', '')}")
            continue
        cleaned.append(record)
    return cleaned, removed


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


def _save_state(store, seen, published_stories, outbox, pending_news=None):
    records = published_stories[-MAX_PUBLISHED_STORIES:]
    try:
        store.save(seen, records, outbox, pending_news)
    except TypeError as exc:
        if "positional" not in str(exc) and "argument" not in str(exc):
            raise
        try:
            store.save(seen, records, outbox)
        except TypeError as nested_exc:
            if "positional" not in str(nested_exc) and "argument" not in str(nested_exc):
                raise
            store.save(seen, records)


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
    for source in sources:
        source["limit"] = 0
    store = StateStore()
    try:
        seen = store.load()
        published_stories = store.load_records()
        published_stories, historical_duplicates = _deduplicate_history_records(published_stories)
        outbox = store.load_outbox() if hasattr(store, "load_outbox") else []
        pending_records = store.load_pending_news() if hasattr(store, "load_pending_news") else []
        pending_news = {}
        for record in pending_records:
            item = _pending_news_item(record)
            if is_new_item(item.item_id, item.url, seen):
                pending_news[_pending_news_key(item)] = item
    except StateStoreError as exc:
        print(f"[STATE_ERROR] refusing to publish with untrusted state: {exc}")
        raise

    now = datetime.now(timezone.utc)
    deadline = time.monotonic() + RUN_DEADLINE_SECONDS
    published_count = ai_failed = duplicates = telegram_failed = publish_failed = 0
    duplicates = historical_duplicates
    candidates = []

    def persist():
        # Once a story is published or confidently classified as a duplicate, it no
        # longer belongs in the retry queue. Unresolved items stay durable across runs.
        for key, item in list(pending_news.items()):
            if not is_new_item(item.item_id, item.url, seen):
                del pending_news[key]
        _save_state(
            store,
            seen,
            published_stories,
            outbox,
            [_pending_news_record(item) for item in pending_news.values()],
        )

    try:
        _retry_outbox(store, seen, published_stories, outbox, deadline)

        # A delivered outbox item may have become seen during retry. Remove it before
        # identifying which items were already queued at the beginning of this cycle.
        for key, item in list(pending_news.items()):
            if not is_new_item(item.item_id, item.url, seen):
                del pending_news[key]
        queued_before_fetch = set(pending_news)

        collected_items = _collect_recent_items(sources, seen, now)
        fresh_discovered_keys = set()
        deferred_new_items = 0
        for item in collected_items:
            key = _pending_news_key(item)
            if key in pending_news:
                pending_news[key] = _merge_pending_news_item(pending_news[key], item)
                continue

            pending_news[key] = item
            if is_recent_news(item.published_at, now, NEWS_WINDOW):
                fresh_discovered_keys.add(key)
            else:
                # First discovery outside the fresh window is persisted now and
                # becomes eligible on the next workflow run, even if it ages further.
                deferred_new_items += 1

        eligible_keys = queued_before_fetch | fresh_discovered_keys
        candidates = _sort_newest_first([
            pending_news[key]
            for key in eligible_keys
            if key in pending_news
            and is_new_item(pending_news[key].item_id, pending_news[key].url, seen)
        ])

        # Persist freshly discovered stale items before any image or AI work so a
        # timeout, API failure, or process interruption cannot make them disappear.
        persist()
        print(
            f"[QUEUE] existing={len(queued_before_fetch)} deferred_new={deferred_new_items} "
            f"pending={len(pending_news)} eligible_now={len(candidates)}"
        )

        hydrated_candidates = _hydrate_missing_images(candidates, deadline)
        for item in hydrated_candidates:
            pending_news[_pending_news_key(item)] = item

        candidates = _prioritize_duplicate_candidates(hydrated_candidates)
        selected_keys = {_pending_news_key(item) for item in candidates}
        # A duplicate loser with a verified image is terminal; a no-image item is
        # not returned by hydration and deliberately remains queued for recovery.
        for item in hydrated_candidates:
            if _pending_news_key(item) not in selected_keys:
                seen.update((item.item_id, item.url))

        print(
            f"[RUN] now={now.isoformat()} fresh_window_minutes={NEWS_WINDOW} "
            f"backlog_window_hours={MAX_BACKLOG_AGE_HOURS} candidates={len(candidates)} "
            f"deadline_seconds={RUN_DEADLINE_SECONDS}"
        )

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

        # Layer 2: Gemini Embedding 2 retrieves likely matches, then Gemini verifies
        # the real-world event and separates DUPLICATE from UPDATE.
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

        processed_results.sort(
            key=lambda result: (result[0].published_at is not None, _published_key(result[0])),
            reverse=True,
        )
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
    finally:
        persist()
        print(f"[SUMMARY] candidates={len(candidates)} published={published_count} gemini_failed={ai_failed} duplicates={duplicates} telegram_failed={telegram_failed} process_failed={publish_failed} ")


if __name__ == "__main__":
    main()
