import json
import os
from pathlib import Path

STATE_SCHEMA_VERSION = 3
MAX_SEEN = 5000
MAX_PUBLISHED_STORIES = 500
MAX_OUTBOX = 100


class StateStoreError(RuntimeError):
    """The deduplication state cannot be trusted."""


class StateStore:
    def __init__(self, path="data/state.json"):
        self.path = Path(path)

    def _read(self):
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise StateStoreError(f"state file cannot be read safely: {self.path}") from exc
        if not isinstance(data, dict):
            raise StateStoreError("state root must be an object")
        return data

    @staticmethod
    def _migrate(data):
        if not isinstance(data, dict):
            raise StateStoreError("state root must be an object")
        raw_seen = data.get("seen", [])
        raw_records = data.get("published_stories", [])
        raw_outbox = data.get("outbox", [])
        if not isinstance(raw_seen, list) or not all(isinstance(value, str) for value in raw_seen):
            raise StateStoreError("state seen list is invalid")
        if not isinstance(raw_records, list):
            raise StateStoreError("state published_stories list is invalid")
        if not isinstance(raw_outbox, list):
            raise StateStoreError("state outbox list is invalid")
        migrated_records = []
        for record in raw_records:
            if not isinstance(record, dict):
                raise StateStoreError("state contains an invalid published story")
            migrated_records.append({
                "title": record.get("title", "") if isinstance(record.get("title", ""), str) else "",
                "summary": record.get("summary", "") if isinstance(record.get("summary", ""), str) else "",
                "url": record.get("url", "") if isinstance(record.get("url", ""), str) else "",
            })
        outbox = []
        for record in raw_outbox:
            if not isinstance(record, dict):
                raise StateStoreError("state contains an invalid outbox record")
            if not isinstance(record.get("key"), str) or not isinstance(record.get("message"), str):
                raise StateStoreError("state outbox record is invalid")
            status = record.get("status", "pending")
            if status not in {"pending", "sent"}:
                raise StateStoreError("state outbox status is invalid")
            outbox.append({
                "key": record["key"],
                "url": record.get("url", "") if isinstance(record.get("url", ""), str) else "",
                "message": record["message"],
                "image_url": record.get("image_url", "") if isinstance(record.get("image_url", ""), str) else "",
                "status": status,
            })
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "seen": raw_seen,
            "published_stories": migrated_records,
            "outbox": outbox,
        }, data.get("schema_version") != STATE_SCHEMA_VERSION

    def _migrated_data(self):
        data, changed = self._migrate(self._read())
        if changed and self.path.exists():
            self._write(data)
        return data

    def _write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp_path, self.path)

    def load(self):
        return set(self._migrated_data().get("seen", []))

    def load_records(self):
        return self._migrated_data().get("published_stories", [])

    def load_outbox(self):
        return self._migrated_data().get("outbox", [])

    def save(self, seen, records=None, outbox=None):
        current, _ = self._migrate(self._read())
        previous = [value for value in current.get("seen", []) if value in seen]
        previous_set = set(previous)
        ordered = previous + sorted(value for value in seen if value not in previous_set)
        if records is None:
            records = current.get("published_stories", [])
        if not isinstance(records, list):
            raise StateStoreError("records must be a list")
        if outbox is None:
            outbox = current.get("outbox", [])
        if not isinstance(outbox, list):
            raise StateStoreError("outbox must be a list")
        data = {
            "schema_version": STATE_SCHEMA_VERSION,
            "seen": ordered[-MAX_SEEN:],
            "published_stories": list(records)[-MAX_PUBLISHED_STORIES:],
            "outbox": list(outbox)[-MAX_OUTBOX:],
        }
        self._write(data)

    def complete_outbox(self, key):
        data = self._migrated_data()
        changed = False
        for record in data.get("outbox", []):
            if record.get("key") == key and record.get("status") != "sent":
                record["status"] = "sent"
                changed = True
        if changed:
            self._write(data)
