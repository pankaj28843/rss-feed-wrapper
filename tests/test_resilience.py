import pytest

from rss_feed_wrapper.config import Settings
from rss_feed_wrapper.db import CacheDB
from rss_feed_wrapper.service import RSSWrapperService


@pytest.mark.asyncio
async def test_proxy_incompatibility_disables_proxies(tmp_path, monkeypatch) -> None:
    db = CacheDB(str(tmp_path / "resilience.db"))
    await db.connect()

    service = RSSWrapperService(
        db=db,
        settings=Settings(
            db_path=str(tmp_path / "resilience.db"),
            proxy_pool="http://proxy.local:8080",
        ),
    )

    async def fake_extract(*_args, **kwargs):
        network = kwargs.get("network")
        if network is not None and network.proxy is not None:
            raise TypeError(
                "AsyncClient.__init__() got an unexpected keyword argument 'proxies'"
            )

        class _Result:
            success = True
            content = "<article><p>ok</p></article>"
            title = "ok"
            date_published = None

        return _Result()

    async def proxy_only_order(_pool_name):
        return ["http://proxy.local:8080"]

    monkeypatch.setattr(
        "rss_feed_wrapper.service.extract_article_from_url", fake_extract
    )
    monkeypatch.setattr(service, "_next_proxy_order", proxy_only_order)

    item = await service._extract_article("https://example.com/x", None)
    assert item is None
    assert service._proxy_support_disabled is True

    order = await RSSWrapperService._next_proxy_order(service, None)
    assert order == [None]

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
