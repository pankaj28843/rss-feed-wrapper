from fastapi.testclient import TestClient

from rss_feed_wrapper.config import Settings
from rss_feed_wrapper.main import create_app
from rss_feed_wrapper.models import WrappedFeedItem


def test_rss_endpoint_e2e_with_cache(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "wrapper.db"
    settings = Settings(
        db_path=str(db_path),
        cache_max_items=100,
        http_timeout=5,
        prefer_playwright=False,
        proxy_pool="http://p1:8080,http://p2:8080",
    )
    app = create_app(settings)

    calls = {"extract": 0, "feed": 0}

    async def fake_feed(_self, _url: str) -> str:
        calls["feed"] += 1
        return """<?xml version='1.0'?>
        <rss version='2.0'>
          <channel>
            <title>Hacker News: Newest</title>
            <item>
              <title>A</title>
              <description><![CDATA[<p>Article URL: <a href=\"https://example.com/a\">a</a></p>]]></description>
              <pubDate>Mon, 23 Feb 2026 00:00:00 +0000</pubDate>
            </item>
          </channel>
        </rss>"""

    async def fake_extract(_self, _url: str):
        calls["extract"] += 1
        return WrappedFeedItem(
            title="Extracted A",
            source_url="https://example.com/a",
            pub_date="Mon, 23 Feb 2026 00:00:00 +0000",
            content_html="<article><p>Hello</p></article>",
        )

    monkeypatch.setattr(
        "rss_feed_wrapper.service.RSSWrapperService._fetch_source_feed", fake_feed
    )
    monkeypatch.setattr(
        "rss_feed_wrapper.service.RSSWrapperService._extract_article", fake_extract
    )

    with TestClient(app) as client:
        first = client.get("/rss", params={"url": "https://hnrss.org/newest?count=1"})
        second = client.get("/rss", params={"url": "https://hnrss.org/newest?count=1"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert "Source URL:" in first.text
    assert "Extracted A" in first.text
    assert calls["feed"] == 2
    assert calls["extract"] == 1


def test_rss_endpoint_rejects_non_hnrss(tmp_path) -> None:
    app = create_app(Settings(db_path=str(tmp_path / "x.db")))
    with TestClient(app) as client:
        resp = client.get("/rss", params={"url": "https://example.com/rss"})
    assert resp.status_code == 400
