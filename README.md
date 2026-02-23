# rss-feed-wrapper

Standalone RSS feed wrapper service that:

- Accepts `GET /rss?url=<source_feed_url>`
- Fetches the source RSS feed (generic)
- Extracts article URL per entry:
: HNRSS `Article URL` pattern when present, otherwise standard item `link`
- Uses `article-extractor` to fetch full article content
- Caches results in local SQLite
- Keeps at most **100 cached items per source feed**
- Returns a new RSS feed with `title`, `pubDate`, source URL in `description`, and full HTML in `content:encoded`

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
uvicorn rss_feed_wrapper.main:app --host 0.0.0.0 --port 8080
```

Try:

```bash
curl 'http://localhost:8080/rss?url=https%3A%2F%2Fhnrss.org%2Fnewest%3Fpoints%3D100%26comments%3D25%26count%3D20'
```

## Config

- `RSS_WRAPPER_DB_PATH` (default: `./data/rss_wrapper.db`)
- `RSS_WRAPPER_CACHE_MAX_ITEMS` (default: `100`)
- `RSS_WRAPPER_HTTP_TIMEOUT` (default: `20`)
- `RSS_WRAPPER_PREFER_PLAYWRIGHT` (default: `true`)
- `RSS_WRAPPER_MAX_PARALLELISM` (default: `32`)
- `RSS_WRAPPER_PER_HOST_INITIAL_PARALLELISM` (default: `2`)
- `RSS_WRAPPER_PER_HOST_MIN_PARALLELISM` (default: `1`)
- `RSS_WRAPPER_PER_HOST_MAX_PARALLELISM` (default: `8`)
- `RSS_WRAPPER_PROXY_POOL` (comma-separated proxy URLs)
- `RSS_WRAPPER_PROXY_POOLS` (multiple named pools)
: format `poolA=http://host:port,http://host2:port;poolB=http://host3:port`

Runtime selection:
- `GET /rss?...&proxy_pool=poolA` to force a named proxy pool for that request.
- If omitted, service uses `default` pool (from `RSS_WRAPPER_PROXY_POOL` if set, otherwise first named pool).

## Dev checks

```bash
ruff format .
ruff check .
pytest -q
```

## Docker

```bash
docker build -t ghcr.io/<user>/rss-feed-wrapper:latest .
docker run --rm -p 8080:8080 ghcr.io/<user>/rss-feed-wrapper:latest
```
