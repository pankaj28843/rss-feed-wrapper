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


def test_parse_source_feed_supports_atom() -> None:
    xml = """<?xml version='1.0' encoding='utf-8'?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <title>Atom Feed</title>
      <entry>
        <title>Atom Story</title>
        <link rel="alternate" href="https://example.com/atom-story"/>
        <updated>2026-02-25T20:10:00Z</updated>
      </entry>
    </feed>"""
    title, entries = parse_source_feed(xml, limit=5)
    assert title == "Atom Feed"
    assert len(entries) == 1
    assert entries[0].article_url == "https://example.com/atom-story"
    assert entries[0].pub_date == "2026-02-25T20:10:00Z"
