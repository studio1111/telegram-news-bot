from datetime import datetime, timedelta, timezone
from app.collector import NewsItem
import app.main as news_main


def _store():
    class FakeStore:
        saves=[]
        def load(self): return set()
        def load_records(self): return []
        def save(self, seen, records=None, outbox=None): self.saves.append((set(seen), list(records or []), list(outbox or [])))
    FakeStore.saves=[]
    return FakeStore


def _patch(monkeypatch, items, processor, publisher, store):
    monkeypatch.setattr(news_main, "_collect_recent_items", lambda *a, **k: list(items))
    monkeypatch.setattr(news_main, "fetch_article_text", lambda *a, **k: "article")
    monkeypatch.setattr(news_main, "fetch_article_image_url", lambda *a, **k: "")
    monkeypatch.setattr(news_main, "process_with_gemini", processor)
    monkeypatch.setattr(news_main, "publish_rich_message", publisher)
    monkeypatch.setattr(news_main, "StateStore", store)
    monkeypatch.setattr(news_main.Path, "read_text", lambda *a, **k: "[]")


def _tech(title, summary): return {"title_fa": title, "summary_fa": summary, "article_fa": "متن", "category": "technology"}


def test_main_uses_90_minute_window():
    now=datetime(2026,10,6,18,10,tzinfo=timezone.utc)
    assert news_main.is_recent_news(now-timedelta(minutes=90), now)
    assert not news_main.is_recent_news(now-timedelta(minutes=91), now)


def test_candidate_collection_is_capped(monkeypatch):
    now = datetime.now(timezone.utc)
    items = [NewsItem(str(i), f"Story {i}", f"https://example.com/{i}", "s", "S", "", now-timedelta(minutes=i)) for i in range(news_main.MAX_CANDIDATES + 5)]
    monkeypatch.setattr(news_main, "collect_feed", lambda *a, **k: items)
    result = news_main._collect_recent_items([{"url":"https://example.com/feed", "name":"S", "limit":100}], set(), now)
    assert len(result) == news_main.MAX_CANDIDATES


def test_incomplete_gemini_result_does_not_crash_run(monkeypatch):
    now=datetime.now(timezone.utc)
    bad=NewsItem("b","OpenAI AI model","https://example.com/b","s","S","",now-timedelta(minutes=2))
    good=NewsItem("g","Nvidia GPU software","https://example.com/g","s","S","",now-timedelta(minutes=1))
    def process(title, summary, article): return {"category":"technology","summary_fa":"x"} if title.startswith("OpenAI") else _tech("خبر","خلاصه")
    published=[]; store=_store(); _patch(monkeypatch,[bad,good],process,lambda m,i:published.append(m),store)
    news_main.main()
    assert len(published)==1 and "g" in store.saves[-1][0] and "b" not in store.saves[-1][0]


def test_state_is_saved_after_each_publication(monkeypatch):
    now=datetime.now(timezone.utc); first=NewsItem("1","Software story","https://example.com/1","s","S","",now-timedelta(minutes=3)); second=NewsItem("2","Nvidia chip software story","https://example.com/2","s","S","",now-timedelta(minutes=2))
    calls={"n":0}
    def publish(m,i):
        calls["n"]+=1
        if calls["n"]==2: raise KeyboardInterrupt
    store=_store(); _patch(monkeypatch,[first,second],lambda t,s,a:_tech(t,s),publish,store); monkeypatch.setattr(news_main,"is_duplicate_story",lambda *a,**k:False)
    try: news_main.main()
    except KeyboardInterrupt: pass
    assert store.saves and "1" in store.saves[-1][0] and "2" not in store.saves[-1][0]


def test_stories_are_published_oldest_first(monkeypatch):
    now=datetime.now(timezone.utc); newer=NewsItem("n","Newer software story","https://example.com/n","s","S","",now-timedelta(minutes=1)); older=NewsItem("o","Older software story","https://example.com/o","s","S","",now-timedelta(minutes=50))
    order=[]; store=_store(); _patch(monkeypatch,[newer,older],lambda t,s,a:_tech(t,s),lambda m,i:order.append(m),store); monkeypatch.setattr(news_main,"is_duplicate_story",lambda *a,**k:False)
    news_main.main()
    assert "Older" in order[0] and "Newer" in order[1]


def test_published_story_record_keeps_url(monkeypatch):
    now=datetime.now(timezone.utc); item=NewsItem("u","Software story","https://example.com/u","s","S","",now)
    store=_store(); _patch(monkeypatch,[item],lambda t,s,a:_tech(t,s),lambda m,i:None,store); news_main.main()
    assert store.saves[-1][1][-1]["url"] == "https://example.com/u"


def test_deadline_stops_before_ai_work(monkeypatch):
    now = datetime.now(timezone.utc)
    item = NewsItem("deadline", "AI software story", "https://example.com/deadline", "s", "S", "", now)
    calls = {"ai": 0}
    store = _store()
    _patch(monkeypatch, [item], lambda t,s,a: calls.__setitem__("ai", calls["ai"] + 1) or _tech(t,s), lambda m,i: None, store)
    monkeypatch.setattr(news_main, "_collect_recent_items", lambda *a, **k: [item])
    monkeypatch.setattr(news_main.time, "monotonic", lambda: 10_000)
    monkeypatch.setattr(news_main, "RUN_DEADLINE_SECONDS", 0)
    news_main.main()
    assert calls["ai"] == 0


def test_pending_outbox_is_retried_before_new_candidates(monkeypatch):
    store = _store()
    pending = {"key": "url:https://example.com/pending", "url": "https://example.com/pending", "message": "<b>Pending</b>", "image_url": "", "status": "pending"}
    store.load_outbox = lambda: [pending]
    completed = []
    store.complete_outbox = lambda key: completed.append(key)
    published = []
    monkeypatch.setattr(news_main, "StateStore", store)
    monkeypatch.setattr(news_main, "_collect_recent_items", lambda *a, **k: [])
    monkeypatch.setattr(news_main, "publish_rich_message", lambda m, i: published.append((m, i)))
    monkeypatch.setattr(news_main.Path, "read_text", lambda *a, **k: "[]")
    news_main.main()
    assert published == [("<b>Pending</b>", "")]
    assert completed == ["url:https://example.com/pending"]


def test_send_crash_leaves_outbox_pending_for_recovery(monkeypatch):
    now = datetime.now(timezone.utc)
    item = NewsItem("crash", "Nvidia software story", "https://example.com/crash", "s", "S", "", now)
    store = _store()
    outbox = []
    store.load_outbox = lambda: outbox
    def save(seen, records=None, pending=None):
        outbox[:] = list(pending or [])
    store.save = save
    monkeypatch.setattr(news_main, "StateStore", store)
    monkeypatch.setattr(news_main, "_collect_recent_items", lambda *a, **k: [item])
    monkeypatch.setattr(news_main, "fetch_article_text", lambda *a, **k: "article")
    monkeypatch.setattr(news_main, "fetch_article_image_url", lambda *a, **k: "")
    monkeypatch.setattr(news_main, "process_with_gemini", lambda *a, **k: _tech("خبر", "خلاصه"))
    def crash_publish(message, image):
        raise KeyboardInterrupt
    monkeypatch.setattr(news_main, "publish_rich_message", crash_publish)
    try:
        news_main.main()
    except KeyboardInterrupt:
        pass
    assert outbox and outbox[0]["status"] == "pending"
