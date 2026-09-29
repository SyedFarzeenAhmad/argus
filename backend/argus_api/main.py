"""FastAPI application.

uv run fastapi dev argus_api/main.py          # :8000, docs at /docs
"""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from argus_api.api.routes import admin, analytics, assets, fleet, incidents, work_orders
from argus_api.core.config import get_settings
from argus_api.core.logging import configure_logging
from argus_api.db import session as db_session

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop = threading.Event()
    thread = None
    if get_settings().embedded_fusion:
        from argus_api.fusion.worker import loop

        thread = threading.Thread(target=loop, args=(stop, 2.0), name="fusion", daemon=True)
        thread.start()
        log.info("fusion.embedded_started")
    yield
    stop.set()
    if thread is not None:
        thread.join(timeout=5)


def create_app() -> FastAPI:
    configure_logging()
    s = get_settings()
    app = FastAPI(
        title="ARGUS",
        version="0.1.0",
        summary="Central urban intelligence platform: a public bus fleet as a city-wide sensor.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for r in (
        assets.router,
        work_orders.router,
        incidents.router,
        analytics.router,
        fleet.router,
        admin.router,
    ):
        app.include_router(r)

    @app.get("/healthz", tags=["ops"])
    def healthz():
        with db_session.get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok"}

    return app


app = create_app()
