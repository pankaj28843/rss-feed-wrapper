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
        proxy_pools="poolA=http://p1:8080,http://p2:8080;poolB=http://p3:8080",
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

    async def fake_extract(_self, _url: str, _pool: str | None, _source_url: str):
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


def test_rss_endpoint_rejects_invalid_scheme(tmp_path) -> None:
    app = create_app(Settings(db_path=str(tmp_path / "x.db")))
    with TestClient(app) as client:
        resp = client.get("/rss", params={"url": "ftp://example.com/rss"})
    assert resp.status_code == 400


def test_rss_endpoint_rejects_unknown_proxy_pool(tmp_path) -> None:
    app = create_app(
        Settings(
            db_path=str(tmp_path / "x.db"),
            proxy_pools="poolA=http://p1:8080,http://p2:8080",
        )
    )
    with TestClient(app) as client:
        resp = client.get(
            "/rss",
            params={
                "url": "https://hnrss.org/newest?count=1",
                "proxy_pool": "missing-pool",
            },
        )
    assert resp.status_code == 400
    assert "unknown proxy_pool" in resp.json()["detail"]


def test_dashboard_endpoints(tmp_path) -> None:
    app = create_app(Settings(db_path=str(tmp_path / "x.db")))
    with TestClient(app) as client:
        j = client.get("/dashboard.json")
        h = client.get("/dashboard")
    assert j.status_code == 200
    assert "feeds" in j.json()
    assert h.status_code == 200
    assert "rss-feed-wrapper dashboard" in h.text


def test_rss_endpoint_skips_binary_and_oversized_entries(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "wrapper2.db"
    settings = Settings(db_path=str(db_path))
    app = create_app(settings)

    async def fake_feed(_self, _url: str) -> str:
        return """<?xml version='1.0'?>
        <rss version='2.0'>
          <channel>
            <title>Mixed Feed</title>
            <item><title>PDF</title><link>https://example.com/a.pdf</link></item>
            <item><title>Big</title><link>https://example.com/big</link></item>
            <item><title>OK</title><link>https://example.com/ok</link></item>
          </channel>
        </rss>"""

    async def fake_extract(_self, url: str, _pool: str | None, _source_url: str):
        if url.endswith("/big"):
            return WrappedFeedItem(
                title="Big",
                source_url=url,
                pub_date=None,
                content_html="<p>" + ("x " * 9001) + "</p>",
            )
        return WrappedFeedItem(
            title="OK",
            source_url=url,
            pub_date=None,
            content_html="<article><p>Hello</p></article>",
        )

    monkeypatch.setattr(
        "rss_feed_wrapper.service.RSSWrapperService._fetch_source_feed", fake_feed
    )
    monkeypatch.setattr(
        "rss_feed_wrapper.service.RSSWrapperService._extract_article", fake_extract
    )

    with TestClient(app) as client:
        resp = client.get("/rss", params={"url": "https://hnrss.org/newest?count=3"})

    assert resp.status_code == 200
    assert "https://example.com/a.pdf" not in resp.text
    assert "https://example.com/big" not in resp.text
    assert "https://example.com/ok" in resp.text
