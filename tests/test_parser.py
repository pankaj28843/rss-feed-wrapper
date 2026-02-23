from rss_feed_wrapper.parser import parse_source_feed


def test_parse_source_feed_extracts_hnrss_article_urls() -> None:
    xml = """<?xml version='1.0'?>
    <rss version='2.0'>
      <channel>
        <title>Hacker News: Newest</title>
        <item>
          <title>Story</title>
          <description><![CDATA[
            <p>Article URL: <a href="https://example.com/story">https://example.com/story</a></p>
          ]]></description>
          <pubDate>Mon, 23 Feb 2026 00:00:00 +0000</pubDate>
        </item>
      </channel>
    </rss>"""
    title, entries = parse_source_feed(xml, limit=5)
    assert title == "Hacker News: Newest"
    assert len(entries) == 1
    assert entries[0].article_url == "https://example.com/story"


def test_parse_source_feed_extracts_generic_link() -> None:
    xml = """<?xml version='1.0'?>
    <rss version='2.0'>
      <channel>
        <title>Generic Feed</title>
        <item>
          <title>Generic Story</title>
          <link>https://example.com/generic</link>
          <pubDate>Mon, 23 Feb 2026 00:00:00 +0000</pubDate>
        </item>
      </channel>
    </rss>"""
    title, entries = parse_source_feed(xml, limit=5)
    assert title == "Generic Feed"
    assert len(entries) == 1
    assert entries[0].article_url == "https://example.com/generic"
