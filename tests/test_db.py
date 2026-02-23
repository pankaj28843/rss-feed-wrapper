import pytest

from rss_feed_wrapper.db import CacheDB
from rss_feed_wrapper.models import WrappedFeedItem


@pytest.mark.asyncio
async def test_db_prunes_to_max_items(tmp_path) -> None:
    db = CacheDB(str(tmp_path / "test.db"))
    await db.connect()
    feed_id = await db.upsert_feed("https://hnrss.org/newest", "Newest")

    for i in range(120):
        await db.upsert_item(
            feed_id,
            WrappedFeedItem(
                title=f"t{i}",
                source_url=f"https://example.com/{i}",
                pub_date=None,
                content_html=f"<p>{i}</p>",
            ),
        )

    await db.prune_feed(feed_id, 100)
    count = await db.count_feed_items(feed_id)
    await db.close()
    assert count == 100
