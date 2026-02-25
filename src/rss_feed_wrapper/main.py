from __future__ import annotations

from contextlib import asynccontextmanager
from html import escape

from fastapi import FastAPI, HTTPException, Query, Response

from .config import Settings
from .db import CacheDB
from .logging_setup import configure_logging
from .rss import render_wrapped_rss
from .service import RSSWrapperService


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or Settings()
    configure_logging(cfg)
    db = CacheDB(cfg.db_path)
    service = RSSWrapperService(db=db, settings=cfg)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await db.connect()
        app.state.service = service
        app.state.settings = cfg
        try:
            yield
        finally:
            await db.close()

    app = FastAPI(
        title="rss-feed-wrapper",
        description="Standalone HNRSS fulltext RSS wrapper",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy", "service": "rss-feed-wrapper"}

    @app.get("/rss", response_class=Response)
    async def wrap_rss(
        url: str = Query(..., description="Original HNRSS URL"),
        max_items: int = Query(100, ge=1, le=200),
        proxy_pool: str | None = Query(
            None, description="Optional proxy pool name from RSS_WRAPPER_PROXY_POOLS"
        ),
    ) -> Response:
        try:
            source_url = service.validate_source_url(url)
            selected_pool = service.validate_pool_name(proxy_pool)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            source_title, items = await service.build_wrapped_items(
                source_url, max_items, selected_pool
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail=f"wrapper failed: {exc!s}"
            ) from exc

        rss_xml = render_wrapped_rss(source_url, source_title, items)
        return Response(content=rss_xml, media_type="application/rss+xml")

    @app.get("/dashboard.json")
    async def dashboard_json() -> dict:
        lookback = max(1, cfg.dashboard_lookback_days)
        return await db.dashboard_snapshot(lookback)

    @app.get("/dashboard", response_class=Response)
    async def dashboard_html() -> Response:
        lookback = max(1, cfg.dashboard_lookback_days)
        snapshot = await db.dashboard_snapshot(lookback)
        feed_stats = snapshot["feeds"]
        extraction = snapshot["extraction"]

        proxy_rows = "".join(
            f"<tr><td>{escape(str(row['proxy_name']))}</td><td>{row['attempts']}</td>"
            f"<td>{row['success_count']}</td><td>{row['fail_count']}</td>"
            f"<td>{int(row['avg_latency_ms'] or 0)}</td></tr>"
            for row in snapshot["proxies"]
        )
        host_rows = "".join(
            f"<tr><td>{escape(str(row['host']))}</td><td>{row['attempts']}</td>"
            f"<td>{row['success_count']}</td><td>{row['fail_count']}</td></tr>"
            for row in snapshot["hosts"]
        )
        error_rows = "".join(
            f"<tr><td>{escape(str(row['error_text']))}</td>"
            f"<td>{row['occurrences']}</td></tr>"
            for row in snapshot["top_errors"]
        )
        warning_rows = "".join(
            f"<li>{escape(str(row['proxy_name']))}: {row['fail_count']}/"
            f"{row['attempts']} failed ({row['fail_rate_pct']}%)</li>"
            for row in snapshot["warnings"]
        )
        failed_feed_rows = "".join(
            f"<tr><td>{escape(str(row['requested_at']))}</td>"
            f"<td>{escape(str(row['source_url']))}</td>"
            f"<td>{escape(str(row['error'] or ''))}</td></tr>"
            for row in snapshot["recent_failed_feeds"]
        )

        extraction_total = max(1, int(extraction["total_attempts"]))
        extraction_success_rate = round(
            (int(extraction["success"]) / extraction_total) * 100, 1
        )
        warning_block = (
            f"<ul>{warning_rows}</ul>"
            if warning_rows
            else "<div class='ok'>No high-failure proxies detected.</div>"
        )
        failed_block = (
            failed_feed_rows
            if failed_feed_rows
            else "<tr><td colspan='3'>No feed failures in lookback window.</td></tr>"
        )
        error_block = (
            error_rows
            if error_rows
            else (
                "<tr><td colspan='2'>"
                "No extraction errors in lookback window."
                "</td></tr>"
            )
        )

        html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta http-equiv="refresh" content="60" />
  <title>rss-feed-wrapper dashboard</title>
  <style>
    body {{
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif;
      margin: 20px; background: #f8fafc; color: #0f172a;
    }}
    .grid {{
      display: grid; gap: 12px;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    }}
    .card {{
      background: white; border: 1px solid #dbe3ee;
      border-radius: 10px; padding: 12px;
    }}
    h1 {{ margin: 0 0 10px 0; font-size: 1.15rem; }}
    h2 {{ margin: 0 0 10px 0; font-size: 1rem; }}
    table {{
      width: 100%; border-collapse: collapse; background: white;
      border-radius: 10px; overflow: hidden;
    }}
    th, td {{
      border-bottom: 1px solid #e2e8f0; padding: 8px;
      text-align: left; font-size: 0.9rem;
    }}
    th {{ background: #f1f5f9; }}
    .section {{ margin-top: 16px; }}
    .meta {{ color: #334155; font-size: 0.9rem; margin-bottom: 12px; }}
    .warn {{ color: #b45309; font-weight: 600; }}
    .ok {{ color: #166534; }}
    code {{ background: #eef2ff; border-radius: 4px; padding: 1px 4px; }}
    form {{ display: flex; gap: 8px; flex-wrap: wrap; }}
    input {{ padding: 8px; border: 1px solid #cbd5e1; border-radius: 8px; }}
    button {{
      padding: 8px 12px; border-radius: 8px; border: 1px solid #94a3b8;
      background: #0f172a; color: #fff;
    }}
    @media (max-width: 640px) {{
      body {{ margin: 10px; }}
      th, td {{ font-size: 0.82rem; }}
    }}
  </style>
</head>
<body>
  <h1>rss-feed-wrapper dashboard</h1>
  <div class="meta">
    lookback: last {lookback} days | refresh: 60s |
    base: <code>/rss?url=&lt;encoded-feed-url&gt;</code>
  </div>
  <div class="card">
    <h2>Quick test</h2>
    <form method="get" action="/rss">
      <input
        type="text"
        name="url"
        placeholder="https://hnrss.org/newest?count=20"
        size="48"
      />
      <input type="number" name="max_items" value="20" min="1" max="200" />
      <button type="submit">Fetch wrapped feed</button>
    </form>
  </div>
  <div class="grid">
    <div class="card">
      <h2>Feed requests</h2>
      <div>Total: {feed_stats["total"]}</div>
      <div>OK: {feed_stats["ok"]}</div>
      <div>Fail: {feed_stats["fail"]}</div>
      <div>Avg duration (ms): {feed_stats["avg_duration_ms"]}</div>
    </div>
    <div class="card">
      <h2>Extraction</h2>
      <div>Total attempts: {extraction["total_attempts"]}</div>
      <div>Success: {extraction["success"]}</div>
      <div>Fail: {extraction["fail"]}</div>
      <div>Success rate: {extraction_success_rate}%</div>
      <div>Avg latency (ms): {extraction["avg_latency_ms"]}</div>
    </div>
    <div class="card">
      <h2>Warnings</h2>
      {warning_block}
    </div>
  </div>
  <div class="section">
    <h2>Proxy attempts</h2>
    <table>
      <thead>
        <tr>
          <th>Proxy</th>
          <th>Attempts</th>
          <th>Success</th>
          <th>Fail</th>
          <th>Avg latency (ms)</th>
        </tr>
      </thead>
      <tbody>{proxy_rows}</tbody>
    </table>
  </div>
  <div class="section">
    <h2>Host attempts</h2>
    <table>
      <thead><tr><th>Host</th><th>Attempts</th><th>Success</th><th>Fail</th></tr></thead>
      <tbody>{host_rows}</tbody>
    </table>
  </div>
  <div class="section">
    <h2>Top extraction errors</h2>
    <table>
      <thead><tr><th>Error</th><th>Count</th></tr></thead>
      <tbody>{error_block}</tbody>
    </table>
  </div>
  <div class="section">
    <h2>Recent failed feed requests</h2>
    <table>
      <thead><tr><th>At</th><th>Source</th><th>Error</th></tr></thead>
      <tbody>{failed_block}</tbody>
    </table>
  </div>
</body>
</html>"""
        return Response(content=html, media_type="text/html; charset=utf-8")

    return app


app = create_app()
