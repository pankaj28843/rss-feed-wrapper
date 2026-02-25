from __future__ import annotations

import logging
import os
from logging.handlers import TimedRotatingFileHandler

from .config import Settings


def configure_logging(settings: Settings) -> None:
    os.makedirs(settings.log_dir, exist_ok=True)
    logfile = os.path.join(settings.log_dir, "rss-feed-wrapper.log")

    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )

    has_console = any(
        isinstance(h, logging.StreamHandler)
        and not isinstance(h, TimedRotatingFileHandler)
        for h in root.handlers
    )
    if not has_console:
        console = logging.StreamHandler()
        console.setLevel(level)
        console.setFormatter(formatter)
        root.addHandler(console)

    existing = [
        h
        for h in root.handlers
        if isinstance(h, TimedRotatingFileHandler)
        and os.path.abspath(getattr(h, "baseFilename", "")) == os.path.abspath(logfile)
    ]
    if not existing:
        file_handler = TimedRotatingFileHandler(
            filename=logfile,
            when="midnight",
            interval=1,
            backupCount=max(365, settings.log_retention_days),
            utc=True,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
