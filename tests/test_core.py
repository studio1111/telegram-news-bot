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


def test_technology_tag_matching_does_not_accept_source_names_as_tags():
    from app.core import is_technology_feed_item
    assert not is_technology_feed_item(("TechCrunch",), "Any source")
    assert not is_technology_feed_item(("TechCrunch Disrupt 2026",), "Any source")
    assert is_technology_feed_item(("Technology",), "Any source")
    assert is_technology_feed_item(("Tech Policy",), "Any source")
    assert is_technology_feed_item(("Artificial Intelligence",), "Any source")


def test_real_google_synthid_rewrites_are_duplicates():
    first = {
        "title": "راه‌اندازی وب‌سایت جدید گوگل (Google) برای شناسایی رسانه‌های تولیدشده با هوش مصنوعی شرکت گوگل (Google)",
        "summary": "شرکت گوگل (Google) از راه‌اندازی وب‌سایت جدیدی خبر داد که به کاربران امکان می‌دهد اصالت فایل‌های چندرسانه‌ای مانند تصاویر، ویدیوها و صوت را بررسی کنند. این ابزار با استفاده از فناوری سینث‌آی‌دی (SynthID) به شناسایی محتوای تولیدشده توسط هوش مصنوعی کمک می‌کند",
    }
    rewritten = {
        "title": "ابزار تشخیص هوش مصنوعی گوگل (Google) منتشر شد",
        "summary": "شرکت گوگل (Google) ابزار جدیدی به نام «سینت‌اید دکتور» (SynthID Detector) را عرضه کرده است که می‌تواند محتوای تولید شده توسط ابزارهای هوش مصنوعی مختلف را شناسایی کند.",
    }
    assert is_duplicate_story(rewritten, [first])


def test_different_google_products_are_not_duplicates_without_shared_product_entity():
    first = {
        "title": "Google launches SynthID Detector",
        "summary": "Google introduces a new tool for detecting AI-generated media.",
    }
    different = {
        "title": "Google unveils a new Pixel phone",
        "summary": "Google announces new smartphone hardware for consumers.",
    }
    assert not is_duplicate_story(different, [first])


def test_same_company_different_products_are_not_duplicates_when_product_entities_differ():
    first = {
        "title": "مدل جدید هوش مصنوعی جیمینی (Gemini) گوگل (Google) معرفی شد",
        "summary": "گوگل (Google) مدل جیمینی (Gemini) جدید خود را معرفی کرد.",
    }
    different = {
        "title": "گوشی جدید پیکسل (Pixel) گوگل (Google) معرفی شد",
        "summary": "گوگل (Google) گوشی پیکسل (Pixel) جدید خود را معرفی کرد.",
    }
    assert not is_duplicate_story(different, [first])


def test_english_synthid_cross_source_rewrites_are_duplicates():
    first = {
        "title": "Google's AI detection website is now available",
        "summary": "SynthID Detector will flag content created with AI tools from OpenAI, Google, Apple and other companies.",
        "url": "https://www.engadget.com/2279565/google-synth-id-detector-ai-detection-website-is-now-available/",
    }
    rewritten = {
        "title": "Google’s new SynthID website can identify AI-generated media",
        "summary": "Google launched a new site that lets anyone verify whether an image, video, or audio clip is generated using AI.",
        "url": "https://techcrunch.com/2026/10/07/googles-new-synthid-website-can-identify-ai-generated-media/",
    }
    assert is_duplicate_story(rewritten, [first])


def test_same_google_synthid_context_different_event_is_not_a_duplicate():
    first = {
        "title": "Google launches SynthID Detector website for AI-generated media",
        "summary": "The new detector checks images, video, and audio for SynthID watermarks.",
    }
    different = {
        "title": "Google expands SynthID text watermarking to more AI models",
        "summary": "The company is adding text watermarking support for developers using new language models.",
    }
    assert not is_duplicate_story(different, [first])


def test_real_synthid_duplicate_matches_production_state_shape():
    existing = {
        "title": "Google's AI detection website is now available",
        "summary": "SynthID Detector will flag content created with AI tools from OpenAI, Google, Apple and other companies.",
        "url": "https://www.engadget.com/2279565/google-synth-id-detector-ai-detection-website-is-now-available/",
        "display_title": "ابزار تشخیص هوش مصنوعی گوگل (Google) منتشر شد",
        "display_summary": "شرکت گوگل (Google) ابزار جدیدی به نام «سینت‌اید دکتور» (SynthID Detector) را عرضه کرده است که می‌تواند محتوای تولید شده توسط ابزارهای هوش مصنوعی مختلف را شناسایی کند.",
    }
    incoming = {
        "title": "Google’s new SynthID website can identify AI-generated media",
        "summary": "Google on Tuesday launched a new site that lets anyone verify whether media is generated using AI.",
        "url": "https://techcrunch.com/2026/10/07/googles-new-synthid-website-can-identify-ai-generated-media/",
        "display_title": "راه‌اندازی وب‌سایت جدید گوگل (Google) برای شناسایی رسانه‌های تولیدشده با هوش مصنوعی شرکت گوگل (Google)",
        "display_summary": "شرکت گوگل (Google) از راه‌اندازی وب‌سایت جدیدی خبر داد که با فناوری سینث‌آی‌دی (SynthID) محتوای تولیدشده با هوش مصنوعی را شناسایی می‌کند.",
    }
    assert is_duplicate_story(incoming, [existing])


def test_technology_feed_category_uses_word_boundaries():
    from app.core import is_technology_feed_item
    assert not is_technology_feed_item(("Techniques",), "Any source")
    assert not is_technology_feed_item(("Biotechnology",), "Any source")
    assert is_technology_feed_item(("Tech News",), "Any source")
    assert is_technology_feed_item(("Technology + Computing",), "Any source")
    assert is_technology_feed_item(("AI Research",), "Any source")


def test_persian_technology_categories_are_accepted():
    from app.core import is_technology_news
    assert is_technology_news("فناوری")
    assert is_technology_news("تکنولوژی")
    assert is_technology_news("هوش مصنوعی")
