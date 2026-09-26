"""Fusion worker: observations → assets, forever.

    uv run python -m argus_api.fusion.worker

Wakes when ingest signals new raw data (Redis, or the in-process bus), and otherwise polls
every few seconds so a lost wake-up costs latency, never data. Also runs the stale sweep and,
once a day, relearns free-flow baselines.
"""

from __future__ import annotations

import argparse
import signal
import threading
import time
from datetime import timedelta

import structlog

from argus_api.core.logging import configure_logging
from argus_api.db import session as db_session
from argus_api.db.types import utcnow
from argus_api.fusion.engine import FusionEngine
from argus_api.realtime.bus import get_bus

log = structlog.get_logger()


def run_cycle(batch: int = 1000) -> dict[str, int]:
    """One fusion cycle in its own transaction; events published only after commit."""
    factory = db_session.get_sessionmaker()
    total = {"observations": 0, "passes": 0, "stale": 0}
    while True:
        with factory() as session:
            engine = FusionEngine(session)
            stats = engine.run_once(batch=batch)
            session.commit()
            engine.publish_events()
        for k, v in stats.items():
            total[k] += v
        # Drain a backlog in batches before going back to sleep.
        if stats["observations"] < batch and stats["passes"] < batch:
            return total


def loop(stop: threading.Event, poll_s: float = 5.0, batch: int = 1000) -> None:
    bus = get_bus()
    last_freeflow = utcnow() - timedelta(days=2)
    while not stop.is_set():
        try:
            stats = run_cycle(batch)
            if any(stats.values()):
                log.info("fusion.cycle", **stats)
            if utcnow() - last_freeflow > timedelta(days=1):
                from argus_api.analytics.freeflow import relearn_all

                with db_session.get_sessionmaker()() as session:
                    n = relearn_all(session)
                    session.commit()
                log.info("freeflow.relearned", segments=n)
                last_freeflow = utcnow()
        except Exception:
            log.exception("fusion.cycle_failed")
            time.sleep(poll_s)
        bus.wait_for_fusion_work(poll_s)


def main() -> None:
    ap = argparse.ArgumentParser(description="ARGUS fusion worker")
    ap.add_argument("--poll", type=float, default=5.0, help="seconds between polls")
    ap.add_argument("--batch", type=int, default=1000)
    ap.add_argument("--once", action="store_true", help="drain the backlog once and exit")
    args = ap.parse_args()
    configure_logging()
    if args.once:
        log.info("fusion.once", **run_cycle(args.batch))
        return
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    loop(stop, args.poll, args.batch)


if __name__ == "__main__":
    main()
