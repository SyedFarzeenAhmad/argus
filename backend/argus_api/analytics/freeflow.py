"""Free-flow baseline, learned per segment per direction (docs/07 §1).

    v_ff(s, d) = P85 { dwell-corrected speed : passes of (s, d) in trailing 30 days,
                       excluding 07:00–11:00 and 16:00–21:00 local }
    requires n ≥ 50 passes; otherwise fall back to 0.8 × OSM maxspeed

The 85th percentile, not the maximum: the maximum is one empty Sunday 05:00 run. Learning it
per segment also captures what ``maxspeed`` cannot — a 60 km/h road that never exceeds 35
because of its geometry has a free-flow of 35.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from argus_api.analytics.common import corrected_speed_kmh, local_hour
from argus_api.core.config import get_settings
from argus_api.db.models import RoadSegment, SegmentFreeflow, SegmentPass
from argus_api.db.types import utcnow

PEAKS = ((7.0, 11.0), (16.0, 21.0))

# Used only when neither learned data nor a maxspeed tag exists; urban Bengaluru values.
DEFAULT_BY_HIGHWAY = {
    "motorway": 60.0,
    "trunk": 45.0,
    "primary": 40.0,
    "secondary": 35.0,
    "tertiary": 30.0,
    "residential": 25.0,
    "unclassified": 25.0,
}


def is_off_peak(dt: datetime) -> bool:
    h = local_hour(dt)
    return not any(lo <= h < hi for lo, hi in PEAKS)


def fallback(seg: RoadSegment | None) -> tuple[float, str]:
    if seg is not None and seg.maxspeed_kmh:
        return 0.8 * seg.maxspeed_kmh, "maxspeed"
    if seg is not None and seg.highway:
        return DEFAULT_BY_HIGHWAY.get(seg.highway.removesuffix("_link"), 30.0), "highway_default"
    return 30.0, "default"


def learn(speeds: list[float], seg: RoadSegment | None, min_passes: int) -> tuple[float, int, str]:
    if len(speeds) >= min_passes:
        return float(np.percentile(speeds, 85)), len(speeds), "p85_offpeak"
    v, method = fallback(seg)
    return v, len(speeds), method


def relearn_all(session: Session, now: datetime | None = None) -> int:
    cfg = get_settings()
    now = now or utcnow()
    since = now - timedelta(days=cfg.freeflow_window_days)
    speeds: dict[tuple[str, str], list[float]] = defaultdict(list)
    for p in session.scalars(
        select(SegmentPass).where(SegmentPass.entered_at >= since, SegmentPass.entered_at < now)
    ):
        if is_off_peak(p.entered_at):
            speeds[(p.segment_id, p.direction)].append(corrected_speed_kmh(p))
        else:
            speeds.setdefault((p.segment_id, p.direction), [])
    segs = {s.segment_id: s for s in session.scalars(select(RoadSegment)).all()}
    for (seg_id, direction), vs in speeds.items():
        v, n, method = learn(vs, segs.get(seg_id), cfg.freeflow_min_passes)
        row = session.get(SegmentFreeflow, (seg_id, direction))
        if row is None:
            session.add(
                SegmentFreeflow(
                    segment_id=seg_id,
                    direction=direction,
                    freeflow_kmh=v,
                    n_passes=n,
                    method=method,
                    computed_at=now,
                )
            )
        else:
            row.freeflow_kmh, row.n_passes, row.method, row.computed_at = v, n, method, now
        if seg_id in segs and direction == "forward":
            segs[seg_id].freeflow_kmh = v
    return len(speeds)


def freeflow_lookup(
    session: Session, keys: set[tuple[str, str]], segments: dict[str, RoadSegment]
) -> dict[tuple[str, str], tuple[float, str]]:
    out: dict[tuple[str, str], tuple[float, str]] = {}
    if keys:
        seg_ids = {k[0] for k in keys}
        for r in session.scalars(
            select(SegmentFreeflow).where(SegmentFreeflow.segment_id.in_(seg_ids))
        ):
            out[(r.segment_id, r.direction)] = (r.freeflow_kmh, r.method)
    missing = {k[0] for k in keys if k not in out} - set(segments)
    if missing:
        segments = {
            **segments,
            **{
                s.segment_id: s
                for s in session.scalars(
                    select(RoadSegment).where(RoadSegment.segment_id.in_(missing))
                )
            },
        }
    for k in keys:
        if k not in out:
            out[k] = fallback(segments.get(k[0]))
    return out
