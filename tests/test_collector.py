from app.collector import _image_url, _article_image_url

def test_image_url_reads_og_image_from_entry_content():
    entry = {
        "summary": '<p>خبر</p>',
        "content": [{"value": '<meta property="og:image" content="https://example.com/hero.jpg">'}],
    }
    assert _image_url(entry) == "https://example.com/hero.jpg"

def test_article_image_url_reads_open_graph_image():
    html = '<html><head><meta property="og:image" content="https://example.com/hero.webp"></head></html>'
    assert _article_image_url(html) == "https://example.com/hero.webp"
