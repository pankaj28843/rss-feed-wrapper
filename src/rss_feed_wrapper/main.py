from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
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
            f"<tr><td class='cell-wrap'>{escape(str(row['proxy_name']))}</td>"
            f"<td>{row['attempts']}</td>"
            f"<td>{row['success_count']}</td><td>{row['fail_count']}</td>"
            f"<td>{row['transport_fail_count']}</td>"
            f"<td>{int(row['avg_latency_ms'] or 0)}</td></tr>"
            for row in snapshot["proxies"]
        )
        host_rows = "".join(
            f"<tr><td>{escape(str(row['host']))}</td><td>{row['attempts']}</td>"
            f"<td>{row['success_count']}</td><td>{row['fail_count']}</td></tr>"
            for row in snapshot["hosts"]
        )
        error_rows = "".join(
            f"<tr><td class='cell-wrap'>{escape(str(row['error_text']))}</td>"
            f"<td>{row['occurrences']}</td></tr>"
            for row in snapshot["top_errors"]
        )
        warning_rows = "".join(
            f"<li>{escape(str(row['proxy_name']))}: {row['fail_count']}/"
            f"{row['attempts']} failed ({row['fail_rate_pct']}%), "
            f"transport errors {row['transport_fail_count']}/"
            f"{row['attempts']} ({row['transport_fail_rate_pct']}%)</li>"
            for row in snapshot["warnings"]
        )
        failed_feed_rows = "".join(
            f"<tr><td>{escape(str(row['requested_at']))}</td>"
            f"<td class='cell-wrap'>{escape(str(row['source_url']))}</td>"
            f"<td class='cell-wrap'>{escape(str(row['error'] or ''))}</td></tr>"
            for row in snapshot["recent_failed_feeds"]
        )

        extraction_total = max(1, int(extraction["total_attempts"]))
        extraction_success_rate = round(
            (int(extraction["success"]) / extraction_total) * 100, 1
        )
        feed_total = max(1, int(feed_stats["total"]))
        feed_success_rate = round((int(feed_stats["ok"]) / feed_total) * 100, 1)
        degraded_proxies = len(snapshot["warnings"])
        health_label = "healthy"
        if feed_success_rate < 80 or extraction_success_rate < 70:
            health_label = "degraded"
        if feed_success_rate < 50 or extraction_success_rate < 35:
            health_label = "critical"
        health_badge_class = (
            "b-ok"
            if health_label == "healthy"
            else "b-warn"
            if health_label == "degraded"
            else "b-bad"
        )
        generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%SZ")
        warning_block = (
            f"<ul>{warning_rows}</ul>"
            if warning_rows
            else (
                "<div class='ok'>"
                "No high proxy transport-failure signals detected."
                "</div>"
            )
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
                "<tr><td colspan='2'>No extraction errors in lookback window.</td></tr>"
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
      --bg: #f8fafc;
      --card: #ffffff;
      --line: #dbe3ee;
      --head: #f1f5f9;
      --text: #0f172a;
      --muted: #334155;
      --ok: #166534;
      --warn: #b45309;
      --bad: #991b1b;
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif;
      margin: 16px;
      background: var(--bg);
      color: var(--text);
    }}
    .page {{ max-width: 1300px; margin: 0 auto; }}
    .topline {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      align-items: center;
      margin-bottom: 8px;
    }}
    .badge {{
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 3px 10px;
      font-size: 0.85rem;
      background: var(--card);
    }}
    .b-ok {{ color: var(--ok); }}
    .b-warn {{ color: var(--warn); }}
    .b-bad {{ color: var(--bad); }}
    .grid {{
      display: grid; gap: 12px;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    }}
    .card {{
      background: var(--card); border: 1px solid var(--line);
      border-radius: 10px; padding: 12px;
    }}
    .card ul {{ margin: 8px 0 0 18px; }}
    h1 {{ margin: 0 0 10px 0; font-size: 1.15rem; }}
    h2 {{ margin: 0 0 10px 0; font-size: 1rem; }}
    .table-wrap {{
      width: 100%;
      overflow-x: auto;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: var(--card);
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 760px;
    }}
    th, td {{
      border-bottom: 1px solid #e2e8f0; padding: 8px;
      text-align: left; font-size: 0.9rem;
      vertical-align: top;
    }}
    th {{ background: var(--head); position: sticky; top: 0; z-index: 1; }}
    .section {{ margin-top: 16px; }}
    .meta {{ color: var(--muted); font-size: 0.9rem; margin-bottom: 12px; }}
    .warn {{ color: var(--warn); font-weight: 600; }}
    .ok {{ color: var(--ok); }}
    .cell-wrap {{
      max-width: 460px;
      overflow-wrap: anywhere;
      word-break: break-word;
    }}
    code {{ background: #eef2ff; border-radius: 4px; padding: 1px 4px; }}
    form {{ display: flex; gap: 8px; flex-wrap: wrap; }}
    input {{
      padding: 8px;
      border: 1px solid #cbd5e1;
      border-radius: 8px;
      min-width: 180px;
    }}
    button {{
      padding: 8px 12px; border-radius: 8px; border: 1px solid #94a3b8;
      background: #0f172a; color: #fff;
    }}
    @media (max-width: 640px) {{
      body {{ margin: 8px; }}
      th, td {{ font-size: 0.82rem; padding: 6px; }}
      table {{ min-width: 640px; }}
      h1 {{ font-size: 1.05rem; }}
      .meta {{ font-size: 0.82rem; }}
      .badge {{ font-size: 0.78rem; }}
      input {{ width: 100%; min-width: 0; }}
      button {{ width: 100%; }}
    }}
  </style>
</head>
<body>
  <div class="page">
  <h1>rss-feed-wrapper dashboard</h1>
  <div class="topline">
    <span class="badge {health_badge_class}">
      health: {health_label}
    </span>
    <span class="badge">feed success: {feed_success_rate}%</span>
    <span class="badge">extraction success: {extraction_success_rate}%</span>
    <span class="badge {("b-bad" if degraded_proxies else "b-ok")}">
      degraded proxies: {degraded_proxies}
    </span>
    <span class="badge">generated: {generated_at}</span>
  </div>
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
      <div class="meta">Warnings track proxy transport failures only.</div>
      {warning_block}
    </div>
  </div>
  <div class="section">
    <h2>Proxy attempts</h2>
    <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Proxy</th>
          <th>Attempts</th>
          <th>Success</th>
          <th>Fail</th>
          <th>Transport fail</th>
          <th>Avg latency (ms)</th>
        </tr>
      </thead>
      <tbody>{proxy_rows}</tbody>
    </table>
    </div>
  </div>
  <div class="section">
    <h2>Host attempts</h2>
    <div class="table-wrap">
    <table>
      <thead><tr><th>Host</th><th>Attempts</th><th>Success</th><th>Fail</th></tr></thead>
      <tbody>{host_rows}</tbody>
    </table>
    </div>
  </div>
  <div class="section">
    <h2>Top extraction errors</h2>
    <div class="table-wrap">
    <table>
      <thead><tr><th>Error</th><th>Count</th></tr></thead>
      <tbody>{error_block}</tbody>
    </table>
    </div>
  </div>
  <div class="section">
    <h2>Recent failed feed requests</h2>
    <div class="table-wrap">
    <table>
      <thead><tr><th>At</th><th>Source</th><th>Error</th></tr></thead>
      <tbody>{failed_block}</tbody>
    </table>
    </div>
  </div>
  </div>
</body>
</html>"""
        return Response(content=html, media_type="text/html; charset=utf-8")

    return app


app = create_app()
