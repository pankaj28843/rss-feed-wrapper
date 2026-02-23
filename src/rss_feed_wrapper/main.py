from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Response

from .config import Settings
from .db import CacheDB
from .rss import render_wrapped_rss
from .service import RSSWrapperService


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or Settings()
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

    return app


app = create_app()
