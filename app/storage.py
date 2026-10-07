import json
import os
from pathlib import Path

STATE_SCHEMA_VERSION = 2
MAX_SEEN = 5000
MAX_PUBLISHED_STORIES = 500


class StateStore:
    def __init__(self, path="data/state.json"):
        self.path = Path(path)

    def _read(self):
        if not self.path.exists():
            return {"schema_version": STATE_SCHEMA_VERSION, "seen": [], "published_stories": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"state file is unreadable: {self.path}") from exc
        if not isinstance(data, dict):
            raise RuntimeError("state file must contain a JSON object")
        return data

    @staticmethod
    def _migrate(data):
        seen = data.get("seen", [])
        records = data.get("published_stories", [])
        if not isinstance(seen, list) or not all(isinstance(value, str) for value in seen):
            raise RuntimeError("state.seen must be a list of strings")
        if not isinstance(records, list):
            raise RuntimeError("state.published_stories must be a list")
        normalized=[]
        for record in records:
            if not isinstance(record, dict): raise RuntimeError("published record must be an object")
            normalized.append({
                "title": record.get("title", "") if isinstance(record.get("title", ""), str) else "",
                "summary": record.get("summary", "") if isinstance(record.get("summary", ""), str) else "",
                "url": record.get("url", "") if isinstance(record.get("url", ""), str) else "",
            })
        return {"schema_version": STATE_SCHEMA_VERSION, "seen": seen, "published_stories": normalized}

    def _write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp=self.path.with_suffix(self.path.suffix+".tmp")
        tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
        os.replace(tmp,self.path)

    def _data(self):
        return self._migrate(self._read())

    def load(self):
        return set(self._data()["seen"])

    def load_records(self):
        return self._data()["published_stories"]

    def save(self, seen, records=None):
        current=self._data(); previous=[v for v in current["seen"] if v in seen]; previous_set=set(previous)
        ordered=previous+sorted(v for v in seen if v not in previous_set)
        if records is None: records=current["published_stories"]
        self._write({"schema_version":STATE_SCHEMA_VERSION,"seen":ordered[-MAX_SEEN:],"published_stories":list(records)[-MAX_PUBLISHED_STORIES:]})
