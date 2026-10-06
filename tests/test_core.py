from app.core import normalize_text, is_new_item, build_telegram_message, build_expanded_message

def test_normalize_text_collapses_whitespace():
    assert normalize_text("  خبر   فوری\n\n امروز  ") == "خبر فوری امروز"

def test_is_new_item_uses_stable_id_and_url():
    seen={"abc","https://example.com/old"}
    assert is_new_item("abc","https://example.com/new",seen) is False
    assert is_new_item("xyz","https://example.com/old",seen) is False
    assert is_new_item("xyz","https://example.com/new",seen) is True

def test_build_telegram_message_uses_expanding_article_without_source_url():
    msg=build_telegram_message("عنوان فارسی","خلاصه خبر","فناوری","Example","https://example.com/news")
    assert "عنوان فارسی" in msg
    assert "خلاصه خبر" in msg
    assert "فناوری" in msg
    assert "Example" in msg
    assert "https://example.com/news" not in msg

def test_build_expanded_message_contains_channel_at_end():
    msg=build_expanded_message("عنوان فارسی","متن بازنویسی‌شده و کامل خبر.","TechCrunch")
    assert "<blockquote expandable>" in msg
    assert "متن بازنویسی‌شده و کامل خبر." in msg
    assert "مشاهده متن کامل خبر" in msg
    assert msg.endswith("@MyNewsTechnology")

def test_only_technology_category_is_publishable():
    from app.core import is_technology_news
    assert is_technology_news("technology") is True
    assert is_technology_news("political") is False
    assert is_technology_news("economy") is False
    assert is_technology_news("general") is False

def test_similar_rewrites_of_same_event_are_duplicates():
    from app.core import is_duplicate_story
    first = {
        "title": "Type One Energy raised $200M to build a fusion power plant by 2034",
        "summary": "Type One Energy raised 200 million dollars to build a fusion power plant.",
    }
    rewritten = {
        "title": "Type One Energy raises $200 million for a fusion power plant",
        "summary": "The fusion company secured $200 million to bring a power plant to the grid.",
    }
    assert is_duplicate_story(rewritten, [first]) is True

def test_different_technology_events_are_not_duplicates():
    from app.core import is_duplicate_story
    first = {
        "title": "Type One Energy raised $200M to build a fusion power plant by 2034",
        "summary": "Type One Energy raised 200 million dollars to build a fusion power plant.",
    }
    different = {
        "title": "Type One Energy connects its prototype fusion system to the grid",
        "summary": "The company demonstrated a new prototype milestone at its test facility.",
    }
    assert is_duplicate_story(different, [first]) is False
