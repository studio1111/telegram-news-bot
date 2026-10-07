import json

from app.storage import STATE_SCHEMA_VERSION, StateStore


def test_legacy_records_drop_empty_url_and_keep_valid_url(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({
            "seen": ["legacy-id"],
            "published_stories": [
                {"title": "Old story", "summary": "Old summary"},
                {"title": "New story", "summary": "New summary", "url": "https://example.com/new"},
            ],
        }),
        encoding="utf-8",
    )

    store = StateStore(path)
    records = store.load_records()
    data = json.loads(path.read_text(encoding="utf-8"))

    assert records == [{"title": "New story", "summary": "New summary", "url": "https://example.com/new"}]
    assert data["schema_version"] == STATE_SCHEMA_VERSION
    assert data["published_stories"] == [{"title": "New story", "summary": "New summary", "url": "https://example.com/new"}]


def test_migration_drops_legacy_records_without_urls(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({
            "seen": ["legacy-id"],
            "published_stories": [{"title": "Old story", "summary": "Old summary"}],
        }),
        encoding="utf-8",
    )

    store = StateStore(path)
    assert store.load_records() == []
