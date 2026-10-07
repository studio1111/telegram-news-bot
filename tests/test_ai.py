import json
import pytest
from app import ai


class FakeResponse:
    ok = True
    def json(self):
        return {"candidates":[{"content":{"parts":[{"text":json.dumps({"title_fa":"عنوان","summary_fa":"خلاصه","article_fa":"متن کامل","category":"technology","importance":4,"tags":["فناوری"]})}]}}]}


def _response_with(payload_text):
    class Response:
        ok = True
        def json(self): return {"candidates":[{"content":{"parts":[{"text":payload_text}]}}]}
    return Response()


def test_process_with_gemini_retries_after_timeout(monkeypatch):
    calls={"count":0}
    def fake_post(*args,**kwargs):
        calls["count"]+=1
        if calls["count"]==1: raise ai.requests.exceptions.ReadTimeout("temporary")
        return FakeResponse()
    monkeypatch.setenv("GEMINI_API_KEY","test-key"); monkeypatch.setattr(ai.requests,"post",fake_post); monkeypatch.setattr(ai.time,"sleep",lambda *_:None)
    assert ai.process_with_gemini("Title","Summary")["category"]=="technology"


def test_process_with_gemini_retries_transient_503(monkeypatch):
    calls={"count":0}
    class BusyResponse(FakeResponse):
        ok=False; status_code=503; text="temporarily unavailable"
    def fake_post(*args,**kwargs):
        calls["count"]+=1
        return BusyResponse() if calls["count"]<3 else FakeResponse()
    monkeypatch.setenv("GEMINI_API_KEY","test-key"); monkeypatch.setattr(ai.requests,"post",fake_post); monkeypatch.setattr(ai.time,"sleep",lambda *_:None)
    assert ai.process_with_gemini("Title","Summary")["category"]=="technology" and calls["count"]==3


def test_process_with_gemini_rejects_missing_required_keys(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY","test-key")
    monkeypatch.setattr(ai.requests,"post",lambda *a,**k:_response_with(json.dumps({"summary_fa":"x","category":"technology"})))
    with pytest.raises(RuntimeError,match="title_fa"): ai.process_with_gemini("Title","Summary")


def test_process_with_gemini_rejects_invalid_json(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY","test-key"); monkeypatch.setattr(ai.requests,"post",lambda *a,**k:_response_with("not json"))
    with pytest.raises(RuntimeError,match="invalid JSON"): ai.process_with_gemini("Title","Summary")


def test_process_with_gemini_requests_json_and_keeps_key_out_of_url(monkeypatch):
    captured={}
    def fake_post(url,**kwargs): captured.update(kwargs); captured["url"]=url; return FakeResponse()
    monkeypatch.setenv("GEMINI_API_KEY","secret-key"); monkeypatch.setattr(ai.requests,"post",fake_post)
    ai.process_with_gemini("Title","Summary")
    assert captured["json"]["generationConfig"]["responseMimeType"]=="application/json" and "secret-key" not in captured["url"] and "params" not in captured and captured["headers"]["x-goog-api-key"]=="secret-key"


def test_missing_article_falls_back_to_summary(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY","test-key")
    monkeypatch.setattr(ai.requests,"post",lambda *a,**k:_response_with(json.dumps({"title_fa":"t","summary_fa":"خلاصه","category":"technology"})))
    assert ai.process_with_gemini("Title","Summary")["article_fa"]=="خلاصه"


def test_gemini_rejects_unknown_or_non_technology_category_for_tech_news(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY","test-key")
    payload={"title_fa":"عنوان","summary_fa":"خلاصه","article_fa":"AI model","category":"political"}
    monkeypatch.setattr(ai.requests,"post",lambda *a,**k:_response_with(json.dumps(payload)))
    with pytest.raises(RuntimeError,match="category"):
        ai.process_with_gemini("OpenAI launches AI model","AI model released")
