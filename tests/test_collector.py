from app.collector import collect_feed


def test_collect_feed_returns_empty_list_when_feed_request_fails(monkeypatch):
    import requests

    def fail(*args, **kwargs):
        raise requests.RequestException("network down")

    monkeypatch.setattr("app.collector.requests.get", fail)

    assert collect_feed("https://example.com/feed", "Example") == []


def test_article_page_is_downloaded_once_for_text_and_image(monkeypatch):
    import app.collector as collector

    calls = []

    class Response:
        text = (
            '<html><head><meta property="og:image" content="https://example.com/a.jpg">'
            "</head><body><p>Article body</p></body></html>"
        )

        def raise_for_status(self):
            pass

    def fake_get(url, **kwargs):
        calls.append(url)
        return Response()

    collector._ARTICLE_CACHE.clear()
    monkeypatch.setattr(collector.requests, "get", fake_get)

    url = "https://example.com/once"
    assert "Article body" in collector.fetch_article_text(url)
    assert collector.fetch_article_image_url(url) == "https://example.com/a.jpg"
    assert calls == [url]
    collector._ARTICLE_CACHE.clear()
