import json
import os
from pathlib import Path

# "seen" used to grow forever. Keep the most recent entries only; this is far
# more than the look-back window can ever produce (about 7 feeds x 100 items).
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

    def load(self):
        return set(self._read().get("seen", []))

    def load_records(self):
        records = self._read().get("published_stories", [])
        return records if isinstance(records, list) else []

    def save(self, seen, records=None):
        """Persist state atomically, preserving insertion order of "seen".

        Older entries keep their original position and new ones are appended,
        so trimming to MAX_SEEN always drops the oldest ids.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        previous = [value for value in self._read().get("seen", []) if value in seen]
        previous_set = set(previous)
        ordered = previous + sorted(value for value in seen if value not in previous_set)
        if records is None:
            records = self.load_records()
        data = {
            "seen": ordered[-MAX_SEEN:],
            "published_stories": list(records)[-MAX_PUBLISHED_STORIES:],
        }
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp_path, self.path)
