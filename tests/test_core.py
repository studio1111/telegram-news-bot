from datetime import datetime, timedelta, timezone
from app.core import NEWS_WINDOW_MINUTES, build_expanded_message, build_rich_message_html, build_telegram_message, is_new_item, is_duplicate_story, is_recent_news, normalize_text


def test_normalize_text_collapses_whitespace(): assert normalize_text("  خبر   فوری\n\n امروز  ") == "خبر فوری امروز"
def test_is_new_item_uses_stable_id_and_url():
    seen={"abc","https://example.com/old"}; assert not is_new_item("abc","https://example.com/new",seen); assert not is_new_item("xyz","https://example.com/old",seen); assert is_new_item("xyz","https://example.com/new",seen)
def test_news_window_is_90_minutes(): assert NEWS_WINDOW_MINUTES == 90
def test_news_window_boundaries():
    now=datetime(2026,10,6,18,30,tzinfo=timezone.utc); assert is_recent_news(now-timedelta(minutes=45),now); assert is_recent_news(now-timedelta(minutes=90),now); assert not is_recent_news(now-timedelta(minutes=90,seconds=1),now); assert is_recent_news(now+timedelta(minutes=1),now); assert not is_recent_news(now+timedelta(hours=1),now)
def test_build_telegram_message_uses_expanding_article_without_source_url():
    msg=build_telegram_message("عنوان فارسی","خلاصه خبر","فناوری","Example","https://example.com/news"); assert all(x in msg for x in ("عنوان فارسی","خلاصه خبر","فناوری","Example")); assert "https://example.com/news" not in msg
def test_build_rich_message_footer():
    msg=build_rich_message_html("عنوان فارسی","خلاصه خبر","متن کامل","TechCrunch"); assert msg.splitlines()[-2]=="📡 منبع: TechCrunch<br>"; assert msg.endswith("آخرین اخبار تکنولوژی | @MyNewsTechnology")
def test_build_expanded_message_contains_channel_at_end(): assert build_expanded_message("عنوان","متن","منبع").endswith("آخرین اخبار تکنولوژی | @MyNewsTechnology")
def test_only_technology_category_is_publishable():
    from app.core import is_technology_news; assert is_technology_news("technology") and not is_technology_news("political")
def test_similar_rewrites_of_same_event_are_duplicates():
    first={"title":"Type One Energy raised $200M to build a fusion power plant by 2034","summary":"Type One Energy raised 200 million dollars to build a fusion power plant."}; rewritten={"title":"Type One Energy raises $200 million for a fusion power plant","summary":"The fusion company secured $200 million to bring a power plant to the grid."}; assert is_duplicate_story(rewritten,[first])
def test_different_technology_events_are_not_duplicates():
    first={"title":"Type One Energy raised $200M to build a fusion power plant by 2034","summary":"Type One Energy raised 200 million dollars to build a fusion power plant."}; different={"title":"Type One Energy connects its prototype fusion system to the grid","summary":"The company demonstrated a new prototype milestone at its test facility."}; assert not is_duplicate_story(different,[first])
def test_technology_story_cases():
    from app.core import is_technology_story; assert is_technology_story("AI","New AI model"); assert is_technology_story("world","OpenAI","artificial intelligence and ChatGPT","AI model"); assert not is_technology_story("world","Diplomatic talks continue","Officials met about foreign policy"); assert not is_technology_story("sports","Antigua and Barbuda vs Aruba","Concacaf Nations League and head-to-head","Football match statistics")
def test_whole_word_and_weak_brand_filtering():
    from app.core import is_technology_story; assert not is_technology_story("general","Technician said the rain technique failed"); assert not is_technology_story("economy","Apple and Tesla shares move","Investors reacted to results")
def test_persian_technology_signals_count():
    from app.core import is_technology_story; assert is_technology_story("world","خبر","","این گزارش درباره هوش مصنوعی و تراشه و نرم‌افزار است")
def test_same_url_is_always_a_duplicate():
    first={"title":"Original","summary":"Summary","url":"https://example.com/story"}; assert is_duplicate_story({"title":"Rewritten","summary":"Different","url":"https://example.com/story"},[first])


def test_technology_filter_does_not_trust_category_alone_for_non_technology_content():
    from app.core import is_technology_story
    assert not is_technology_story("technology", "Diplomatic talks continue", "Officials discuss tariffs", "Foreign policy negotiations continue")


def test_feed_category_or_tag_is_the_technology_gate():
    from app.core import is_technology_feed_item
    assert is_technology_feed_item(("Technology",), "Any source")
    assert is_technology_feed_item(("Artificial Intelligence",), "Any source")
    assert not is_technology_feed_item(("Politics", "World"), "Any source")
    assert is_technology_feed_item((), "TechCrunch")
