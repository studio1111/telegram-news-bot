import html

from app import rubika


def test_rubika_disabled_without_both_secrets(monkeypatch):
    monkeypatch.delenv("RUBIKA_BOT_TOKEN", raising=False)
    monkeypatch.setenv("RUBIKA_CHANNEL_ID", "MyNewsTechnology")
    assert not rubika.rubika_enabled()


def test_rubika_enabled_with_both_secrets(monkeypatch):
    monkeypatch.setenv("RUBIKA_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("RUBIKA_CHANNEL_ID", "MyNewsTechnology")
    assert rubika.rubika_enabled()


def test_plain_text_removes_html_and_decodes_entities():
    value = rubika._plain_text("<b>📰 عنوان</b><br>متن &amp; توضیح")
    assert "<b>" not in value
    assert "عنوان" in value
    assert "متن & توضیح" in value


def test_rubika_requires_image(monkeypatch):
    monkeypatch.setenv("RUBIKA_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("RUBIKA_CHANNEL_ID", "MyNewsTechnology")
    try:
        rubika.publish_rubika_message("<b>خبر</b>", "")
    except RuntimeError as exc:
        assert "requires an image" in str(exc)
    else:
        raise AssertionError("text-only Rubika publication should be rejected")
