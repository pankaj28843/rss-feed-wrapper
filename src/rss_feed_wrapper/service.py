from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlparse

import httpx
from article_extractor import ExtractionOptions, extract_article_from_url
from article_extractor.types import NetworkOptions

from .config import Settings
from .db import CacheDB
from .models import WrappedFeedItem
from .parser import parse_hnrss

logger = logging.getLogger(__name__)
_ALLOWED_HOSTS = {"hnrss.org", "www.hnrss.org"}


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
        if parsed.netloc.lower() not in _ALLOWED_HOSTS:
            raise ValueError("url host must be hnrss.org")
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
        source_title, entries = parse_hnrss(source_xml, limit=max_items)
        feed_id = await self.db.upsert_feed(source_url, source_title)

        wrapped_items: list[WrappedFeedItem] = []
        for entry in entries:
            cached = await self.db.get_cached_item(feed_id, entry.article_url)
            if cached is not None:
                if entry.pub_date and not cached.pub_date:
                    cached.pub_date = entry.pub_date
                wrapped_items.append(cached)
                continue

            extracted = await self._extract_article(entry.article_url, pool_name)
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
