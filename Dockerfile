FROM python:3.12-slim

LABEL org.opencontainers.image.title="rss-feed-wrapper" \
      org.opencontainers.image.description="Generic full-text RSS wrapper using article-extractor" \
      org.opencontainers.image.source="https://github.com/pankaj28843/rss-feed-wrapper"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md /app/
COPY src /app/src

RUN pip install --no-cache-dir .
RUN python -m playwright install --with-deps chromium

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app /ms-playwright
USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8080/health || exit 1

CMD ["uvicorn", "rss_feed_wrapper.main:app", "--host", "0.0.0.0", "--port", "8080"]
