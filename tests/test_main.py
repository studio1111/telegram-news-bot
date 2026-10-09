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
    monkeypatch.setattr(news_main, "fetch_article_image_url", lambda *a, **k: "https://example.com/test.jpg")
    monkeypatch.setattr(news_main, "process_with_gemini", processor)
    monkeypatch.setattr(news_main, "publish_rich_message", publisher)
    monkeypatch.setattr(news_main, "StateStore", store)
    monkeypatch.setattr(news_main.Path, "read_text", lambda *a, **k: "[]")


def _tech(title, summary): return {"title_fa": title, "summary_fa": summary, "article_fa": "متن", "category": "technology"}


def test_main_uses_90_minute_window():
    now=datetime(2026,10,6,18,10,tzinfo=timezone.utc)
    assert news_main.is_recent_news(now-timedelta(minutes=90), now)
    assert not news_main.is_recent_news(now-timedelta(minutes=91), now)


def test_candidate_collection_checks_later_sources_without_cap(monkeypatch):
    now = datetime.now(timezone.utc)
    first_items = [NewsItem(f"first-{i}", f"Nvidia software story {i}", f"https://example.com/first/{i}", "s", "First", "", now-timedelta(minutes=i+1), ("Technology",)) for i in range(30)]
    later = NewsItem("later", "OpenAI AI model software story", "https://example.com/later", "s", "Later", "", now-timedelta(minutes=60), ("Technology",))
    calls = []

    def collect(url, name, limit):
        calls.append(name)
        return first_items if name == "First" else [later]

    monkeypatch.setattr(news_main, "collect_feed", collect)
    sources = [
        {"url": "https://example.com/first-feed", "name": "First", "limit": 100},
        {"url": "https://example.com/later-feed", "name": "Later", "limit": 100},
    ]

    result = news_main._collect_recent_items(sources, set(), now)

    assert calls == ["First", "Later"]
    assert len(result) == 31
    assert any(item.url == later.url for item in result)


def test_candidate_collection_is_not_capped(monkeypatch):
    now = datetime.now(timezone.utc)
    items = [NewsItem(str(i), f"Story {i}", f"https://example.com/{i}", "s", "S", "", now-timedelta(minutes=i), ("Technology",)) for i in range(35)]
    monkeypatch.setattr(news_main, "collect_feed", lambda *a, **k: items)
    result = news_main._collect_recent_items([{"url":"https://example.com/feed", "name":"S", "limit":100}], set(), now)
    assert len(result) == 35


def test_incomplete_gemini_result_does_not_crash_run(monkeypatch):
    now=datetime.now(timezone.utc)
    bad=NewsItem("b","OpenAI AI model","https://example.com/b","s","S","",now-timedelta(minutes=2), ("Technology",))
    good=NewsItem("g","Nvidia GPU software","https://example.com/g","s","S","",now-timedelta(minutes=1), ("Technology",))
    def process(title, summary, article): return {"category":"technology","summary_fa":"x"} if title.startswith("OpenAI") else _tech("خبر","خلاصه")
    published=[]; store=_store(); _patch(monkeypatch,[bad,good],process,lambda m,i:published.append(m),store)
    news_main.main()
    assert len(published)==1 and "g" in store.saves[-1][0] and "b" not in store.saves[-1][0]


def test_state_is_saved_after_each_publication(monkeypatch):
    now=datetime.now(timezone.utc); first=NewsItem("1","Software story","https://example.com/1","s","S","",now-timedelta(minutes=3), ("Technology",)); second=NewsItem("2","Nvidia chip software story","https://example.com/2","s","S","",now-timedelta(minutes=2), ("Technology",))
    calls={"n":0}
    def publish(m,i):
        calls["n"]+=1
        if calls["n"]==2: raise KeyboardInterrupt
    store=_store(); _patch(monkeypatch,[first,second],lambda t,s,a:_tech(t,s),publish,store); monkeypatch.setattr(news_main,"is_duplicate_story",lambda *a,**k:False)
    try: news_main.main()
    except KeyboardInterrupt: pass
    assert store.saves and "https://example.com/2" in store.saves[-1][0] and "https://example.com/1" not in store.saves[-1][0]


