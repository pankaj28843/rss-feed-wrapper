import pytest

from rss_feed_wrapper.config import Settings
from rss_feed_wrapper.db import CacheDB
from rss_feed_wrapper.service import RSSWrapperService


async def _allow_preflight(_url: str):
    return False, None


@pytest.mark.asyncio
async def test_always_iterates_all_proxies_on_errors(tmp_path, monkeypatch) -> None:
    db = CacheDB(str(tmp_path / "resilience.db"))
    await db.connect()

    service = RSSWrapperService(
        db=db,
        settings=Settings(
            db_path=str(tmp_path / "resilience.db"),
            proxy_pools="default=http://proxy.local:8080,http://proxy2.local:8080",
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
    monkeypatch.setattr(service, "_preflight_article_url", _allow_preflight)

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


@pytest.mark.asyncio
async def test_skips_binary_article_urls_without_extractor(
    tmp_path, monkeypatch
) -> None:
    db = CacheDB(str(tmp_path / "binary.db"))
    await db.connect()
    service = RSSWrapperService(
        db=db,
        settings=Settings(db_path=str(tmp_path / "binary.db")),
    )

    called = False

    async def fake_extract(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("extractor should not be called for binary urls")

    monkeypatch.setattr(
        "rss_feed_wrapper.service.extract_article_from_url", fake_extract
    )

    item = await service._extract_article(
        "https://example.com/report.pdf", None, "https://hnrss.org/newest"
    )
    assert item is None
    assert called is False
    await db.close()


@pytest.mark.asyncio
async def test_drops_oversized_inner_text_content(tmp_path, monkeypatch) -> None:
    db = CacheDB(str(tmp_path / "oversized.db"))
    await db.connect()
    service = RSSWrapperService(
        db=db,
        settings=Settings(
            db_path=str(tmp_path / "oversized.db"),
            max_article_inner_text_chars=100,
        ),
    )

    class DummyResult:
        success = True
        content = "<article><p>" + ("word " * 5001) + "</p></article>"
        title = "Big"
        date_published = None
        error = None

    async def fake_extract(*_args, **_kwargs):
        return DummyResult()

    monkeypatch.setattr(
        "rss_feed_wrapper.service.extract_article_from_url", fake_extract
    )
    monkeypatch.setattr(service, "_preflight_article_url", _allow_preflight)

    item = await service._extract_article(
        "https://example.com/too-big", None, "https://hnrss.org/newest"
    )
    assert item is None
    await db.close()


@pytest.mark.asyncio
async def test_skips_oversized_raw_body_inner_text(tmp_path, monkeypatch) -> None:
    db = CacheDB(str(tmp_path / "oversized_raw.db"))
    await db.connect()
    service = RSSWrapperService(
        db=db,
        settings=Settings(
            db_path=str(tmp_path / "oversized_raw.db"),
            max_raw_inner_text_chars=200000,
            max_article_inner_text_chars=15000,
        ),
    )

    called = False

    async def fake_extract(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("extractor should not run for oversized raw body text")

    monkeypatch.setattr(
        "rss_feed_wrapper.service.extract_article_from_url", fake_extract
    )

    async def skip_preflight(_url: str):
        return True, "skipped_raw_inner_text_chars:250000>200000"

    monkeypatch.setattr(service, "_preflight_article_url", skip_preflight)

    item = await service._extract_article(
        "https://example.com/too-big-raw", None, "https://hnrss.org/newest"
    )
    assert item is None
    assert called is False
    await db.close()


@pytest.mark.asyncio
async def test_skips_non_text_content_type_via_preflight(tmp_path, monkeypatch) -> None:
    db = CacheDB(str(tmp_path / "ctype.db"))
    await db.connect()
    service = RSSWrapperService(
        db=db,
        settings=Settings(db_path=str(tmp_path / "ctype.db")),
    )

    called = False

    async def fake_extract(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("extractor should not run when preflight skips")

    monkeypatch.setattr(
        "rss_feed_wrapper.service.extract_article_from_url", fake_extract
    )

    async def skip_preflight(_url: str):
        return True, "skipped_content_type:application/pdf"

    monkeypatch.setattr(service, "_preflight_article_url", skip_preflight)

    item = await service._extract_article(
        "https://example.com/download?id=123", None, "https://hnrss.org/newest"
    )
    assert item is None
    assert called is False
    await db.close()
