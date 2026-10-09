from app.collector import collect_feed


def test_collect_feed_returns_empty_list_when_feed_request_fails(monkeypatch):
    import requests

    def fail(*args, **kwargs):
        raise requests.RequestException("network down")

    monkeypatch.setattr("app.collector.requests.get", fail)

    assert collect_feed("https://example.com/feed", "Example") == []


def test_safe_get_does_not_append_cache_busting_query_parameters(monkeypatch):
    import app.collector as collector

    captured = {}

    class Response:
        status_code = 200
        headers = {}

        def raise_for_status(self):
            pass

    monkeypatch.setattr(collector, "_validate_url", lambda url: url)

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return Response()

    monkeypatch.setattr(collector.requests, "get", fake_get)

    collector._safe_get("https://www.theverge.com/rss/index.xml")

    assert captured["url"] == "https://www.theverge.com/rss/index.xml"
    assert "params" not in captured
    assert captured["headers"]["Cache-Control"] == "no-cache, no-store, max-age=0"


def test_article_page_is_downloaded_once_for_text_and_image(monkeypatch):
    import app.collector as collector

    calls = []

    class Response:
        headers = {"Content-Type": "image/jpeg"}
        text = ('<html><head><meta property="og:image" content="https://example.com/a.jpg">'
                "</head><body><p>Article body</p></body></html>")

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
    assert calls == [url, "https://example.com/a.jpg"]
    collector._ARTICLE_CACHE.clear()


def test_rejects_private_or_local_article_urls(monkeypatch):
    import pytest
    import app.collector as collector

    with pytest.raises(ValueError):
        collector.fetch_article_text("http://127.0.0.1/admin")
    with pytest.raises(ValueError):
        collector.fetch_article_text("http://localhost/admin")
    with pytest.raises(ValueError):
        collector.fetch_article_text("file:///etc/passwd")


def test_rejects_oversized_article_response(monkeypatch):
    import pytest
    import app.collector as collector

    class Response:
        headers = {"Content-Length": str(collector.MAX_RESPONSE_BYTES + 1)}
        content = b""
        text = ""

        def raise_for_status(self):
            pass

    monkeypatch.setattr(collector.requests, "get", lambda *a, **k: Response())
    with pytest.raises(ValueError, match="response too large"):
        collector.fetch_article_text("https://example.com/large")


def test_collect_feed_preserves_entry_category_and_label_tags(monkeypatch):
    import app.collector as collector

    class Response:
        status_code = 200
        headers = {}
        content = b"feed"

        def raise_for_status(self):
            pass

    class Parsed:
        entries = [{
            "id": "story-1",
            "title": "AI story",
            "link": "https://example.com/story",
            "summary": "Technology news",
            "published_parsed": None,
            "tags": [
                {"term": "Technology"},
                {"label": "Artificial Intelligence"},
            ],
        }]

    monkeypatch.setattr(collector, "_safe_get", lambda url: Response())
    monkeypatch.setattr(collector.feedparser, "parse", lambda data: Parsed())
    items = collector.collect_feed("https://example.com/feed", "Example")
    assert items[0].categories == ("Technology", "Artificial Intelligence")


def test_article_fetch_uses_short_timeout(monkeypatch):
    import app.collector as collector

    captured = {}

    class Response:
        headers = {}
        content = b"<html><body>Article</body></html>"
        encoding = "utf-8"

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size=65536):
            yield self.content

    def fake_get(url, **kwargs):
        captured.update(kwargs)
        return Response()

    monkeypatch.setattr(collector.requests, "get", fake_get)
    collector._ARTICLE_CACHE.clear()
    collector.fetch_article_text("https://example.com/timeout-check")
    assert captured["timeout"] == 10
    collector._ARTICLE_CACHE.clear()


def test_public_host_resolution_has_a_timeout(monkeypatch):
    import app.collector as collector

    captured = {}

    def fake_getaddrinfo(*args, **kwargs):
        captured["timeout"] = collector.socket.getdefaulttimeout()
        raise collector.socket.timeout("dns timeout")

    monkeypatch.setattr(collector.socket, "getaddrinfo", fake_getaddrinfo)
    with __import__("pytest").raises(ValueError, match="unable to resolve"):
        collector._is_public_host("example.com")
    assert captured["timeout"] == 3
