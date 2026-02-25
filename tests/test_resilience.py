import pytest

from rss_feed_wrapper.config import Settings
from rss_feed_wrapper.db import CacheDB
from rss_feed_wrapper.service import RSSWrapperService


@pytest.mark.asyncio
async def test_always_iterates_all_proxies_on_errors(tmp_path, monkeypatch) -> None:
    db = CacheDB(str(tmp_path / "resilience.db"))
    await db.connect()

    service = RSSWrapperService(
        db=db,
        settings=Settings(
            db_path=str(tmp_path / "resilience.db"),
            proxy_pool="http://proxy.local:8080,http://proxy2.local:8080",
            extract_fallback_playwright=False,
        ),
    )

    attempts: list[str] = []

    async def fake_extract(*_args, **kwargs):
        network = kwargs.get("network")
        attempts.append(str(network.proxy))
        raise RuntimeError("simulated failure")

    async def ordered_proxies(_pool_name):
        return [None, "http://proxy.local:8080", "http://proxy2.local:8080"]

    monkeypatch.setattr(
        "rss_feed_wrapper.service.extract_article_from_url", fake_extract
    )
    monkeypatch.setattr(service, "_next_proxy_order", ordered_proxies)

    item = await service._extract_article(
        "https://example.com/x", None, "https://hnrss.org/newest"
    )
    assert item is None
    assert attempts == [
        "None",
        "http://proxy.local:8080",
        "http://proxy2.local:8080",
    ]

    await db.close()


def test_extractor_modes_http_first_and_fallback(tmp_path) -> None:
    service = RSSWrapperService(
        db=CacheDB(str(tmp_path / "modes.db")),
        settings=Settings(
            db_path=str(tmp_path / "modes.db"),
            extract_http_first=True,
            extract_fallback_playwright=True,
        ),
    )

    assert service._extractor_modes() == [False, True]
