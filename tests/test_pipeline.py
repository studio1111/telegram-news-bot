import json
from datetime import datetime, timedelta, timezone

import pytest
import requests

import app.main as news_main
from app import ai
from app.collector import NewsItem


def _store(history=None, outbox=None):
    class FakeStore:
        saves = []

        def load(self):
            return set()

        def load_records(self):
            return list(history or [])

        def load_outbox(self):
            return outbox if outbox is not None else []

        def save(self, seen, records=None, pending=None):
            FakeStore.saves.append((set(seen), list(records or []), list(pending or [])))

    FakeStore.saves = []
    return FakeStore


def _patch(monkeypatch, items, processor, publisher, store):
    monkeypatch.setattr(news_main, "_collect_recent_items", lambda *a, **k: list(items))
    monkeypatch.setattr(news_main, "fetch_article_text", lambda *a, **k: "article")
    monkeypatch.setattr(news_main, "fetch_article_image_url", lambda *a, **k: "https://example.com/test.jpg")
    monkeypatch.setattr(news_main, "process_with_gemini", processor)
    monkeypatch.setattr(news_main, "publish_rich_message", publisher)
    monkeypatch.setattr(news_main, "StateStore", store)
    monkeypatch.setattr(news_main.Path, "read_text", lambda *a, **k: "[]")


def _tech(title, summary):
    return {"title_fa": title, "summary_fa": summary, "article_fa": "متن", "category": "technology"}


def _item(item_id, title, source="Source", image="", minutes=1, categories=("Technology",)):
    now = datetime.now(timezone.utc)
    return NewsItem(item_id, title, f"https://example.com/{item_id}", "summary", source, image,
                    now - timedelta(minutes=minutes), categories)


# ---- no topic filter for the three configured sources ---------------------

def test_non_technology_story_is_allowed_when_it_is_not_an_ad(monkeypatch):
    sports = _item("sports", "Football final result", source="The Verge", categories=("Sports",), minutes=3)
    tech = _item("tech", "New chip announced", source="TechCrunch", categories=("Technology",), minutes=2)

    def process(title, summary, article):
        if title.startswith("Football"):
            return {"title_fa": "فوتبال", "summary_fa": "ورزش", "article_fa": "متن", "category": "sports"}
        return _tech("چیپ", "خلاصه")

    published = []
    store = _store()
    _patch(monkeypatch, [sports, tech], process, lambda m, i: published.append(m), store)
    monkeypatch.setattr(news_main, "is_duplicate_story", lambda *a, **k: False)
    news_main.main()
    assert len(published) == 2
    assert "sports" in store.saves[-1][0]
    assert "tech" in store.saves[-1][0]

# ---- no candidate cap ------------------------------------------------------

def test_every_new_story_is_published_without_a_cap(monkeypatch):
    items = [_item(f"s{i}", f"Technology story {i}", minutes=i + 1) for i in range(40)]
    published = []
    store = _store()
    _patch(monkeypatch, items, lambda t, s, a: _tech(t, s), lambda m, i: published.append(m), store)
    monkeypatch.setattr(news_main, "is_duplicate_story", lambda *a, **k: False)
    news_main.main()
    assert len(published) == 40


# ---- semantic duplicate layer ---------------------------------------------

def test_candidate_matching_a_published_story_is_dropped(monkeypatch):
    history = [{"title": "Google launches SynthID Detector", "summary": "s", "url": "https://old.example/a"}]
    item = _item("new", "A watermark checker for AI media arrives", source="Source B")
    published = []
    store = _store(history=history)
    _patch(monkeypatch, [item], lambda t, s, a: _tech(t, s), lambda m, i: published.append(m), store)
    monkeypatch.setattr(news_main, "is_duplicate_story", lambda *a, **k: False)
    monkeypatch.setattr(news_main, "find_duplicate_groups", lambda candidates, hist: [["C1", "H1"]])
    news_main.main()
    assert published == []
    assert "new" in store.saves[-1][0]