def test_stories_are_published_newest_first(monkeypatch):
    now=datetime.now(timezone.utc); newer=NewsItem("n","Newer software story","https://example.com/n","s","S","",now-timedelta(minutes=1), ("Technology",)); older=NewsItem("o","Older software story","https://example.com/o","s","S","",now-timedelta(minutes=50), ("Technology",))
    order=[]; store=_store(); _patch(monkeypatch,[newer,older],lambda t,s,a:_tech(t,s),lambda m,i:order.append(m),store); monkeypatch.setattr(news_main,"is_duplicate_story",lambda *a,**k:False)
    news_main.main()
    assert "Newer" in order[0] and "Older" in order[1]


def test_published_story_record_keeps_url(monkeypatch):
    now=datetime.now(timezone.utc); item=NewsItem("u","Software story","https://example.com/u","s","S","",now, ("Technology",))
    store=_store(); _patch(monkeypatch,[item],lambda t,s,a:_tech(t,s),lambda m,i:None,store); news_main.main()
    assert store.saves[-1][1][-1]["url"] == "https://example.com/u"


def test_deadline_stops_before_ai_work(monkeypatch):
    now = datetime.now(timezone.utc)
    item = NewsItem("deadline", "AI software story", "https://example.com/deadline", "s", "S", "", now, ("Technology",))
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
    store.load_outbox = lambda self: [pending]
    published = []
    monkeypatch.setattr(news_main, "StateStore", store)
    monkeypatch.setattr(news_main, "_collect_recent_items", lambda *a, **k: [])
    monkeypatch.setattr(news_main, "publish_rich_message", lambda m, i: published.append((m, i)))
    monkeypatch.setattr(news_main.Path, "read_text", lambda *a, **k: "[]")
    news_main.main()
    assert published == [("<b>Pending</b>", "")]
    assert pending["status"] == "sent"


def test_send_crash_leaves_outbox_pending_for_recovery(monkeypatch):
    now = datetime.now(timezone.utc)
    item = NewsItem("crash", "Nvidia software story", "https://example.com/crash", "s", "S", "", now, ("Technology",))
    store = _store()
    outbox = []
    store.load_outbox = lambda self: outbox
    def save(self, seen, records=None, pending=None):
        outbox[:] = list(pending or [])
    store.save = save
    monkeypatch.setattr(news_main, "StateStore", store)
    monkeypatch.setattr(news_main, "_collect_recent_items", lambda *a, **k: [item])
    monkeypatch.setattr(news_main, "fetch_article_text", lambda *a, **k: "article")
    monkeypatch.setattr(news_main, "fetch_article_image_url", lambda *a, **k: "https://example.com/crash.jpg")
    monkeypatch.setattr(news_main, "process_with_gemini", lambda *a, **k: _tech("خبر", "خلاصه"))
    def crash_publish(message, image):
        raise KeyboardInterrupt
    monkeypatch.setattr(news_main, "publish_rich_message", crash_publish)
    try:
        news_main.main()
    except KeyboardInterrupt:
        pass
    assert outbox and outbox[0]["status"] == "pending"


def test_candidate_processing_uses_full_article_when_available(monkeypatch):
    now = datetime.now(timezone.utc)
    item = NewsItem("rss-first", "Technology story", "https://example.com/story", "RSS summary", "S", "", now, ("Technology",))
    monkeypatch.setattr(news_main, "fetch_article_text", lambda *a, **k: "Full article text")
    monkeypatch.setattr(news_main, "fetch_article_image_url", lambda *a, **k: (_ for _ in ()).throw(AssertionError("image fetch must not run")))
    captured = {}
    monkeypatch.setattr(
        news_main,
        "process_with_gemini",
        lambda title, summary, article: captured.setdefault("article", article) or _tech(title, summary),
    )
    result = news_main._process_candidate(item)
    assert result[0] is item and result[1] == ""
    assert captured["article"] == "Full article text"


