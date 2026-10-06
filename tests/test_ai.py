import app.ai as ai


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def test_process_with_gemini_uses_current_default_model(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        return FakeResponse({
            "candidates": [{
                "content": {
                    "parts": [{
                        "text": '{"title_fa":"عنوان","summary_fa":"خلاصه","category":"technology","importance":4,"tags":["AI"]}'
                    }]
                }
            }]
        })

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.setattr(ai.requests, "post", fake_post)

    result = ai.process_with_gemini("Title", "Summary")

    assert result["title_fa"] == "عنوان"
    assert captured["url"].endswith("/v1beta/models/gemini-2.5-pro:generateContent")


def test_process_with_gemini_allows_model_override(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        return FakeResponse({
            "candidates": [{
                "content": {
                    "parts": [{
                        "text": '{"title_fa":"عنوان","summary_fa":"خلاصه","category":"general","importance":3,"tags":[]}'
                    }]
                }
            }]
        })

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-pro")
    monkeypatch.setattr(ai.requests, "post", fake_post)

    ai.process_with_gemini("Title", "Summary")

    assert captured["url"].endswith("/v1beta/models/gemini-3.7-flash:generateContent")
