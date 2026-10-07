import json
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
from datetime import datetime, timezone
from pathlib import Path
import time

from .ai import process_with_gemini
from .collector import collect_feed, fetch_article_image_url, fetch_article_text
from .core import NEWS_WINDOW_MINUTES, build_rich_message_html, is_duplicate_story, is_new_item, is_recent_news, is_technology_story
from .storage import MAX_PUBLISHED_STORIES, StateStore, StateStoreError
from .telegram import publish_rich_message


GEMINI_WORKERS=4
MAX_CANDIDATES=20
RUN_DEADLINE_SECONDS=720
_OLDEST=datetime.min.replace(tzinfo=timezone.utc)


def _published_key(item): return item.published_at or _OLDEST


def _collect_recent_items(sources, seen, now):
    candidates=[]; stats={"sources":len(sources),"feed_items":0,"unseen_items":0,"recent_items":0,"missing_dates":0,"source_errors":0}; batch_keys=set()
    for source in sources:
        try: items=collect_feed(source["url"],source["name"],source.get("limit",100))
        except Exception as exc: stats["source_errors"]+=1; print(f"[SOURCE_ERROR] {source['name']}: {exc}"); continue
        stats["feed_items"]+=len(items); unseen=recent=missing_dates=0
        for item in items:
            if not is_new_item(item.item_id,item.url,seen) or item.item_id in batch_keys or item.url in batch_keys: continue
            unseen+=1
            if item.published_at is None: missing_dates+=1; continue
            if is_recent_news(item.published_at,now): candidates.append(item); batch_keys.update((item.item_id,item.url)); recent+=1
        stats["unseen_items"]+=unseen; stats["recent_items"]+=recent; stats["missing_dates"]+=missing_dates
        print(f"[SOURCE] {source['name']}: fetched={len(items)} unseen={unseen} recent={recent} missing_date={missing_dates}")
        if len(candidates)>=MAX_CANDIDATES: break
    print(f"[COLLECT] sources={stats['sources']} feeds={stats['feed_items']} unseen={stats['unseen_items']} recent={stats['recent_items']} missing_dates={stats['missing_dates']} errors={stats['source_errors']}")
    return sorted(candidates,key=_published_key)[:MAX_CANDIDATES]


def _process_candidate(item):
    article_text=fetch_article_text(item.url); image_url=item.image_url or fetch_article_image_url(item.url)
    return item,image_url,process_with_gemini(item.title,item.summary,article_text)


def _story_record(item): return {"title":item.title,"summary":item.summary,"url":item.url}


def main():
    sources=json.loads(Path("data/sources.json").read_text(encoding="utf-8")); store=StateStore()
    try:
        seen=store.load(); published_stories=store.load_records()
    except StateStoreError as exc:
        print(f"[STATE_ERROR] refusing to publish with untrusted state: {exc}")
        raise
    now=datetime.now(timezone.utc); deadline=time.monotonic()+RUN_DEADLINE_SECONDS
    published_count=ai_failed=duplicates=telegram_failed=non_technology=publish_failed=0; candidates=[]
    def persist(): store.save(seen,published_stories[-MAX_PUBLISHED_STORIES:])
    try:
        candidates=_collect_recent_items(sources,seen,now)
        print(f"[RUN] now={now.isoformat()} window_minutes={NEWS_WINDOW_MINUTES} candidates={len(candidates)} deadline_seconds={RUN_DEADLINE_SECONDS}")
        ai_candidates=[]
        for item in candidates:
            if time.monotonic()>=deadline: print("[DEADLINE] reached before AI processing"); break
            if is_duplicate_story(_story_record(item),published_stories):
                duplicates+=1; seen.update((item.item_id,item.url)); continue
            ai_candidates.append(item)
        processed_results=[]
        if ai_candidates and time.monotonic()<deadline:
            workers=min(GEMINI_WORKERS,len(ai_candidates)); executor=ThreadPoolExecutor(max_workers=workers)
            future_map={executor.submit(_process_candidate,item):item for item in ai_candidates}
            try:
                while future_map and time.monotonic()<deadline:
                    remaining=max(0.01,deadline-time.monotonic())
                    try:
                        for future in as_completed(list(future_map),timeout=remaining):
                            item=future_map.pop(future)
                            try: processed_results.append(future.result())
                            except Exception as exc: ai_failed+=1; print(f"[GEMINI_ERROR] source={item.source} url={item.url}: {exc}")
                            if time.monotonic()>=deadline: break
                    except FuturesTimeout:
                        print("[DEADLINE] AI processing deadline reached")
            finally:
                for future in future_map: future.cancel()
                executor.shutdown(wait=False,cancel_futures=True)
        processed_results.sort(key=lambda result:_published_key(result[0]))
        for item,image_url,processed in processed_results:
            if time.monotonic()>=deadline: print("[DEADLINE] reached before Telegram publishing"); break
            story=_story_record(item)
            if is_duplicate_story(story,published_stories): duplicates+=1; seen.update((item.item_id,item.url)); continue
            try:
                category=str(processed.get("category","")).strip().lower()
                if not is_technology_story(category,item.title,item.summary,processed.get("article_fa","") or ""):
                    non_technology+=1; seen.update((item.item_id,item.url)); print(f"[NON_TECHNOLOGY] source={item.source} category={category} url={item.url}"); continue
                message=build_rich_message_html(processed["title_fa"],processed["summary_fa"],processed.get("article_fa") or processed["summary_fa"],item.source)
            except Exception as exc: publish_failed+=1; print(f"[PROCESS_ERROR] source={item.source} url={item.url}: {exc}"); continue
            try: publish_rich_message(message,image_url)
            except Exception as exc: telegram_failed+=1; print(f"[TELEGRAM_ERROR] source={item.source} url={item.url}: {exc}"); continue
            published_count+=1; seen.update((item.item_id,item.url)); published_stories.append(story); del published_stories[:-MAX_PUBLISHED_STORIES:]; persist(); print(f"[PUBLISHED] source={item.source} url={item.url}")
    finally:
        persist(); print(f"[SUMMARY] candidates={len(candidates)} published={published_count} gemini_failed={ai_failed} duplicates={duplicates} telegram_failed={telegram_failed} non_technology={non_technology} process_failed={publish_failed}")


if __name__=="__main__": main()
