import json

from app import ai


class FakeResponse:
    ok = True

    def json(self):
        return {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps(
                                    {
                                        "title_fa": "عنوان",
                                        "summary_fa": "خلاصه",
                                        "article_fa": "متن کامل",
                                        "category": "technology",
                                        "importance": 4,
                                        "tags": ["فناوری"],
                                    }
                                )
                            }
                        ]
                    }
                }
            ]
        }


def test_process_with_gemini_retries_after_timeout(monkeypatch):
    calls = {"count": 0}

    def fake_post(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise ai.requests.exceptions.ReadTimeout("temporary")
        return FakeResponse()

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(ai.requests, "post", fake_post)
    monkeypatch.setattr(ai.time, "sleep", lambda *_: None)

    result = ai.process_with_gemini("Title", "Summary")

    assert calls["count"] == 2
    assert result["category"] == "technology"


def test_process_with_gemini_retries_transient_503(monkeypatch):
    calls = {"count": 0}

    class BusyResponse(FakeResponse):
        ok = False
        status_code = 503
        text = "temporarily unavailable"

    def fake_post(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] < 3:
            return BusyResponse()
        return FakeResponse()

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(ai.requests, "post", fake_post)
    monkeypatch.setattr(ai.time, "sleep", lambda *_: None)

    result = ai.process_with_gemini("Title", "Summary")

    assert calls["count"] == 3
    assert result["category"] == "technology"


def _response_with(payload_text):
    class Response:
        ok = True

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": payload_text}]}}]}

    return Response()


def test_process_with_gemini_rejects_missing_required_keys(monkeypatch):
    import pytest

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(
        ai.requests, "post",
        lambda *a, **k: _response_with(json.dumps({"summary_fa": "x", "category": "technology"})),
    )
    with pytest.raises(RuntimeError, match="title_fa"):
        ai.process_with_gemini("Title", "Summary")


def test_process_with_gemini_rejects_invalid_json(monkeypatch):
    import pytest

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(ai.requests, "post", lambda *a, **k: _response_with("not json"))
    with pytest.raises(RuntimeError, match="invalid JSON"):
        ai.process_with_gemini("Title", "Summary")


def test_process_with_gemini_requests_json_and_keeps_key_out_of_url(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setenv("GEMINI_API_KEY", "secret-key")
    monkeypatch.setattr(ai.requests, "post", fake_post)

    ai.process_with_gemini("Title", "Summary")

    assert captured["json"]["generationConfig"]["responseMimeType"] == "application/json"
    assert "secret-key" not in captured["url"]
    assert "params" not in captured
    assert captured["headers"]["x-goog-api-key"] == "secret-key"


def test_missing_article_falls_back_to_summary(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(
        ai.requests, "post",
        lambda *a, **k: _response_with(json.dumps({
            "title_fa": "t", "summary_fa": "خلاصه", "category": "technology"
        })),
    )
    assert ai.process_with_gemini("Title", "Summary")["article_fa"] == "خلاصه"
