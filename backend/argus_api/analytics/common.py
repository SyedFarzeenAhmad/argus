"""Shared plumbing for analytics: time handling, segment lookup, and the aggregate source.

**The aggregate source.** On PostgreSQL every heat map reads the TimescaleDB continuous
aggregates created by the initial migration (``congestion_15min``,
``pedestrian_density_hourly``); scanning raw passes per request does not survive a live demo.
Elsewhere (SQLite in tests, or before the migration has run) the same bins are computed in
Python from the raw rows. Both paths return identical ``*Bin`` records, so everything above
this module is oblivious to which one ran.

Rates never appear in a bin. Numerators and denominators travel separately so any later
rollup — segment → hexagon → ward — is still a correct weighted rate (docs/07 §3).
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from argus_api.core.config import get_settings
from argus_api.core.geo import LocalProjection
from argus_api.db.models import RoadSegment, SegmentPass
from argus_api.db.session import is_postgres

MAX_PLAUSIBLE_BUS_KMH = 120.0


def local_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def local_hour(dt: datetime) -> float:
    lt = dt.astimezone(local_tz())
    return lt.hour + lt.minute / 60.0


def floor_time(dt: datetime, minutes: int) -> datetime:
    epoch = int(dt.timestamp())
    step = minutes * 60
    return datetime.fromtimestamp(epoch - epoch % step, tz=UTC)


def corrected_speed_kmh(p: SegmentPass) -> float:
    """Bus speed over the segment with bus-stop dwell removed (docs/07 §1).

    Without this correction every bus stop in the city renders as a permanent traffic jam.
    """
    # Mirrors the speed_sum expression in the congestion_15min continuous aggregate
    # (migration 0001); the two must agree or PostgreSQL and the fallback diverge.
    dur = (p.exited_at - p.entered_at).total_seconds() - (p.dwell_excl_s or 0.0)
    if p.length_m and p.length_m > 0 and dur > 1.0:
        return min(MAX_PLAUSIBLE_BUS_KMH, p.length_m / dur * 3.6)
    return min(MAX_PLAUSIBLE_BUS_KMH, p.mean_speed_kmh)


# ─── Segments ────────────────────────────────────────────────────────────────


def segments_in_bbox(
    session: Session, bbox: tuple[float, float, float, float] | None
) -> dict[str, RoadSegment]:
    q = select(RoadSegment)
    if bbox is not None:
        min_lon, min_lat, max_lon, max_lat = bbox
        q = q.where(
            RoadSegment.max_lon >= min_lon,
            RoadSegment.min_lon <= max_lon,
            RoadSegment.max_lat >= min_lat,
            RoadSegment.min_lat <= max_lat,
        )
    return {s.segment_id: s for s in session.scalars(q).all()}


def distance_to_segment_m(seg: RoadSegment, lat: float, lon: float) -> float:
    from shapely.geometry import LineString, Point

    proj = LocalProjection(lat, lon)
    coords = [proj.to_xy(c[1], c[0]) for c in seg.coordinates]
    if len(coords) == 1:
        return math.hypot(*coords[0])
    return LineString(coords).distance(Point(0.0, 0.0))


# ─── Bins ────────────────────────────────────────────────────────────────────


@dataclass
class CongestionBin:
    bucket: datetime
    segment_id: str
    direction: str
    passes: int
    speed_sum: float  # Σ dwell-corrected speed
    occupancy_sum: float
    occupancy_n: int
    pcu_sum: float
    pcu_n: int
    stopped_sum: float
    dwell_sum: float
    two_wheeler: int
    vehicles: int


@dataclass
class PedestrianBin:
    bucket: datetime
    segment_id: str
    passes: int
    pedestrians: int
    observed_area_m2: float
    crossings: int
    cluster_max: int
    school_zone_passes: int


def _has_view(session: Session, name: str) -> bool:
    return bool(session.execute(text("SELECT to_regclass(:n) IS NOT NULL"), {"n": name}).scalar())


def _raw_passes(
    session: Session, since: datetime, until: datetime, segment_ids: set[str] | None
) -> list[SegmentPass]:
    q = select(SegmentPass).where(SegmentPass.entered_at >= since, SegmentPass.entered_at < until)
    if segment_ids is not None:
        if not segment_ids:
            return []
        q = q.where(SegmentPass.segment_id.in_(segment_ids))
    return list(session.scalars(q).all())


def congestion_bins(
    session: Session, since: datetime, until: datetime, segment_ids: set[str] | None = None
) -> list[CongestionBin]:
    if is_postgres(session) and _has_view(session, "congestion_15min"):
        sql = """
            SELECT bucket, segment_id, direction, passes, speed_sum, occupancy_sum, occupancy_n,
                   pcu_sum, pcu_n, stopped_sum, dwell_sum, two_wheeler, vehicles
            FROM congestion_15min WHERE bucket >= :since AND bucket < :until
        """
        params: dict = {"since": floor_time(since, 15), "until": until}
        if segment_ids is not None:
            if not segment_ids:
                return []
            sql += " AND segment_id = ANY(:ids)"
            params["ids"] = list(segment_ids)
        return [CongestionBin(**dict(r._mapping)) for r in session.execute(text(sql), params)]

    bins: dict[tuple, CongestionBin] = {}
    for p in _raw_passes(session, since, until, segment_ids):
        key = (floor_time(p.entered_at, 15), p.segment_id, p.direction)
        b = bins.get(key)
        if b is None:
            b = bins[key] = CongestionBin(
                key[0], p.segment_id, p.direction, 0, 0.0, 0.0, 0, 0.0, 0, 0.0, 0.0, 0, 0
            )
        b.passes += 1
        b.speed_sum += corrected_speed_kmh(p)
        if p.occupancy_ratio is not None:
            b.occupancy_sum += p.occupancy_ratio
            b.occupancy_n += 1
        if p.pcu_estimate is not None:
            b.pcu_sum += p.pcu_estimate
            b.pcu_n += 1
        b.stopped_sum += p.stopped_s or 0.0
        b.dwell_sum += p.dwell_excl_s or 0.0
        counts = p.vehicle_counts or {}
        b.two_wheeler += int(counts.get("two_wheeler", 0))
        b.vehicles += int(sum(counts.values()))
    return list(bins.values())


def pedestrian_bins(
    session: Session, since: datetime, until: datetime, segment_ids: set[str] | None = None
) -> list[PedestrianBin]:
    if is_postgres(session) and _has_view(session, "pedestrian_density_hourly"):
        sql = """
            SELECT bucket, segment_id, passes, pedestrians, observed_area_m2, crossings,
                   cluster_max, school_zone_passes
            FROM pedestrian_density_hourly WHERE bucket >= :since AND bucket < :until
        """
        params: dict = {"since": floor_time(since, 60), "until": until}
        if segment_ids is not None:
            if not segment_ids:
                return []
            sql += " AND segment_id = ANY(:ids)"
            params["ids"] = list(segment_ids)
        return [PedestrianBin(**dict(r._mapping)) for r in session.execute(text(sql), params)]

    bins: dict[tuple, PedestrianBin] = {}
    for p in _raw_passes(session, since, until, segment_ids):
        ped = p.pedestrian
        if not ped:
            continue
        key = (floor_time(p.entered_at, 60), p.segment_id)
        b = bins.get(key)
        if b is None:
            b = bins[key] = PedestrianBin(key[0], p.segment_id, 0, 0, 0.0, 0, 0, 0)
        b.passes += 1
        b.pedestrians += int(ped.get("unique_tracks") or 0)
        b.observed_area_m2 += float(ped.get("observed_area_m2") or 0.0)
        b.crossings += int(ped.get("crossing_events") or 0)
        b.cluster_max = max(b.cluster_max, int(ped.get("cluster_max") or 0))
        b.school_zone_passes += 1 if ped.get("in_school_zone") else 0
    return list(bins.values())


def last_pass_by_segment(
    session: Session, segment_ids: set[str] | None, lookback_days: int = 3650
) -> dict[str, datetime]:
    """Most recent survey of each segment, from the aggregate (which outlives raw passes)."""
    until = datetime.now(UTC) + timedelta(days=1)
    since = until - timedelta(days=lookback_days)
    out: dict[str, datetime] = {}
    for b in congestion_bins(session, since, until, segment_ids):
        if b.segment_id not in out or b.bucket > out[b.segment_id]:
            out[b.segment_id] = b.bucket
    return out


def group_bins(bins: list[CongestionBin]) -> dict[tuple[str, str], list[CongestionBin]]:
    out: dict[tuple[str, str], list[CongestionBin]] = defaultdict(list)
    for b in bins:
        out[(b.segment_id, b.direction)].append(b)
    return out
