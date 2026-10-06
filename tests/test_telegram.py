from app.telegram import build_rich_message_payload

def test_rich_message_embeds_photo_and_text_in_one_message():
    payload = build_rich_message_payload(
        "<b>تیتر خبر</b><details><summary>مشاهده متن کامل خبر</summary><p>متن کامل</p></details>",
        "https://example.com/hero.jpg",
    )
    rich = payload["rich_message"]
    assert rich["html"].startswith('<img src="tg://photo?id=hero"/>')
    assert rich["media"] == [
        {
            "id": "hero",
            "media": {"type": "photo", "media": "https://example.com/hero.jpg"},
        }
    ]
    assert "تیتر خبر" in rich["html"]
    assert "متن کامل" in rich["html"]

def test_rich_message_without_image_keeps_text():
    payload = build_rich_message_payload("<b>تیتر خبر</b>", "")
    assert payload["rich_message"]["html"] == "<b>تیتر خبر</b>"
    assert "media" not in payload["rich_message"]
