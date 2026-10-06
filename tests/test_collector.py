from app.collector import collect_feed


def test_collect_feed_returns_empty_list_when_feed_request_fails(monkeypatch):
    import requests

    def fail(*args, **kwargs):
        raise requests.RequestException("network down")

    monkeypatch.setattr("app.collector.requests.get", fail)

    assert collect_feed("https://example.com/feed", "Example") == []
