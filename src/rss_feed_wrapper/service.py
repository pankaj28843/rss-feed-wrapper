from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from time import perf_counter
from urllib.parse import urlparse

import httpx
from article_extractor import ExtractionOptions, extract_article_from_url
from article_extractor.types import NetworkOptions

from .config import Settings
from .db import CacheDB
from .models import SourceFeedEntry, WrappedFeedItem
from .parser import parse_source_feed

logger = logging.getLogger(__name__)


@dataclass
class _HostState:
    limit: int
    in_flight: int = 0
    success_count: int = 0
    failure_count: int = 0


class _AdaptiveHostLimiter:
    def __init__(self, *, initial: int, minimum: int, maximum: int):
        self._initial = initial
        self._min = minimum
        self._max = maximum
        self._states: dict[str, _HostState] = {}
        self._lock = asyncio.Lock()
        self._cond = asyncio.Condition(self._lock)

    async def acquire(self, host: str) -> None:
        async with self._cond:
            state = self._states.setdefault(host, _HostState(limit=self._initial))
            while state.in_flight >= state.limit:
                await self._cond.wait()
            state.in_flight += 1

    async def release(self, host: str, *, success: bool, latency_s: float) -> None:
        async with self._cond:
            state = self._states.setdefault(host, _HostState(limit=self._initial))
            state.in_flight = max(0, state.in_flight - 1)

            if success:
                state.success_count += 1
                # Fast healthy host: ramp up gradually.
                if latency_s < 8.0 and state.success_count % 3 == 0:
                    state.limit = min(self._max, state.limit + 1)
            else:
                state.failure_count += 1
                # On failures, quickly back off.
                state.limit = max(self._min, state.limit // 2 or self._min)

            self._cond.notify_all()


class RSSWrapperService:
    def __init__(self, db: CacheDB, settings: Settings):
        self.db = db
        self.settings = settings
        self._proxy_cursors: dict[str, int] = {}
        self._proxy_lock = asyncio.Lock()

    async def _fetch_source_feed(self, source_url: str) -> str:
        async with httpx.AsyncClient(
            timeout=self.settings.http_timeout, follow_redirects=True
        ) as client:
            response = await client.get(source_url)
            response.raise_for_status()
            return response.text

    async def _next_proxy_order(self, pool_name: str | None) -> list[str | None]:
        pools = self.settings.proxy_pools_map()
        if pool_name is None:
            pool_key = "default" if "default" in pools else next(iter(pools), "")
        else:
            pool_key = pool_name

        proxies = pools.get(pool_key, [])
        if not proxies:
            return [None]
        async with self._proxy_lock:
            cursor = self._proxy_cursors.get(pool_key, 0)
            offset = cursor % len(proxies)
            self._proxy_cursors[pool_key] = cursor + 1
        ordered = proxies[offset:] + proxies[:offset]
        return [None, *ordered]

    async def _extract_article(
        self, article_url: str, pool_name: str | None
    ) -> WrappedFeedItem | None:
        options = ExtractionOptions(
            min_word_count=80,
            min_char_threshold=500,
            include_images=True,
            include_code_blocks=True,
            safe_markdown=True,
        )
        for proxy in await self._next_proxy_order(pool_name):
            network = NetworkOptions(proxy=proxy, randomize_user_agent=True)
            try:
                result = await extract_article_from_url(
                    article_url,
                    options=options,
                    network=network,
                    prefer_playwright=self.settings.prefer_playwright,
                )
            except Exception as exc:
                logger.warning(
                    "Extraction error for %s via proxy=%s: %s", article_url, proxy, exc
                )
                continue

            if result.success and result.content.strip():
                return WrappedFeedItem(
                    title=(result.title or article_url).strip(),
                    source_url=article_url,
                    pub_date=result.date_published,
                    content_html=result.content,
                )

            logger.info("Extraction failed for %s via proxy=%s", article_url, proxy)

        return None

    @staticmethod
    def validate_source_url(source_url: str) -> str:
        parsed = urlparse(source_url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("url must use http or https")
        if not parsed.netloc:
            raise ValueError("url host is required")
        return source_url

    def validate_pool_name(self, pool_name: str | None) -> str | None:
        if pool_name is None:
            return None
        known = self.settings.proxy_pools_map().keys()
        if pool_name not in known:
            raise ValueError(f"unknown proxy_pool '{pool_name}'")
        return pool_name

    async def build_wrapped_items(
        self, source_url: str, max_items: int, pool_name: str | None = None
    ) -> tuple[str, list[WrappedFeedItem]]:
        source_xml = await self._fetch_source_feed(source_url)
        source_title, entries = parse_source_feed(source_xml, limit=max_items)
        feed_id = await self.db.upsert_feed(source_url, source_title)

        wrapped_items: list[WrappedFeedItem] = []
        uncached_entries = []
        for entry in entries:
            cached = await self.db.get_cached_item(feed_id, entry.article_url)
            if cached is not None:
                if entry.pub_date and not cached.pub_date:
                    cached.pub_date = entry.pub_date
                wrapped_items.append(cached)
                continue
            uncached_entries.append(entry)

        extracted_results = await self._extract_uncached_entries(
            uncached_entries, pool_name
        )
        for entry, extracted in extracted_results:
            if extracted is None:
                continue
            if entry.pub_date:
                extracted.pub_date = entry.pub_date
            if not extracted.title.strip():
                extracted.title = entry.title
            await self.db.upsert_item(feed_id, extracted)
            wrapped_items.append(extracted)

        await self.db.prune_feed(feed_id, self.settings.cache_max_items)
        return source_title, wrapped_items

    async def _extract_uncached_entries(
        self, entries: list[SourceFeedEntry], pool_name: str | None
    ) -> list[tuple[SourceFeedEntry, WrappedFeedItem | None]]:
        if not entries:
            return []

        global_sem = asyncio.Semaphore(max(1, self.settings.max_parallelism))
        limiter = _AdaptiveHostLimiter(
            initial=max(1, self.settings.per_host_initial_parallelism),
            minimum=max(1, self.settings.per_host_min_parallelism),
            maximum=max(
                self.settings.per_host_min_parallelism,
                self.settings.per_host_max_parallelism,
            ),
        )

        async def worker(entry):
            parsed = urlparse(entry.article_url)
            host = parsed.netloc.lower() or "unknown-host"
            async with global_sem:
                await limiter.acquire(host)
                started = perf_counter()
                success = False
                try:
                    extracted = await self._extract_article(
                        entry.article_url, pool_name
                    )
                    success = extracted is not None
                    return entry, extracted
                finally:
                    await limiter.release(
                        host,
                        success=success,
                        latency_s=perf_counter() - started,
                    )

        tasks = [asyncio.create_task(worker(entry)) for entry in entries]
        return await asyncio.gather(*tasks)