def test_retried_outbox_marks_real_url_as_seen(monkeypatch):
    store = _store()
    pending = {
        "key": "url:hash",
        "url": "https://example.com/pending",
        "message": "<b>Pending</b>",
        "image_url": "",
        "status": "pending",
    }
    store.load_outbox = lambda self: [pending]
    published = []
    monkeypatch.setattr(news_main, "StateStore", store)
    monkeypatch.setattr(news_main, "_collect_recent_items", lambda *a, **k: [])
    monkeypatch.setattr(news_main, "publish_rich_message", lambda m, i: published.append((m, i)))
    monkeypatch.setattr(news_main.Path, "read_text", lambda *a, **k: "[]")
    news_main.main()
    assert "https://example.com/pending" in store.saves[-1][0]
    assert "url:hash" not in store.saves[-1][0]


def test_main_deduplicates_two_different_source_rewrites_of_same_rendered_story(monkeypatch):
    now = datetime.now(timezone.utc)
    first = NewsItem(
        "google-1",
        "Google launches a new website for identifying AI-generated media",
        "https://source-a.example/google-ai-media",
        "Google launches a website using SynthID to identify AI-generated media.",
        "Source A",
        "",
        now - timedelta(minutes=5),
        ("Technology",),
    )
    second = NewsItem(
        "google-2",
        "Google releases SynthID Detector for AI content",
        "https://source-b.example/synthid-detector",
        "Google releases SynthID Detector to identify AI-generated images, video and audio.",
        "Source B",
        "",
        now - timedelta(minutes=4),
        ("Technology",),
    )
    rendered = [
        {
            "title_fa": "راه‌اندازی وب‌سایت جدید گوگل (Google) برای شناسایی رسانه‌های تولیدشده با هوش مصنوعی شرکت گوگل (Google)",
            "summary_fa": "شرکت گوگل (Google) از راه‌اندازی وب‌سایت جدیدی خبر داد که با فناوری سینث‌آی‌دی (SynthID) محتوای تولیدشده با هوش مصنوعی را شناسایی می‌کند.",
            "article_fa": "متن کامل خبر اول",
            "category": "technology",
        },
        {
            "title_fa": "ابزار تشخیص هوش مصنوعی گوگل (Google) منتشر شد",
            "summary_fa": "شرکت گوگل (Google) ابزار جدیدی به نام «سینت‌اید دکتور» (SynthID Detector) را برای شناسایی محتوای تولیدشده با هوش مصنوعی عرضه کرده است.",
            "article_fa": "متن کامل خبر دوم",
            "category": "technology",
        },
    ]
    store = _store()
    published = []
    calls = {"n": 0}

    def process(title, summary, article):
        result = rendered[calls["n"]]
        calls["n"] += 1
        return result

    _patch(monkeypatch, [first, second], process, lambda m, i: published.append(m), store)
    news_main.main()

    assert len(published) == 1


def test_sent_outbox_history_is_used_for_cross_source_dedup(monkeypatch):
    store = _store()
    sent = {
        "key": "url:old",
        "url": "https://source-a.example/synthid",
        "title": "Google's AI detection website is now available",
        "summary": "SynthID Detector will flag content created with AI tools.",
        "display_title": "راه‌اندازی وب‌سایت جدید گوگل (Google) برای شناسایی رسانه‌های تولیدشده با هوش مصنوعی",
        "display_summary": "گوگل (Google) از وب‌سایت SynthID برای شناسایی محتوای هوش مصنوعی خبر داد.",
        "message": "خبر قبلی",
        "image_url": "",
        "status": "sent",
    }
    store.load_outbox = lambda self: [sent]
    now = datetime.now(timezone.utc)
    item = NewsItem(
        "synthid-new",
        "Google’s new SynthID website can identify AI-generated media",
        "https://source-b.example/synthid",
        "Google launched a new site that lets anyone verify AI-generated media.",
        "Source B",
        "",
        now,
        ("Technology",),
    )
    published = []
    monkeypatch.setattr(news_main, "StateStore", store)
    monkeypatch.setattr(news_main, "_collect_recent_items", lambda *a, **k: [item])
    monkeypatch.setattr(news_main, "process_with_gemini", lambda *a, **k: _tech("عنوان", "خلاصه"))
    monkeypatch.setattr(news_main, "publish_rich_message", lambda *a: published.append(a))
    monkeypatch.setattr(news_main.Path, "read_text", lambda *a, **k: "[]")
    news_main.main()
    assert published == []


