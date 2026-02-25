from __future__ import annotations

import os
from datetime import UTC, datetime

import aiosqlite

from .models import WrappedFeedItem


class CacheDB:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA foreign_keys = ON")
        await self.conn.execute("PRAGMA journal_mode = WAL")
        await self.conn.execute("PRAGMA busy_timeout = 5000")
        await self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS feeds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_url TEXT NOT NULL UNIQUE,
                source_title TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS feed_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feed_id INTEGER NOT NULL,
                article_url TEXT NOT NULL,
                title TEXT NOT NULL,
                pub_date TEXT,
                content_html TEXT NOT NULL,
                extracted_at TEXT NOT NULL,
                FOREIGN KEY(feed_id) REFERENCES feeds(id) ON DELETE CASCADE,
                UNIQUE(feed_id, article_url)
            );

            CREATE INDEX IF NOT EXISTS idx_feed_items_feed_time
            ON feed_items(feed_id, extracted_at DESC);

            CREATE TABLE IF NOT EXISTS extraction_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                attempted_at TEXT NOT NULL,
                source_url TEXT NOT NULL,
                article_url TEXT NOT NULL,
                host TEXT NOT NULL,
                proxy TEXT,
                mode TEXT NOT NULL,
                success INTEGER NOT NULL,
                latency_ms INTEGER NOT NULL,
                error TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_extraction_attempts_time
            ON extraction_attempts(attempted_at DESC);

            CREATE INDEX IF NOT EXISTS idx_extraction_attempts_proxy_time
            ON extraction_attempts(proxy, attempted_at DESC);

            CREATE TABLE IF NOT EXISTS feed_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                requested_at TEXT NOT NULL,
                source_url TEXT NOT NULL,
                proxy_pool TEXT,
                requested_items INTEGER NOT NULL,
                returned_items INTEGER NOT NULL,
                duration_ms INTEGER NOT NULL,
                status TEXT NOT NULL,
                error TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_feed_requests_time
            ON feed_requests(requested_at DESC);
            """
        )
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn is not None:
            await self.conn.close()
            self.conn = None

    async def upsert_feed(self, source_url: str, source_title: str) -> int:
        if self.conn is None:
            raise RuntimeError("database not initialized")
        now = datetime.now(UTC).isoformat()
        await self.conn.execute(
            """
            INSERT INTO feeds(source_url, source_title, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source_url) DO UPDATE SET
              source_title = excluded.source_title,
              updated_at = excluded.updated_at
            """,
            (source_url, source_title, now, now),
        )
        await self.conn.commit()
        cursor = await self.conn.execute(
            "SELECT id FROM feeds WHERE source_url = ?", (source_url,)
        )
        row = await cursor.fetchone()
        return int(row["id"])

    async def get_cached_item(
        self, feed_id: int, article_url: str
    ) -> WrappedFeedItem | None:
        if self.conn is None:
            raise RuntimeError("database not initialized")
        cur = await self.conn.execute(
            """
            SELECT title, article_url, pub_date, content_html
            FROM feed_items
            WHERE feed_id = ? AND article_url = ?
            """,
            (feed_id, article_url),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return WrappedFeedItem(
            title=row["title"],
            source_url=row["article_url"],
            pub_date=row["pub_date"],
            content_html=row["content_html"],
        )

    async def upsert_item(self, feed_id: int, item: WrappedFeedItem) -> None:
        if self.conn is None:
            raise RuntimeError("database not initialized")
        now = datetime.now(UTC).isoformat()
        await self.conn.execute(
            """
            INSERT INTO feed_items(
              feed_id, article_url, title, pub_date, content_html, extracted_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(feed_id, article_url) DO UPDATE SET
              title = excluded.title,
              pub_date = excluded.pub_date,
              content_html = excluded.content_html,
              extracted_at = excluded.extracted_at
            """,
            (
                feed_id,
                item.source_url,
                item.title,
                item.pub_date,
                item.content_html,
                now,
            ),
        )
        await self.conn.commit()

    async def prune_feed(self, feed_id: int, keep_max: int) -> None:
        if self.conn is None:
            raise RuntimeError("database not initialized")
        await self.conn.execute(
            """
            DELETE FROM feed_items
            WHERE feed_id = ?
              AND id NOT IN (
                SELECT id FROM feed_items
                WHERE feed_id = ?
                ORDER BY extracted_at DESC
                LIMIT ?
              )
            """,
            (feed_id, feed_id, keep_max),
        )
        await self.conn.commit()

    async def count_feed_items(self, feed_id: int) -> int:
        if self.conn is None:
            raise RuntimeError("database not initialized")
        cur = await self.conn.execute(
            "SELECT COUNT(*) AS c FROM feed_items WHERE feed_id = ?", (feed_id,)
        )
        row = await cur.fetchone()
        return int(row["c"])

    async def record_extraction_attempt(
        self,
        *,
        source_url: str,
        article_url: str,
        host: str,
        proxy: str | None,
        mode: str,
        success: bool,
        latency_ms: int,
        error: str | None,
    ) -> None:
        if self.conn is None:
            raise RuntimeError("database not initialized")
        now = datetime.now(UTC).isoformat()
        await self.conn.execute(
            """
            INSERT INTO extraction_attempts(
              attempted_at, source_url, article_url, host, proxy, mode,
              success, latency_ms, error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                source_url,
                article_url,
                host,
                proxy,
                mode,
                1 if success else 0,
                latency_ms,
                error,
            ),
        )
        await self.conn.commit()

    async def record_feed_request(
        self,
        *,
        source_url: str,
        proxy_pool: str | None,
        requested_items: int,
        returned_items: int,
        duration_ms: int,
        status: str,
        error: str | None,
    ) -> None:
        if self.conn is None:
            raise RuntimeError("database not initialized")
        now = datetime.now(UTC).isoformat()
        await self.conn.execute(
            """
            INSERT INTO feed_requests(
              requested_at, source_url, proxy_pool, requested_items, returned_items,
              duration_ms, status, error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                source_url,
                proxy_pool,
                requested_items,
                returned_items,
                duration_ms,
                status,
                error,
            ),
        )
        await self.conn.commit()

    async def dashboard_snapshot(self, lookback_days: int) -> dict:
        if self.conn is None:
            raise RuntimeError("database not initialized")

        feed_cur = await self.conn.execute(
            """
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok_count,
              SUM(CASE WHEN status != 'ok' THEN 1 ELSE 0 END) AS fail_count,
              AVG(duration_ms) AS avg_ms
            FROM feed_requests
            WHERE requested_at >= datetime('now', ?)
            """,
            (f"-{lookback_days} days",),
        )
        feed_row = await feed_cur.fetchone()

        proxy_cur = await self.conn.execute(
            """
            SELECT
              COALESCE(proxy, 'direct') AS proxy_name,
              COUNT(*) AS attempts,
              SUM(success) AS success_count,
              SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS fail_count,
              AVG(latency_ms) AS avg_latency_ms
            FROM extraction_attempts
            WHERE attempted_at >= datetime('now', ?)
            GROUP BY COALESCE(proxy, 'direct')
            ORDER BY attempts DESC
            LIMIT 20
            """,
            (f"-{lookback_days} days",),
        )
        proxy_rows = [dict(row) for row in await proxy_cur.fetchall()]

        host_cur = await self.conn.execute(
            """
            SELECT
              host,
              COUNT(*) AS attempts,
              SUM(success) AS success_count,
              SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS fail_count
            FROM extraction_attempts
            WHERE attempted_at >= datetime('now', ?)
            GROUP BY host
            ORDER BY attempts DESC
            LIMIT 20
            """,
            (f"-{lookback_days} days",),
        )
        host_rows = [dict(row) for row in await host_cur.fetchall()]

        return {
            "feeds": {
                "total": int(feed_row["total"] or 0),
                "ok": int(feed_row["ok_count"] or 0),
                "fail": int(feed_row["fail_count"] or 0),
                "avg_duration_ms": int(feed_row["avg_ms"] or 0),
            },
            "proxies": proxy_rows,
            "hosts": host_rows,
        }
