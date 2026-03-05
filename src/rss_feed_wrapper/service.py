from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from time import perf_counter
from urllib.parse import urlparse

import httpx
import justhtml
from article_extractor import ExtractionOptions, extract_article_from_url
from article_extractor.types import NetworkOptions

from .config import Settings
from .db import CacheDB
from .models import SourceFeedEntry, WrappedFeedItem
from .parser import parse_source_feed

logger = logging.getLogger(__name__)
_BINARY_EXTENSIONS = {
    ".7z",
    ".avi",
    ".bin",
    ".bz2",
    ".class",
    ".csv",
    ".doc",
    ".docm",
    ".docx",
    ".dmg",
    ".epub",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".iso",
    ".jar",
    ".jpeg",
    ".jpg",
    ".mobi",
    ".mov",
    ".mp3",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".odp",
    ".ods",
    ".odt",
    ".pdf",
    ".png",
    ".ppt",
    ".pptm",
    ".pptx",
    ".rar",
    ".svg",
    ".tar",
    ".tgz",
    ".tif",
    ".tiff",
    ".wav",
    ".webm",
    ".webp",
    ".xls",
    ".xlsb",
    ".xlsm",
    ".xlsx",
    ".xz",
    ".zip",
}
_ALLOWED_TEXT_CONTENT_TYPES = (
    "text/html",
    "application/xhtml+xml",
    "text/plain",
)


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

    def _is_binary_url(self, article_url: str) -> bool:
        path = (urlparse(article_url).path or "").lower()
        return any(path.endswith(ext) for ext in _BINARY_EXTENSIONS)

    def _count_dom_inner_text_chars(self, html: str, *, body_only: bool) -> int:
        try:
            doc = justhtml.JustHTML(html)
            if body_only:
                body = doc.query_one("body")
                text = body.to_text() if body is not None else doc.to_text()
            else:
                text = doc.to_text()
            return len(text)
        except Exception as exc:
            logger.debug("justhtml parse failed (body_only=%s): %s", body_only, exc)
            return 0

    def _is_within_article_size_limits(self, content_html: str) -> tuple[bool, int]:
        article_inner_chars = self._count_dom_inner_text_chars(
            content_html, body_only=False
        )
        ok = article_inner_chars <= self.settings.max_article_inner_text_chars
        return ok, article_inner_chars

    @staticmethod
    def _normalize_content_type(content_type: str | None) -> str:
        return (content_type or "").split(";", 1)[0].strip().lower()

    async def _preflight_article_url(self, article_url: str) -> tuple[bool, str | None]:
        max_bytes = max(1, self.settings.max_article_content_mb) * 1024 * 1024
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.http_timeout, follow_redirects=True
            ) as client:
                head_resp: httpx.Response | None = None
                try:
                    candidate = await client.head(article_url)
                    if candidate.status_code < 400 and candidate.status_code not in {
                        405,
                        501,
                    }:
                        head_resp = candidate
                except Exception:
                    head_resp = None

                ctype = self._normalize_content_type(
                    head_resp.headers.get("content-type") if head_resp else None
                )
                if ctype and not any(
                    ctype.startswith(prefix) for prefix in _ALLOWED_TEXT_CONTENT_TYPES
                ):
                    return True, f"skipped_content_type:{ctype}"

                clen_raw = (
                    head_resp.headers.get("content-length") if head_resp else ""
                ) or ""
                clen_raw = clen_raw.strip()
                if clen_raw.isdigit() and int(clen_raw) > max_bytes:
                    return True, (
                        f"skipped_content_length:{clen_raw}>"
                        f"{max_bytes}({self.settings.max_article_content_mb}MB)"
                    )

                page_resp = await client.get(article_url)
                page_resp.raise_for_status()

                ctype_get = self._normalize_content_type(
                    page_resp.headers.get("content-type")
                )
                if ctype_get and not any(
                    ctype_get.startswith(prefix)
                    for prefix in _ALLOWED_TEXT_CONTENT_TYPES
                ):
                    return True, f"skipped_content_type:{ctype_get}"

                clen_get_raw = (page_resp.headers.get("content-length") or "").strip()
                if clen_get_raw.isdigit() and int(clen_get_raw) > max_bytes:
                    return True, (
                        f"skipped_content_length:{clen_get_raw}>"
                        f"{max_bytes}({self.settings.max_article_content_mb}MB)"
                    )

                raw_inner_chars = self._count_dom_inner_text_chars(
                    page_resp.text, body_only=True
                )
                if raw_inner_chars > self.settings.max_raw_inner_text_chars:
                    return True, (
                        f"skipped_raw_inner_text_chars:{raw_inner_chars}>"
                        f"{self.settings.max_raw_inner_text_chars}"
                    )
        except Exception as exc:
            logger.debug("Preflight failed for %s: %s", article_url, exc)

        return False, None

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

    def _extractor_modes(self) -> list[bool]:
        primary = (
            False
            if self.settings.extract_http_first
            else self.settings.prefer_playwright
        )
        modes = [primary]
        if self.settings.extract_fallback_playwright and (not primary) not in modes:
            modes.append(not primary)
        return modes

    async def _extract_article(
        self, article_url: str, pool_name: str | None, source_url: str
    ) -> WrappedFeedItem | None:
        host = urlparse(article_url).netloc.lower() or "unknown-host"
        if self._is_binary_url(article_url):
            logger.info("Skipping binary URL by extension: %s", article_url)
            await self.db.record_extraction_attempt(
                source_url=source_url,
                article_url=article_url,
                host=host,
                proxy=None,
                mode="skip",
                success=False,
                latency_ms=0,
                error="skipped_binary_url_extension",
            )
            return None

        should_skip, skip_reason = await self._preflight_article_url(article_url)
        if should_skip:
            logger.info(
                "Skipping URL by header preflight %s: %s", skip_reason, article_url
            )
            await self.db.record_extraction_attempt(
                source_url=source_url,
                article_url=article_url,
                host=host,
                proxy=None,
                mode="skip",
                success=False,
                latency_ms=0,
                error=skip_reason or "skipped_preflight",
            )
            return None

        options = ExtractionOptions(
            min_word_count=80,
            min_char_threshold=500,
            include_images=True,
            include_code_blocks=True,
            safe_markdown=True,
        )
        modes = self._extractor_modes()
        for proxy in await self._next_proxy_order(pool_name):
            for use_playwright in modes:
                network = NetworkOptions(proxy=proxy, randomize_user_agent=True)
                started = perf_counter()
                mode = "playwright" if use_playwright else "http"
                try:
                    result = await extract_article_from_url(
                        article_url,
                        options=options,
                        network=network,
                        prefer_playwright=use_playwright,
                    )
                except Exception as exc:
                    logger.warning(
                        "Extraction error for %s via proxy=%s mode=%s: %s",
                        article_url,
                        proxy,
                        mode,
                        exc,
                    )
                    await self.db.record_extraction_attempt(
                        source_url=source_url,
                        article_url=article_url,
                        host=host,
                        proxy=proxy,
                        mode=mode,
                        success=False,
                        latency_ms=int((perf_counter() - started) * 1000),
                        error=str(exc),
                    )
                    continue

                if result.success and result.content.strip():
                    within_limits, article_inner_chars = (
                        self._is_within_article_size_limits(result.content)
                    )
                    if not within_limits:
                        logger.info(
                            "Dropping oversized extraction for %s "
                            "(article_inner_chars=%s)",
                            article_url,
                            article_inner_chars,
                        )
                        await self.db.record_extraction_attempt(
                            source_url=source_url,
                            article_url=article_url,
                            host=host,
                            proxy=proxy,
                            mode=mode,
                            success=False,
                            latency_ms=int((perf_counter() - started) * 1000),
                            error=(
                                "content_too_large("
                                f"article_inner_chars={article_inner_chars},"
                                "max_article_inner_chars="
                                f"{self.settings.max_article_inner_text_chars})"
                            ),
                        )
                        continue

                    await self.db.record_extraction_attempt(
                        source_url=source_url,
                        article_url=article_url,
                        host=host,
                        proxy=proxy,
                        mode=mode,
                        success=True,
                        latency_ms=int((perf_counter() - started) * 1000),
                        error=None,
                    )
                    return WrappedFeedItem(
                        title=(result.title or article_url).strip(),
                        source_url=article_url,
                        pub_date=result.date_published,
                        content_html=result.content,
                    )

                logger.info(
                    "Extraction failed for %s via proxy=%s mode=%s",
                    article_url,
                    proxy,
                    mode,
                )
                error_reason = (result.error or "").strip()
                if not error_reason:
                    if result.success:
                        error_reason = "empty_content"
                    else:
                        error_reason = "empty_or_unsuccessful_result"
                await self.db.record_extraction_attempt(
                    source_url=source_url,
                    article_url=article_url,
                    host=host,
                    proxy=proxy,
                    mode=mode,
                    success=False,
                    latency_ms=int((perf_counter() - started) * 1000),
                    error=error_reason,
                )

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
        started = perf_counter()
        try:
            source_xml = await self._fetch_source_feed(source_url)
            source_title, entries = parse_source_feed(source_xml, limit=max_items)
            feed_id = await self.db.upsert_feed(source_url, source_title)

            wrapped_items: list[WrappedFeedItem] = []
            uncached_entries = []
            for entry in entries:
                if self._is_binary_url(entry.article_url):
                    logger.info(
                        "Skipping binary entry URL from feed: %s", entry.article_url
                    )
                    continue

                cached = await self.db.get_cached_item(feed_id, entry.article_url)
                if cached is not None:
                    within_limits, article_inner_chars = (
                        self._is_within_article_size_limits(cached.content_html)
                    )
                    if not within_limits:
                        logger.info(
                            "Dropping oversized cached item for %s "
                            "(article_inner_chars=%s)",
                            entry.article_url,
                            article_inner_chars,
                        )
                        continue
                    if entry.pub_date and not cached.pub_date:
                        cached.pub_date = entry.pub_date
                    wrapped_items.append(cached)
                    continue
                uncached_entries.append(entry)

            extracted_results = await self._extract_uncached_entries(
                uncached_entries, pool_name, source_url
            )
            for entry, extracted in extracted_results:
                if extracted is None:
                    continue
                within_limits, article_inner_chars = (
                    self._is_within_article_size_limits(extracted.content_html)
                )
                if not within_limits:
                    logger.info(
                        "Dropping oversized extracted item for %s "
                        "(article_inner_chars=%s)",
                        entry.article_url,
                        article_inner_chars,
                    )
                    continue
                if entry.pub_date:
                    extracted.pub_date = entry.pub_date
                if not extracted.title.strip():
                    extracted.title = entry.title
                await self.db.upsert_item(feed_id, extracted)
                wrapped_items.append(extracted)

            await self.db.prune_feed(feed_id, self.settings.cache_max_items)
            await self.db.record_feed_request(
                source_url=source_url,
                proxy_pool=pool_name,
                requested_items=max_items,
                returned_items=len(wrapped_items),
                duration_ms=int((perf_counter() - started) * 1000),
                status="ok",
                error=None,
            )
            return source_title, wrapped_items
        except Exception as exc:
            await self.db.record_feed_request(
                source_url=source_url,
                proxy_pool=pool_name,
                requested_items=max_items,
                returned_items=0,
                duration_ms=int((perf_counter() - started) * 1000),
                status="error",
                error=str(exc),
            )
            raise

    async def _extract_uncached_entries(
        self, entries: list[SourceFeedEntry], pool_name: str | None, source_url: str
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
                        entry.article_url, pool_name, source_url
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
