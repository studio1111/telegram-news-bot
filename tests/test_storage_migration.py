import json

from app.storage import STATE_SCHEMA_VERSION, StateStore


def test_legacy_records_get_schema_version_and_empty_url(tmp_path):
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

    assert records[0]["url"] == ""
    assert records[1]["url"] == "https://example.com/new"
    assert data["schema_version"] == STATE_SCHEMA_VERSION
    assert all(set(record) == {"title", "summary", "url"} for record in data["published_stories"])


def test_migration_does_not_guess_legacy_urls(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({
            "seen": ["legacy-id"],
            "published_stories": [{"title": "Old story", "summary": "Old summary"}],
        }),
        encoding="utf-8",
    )

    store = StateStore(path)
    assert store.load_records() == [{"title": "Old story", "summary": "Old summary", "url": ""}]