def test_outbox_recovery_preserves_story_fields_for_dedup(monkeypatch):
    store = _store()
    pending = {
        "key": "url:pending",
        "url": "https://example.com/pending",
        "title": "Google launches SynthID Detector",
        "summary": "A detector for AI-generated media.",
        "display_title": "ابزار تشخیص هوش مصنوعی گوگل (Google)",
        "display_summary": "گوگل (Google) ابزار SynthID Detector را عرضه کرد.",
        "message": "خبر",
        "image_url": "",
        "status": "pending",
    }
    store.load_outbox = lambda self: [pending]
    monkeypatch.setattr(news_main, "StateStore", store)
    monkeypatch.setattr(news_main, "_collect_recent_items", lambda *a, **k: [])
    monkeypatch.setattr(news_main, "publish_rich_message", lambda *a: None)
    monkeypatch.setattr(news_main.Path, "read_text", lambda *a, **k: "[]")
    news_main.main()
    recovered = store.saves[-1][1][-1]
    assert recovered["title"] == pending["title"]
    assert recovered["display_title"] == pending["display_title"]


def test_duplicate_candidate_with_image_wins_over_newer_text_only_story():
    from datetime import datetime, timezone
    with_image = NewsItem(
        "img", "Google launches SynthID Detector", "https://example.com/img",
        "Google launches a tool to identify AI-generated media.", "TechCrunch",
        "https://example.com/hero.jpg", datetime(2026, 10, 7, 18, 0, tzinfo=timezone.utc),
        ("Technology",),
    )
    without_image = NewsItem(
        "noimg", "Google launches SynthID Detector", "https://example.com/noimg",
        "Google launches a tool to identify AI-generated media.", "WIRED",
        "", datetime(2026, 10, 7, 18, 5, tzinfo=timezone.utc),
        ("Technology",),
    )
    result = news_main._prioritize_duplicate_candidates([without_image, with_image])
    assert len(result) == 1
    assert result[0].source == "TechCrunch"
    assert result[0].image_url == "https://example.com/hero.jpg"


def test_missing_rss_image_is_recovered_from_article_page(monkeypatch):
    now = datetime.now(timezone.utc)
    item = NewsItem(
        "img-recovery", "Technology story", "https://example.com/story",
        "Technology summary", "TechCrunch", "", now, ("Technology",)
    )
    monkeypatch.setattr(news_main, "fetch_article_image_url", lambda url: "https://example.com/recovered.jpg")
    monkeypatch.setattr(news_main.time, "monotonic", lambda: 100)
    recovered = news_main._hydrate_missing_images([item], 200)
    assert recovered[0].image_url == "https://example.com/recovered.jpg"


def test_main_recovers_images_and_prioritizes_duplicate_candidates(monkeypatch):
    now = datetime.now(timezone.utc)
    older = NewsItem(
        "older",
        "Google launches SynthID Detector",
        "https://example.com/older",
        "Google launches a tool to identify AI-generated media.",
        "TechCrunch",
        "",
        now - timedelta(minutes=5),
        ("Technology",),
    )
    newer_duplicate = NewsItem(
        "newer",
        "Google launches SynthID Detector",
        "https://example.com/newer",
        "Google launches a tool to identify AI-generated media.",
        "WIRED",
        "",
        now - timedelta(minutes=1),
        ("Technology",),
    )
    store = _store()
    published = []
    _patch(
        monkeypatch,
        [older, newer_duplicate],
        lambda title, summary, article: _tech(title, summary),
        lambda message, image: published.append(image),
        store,
    )
    monkeypatch.setattr(
        news_main,
        "fetch_article_image_url",
        lambda url: "https://example.com/recovered.jpg" if url == older.url else "",
    )
    news_main.main()
    assert published == ["https://example.com/recovered.jpg"]


