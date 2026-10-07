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
    from app.core import is_technology_story
    assert is_technology_story("AI", "Diplomatic talks continue")
    assert is_technology_story("Technology", "Any subject")
    assert not is_technology_story("world", "OpenAI", "artificial intelligence and ChatGPT")
    assert not is_technology_story("sports", "Football match statistics")


def test_technology_category_matching_does_not_use_substrings():
    from app.core import is_technology_news
    assert not is_technology_news("Daily")
    assert not is_technology_news("Paid")
    assert not is_technology_news("Techniques")
    assert is_technology_news("Technology News")
    assert is_technology_news("Tech & Gadgets")
    assert is_technology_news("AI")


def test_same_url_is_always_a_duplicate():
    first={"title":"Original","summary":"Summary","url":"https://example.com/story"}; assert is_duplicate_story({"title":"Rewritten","summary":"Different","url":"https://example.com/story"},[first])


def test_technology_category_is_the_only_classification_gate():
    from app.core import is_technology_story
    assert is_technology_story("technology", "Diplomatic talks continue", "Officials discuss tariffs", "Foreign policy negotiations continue")
    assert not is_technology_story("world", "AI model", "technology", "OpenAI")


def test_feed_category_or_tag_is_the_technology_gate():
    from app.core import is_technology_feed_item
    assert is_technology_feed_item(("Technology",), "Any source")
    assert is_technology_feed_item(("Artificial Intelligence",), "Any source")
    assert not is_technology_feed_item(("Politics", "World"), "Any source")
    assert not is_technology_feed_item((), "TechCrunch")
    assert not is_technology_feed_item((), "The Guardian Technology")
    assert not is_technology_feed_item(("Politics",), "TechCrunch")


def test_cross_source_rewrites_with_shared_entities_and_amount_are_duplicates():
    first = {"title": "OpenAI secures $8 billion funding as valuation climbs", "summary": "The company raised eight billion dollars in a major financing round."}
    rewritten = {"title": "OpenAI raises $8B in fresh financing at soaring valuation", "summary": "The AI company completed an eight-billion-dollar funding round."}
    assert is_duplicate_story(rewritten, [first])


def test_same_event_with_different_wording_and_no_shared_amount_is_duplicate_when_core_title_overlaps():
    first = {"title": "Nvidia unveils next generation AI chips for data centers", "summary": "Nvidia introduced a new generation of processors for cloud workloads."}
    rewritten = {"title": "Nvidia introduces new AI processors aimed at data centers", "summary": "The chipmaker announced its latest hardware for cloud computing."}
    assert is_duplicate_story(rewritten, [first])


def test_different_events_from_same_company_are_not_duplicates():
    first = {"title": "OpenAI launches a new coding agent", "summary": "The company introduced an agent designed for software development."}
    different = {"title": "OpenAI raises $8 billion in fresh financing", "summary": "The AI company completed a major funding round."}
    assert not is_duplicate_story(different, [first])


def test_tracking_url_variants_are_duplicates():
    first = {"title": "Example launches new device", "summary": "The company announced a new device.", "url": "https://example.com/news/device?utm_source=rss&utm_medium=feed"}
    rewritten = {"title": "Example launches new device", "summary": "The company announced a new device.", "url": "https://example.com/news/device?utm_source=telegram"}
    assert is_duplicate_story(rewritten, [first])
