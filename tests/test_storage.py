import json

import app.storage as storage
from app.storage import StateStore


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
    store.save(set(), [{"title": str(i)} for i in range(5)])
    assert [r["title"] for r in store.load_records()] == ["3", "4"]


def test_save_roundtrip(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.save({"x"}, [{"title": "t", "summary": "s", "url": "u"}])
    assert store.load() == {"x"}
    assert store.load_records()[0]["url"] == "u"
