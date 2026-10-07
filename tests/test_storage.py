import json

import pytest
import app.storage as storage
from app.storage import StateStore, StateStoreError


def test_save_caps_seen_and_keeps_newest(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "MAX_SEEN", 3)
    path = tmp_path / "state.json"
    store = StateStore(path)
    store.save({"a", "b"}, [])
    store.save({"a", "b", "c", "d"}, [])
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["seen"] == ["b", "c", "d"]


def test_save_caps_published_stories(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "MAX_PUBLISHED_STORIES", 2)
    path = tmp_path / "state.json"
    store = StateStore(path)
    store.save(set(), [{"title": str(i), "summary": "", "url": f"https://example.com/{i}"} for i in range(5)])
    assert [r["title"] for r in store.load_records()] == ["3", "4"]


def test_save_roundtrip(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.save({"x"}, [{"title": "t", "summary": "s", "url": "u"}])
    assert store.load() == {"x"}
    assert store.load_records()[0]["url"] == "u"


def test_corrupt_state_fails_closed_instead_of_becoming_empty(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(StateStoreError):
        StateStore(path).load()


def test_invalid_state_shape_fails_closed(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"seen": "not-a-list"}), encoding="utf-8")
    with pytest.raises(StateStoreError):
        StateStore(path).load()


def test_outbox_roundtrip_and_update(tmp_path):
    store = StateStore(tmp_path / "state.json")
    pending = {"key": "url:https://example.com/1", "url": "https://example.com/1", "message": "خبر", "image_url": "", "status": "pending"}
    store.save(set(), [], [pending])
    assert store.load_outbox() == [pending]
    store.complete_outbox(pending["key"])
    assert store.load_outbox()[0]["status"] == "sent"


def test_legacy_state_migrates_without_outbox(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"schema_version": 2, "seen": ["x"], "published_stories": []}), encoding="utf-8")
    store = StateStore(path)
    assert store.load_outbox() == []
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == storage.STATE_SCHEMA_VERSION


def test_empty_url_published_records_are_dropped_and_state_is_rewritten(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({
        "schema_version": 3,
        "seen": ["x"],
        "published_stories": [
            {"title": "valid", "summary": "s", "url": " https://example.com/story "},
            {"title": "invalid", "summary": "s", "url": ""},
            {"title": "invalid2", "summary": "s", "url": "   "},
        ],
        "outbox": [],
    }), encoding="utf-8")
    store = StateStore(path)
    assert store.load_records() == [{"title": "valid", "summary": "s", "url": "https://example.com/story"}]
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["published_stories"] == [{"title": "valid", "summary": "s", "url": "https://example.com/story"}]


def test_save_does_not_persist_empty_url_published_records(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.save(set(), [
        {"title": "valid", "summary": "s", "url": "https://example.com/story"},
        {"title": "invalid", "summary": "s", "url": ""},
    ])
    assert store.load_records() == [{"title": "valid", "summary": "s", "url": "https://example.com/story"}]


def test_published_story_keeps_rendered_dedup_fields(tmp_path):
    store = StateStore(tmp_path / "state.json")
    record = {
        "title": "Google releases a new tool",
        "summary": "RSS summary",
        "url": "https://example.com/google",
        "display_title": "ابزار جدید گوگل (Google)",
        "display_summary": "گوگل (Google) ابزار جدیدی را عرضه کرد.",
    }
    store.save(set(), [record])
    assert store.load_records()[0]["display_title"] == record["display_title"]
    assert store.load_records()[0]["display_summary"] == record["display_summary"]


def test_outbox_keeps_rendered_dedup_fields(tmp_path):
    store = StateStore(tmp_path / "state.json")
    pending = {
        "key": "url:https://example.com/1",
        "url": "https://example.com/1",
        "title": "Google releases SynthID Detector",
        "summary": "RSS summary",
        "display_title": "ابزار تشخیص هوش مصنوعی گوگل (Google)",
        "display_summary": "گوگل (Google) ابزار SynthID Detector را عرضه کرد.",
        "message": "خبر",
        "image_url": "",
        "status": "pending",
    }
    store.save(set(), [], [pending])
    assert store.load_outbox()[0]["display_title"] == pending["display_title"]


def test_migration_recovers_rendered_title_and_summary_from_sent_outbox(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({
        "schema_version": 4,
        "seen": [],
        "published_stories": [{
            "title": "Google's AI detection website is now available",
            "summary": "SynthID Detector will flag AI-generated content.",
            "url": "https://example.com/google-synthid",
        }],
        "outbox": [{
            "key": "url:google",
            "url": "https://example.com/google-synthid",
            "message": "<b>📰 ابزار تشخیص هوش مصنوعی گوگل (Google) منتشر شد</b>\\n\\nگوگل (Google) ابزار SynthID Detector را عرضه کرد.\\n\\n<details><summary>متن کامل</summary><p>متن کامل</p></details>",
            "image_url": "",
            "status": "sent",
        }],
    }), encoding="utf-8")

    store = StateStore(path)
    record = store.load_records()[0]

    assert record["display_title"] == "ابزار تشخیص هوش مصنوعی گوگل (Google) منتشر شد"
    assert record["display_summary"] == "گوگل (Google) ابزار SynthID Detector را عرضه کرد."
