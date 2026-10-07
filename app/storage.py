import json
import os
from pathlib import Path

# State schema 2 adds explicit URLs to published records. Legacy records are
# preserved with an empty URL because the original URL was not stored and
# cannot be reconstructed safely from a title/summary.
STATE_SCHEMA_VERSION = 2
MAX_SEEN = 5000
MAX_PUBLISHED_STORIES = 500


class StateStore:
    def __init__(self, path="data/state.json"):
        self.path = Path(path)

    def _read(self):
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _migrate(data):
        """Normalize old state without inventing missing URLs.

        Before schema 2, published_stories omitted ``url``. Adding an empty
        value makes the shape stable while avoiding unsafe URL guesses. New
        records written by main.py retain their real URL and therefore use
        URL-based deduplication normally.
        """
        if not isinstance(data, dict):
            data = {}
        changed = data.get("schema_version") != STATE_SCHEMA_VERSION
        raw_seen = data.get("seen", [])
        seen = raw_seen if isinstance(raw_seen, list) else []
        if seen != raw_seen:
            changed = True

        raw_records = data.get("published_stories", [])
        records = raw_records if isinstance(raw_records, list) else []
        if records != raw_records:
            changed = True

        migrated_records = []
        for record in records:
            if not isinstance(record, dict):
                changed = True
                continue
            normalized = {
                "title": record.get("title", "") if isinstance(record.get("title", ""), str) else "",
                "summary": record.get("summary", "") if isinstance(record.get("summary", ""), str) else "",
                "url": record.get("url", "") if isinstance(record.get("url", ""), str) else "",
            }
            if normalized != record:
                changed = True
            migrated_records.append(normalized)

        migrated = {
            "schema_version": STATE_SCHEMA_VERSION,
            "seen": seen,
            "published_stories": migrated_records,
        }
        return migrated, changed

    def _migrated_data(self):
        data, changed = self._migrate(self._read())
        if changed and self.path.exists():
            # Persist the schema upgrade immediately and atomically. This makes
            # migration independent of whether the publisher has candidates.
            self._write(data)
        return data

    def _write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp_path, self.path)

    def load(self):
        return set(self._migrated_data().get("seen", []))

    def load_records(self):
        records = self._migrated_data().get("published_stories", [])
        return records if isinstance(records, list) else []

    def save(self, seen, records=None):
        """Persist state atomically, preserving insertion order of "seen"."""
        current, _ = self._migrate(self._read())
        previous = [value for value in current.get("seen", []) if value in seen]
        previous_set = set(previous)
        ordered = previous + sorted(value for value in seen if value not in previous_set)
        if records is None:
            records = current.get("published_stories", [])
        data = {
            "schema_version": STATE_SCHEMA_VERSION,
            "seen": ordered[-MAX_SEEN:],
            "published_stories": list(records)[-MAX_PUBLISHED_STORIES:],
        }
        self._write(data)
