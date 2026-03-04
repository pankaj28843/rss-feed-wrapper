# rss-feed-wrapper

[![CI](https://github.com/pankaj28843/rss-feed-wrapper/actions/workflows/ci.yml/badge.svg)](https://github.com/pankaj28843/rss-feed-wrapper/actions/workflows/ci.yml)
[![Docker Publish](https://github.com/pankaj28843/rss-feed-wrapper/actions/workflows/docker.yml/badge.svg)](https://github.com/pankaj28843/rss-feed-wrapper/actions/workflows/docker.yml)

Minimal, generic RSS-to-fulltext wrapper.

Input: any RSS feed URL via `GET /rss?url=<feed_url>`.
Output: a new RSS feed where each item includes:
- extracted full article HTML (`content:encoded`)
- extracted title and publication date (when available)
- canonical source URL in `description`

`hnrss.org` is supported as one source pattern, but the wrapper is feed-agnostic.

## Why

- Keep your reader/Kindle pipeline on RSS
- Get full content instead of short snippets
- Cache extracted articles locally to reduce repeat fetches

## Features

- Generic source feed support (`link`-based and HNRSS `Article URL` pattern)
- `article-extractor` integration (no fork, no changes needed)
- SQLite cache with per-source retention cap (`max 100` by default)
- Adaptive high parallelism:
  - global semaphore
  - per-host dynamic concurrency (fast hosts scale up, failing hosts back off)
- Proxy support:
  - multiple named pools (`?proxy_pool=<name>`)
  - automatic retry across all proxies in selected pool (always iterates full pool)
- Persistent logs with daily rotation (1-year retention configurable)
- Built-in dashboard endpoints (`/dashboard`, `/dashboard.json`)

## Quickstart

```bash
git clone git@github.com:pankaj28843/rss-feed-wrapper.git
cd rss-feed-wrapper
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
uvicorn rss_feed_wrapper.main:app --host 0.0.0.0 --port 8080
```

Test:

```bash
curl 'http://localhost:8080/health'
curl 'http://localhost:8080/rss?url=https%3A%2F%2Fhnrss.org%2Fnewest%3Fpoints%3D100%26comments%3D25%26count%3D20' -o wrapped.xml
```

## Docker

```bash
docker run --rm -p 8080:8080 \
  -e RSS_WRAPPER_PROXY_POOLS='default=http://proxy1.local:8080,http://proxy2.local:8080' \
  ghcr.io/pankaj28843/rss-feed-wrapper:latest
```

## API

### `GET /health`
Returns service status.

### `GET /rss`
Query params:
- `url` (required): URL-encoded source RSS/Atom feed URL (`http`/`https`)
- `max_items` (optional): `1..200`, default `100`
- `proxy_pool` (optional): name of pool from `RSS_WRAPPER_PROXY_POOLS`

Example:

```bash
curl 'http://localhost:8080/rss?url=https%3A%2F%2Fhnrss.org%2Fnewest%3Fcount%3D30&max_items=30&proxy_pool=default'
```

### `GET /dashboard`
HTML dashboard with feed request and extraction stats.

### `GET /dashboard.json`
JSON dashboard snapshot for automation.

## Configuration

All config is env-based with prefix `RSS_WRAPPER_`.

| Variable | Default | Notes |
|---|---|---|
| `DB_PATH` | `./data/rss_wrapper.db` | SQLite path |
| `CACHE_MAX_ITEMS` | `100` | Per-source item cap |
| `HTTP_TIMEOUT` | `20` | Source feed fetch timeout (seconds) |
| `PREFER_PLAYWRIGHT` | `true` | Forwarded to `article-extractor` |
| `EXTRACT_HTTP_FIRST` | `false` | Try HTTP extraction before Playwright |
| `EXTRACT_FALLBACK_PLAYWRIGHT` | `true` | Retry with opposite mode on extraction failure |
| `MAX_PARALLELISM` | `32` | Global extraction concurrency |
| `PER_HOST_INITIAL_PARALLELISM` | `2` | Initial per-host concurrency |
| `PER_HOST_MIN_PARALLELISM` | `1` | Minimum per-host concurrency |
| `PER_HOST_MAX_PARALLELISM` | `8` | Maximum per-host concurrency |
| `LOG_DIR` | `./data/logs` | Persistent log directory |
| `LOG_RETENTION_DAYS` | `366` | Daily rotated log retention |
| `LOG_LEVEL` | `INFO` | Application log level |
| `DASHBOARD_LOOKBACK_DAYS` | `7` | Dashboard aggregation window |
| `PROXY_POOLS` | `` | Multiple pools, format: `poolA=http://a:1,http://b:2;poolB=http://c:3` |

## Proxy pools

```bash
export RSS_WRAPPER_PROXY_POOLS='default=http://proxy1.local:8080,http://proxy2.local:8080;fallback=http://proxy3.local:8080,http://proxy4.local:8080'
```

Behavior:
- if no pool is requested, `default` is used when present
- each article extraction tries direct (`None`) first, then all proxies in round-robin order
- on failures it keeps trying remaining proxies (full pool iteration every request)

## Development

```bash
ruff format .
ruff check .
pytest -q
```

## Security notes

- Do not commit proxy addresses/tokens in public repos.
- Keep runtime secrets in deployment env files or secret managers.
- Restrict input URLs at your edge if running this as a public service.

## License

MIT
