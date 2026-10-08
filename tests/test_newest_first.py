from datetime import datetime, timezone

from app.main import _sort_newest_first


def test_sort_newest_first_prioritizes_latest_story():
    old = datetime(2026, 10, 8, 16, 0, tzinfo=timezone.utc)
    newest = datetime(2026, 10, 8, 16, 59, tzinfo=timezone.utc)
    middle = datetime(2026, 10, 8, 16, 30, tzinfo=timezone.utc)

    items = [
        type("Item", (), {"published_at": old})(),
        type("Item", (), {"published_at": newest})(),
        type("Item", (), {"published_at": middle})(),
    ]

    ordered = _sort_newest_first(items)

    assert [item.published_at for item in ordered] == [newest, middle, old]


def test_sort_newest_first_puts_missing_dates_last():
    newest = datetime(2026, 10, 8, 16, 59, tzinfo=timezone.utc)
    items = [
        type("Item", (), {"published_at": None})(),
        type("Item", (), {"published_at": newest})(),
    ]

    ordered = _sort_newest_first(items)

    assert ordered[0].published_at == newest
    assert ordered[1].published_at is None