def test_user_reported_synthid_rewrites_are_duplicates():
    from app.core import is_duplicate_story
    first = {
        "title": "راه‌اندازی وب‌سایت جدید گوگل (Google) برای شناسایی رسانه‌های تولیدشده با هوش مصنوعی",
        "summary": "شرکت گوگل (Google) از راه‌اندازی وب‌سایت جدیدی خبر داد که با فناوری سینث‌آی‌دی (SynthID) محتوای تولیدشده با هوش مصنوعی را شناسایی می‌کند.",
        "url": "https://techcrunch.com/one",
    }
    second = {
        "title": "ابزار تشخیص هوش مصنوعی گوگل (Google) منتشر شد",
        "summary": "شرکت گوگل (Google) ابزار جدیدی به نام «سینت‌اید دکتور» (SynthID Detector) را برای شناسایی محتوای تولیدشده با هوش مصنوعی عرضه کرده است.",
        "url": "https://www.theverge.com/two",
    }
    assert is_duplicate_story(second, [first])


def test_three_source_allowlist_accepts_only_requested_sources():
    assert news_main._source_allowed("TechCrunch")
    assert news_main._source_allowed("The Verge")
    assert news_main._source_allowed("Engadget")
    assert not news_main._source_allowed("WIRED")
    assert not news_main._source_allowed("BBC Technology")


def test_non_technology_item_from_allowed_source_is_not_filtered():
    now = datetime.now(timezone.utc)
    item = NewsItem("non-tech", "A movie story", "https://example.com/non-tech", "Film news", "The Verge", "", now, ())
    monkeypatch = None
    assert news_main._item_is_publishable(item)


def test_advertisement_is_filtered_from_allowed_sources():
    now = datetime.now(timezone.utc)
    item = NewsItem("ad", "Sponsored: Best laptop deals", "https://example.com/ad", "Paid promotion", "Engadget", "", now, ())
    assert not news_main._item_is_publishable(item)


def test_non_technology_allowed_source_is_publishable():
    now = datetime.now(timezone.utc)
    item = NewsItem("non-tech", "A movie story", "https://example.com/non-tech", "Film news", "The Verge", "", now, ())
    assert news_main._item_is_publishable(item)


def test_duplicate_history_is_cleaned_before_it_is_used_for_future_runs():
    first = {
        "title": "راه‌اندازی وب‌سایت جدید گوگل (Google) برای شناسایی رسانه‌های تولیدشده با هوش مصنوعی",
        "summary": "شرکت گوگل (Google) از راه‌اندازی وب‌سایت جدیدی خبر داد که با فناوری سینث‌آی‌دی (SynthID) محتوای تولیدشده با هوش مصنوعی را شناسایی می‌کند.",
        "url": "https://techcrunch.com/one",
    }
    second = {
        "title": "ابزار تشخیص هوش مصنوعی گوگل (Google) منتشر شد",
        "summary": "شرکت گوگل (Google) ابزار جدیدی به نام «سینت‌اید دکتور» (SynthID Detector) را برای شناسایی محتوای تولیدشده با هوش مصنوعی عرضه کرده است.",
        "url": "https://www.engadget.com/two",
    }
    cleaned, removed = news_main._deduplicate_history_records([first, second])
    assert removed == 1
    assert len(cleaned) == 1


def test_feed_failure_is_counted_in_collection_summary(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise RuntimeError("feed unavailable")

    monkeypatch.setattr(news_main, "collect_feed", fail)
    result = news_main._collect_recent_items(
        [{"url": "https://example.com/feed", "name": "Example", "limit": 0}],
        set(),
        datetime.now(timezone.utc),
    )

    assert result == []
    output = capsys.readouterr().out
    assert "[SOURCE_ERROR] Example: feed unavailable" in output
    assert "errors=1" in output


def test_missing_rss_date_is_recovered_from_article_page(monkeypatch):
    now = datetime.now(timezone.utc)
    item = NewsItem("undated", "Recent technology story", "https://example.com/undated", "summary", "Example", "", None, ("Technology",))
    monkeypatch.setattr(news_main, "collect_feed", lambda *a, **k: [item])
    monkeypatch.setattr(news_main, "fetch_article_published_at", lambda url: now - timedelta(minutes=10))

    result = news_main._collect_recent_items(
        [{"url": "https://example.com/feed", "name": "Example", "limit": 0}], set(), now
    )

    assert len(result) == 1
    assert result[0].published_at == now - timedelta(minutes=10)
