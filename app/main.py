import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from .ai import process_with_gemini
from .collector import collect_feed, fetch_article_image_url, fetch_article_text
from .core import NEWS_WINDOW_MINUTES, build_rich_message_html, is_duplicate_story, is_new_item, is_recent_news, is_technology_story
from .storage import MAX_PUBLISHED_STORIES, StateStore
from .telegram import publish_rich_message

GEMINI_WORKERS = 6
MAX_CANDIDATES_PER_RUN = 50
_OLDEST = datetime.min.replace(tzinfo=timezone.utc)

def _published_key(item): return item.published_at or _OLDEST

def _collect_recent_items(sources, seen, now):
    candidates=[]; batch_keys=set(); source_errors=0; feeds=0; unseen_total=0; recent_total=0; missing_total=0
    for source in sources:
        try: items=collect_feed(source["url"], source["name"], source.get("limit",100))
        except Exception as exc: source_errors+=1; print(f"[SOURCE_ERROR] {source['name']}: {exc}"); continue
        feeds+=len(items); unseen=recent=missing=0
        for item in items:
            if not is_new_item(item.item_id,item.url,seen) or item.item_id in batch_keys or item.url in batch_keys: continue
            unseen+=1
            if item.published_at is None: missing+=1; continue
            if is_recent_news(item.published_at,now): candidates.append(item); batch_keys.update((item.item_id,item.url)); recent+=1
        unseen_total+=unseen; recent_total+=recent; missing_total+=missing
        print(f"[SOURCE] {source['name']}: fetched={len(items)} unseen={unseen} recent={recent} missing_date={missing}")
    print(f"[COLLECT] sources={len(sources)} feeds={feeds} unseen={unseen_total} recent={recent_total} missing_dates={missing_total} errors={source_errors}")
    candidates.sort(key=_published_key)
    if len(candidates)>MAX_CANDIDATES_PER_RUN:
        print(f"[CAP] candidates={len(candidates)} limit={MAX_CANDIDATES_PER_RUN}; deferring newest items")
        candidates=candidates[:MAX_CANDIDATES_PER_RUN]
    return candidates

def _process_candidate(item):
    article_text=fetch_article_text(item.url); image_url=item.image_url or fetch_article_image_url(item.url)
    return item,image_url,process_with_gemini(item.title,item.summary,article_text)

def _story_record(item): return {"title":item.title,"summary":item.summary,"url":item.url}

def main():
    sources=json.loads(Path("data/sources.json").read_text(encoding="utf-8")); store=StateStore(); seen=store.load(); published_stories=store.load_records(); now=datetime.now(timezone.utc)
    published_count=ai_failed=duplicates=telegram_failed=non_technology=process_failed=0; candidates=[]
    def persist(): store.save(seen,published_stories[-MAX_PUBLISHED_STORIES:])
    try:
        candidates=_collect_recent_items(sources,seen,now)
        print(f"[RUN] now={now.isoformat()} window_minutes={NEWS_WINDOW_MINUTES} candidates={len(candidates)}")
        ai_candidates=[]
        for item in candidates:
            if is_duplicate_story(_story_record(item),published_stories): duplicates+=1; seen.update((item.item_id,item.url)); continue
            ai_candidates.append(item)
        results=[]
        with ThreadPoolExecutor(max_workers=min(GEMINI_WORKERS,len(ai_candidates))) if ai_candidates else ThreadPoolExecutor(max_workers=1) as executor:
            futures={executor.submit(_process_candidate,item):item for item in ai_candidates}
            for future in as_completed(futures):
                item=futures[future]
                try: results.append(future.result())
                except Exception as exc: ai_failed+=1; print(f"[GEMINI_ERROR] source={item.source} url={item.url}: {exc}")
        results.sort(key=lambda result:_published_key(result[0]))
        for item,image_url,processed in results:
            story=_story_record(item)
            if is_duplicate_story(story,published_stories): duplicates+=1; seen.update((item.item_id,item.url)); continue
            try:
                category=str(processed.get("category","")).strip().lower()
                if not is_technology_story(category,item.title,item.summary,processed.get("article_fa","") or ""):
                    non_technology+=1; seen.update((item.item_id,item.url)); continue
                message=build_rich_message_html(processed["title_fa"],processed["summary_fa"],processed.get("article_fa") or processed["summary_fa"],item.source)
            except Exception as exc: process_failed+=1; print(f"[PROCESS_ERROR] source={item.source} url={item.url}: {exc}"); continue
            try: publish_rich_message(message,image_url)
            except Exception as exc: telegram_failed+=1; print(f"[TELEGRAM_ERROR] source={item.source} url={item.url}: {exc}"); continue
            published_count+=1; seen.update((item.item_id,item.url)); published_stories.append(story); del published_stories[:-MAX_PUBLISHED_STORIES]; persist()
    finally:
        persist(); print(f"[SUMMARY] candidates={len(candidates)} published={published_count} gemini_failed={ai_failed} duplicates={duplicates} telegram_failed={telegram_failed} non_technology={non_technology} process_failed={process_failed}")

if __name__=="__main__": main()
