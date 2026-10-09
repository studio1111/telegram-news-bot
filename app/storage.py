import html
import json
import os
import re
from pathlib import Path

STATE_SCHEMA_VERSION = 6
MAX_SEEN = 5000
MAX_PUBLISHED_STORIES = 500
MAX_OUTBOX = 100


class StateStoreError(RuntimeError):
    """The deduplication state cannot be trusted."""


class StateStore:
    @staticmethod
    def _extract_rendered_fields(message):
        if not isinstance(message, str) or not message:
            return "", ""
        title_match = re.search(r"<b>📰\s*(.*?)</b>", message, flags=re.DOTALL)
        summary_match = re.search(r"</b>\s*\n+\s*(.*?)\s*\n+\s*<details\b", message, flags=re.DOTALL)
        def clean(value):
            value = html.unescape(value or "")
            value = re.sub(r"<[^>]+>", " ", value)
            return re.sub(r"\s+", " ", value).strip()
        return clean(title_match.group(1) if title_match else ""), clean(summary_match.group(1) if summary_match else "")

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
        raw_pending_news = data.get("pending_news", [])
        if not isinstance(raw_seen, list) or not all(isinstance(value, str) for value in raw_seen):
            raise StateStoreError("state seen list is invalid")
        if not isinstance(raw_records, list):
            raise StateStoreError("state published_stories list is invalid")
        if not isinstance(raw_outbox, list):
            raise StateStoreError("state outbox list is invalid")
        if not isinstance(raw_pending_news, list):
            raise StateStoreError("state pending_news list is invalid")
        migrated_records = []
        dropped_empty_url_records = False
        for record in raw_records:
            if not isinstance(record, dict):
                raise StateStoreError("state contains an invalid published story")
            url = record.get("url", "") if isinstance(record.get("url", ""), str) else ""
            if not url.strip():
                dropped_empty_url_records = True
                continue
            migrated = {
                "title": record.get("title", "") if isinstance(record.get("title", ""), str) else "",
                "summary": record.get("summary", "") if isinstance(record.get("summary", ""), str) else "",
                "url": url.strip(),
            }
            for field in ("display_title", "display_summary"):
                if isinstance(record.get(field, ""), str) and record.get(field, "").strip():
                    migrated[field] = record[field].strip()
            migrated_records.append(migrated)
        outbox = []
        for record in raw_outbox:
            if not isinstance(record, dict):
                raise StateStoreError("state contains an invalid outbox record")
            if not isinstance(record.get("key"), str) or not isinstance(record.get("message"), str):
                raise StateStoreError("state outbox record is invalid")
            status = record.get("status", "pending")
            if status not in {"pending", "sent"}:
                raise StateStoreError("state outbox status is invalid")
            migrated_outbox = {
                "key": record["key"],
                "url": record.get("url", "") if isinstance(record.get("url", ""), str) else "",
                "message": record["message"],
                "image_url": record.get("image_url", "") if isinstance(record.get("image_url", ""), str) else "",
                "status": status,
            }
            for field in ("title", "summary", "display_title", "display_summary"):
                if isinstance(record.get(field, ""), str) and record.get(field, "").strip():
                    migrated_outbox[field] = record[field].strip()
            outbox.append(migrated_outbox)

        migrated_pending_news = []
        for record in raw_pending_news:
            if not isinstance(record, dict):
                raise StateStoreError("state contains an invalid pending news record")
            url = record.get("url", "")
            if not isinstance(url, str) or not url.strip():
                raise StateStoreError("state pending news record has an invalid URL")
            item_id = record.get("item_id", "")
            if not isinstance(item_id, str) or not item_id.strip():
                item_id = url.strip()
            title = record.get("title", "")
            summary = record.get("summary", "")
            source = record.get("source", "")
            image_url = record.get("image_url", "")
            for field_name, value in (
                ("title", title),
                ("summary", summary),
                ("source", source),
                ("image_url", image_url),
            ):
                if not isinstance(value, str):
                    raise StateStoreError(f"state pending news {field_name} is invalid")
            published_at = record.get("published_at")
            if published_at is not None and not isinstance(published_at, str):
                raise StateStoreError("state pending news published_at is invalid")
            categories = record.get("categories", [])
            if not isinstance(categories, list) or not all(isinstance(value, str) for value in categories):
                raise StateStoreError("state pending news categories are invalid")
            migrated_pending_news.append({
                "item_id": item_id,
                "title": title,
                "url": url.strip(),
                "summary": summary,
                "source": source,
                "image_url": image_url,
                "published_at": published_at,
                "categories": categories,
            })

        rendered_enriched = False
        for record in migrated_records:
            if record.get("display_title") and record.get("display_summary"):
                continue
            url = record.get("url", "")
            for outbox_record in outbox:
                if outbox_record.get("status") != "sent" or outbox_record.get("url") != url:
                    continue
                title, summary = StateStore._extract_rendered_fields(outbox_record.get("message", ""))
                if title or summary:
                    if title and not record.get("display_title"):
                        record["display_title"] = title
                    if summary and not record.get("display_summary"):
                        record["display_summary"] = summary
                    rendered_enriched = True
                break

        changed = (
            data.get("schema_version") != STATE_SCHEMA_VERSION
            or dropped_empty_url_records
            or rendered_enriched
            or raw_pending_news != migrated_pending_news
        )
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "seen": raw_seen,
            "published_stories": migrated_records,
            "outbox": outbox,
            "pending_news": migrated_pending_news,
        }, changed

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

    def load_pending_news(self):
        return self._migrated_data().get("pending_news", [])

    def save(self, seen, records=None, outbox=None, pending_news=None):
        current, _ = self._migrate(self._read())
        previous = [value for value in current.get("seen", []) if value in seen]
        previous_set = set(previous)
        ordered = previous + sorted(value for value in seen if value not in previous_set)
        if records is None:
            records = current.get("published_stories", [])
        if not isinstance(records, list):
            raise StateStoreError("records must be a list")
        records = [record for record in records
                   if isinstance(record, dict) and isinstance(record.get("url"), str) and record["url"].strip()]
        if outbox is None:
            outbox = current.get("outbox", [])
        if not isinstance(outbox, list):
            raise StateStoreError("outbox must be a list")
        if pending_news is None:
            pending_news = current.get("pending_news", [])
        if not isinstance(pending_news, list):
            raise StateStoreError("pending_news must be a list")
        for record in pending_news:
            if (
                not isinstance(record, dict)
                or not isinstance(record.get("url"), str)
                or not record["url"].strip()
            ):
                raise StateStoreError("pending_news contains an invalid record")
        data = {
            "schema_version": STATE_SCHEMA_VERSION,
            "seen": ordered[-MAX_SEEN:],
            "published_stories": list(records)[-MAX_PUBLISHED_STORIES:],
            "outbox": list(outbox)[-MAX_OUTBOX:],
            "pending_news": list(pending_news),
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
