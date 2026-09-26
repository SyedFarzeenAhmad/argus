"""structlog setup shared by the API and the workers."""

from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(level: int = logging.INFO, json: bool = False) -> None:
    logging.basicConfig(stream=sys.stdout, level=level, format="%(message)s")
    renderer = structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )
