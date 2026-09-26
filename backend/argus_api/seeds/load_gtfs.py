"""Load a GTFS feed (routes, stops, trips, stop_times) for route-delay analytics.

    uv run python -m argus_api.seeds.load_gtfs --source bmtc --path data/bmtc-gtfs.zip

Stop ids are prefixed with the source (``bmtc:4412``), matching what the edge reports in
``telemetry.schedule.next_stop_id``. Route ids are kept as published, since the edge reports
the public route number (``500D``) and ``short_name`` is matched as well.
"""

from __future__ import annotations

import argparse
import csv
import io
import zipfile
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.orm import Session

from argus_api.db import session as db_session
from argus_api.db.models import GtfsRoute, GtfsStop, GtfsStopTime, GtfsTrip


def _rows(path: Path, name: str) -> Iterator[dict[str, str]]:
    if path.is_dir():
        f = path / name
        if not f.exists():
            return
        with f.open(encoding="utf-8-sig", newline="") as fh:
            yield from csv.DictReader(fh)
    else:
        with zipfile.ZipFile(path) as z:
            member = next((m for m in z.namelist() if m.endswith(name)), None)
            if member is None:
                return
            with z.open(member) as raw:
                yield from csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))


def _secs(v: str | None) -> int | None:
    """GTFS times may exceed 24:00:00 for trips running past midnight."""
    if not v:
        return None
    h, m, s = (int(x) for x in v.strip().split(":"))
    return h * 3600 + m * 60 + s


def load(session: Session, path: Path, source: str) -> dict[str, int]:
    for model in (GtfsStopTime, GtfsTrip, GtfsStop, GtfsRoute):
        session.execute(delete(model))
    counts = {"routes": 0, "stops": 0, "trips": 0, "stop_times": 0}
    for r in _rows(path, "routes.txt"):
        session.add(
            GtfsRoute(
                route_id=r["route_id"],
                short_name=r.get("route_short_name"),
                long_name=r.get("route_long_name"),
            )
        )
        counts["routes"] += 1
    for r in _rows(path, "stops.txt"):
        session.add(
            GtfsStop(
                stop_id=f"{source}:{r['stop_id']}",
                name=r.get("stop_name"),
                lat=float(r["stop_lat"]),
                lon=float(r["stop_lon"]),
            )
        )
        counts["stops"] += 1
    for r in _rows(path, "trips.txt"):
        session.add(
            GtfsTrip(
                trip_id=r["trip_id"],
                route_id=r["route_id"],
                service_id=r.get("service_id"),
                direction_id=int(r["direction_id"]) if r.get("direction_id") else None,
                shape_id=r.get("shape_id"),
            )
        )
        counts["trips"] += 1
    session.flush()
    batch = []
    for r in _rows(path, "stop_times.txt"):
        batch.append(
            GtfsStopTime(
                trip_id=r["trip_id"],
                stop_sequence=int(r["stop_sequence"]),
                stop_id=f"{source}:{r['stop_id']}",
                arrival_s=_secs(r.get("arrival_time")),
                departure_s=_secs(r.get("departure_time")),
            )
        )
        if len(batch) >= 5000:
            session.add_all(batch)
            session.flush()
            counts["stop_times"] += len(batch)
            batch = []
    session.add_all(batch)
    counts["stop_times"] += len(batch)
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(description="Load a GTFS feed")
    ap.add_argument("--source", default="bmtc")
    ap.add_argument("--path", type=Path, required=True, help="GTFS .zip or directory")
    args = ap.parse_args()
    with db_session.session_scope() as s:
        print(load(s, args.path, args.source))


if __name__ == "__main__":
    main()
