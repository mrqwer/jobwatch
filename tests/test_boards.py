from jobwatch.sources.boards import rss_items

FEED = """<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel>
<item><title>Senior Python Engineer&nbsp;&mdash; AI</title><link>https://djinni.co/jobs/1/</link>
<description>&lt;p&gt;Remote&lt;/p&gt; FastAPI &amp; LLM</description><guid>https://djinni.co/jobs/1/</guid></item>
</channel></rss>"""


def test_rss_survives_html_entities():
    # &nbsp; / &mdash; are not XML entities: a plain XML parser dies on them (Djinni feed)
    [item] = rss_items(FEED)
    assert item["title"] == "Senior Python Engineer\xa0— AI"
    assert item["description"] == "<p>Remote</p> FastAPI & LLM"  # XML entities still decoded by the parser
