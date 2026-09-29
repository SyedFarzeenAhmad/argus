"""Crowd density — a rate estimator, not a census (docs/07 §3).

                 Σ_i unique_pedestrian_tracks_i
    D̂(s, t) = ─────────────────────────────────  × 100      persons per 100 m²
                 Σ_i observed_area_m2_i

    SE(D̂)  ≈ √(Σ n_i) / (Σ A_i) × 100                        (Poisson)

For the city view each segment's numerator and denominator are split across the H3 cells it
crosses in proportion to the segment length inside each cell, and only then divided — so a
hexagon estimate is a correctly area-weighted rate, not an average of rates.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from typing import Any

import h3
from sqlalchemy import select
from sqlalchemy.orm import Session

from argus_api.analytics.common import local_hour, pedestrian_bins, segments_in_bbox
from argus_api.core.geo import interpolate_line
from argus_api.db.models import RoadSegment
from argus_api.db.types import iso

# Local-time hour windows [start, end). Night wraps midnight.
PRESETS: dict[str, tuple[float, float]] = {
    "school_arrival": (7.0, 9.0),
    "morning_peak": (8.0, 11.0),
    "midday": (11.0, 16.0),
    "evening_peak": (16.0, 21.0),
    "night": (21.0, 6.0),
}

CAVEATS = [
    "Only pedestrians visible from the carriageway; interiors, side lanes and the far side of "
    "a divided road are not observed.",
    "Strongly biased to bus corridors; read alongside the coverage layer.",
    "Sampled at pass times: a crowd that forms and disperses between two buses is missed.",
    "Counts only. Never identity, never demographics.",
]


def in_preset(dt: datetime, preset: str | None) -> bool:
    if not preset:
        return True
    lo, hi = PRESETS[preset]
    h = local_hour(dt)
    return lo <= h < hi if lo < hi else (h >= lo or h < hi)


def _rate(n: float, area: float) -> tuple[float | None, float | None]:
    if area <= 0:
        return None, None
    return n / area * 100.0, math.sqrt(max(n, 0.0)) / area * 100.0


def pedestrian_density(
    session: Session,
    *,
    bbox: tuple[float, float, float, float] | None,
    since: datetime,
    until: datetime,
    preset: str | None = None,
    resolution: str = "10",
) -> dict[str, Any]:
    if preset and preset not in PRESETS:
        raise ValueError(f"unknown preset {preset!r}; one of {sorted(PRESETS)}")
    segments = segments_in_bbox(session, bbox)
    ids = set(segments) if bbox is not None else None

    per_seg: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for b in pedestrian_bins(session, since, until, ids):
        if not in_preset(b.bucket, preset):
            continue
        acc = per_seg[b.segment_id]
        acc["passes"] += b.passes
        acc["pedestrians"] += b.pedestrians
        acc["area"] += b.observed_area_m2
        acc["crossings"] += b.crossings
        acc["cluster_max"] = max(acc["cluster_max"], b.cluster_max)
        acc["school_zone_passes"] += b.school_zone_passes

    header = {
        "window": {"from": iso(since), "to": iso(until)},
        "preset": preset,
        "units": {"density": "persons per 100 m² of observed footpath/verge"},
        "caveats": CAVEATS,
    }

    if resolution == "segment":
        missing = set(per_seg) - set(segments)
        if missing:
            segments.update(
                {
                    s.segment_id: s
                    for s in session.scalars(
                        select(RoadSegment).where(RoadSegment.segment_id.in_(missing))
                    )
                }
            )
        items = []
        for seg_id, a in per_seg.items():
            d, se = _rate(a["pedestrians"], a["area"])
            seg = segments.get(seg_id)
            items.append(
                {
                    "segment_id": seg_id,
                    "name": seg.name if seg else None,
                    "density_per_100m2": _r(d),
                    "standard_error": _r(se),
                    "pedestrians": int(a["pedestrians"]),
                    "observed_area_m2": round(a["area"], 1),
                    "crossing_events": int(a["crossings"]),
                    "cluster_max": int(a["cluster_max"]),
                    "passes": int(a["passes"]),
                    "path": seg.coordinates if seg else None,
                }
            )
        items.sort(key=lambda x: -(x["density_per_100m2"] or 0.0))
        return {**header, "resolution": "segment", "segments": items}

    res = int(resolution)
    if res not in (9, 10, 11):
        raise ValueError("resolution must be segment, 9, 10 or 11")
    cells: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for seg_id, a in per_seg.items():
        seg = segments.get(seg_id)
        if seg is None:
            continue  # no geometry, cannot place it on the hex grid
        pts = interpolate_line(seg.coordinates, step_m=10.0)
        share: dict[str, int] = defaultdict(int)
        for lat, lon in pts:
            share[h3.latlng_to_cell(lat, lon, res)] += 1
        for cell, k in share.items():
            frac = k / len(pts)
            c = cells[cell]
            c["pedestrians"] += a["pedestrians"] * frac
            c["area"] += a["area"] * frac
            c["crossings"] += a["crossings"] * frac
            c["passes"] += a["passes"] * frac
    items = []
    for cell, c in cells.items():
        d, se = _rate(c["pedestrians"], c["area"])
        if d is None:
            continue
        items.append(
            {
                "h3": cell,
                "density_per_100m2": _r(d),
                "standard_error": _r(se),
                "pedestrians": round(c["pedestrians"], 2),
                "observed_area_m2": round(c["area"], 1),
                "crossing_events": round(c["crossings"], 2),
                "effective_passes": round(c["passes"], 2),
            }
        )
    items.sort(key=lambda x: -x["density_per_100m2"])
    return {**header, "resolution": res, "cells": items}


def _r(v: float | None) -> float | None:
    return round(v, 4) if v is not None else None
