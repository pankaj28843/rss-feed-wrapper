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

        html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>rss-feed-wrapper dashboard</title>
  <style>
    body {{
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif;
      margin: 20px; background: #f7fafc; color: #0f172a;
    }}
    .grid {{
      display: grid; gap: 14px;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    }}
    .card {{
      background: white; border: 1px solid #e2e8f0;
      border-radius: 10px; padding: 12px;
    }}
    h1 {{ margin: 0 0 10px 0; font-size: 1.2rem; }}
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
  </style>
</head>
<body>
  <h1>rss-feed-wrapper dashboard</h1>
  <div class="meta">lookback: last {lookback} days</div>
  <div class="grid">
    <div class="card">
      <h2>Feed requests</h2>
      <div>Total: {feed_stats["total"]}</div>
      <div>OK: {feed_stats["ok"]}</div>
      <div>Fail: {feed_stats["fail"]}</div>
      <div>Avg duration (ms): {feed_stats["avg_duration_ms"]}</div>
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
</body>
</html>"""
        return Response(content=html, media_type="text/html; charset=utf-8")

    return app


app = create_app()
