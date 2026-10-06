from app.core import is_recent_news
from datetime import datetime, timedelta, timezone


def test_main_uses_five_minute_window():
    now = datetime(2026, 10, 6, 18, 5, tzinfo=timezone.utc)
    assert is_recent_news(now - timedelta(minutes=5), now)
    assert not is_recent_news(now - timedelta(minutes=5, seconds=1), now)