def test_duplicate_candidates_keep_only_the_one_with_an_image(monkeypatch):
    without_image = _item("a", "Chip maker unveils processor", source="Source A", minutes=5)
    with_image = _item("b", "New processor from chip maker", source="Source B", image="https://example.com/b.jpg", minutes=1)
    images = []
    store = _store()
    _patch(monkeypatch, [without_image, with_image], lambda t, s, a: _tech(t, s), lambda m, i: images.append(i), store)
    monkeypatch.setattr(news_main, "is_duplicate_story", lambda *a, **k: False)
    monkeypatch.setattr(news_main, "find_duplicate_groups", lambda candidates, hist: [["C1", "C2"]])
    news_main.main()
    assert images == ["https://example.com/b.jpg"]
    assert {"a", "b"} <= store.saves[-1][0]


def test_semantic_check_failure_does_not_block_publishing(monkeypatch):
    item = _item("only", "Technology story")
    history = [{"title": "Old story", "summary": "s", "url": "https://old.example/x"}]
    published = []
    calls = []
    store = _store(history=history)
    _patch(monkeypatch, [item], lambda t, s, a: _tech(t, s), lambda m, i: published.append(m), store)
    monkeypatch.setattr(news_main, "is_duplicate_story", lambda *a, **k: False)

    def broken(candidates, hist):
        calls.append(1)
        raise RuntimeError("Gemini unavailable")

    monkeypatch.setattr(news_main, "find_duplicate_groups", broken)
    news_main.main()
    assert calls and len(published) == 1


# ---- Telegram timeouts must not cause double posts -------------------------

def test_read_timeout_is_treated_as_delivered_to_avoid_a_duplicate(monkeypatch):
    item = _item("slow", "Technology story")
    outbox = []
    store = _store(outbox=outbox)

    def publish(message, image):
        raise requests.exceptions.ReadTimeout("slow")

    _patch(monkeypatch, [item], lambda t, s, a: _tech(t, s), publish, store)
    news_main.main()
    assert outbox and outbox[0]["status"] == "sent"
    assert "slow" in store.saves[-1][0]


def test_connection_error_keeps_the_story_pending_for_retry(monkeypatch):
    item = _item("offline", "Technology story")
    outbox = []
    store = _store(outbox=outbox)

    def publish(message, image):
        raise requests.exceptions.ConnectionError("no route")

    _patch(monkeypatch, [item], lambda t, s, a: _tech(t, s), publish, store)
    news_main.main()
    assert outbox and outbox[0]["status"] == "pending"
    assert "offline" not in store.saves[-1][0]


# ---- Gemini duplicate grouping ---------------------------------------------

class _Response:
    ok = True

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return {"candidates": [{"content": {"parts": [{"text": json.dumps(self.payload)}]}}]}


def test_find_duplicate_groups_validates_ids_and_sends_both_lists(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(kwargs)
        return _Response({"groups": [["C1", "H1"], ["C2", "C9"], ["c2", "C1"], "bad", [3, None]]})

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(ai.requests, "post", fake_post)
    candidates = [
        {"title": "Google launches SynthID Detector", "summary": "s", "source": "A"},
        {"title": "OpenAI raises funding", "summary": "s", "source": "B"},
    ]
    history = [{"title": "Google AI detector", "display_title": "ابزار تشخیص گوگل (Google)"}]
    assert ai.find_duplicate_groups(candidates, history) == [["C1", "H1"]]
    prompt = captured["json"]["contents"][0]["parts"][0]["text"]
    assert "C1 [A]" in prompt and "C2 [B]" in prompt and "H1 Google AI detector" in prompt


def test_find_duplicate_groups_without_candidates_makes_no_request():
    assert ai.find_duplicate_groups([], [{"title": "x"}]) == []


def test_find_duplicate_groups_rejects_unexpected_structure(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(ai.requests, "post", lambda *a, **k: _Response({"groups": "nope"}))
    with pytest.raises(RuntimeError, match="unexpected structure"):
        ai.find_duplicate_groups([{"title": "t"}], [])
