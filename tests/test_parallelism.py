import asyncio

import pytest

from rss_feed_wrapper.config import Settings
from rss_feed_wrapper.db import CacheDB
from rss_feed_wrapper.models import WrappedFeedItem
from rss_feed_wrapper.service import RSSWrapperService


@pytest.mark.asyncio
async def test_parallel_extraction_reaches_concurrency(tmp_path, monkeypatch) -> None:
    db = CacheDB(str(tmp_path / "parallel.db"))
    await db.connect()
    settings = Settings(
        db_path=str(tmp_path / "parallel.db"),
        max_parallelism=8,
        per_host_initial_parallelism=2,
        per_host_min_parallelism=1,
        per_host_max_parallelism=6,
    )
    service = RSSWrapperService(db=db, settings=settings)

    xml = """<?xml version='1.0'?>
    <rss version='2.0'>
      <channel>
        <title>Hacker News: Newest</title>
        <item><title>1</title><description><![CDATA[<p>Article URL: <a href=\"https://a.example.com/1\">1</a></p>]]></description></item>
        <item><title>2</title><description><![CDATA[<p>Article URL: <a href=\"https://a.example.com/2\">2</a></p>]]></description></item>
        <item><title>3</title><description><![CDATA[<p>Article URL: <a href=\"https://b.example.com/3\">3</a></p>]]></description></item>
        <item><title>4</title><description><![CDATA[<p>Article URL: <a href=\"https://b.example.com/4\">4</a></p>]]></description></item>
      </channel>
    </rss>"""

    async def fake_feed(_self, _url: str) -> str:
        return xml

    lock = asyncio.Lock()
    in_flight = 0
    max_in_flight = 0

    async def fake_extract(_self, url: str, _pool_name: str | None):
        nonlocal in_flight, max_in_flight
        async with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.15)
        async with lock:
            in_flight -= 1
        return WrappedFeedItem(
            title=url,
            source_url=url,
            pub_date=None,
            content_html="<article><p>x</p></article>",
        )

    monkeypatch.setattr(
        RSSWrapperService,
        "_fetch_source_feed",
        fake_feed,
    )
    monkeypatch.setattr(
        RSSWrapperService,
        "_extract_article",
        fake_extract,
    )

    source_title, items = await service.build_wrapped_items(
        "https://hnrss.org/newest?count=4", max_items=4
    )

    assert source_title == "Hacker News: Newest"
    assert len(items) == 4
    assert max_in_flight >= 2

    await db.close()
