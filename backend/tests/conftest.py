"""Shared fixtures. Tests run on in-memory SQLite with the in-process bus and store, so the
suite needs no docker. PostgreSQL-only paths (continuous aggregates, PostGIS columns) live in
the migration and are exercised by ``tests/test_postgres.py`` when ARGUS_TEST_PG_URL is set.
"""

from __future__ import annotations

import os

os.environ.setdefault("ARGUS_DATABASE_URL", "sqlite://")
os.environ.setdefault("ARGUS_REDIS_URL", "")
os.environ.setdefault("ARGUS_EVIDENCE_VERIFICATION", "best_effort")

import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from argus_api.core import storage  # noqa: E402
from argus_api.core.config import get_settings  # noqa: E402
from argus_api.db import session as db_session  # noqa: E402
from argus_api.db.models import Base  # noqa: E402
from argus_api.realtime import bus as bus_mod  # noqa: E402


@pytest.fixture()
def settings():
    return get_settings()


@pytest.fixture()
def engine():
    # ARGUS_TEST_PG_URL runs the whole suite against a database migrated with
    # `alembic upgrade head`, so the PostGIS columns and continuous aggregates are live.
    pg_url = os.environ.get("ARGUS_TEST_PG_URL")
    if pg_url:
        eng = db_session.make_engine(pg_url)
        tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
        with eng.begin() as conn:
            conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
            # Materialized aggregate rows outlive their raw passes by design (docs/08 §5),
            # so they must be cleared too or one test's heat map leaks into the next.
            conn.execute(text("TRUNCATE congestion_15min, pedestrian_density_hourly"))
    else:
        eng = db_session.make_engine("sqlite://")
        Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def factory(engine, monkeypatch):
    f = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(db_session, "get_sessionmaker", lambda: f)
    monkeypatch.setattr(db_session, "get_engine", lambda: engine)
    return f


@pytest.fixture()
def session(factory):
    with factory() as s:
        yield s


@pytest.fixture()
def bus():
    b = bus_mod.MemoryBus()
    bus_mod.set_bus(b)
    yield b
    bus_mod.set_bus(None)


@pytest.fixture()
def store():
    st = storage.MemoryStore()
    storage.set_store(st)
    yield st
    storage.set_store(None)


@pytest.fixture()
def ingestor(factory, bus, store):
    from argus_api.ingest.pipeline import Ingestor

    return Ingestor(session_factory=factory, bus=bus, store=lambda: store)
