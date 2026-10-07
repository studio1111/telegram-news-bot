import pytest
import app.telegram as telegram
from app.telegram import build_rich_message_payload


def test_rich_message_embeds_photo_and_text_in_one_message():
    payload=build_rich_message_payload("<b>تیتر خبر</b><details><summary>مشاهده متن کامل خبر</summary><p>متن کامل</p></details>","https://example.com/hero.jpg"); rich=payload["rich_message"]; assert rich["html"].startswith('<img src="tg://photo?id=hero"/>'); assert rich["media"]==[{"id":"hero","media":{"type":"photo","media":"https://example.com/hero.jpg"}}]
def test_rich_message_without_image_keeps_text():
    payload=build_rich_message_payload("<b>تیتر خبر</b>",""); assert payload["rich_message"]["html"]=="<b>تیتر خبر</b>" and "media" not in payload["rich_message"]
def test_post_raises_when_telegram_returns_ok_false(monkeypatch):
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"ok":False,"description":"chat not found"}
    monkeypatch.setattr(telegram.requests,"post",lambda *args,**kwargs:Response())
    with pytest.raises(RuntimeError,match="chat not found"): telegram._post("token","sendMessage",{"chat_id":"chat","text":"hello"})

def test_rich_message_falls_back_to_send_message_when_rich_api_rejects(monkeypatch):
    calls=[]
    class Response:
        def __init__(self,payload): self.payload=payload
        def raise_for_status(self): pass
        def json(self): return self.payload
    def fake_post(url,json,timeout):
        calls.append((url,json)); return Response({"ok":False,"error_code":400,"description":"rich messages are unavailable"}) if url.endswith("/sendRichMessage") else Response({"ok":True,"result":{"message_id":123}})
    monkeypatch.setattr(telegram.requests,"post",fake_post); monkeypatch.setenv("TELEGRAM_BOT_TOKEN","test-token"); monkeypatch.setenv("TELEGRAM_CHAT_ID","@channel")
    result=telegram.publish_rich_message("<b>تیتر خبر</b><details><p>متن کامل</p></details>",""); assert result["ok"] is True; assert calls[1][1]["chat_id"]=="@channel"; assert "parse_mode" not in calls[1][1]; assert "<details>" not in calls[1][1]["text"]

def test_fallback_runs_when_rich_api_returns_http_400(monkeypatch):
    calls=[]
    class R:
        def __init__(self,status,payload): self.status_code=status; self.payload=payload
        def raise_for_status(self):
            if self.status_code>=400: raise telegram.requests.HTTPError(str(self.status_code))
        def json(self): return self.payload
    def fake_post(url,json,timeout):
        calls.append(url); return R(400,{"ok":False,"error_code":400,"description":"Bad Request: method not found"}) if url.endswith("/sendRichMessage") else R(200,{"ok":True,"result":{"message_id":1}})
    monkeypatch.setattr(telegram.requests,"post",fake_post); monkeypatch.setenv("TELEGRAM_BOT_TOKEN","t"); monkeypatch.setenv("TELEGRAM_CHAT_ID","@c")
    assert telegram.publish_rich_message("<b>تیتر</b>","")["ok"] is True and calls[1].endswith("/sendMessage")

def test_network_timeout_does_not_trigger_fallback(monkeypatch):
    calls=[]
    def fail_post(url, **kwargs):
        calls.append(url)
        raise telegram.requests.exceptions.ReadTimeout("slow")
    monkeypatch.setattr(telegram.requests,"post",fail_post)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN","t"); monkeypatch.setenv("TELEGRAM_CHAT_ID","@c")
    with pytest.raises(telegram.requests.exceptions.ReadTimeout): telegram.publish_rich_message("<b>تیتر</b>","")
    assert len(calls)==1

def test_rate_limit_is_retried_after_retry_after(monkeypatch):
    responses=[type("R",(),{"status_code":429,"json":lambda self:{"ok":False,"error_code":429,"description":"Too Many Requests","parameters":{"retry_after":3}}})(),type("R",(),{"status_code":200,"json":lambda self:{"ok":True,"result":{}}})()]; sleeps=[]
    monkeypatch.setattr(telegram.requests,"post",lambda *a,**k:responses.pop(0)); monkeypatch.setattr(telegram.time,"sleep",lambda s:sleeps.append(s)); assert telegram._post("t","sendMessage",{})["ok"] is True and sleeps==[4]

def test_long_fallback_keeps_source_and_footer():
    body="متن "*3000; plain=f"تیتر\n\n{body}\n📡 منبع: TechCrunch\n{telegram.FOOTER_MARKER}"; result=telegram._truncate_plain(plain); assert len(result)<=telegram.TELEGRAM_MESSAGE_LIMIT and result.endswith("📡 منبع: TechCrunch\n"+telegram.FOOTER_MARKER)


def test_fallback_plain_text_never_interprets_html_as_markup(monkeypatch):
    calls=[]
    class R:
        def __init__(self,p): self.p=p
        def json(self): return self.p
        def raise_for_status(self): pass
    def fake_post(url,json,timeout):
        calls.append(json)
        return R({"ok":False,"error_code":400,"description":"unsupported"}) if url.endswith("/sendRichMessage") else R({"ok":True,"result":{}})
    monkeypatch.setattr(telegram.requests,"post",fake_post); monkeypatch.setenv("TELEGRAM_BOT_TOKEN","t"); monkeypatch.setenv("TELEGRAM_CHAT_ID","@c")
    telegram.publish_rich_message("<b>عنوان</b><p>&lt;script&gt;نه&lt;/script&gt;</p>","")
    assert "parse_mode" not in calls[1] and "<script>" in calls[1]["text"]
