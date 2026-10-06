import json
from pathlib import Path

class StateStore:
    def __init__(self, path="data/state.json"):
        self.path = Path(path)

    def load(self):
        if not self.path.exists():
            return set()
        try:
            return set(json.loads(self.path.read_text(encoding="utf-8")).get("seen", []))
        except (OSError, json.JSONDecodeError):
            return set()

    def save(self, seen):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"seen": sorted(seen)}, ensure_ascii=False, indent=2), encoding="utf-8")
