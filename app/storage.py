import json
from pathlib import Path

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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "seen": sorted(seen),
            "published_stories": records if records is not None else self.load_records(),
        }
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
