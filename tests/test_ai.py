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
