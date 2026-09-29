"""Congestion: a speed-deficit signal that computer vision explains rather than produces.

    CI(s, d, t)    = clamp(1 − v_obs / v_ff, 0, 1)
    delay(s, d, t) = (1/v_obs − 1/v_ff) × 3600          seconds lost per kilometre

For CI > 0.4 the concurrent CV signals attribute a cause (docs/07 §1): demand, drainage,
incident, capacity loss, or mixed-traffic friction. Bins with fewer than 3 passes carry a
low-confidence flag — an estimate from one bus is shown as an estimate from one bus.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from argus_api.analytics.common import (
    CongestionBin,
    congestion_bins,
    distance_to_segment_m,
    group_bins,
    segments_in_bbox,
)
from argus_api.analytics.freeflow import freeflow_lookup
from argus_api.core.config import get_settings
from argus_api.db.models import Asset, Incident, RoadSegment
from argus_api.db.types import iso

ATTRIBUTION_THRESHOLD = 0.4
ACTIVE = ("confirmed", "reported", "in_progress")


@dataclass
class SegmentTraffic:
    segment_id: str
    direction: str
    passes: int
    v_obs: float
    v_ff: float
    freeflow_method: str
    occupancy: float | None
    pcu_per_pass: float | None
    two_wheeler_share: float | None
    stopped_s_per_pass: float
    dwell_s_per_pass: float

    @property
    def ci(self) -> float:
        if self.v_ff <= 0:
            return 0.0
        return min(1.0, max(0.0, 1.0 - self.v_obs / self.v_ff))

    @property
    def delay_s_per_km(self) -> float:
        if self.v_obs <= 0.5:
            return 3600.0 / 0.5 - 3600.0 / self.v_ff
        return max(0.0, (1.0 / self.v_obs - 1.0 / self.v_ff) * 3600.0)


def summarise(
    session: Session, bins: list[CongestionBin], segments: dict[str, RoadSegment]
) -> list[SegmentTraffic]:
    grouped = group_bins(bins)
    ff = freeflow_lookup(session, set(grouped), segments)
    out = []
    for (seg_id, direction), bs in grouped.items():
        passes = sum(b.passes for b in bs)
        if not passes:
            continue
        occ_n = sum(b.occupancy_n for b in bs)
        pcu_n = sum(b.pcu_n for b in bs)
        vehicles = sum(b.vehicles for b in bs)
        v_ff, method = ff[(seg_id, direction)]
        out.append(
            SegmentTraffic(
                segment_id=seg_id,
                direction=direction,
                passes=passes,
                v_obs=sum(b.speed_sum for b in bs) / passes,
                v_ff=v_ff,
                freeflow_method=method,
                occupancy=sum(b.occupancy_sum for b in bs) / occ_n if occ_n else None,
                pcu_per_pass=sum(b.pcu_sum for b in bs) / pcu_n if pcu_n else None,
                two_wheeler_share=sum(b.two_wheeler for b in bs) / vehicles if vehicles else None,
                stopped_s_per_pass=sum(b.stopped_sum for b in bs) / passes,
                dwell_s_per_pass=sum(b.dwell_sum for b in bs) / passes,
            )
        )
    return out


# ─── Attribution ─────────────────────────────────────────────────────────────


class Attributor:
    """Joins concurrent CV evidence to a slow segment to say *why* it is slow."""

    def __init__(
        self, session: Session, since: datetime, until: datetime, segments: dict[str, RoadSegment]
    ) -> None:
        self.segments = segments
        self.assets: dict[str, set[str]] = defaultdict(set)
        for seg_id, cls in session.execute(
            select(Asset.segment_id, Asset.class_id).where(
                Asset.state.in_(ACTIVE),
                Asset.class_id.in_(("waterlogging", "encroachment")),
                Asset.segment_id.is_not(None),
            )
        ):
            self.assets[seg_id].add(cls)
        self.incidents = session.scalars(
            select(Incident).where(
                Incident.started_at >= since - timedelta(minutes=30),
                Incident.started_at <= until,
                Incident.review_status != "dismissed",
            )
        ).all()

    def _incident_near(self, seg_id: str) -> bool:
        seg = self.segments.get(seg_id)
        for inc in self.incidents:
            if inc.segment_id == seg_id:
                return True
            if seg is not None and distance_to_segment_m(seg, inc.lat, inc.lon) <= 200.0:
                return True
        return False

    def cause(self, t: SegmentTraffic) -> dict[str, Any] | None:
        if t.ci <= ATTRIBUTION_THRESHOLD:
            return None
        occ = t.occupancy
        level = (
            None if occ is None else ("high" if occ >= 0.5 else "low" if occ < 0.25 else "medium")
        )
        assets = self.assets.get(t.segment_id, set())
        evidence: dict[str, Any] = {"occupancy": occ, "occupancy_level": level}
        if level == "high":
            cause = "demand"
        elif level == "low" and "waterlogging" in assets:
            cause = "drainage"
        elif level == "low" and self._incident_near(t.segment_id):
            cause = "incident"
        elif level == "low" and "encroachment" in assets:
            cause = "capacity_loss"
        elif level == "medium" and (t.two_wheeler_share or 0.0) > 0.5:
            cause = "mixed_traffic_friction"
            evidence["two_wheeler_share"] = round(t.two_wheeler_share or 0.0, 3)
        else:
            cause = "unattributed"
        return {"cause": cause, "evidence": evidence}


# ─── Endpoints' workhorses ───────────────────────────────────────────────────


def _row(
    t: SegmentTraffic, seg: RoadSegment | None, attributor: Attributor, include_geometry: bool
) -> dict[str, Any]:
    cfg = get_settings()
    intersection_delay = (
        max(0.0, t.stopped_s_per_pass - t.dwell_s_per_pass)
        if seg is not None and seg.has_signal
        else None
    )
    row: dict[str, Any] = {
        "segment_id": t.segment_id,
        "direction": t.direction,
        "name": seg.name if seg else None,
        "passes": t.passes,
        "low_confidence": t.passes < cfg.congestion_low_confidence_passes,
        "speed_kmh": round(t.v_obs, 2),
        "freeflow_kmh": round(t.v_ff, 2),
        "freeflow_method": t.freeflow_method,
        "congestion_index": round(t.ci, 3),
        "delay_s_per_km": round(t.delay_s_per_km, 1),
        "intersection_delay_s": round(intersection_delay, 1)
        if intersection_delay is not None
        else None,
        "occupancy_ratio": round(t.occupancy, 3) if t.occupancy is not None else None,
        "pcu_per_pass": round(t.pcu_per_pass, 1) if t.pcu_per_pass is not None else None,
        "attribution": attributor.cause(t),
    }
    if include_geometry:
        row["path"] = seg.coordinates if seg else None
    return row


def congestion(
    session: Session,
    *,
    bbox: tuple[float, float, float, float] | None,
    at: datetime,
    window_minutes: int = 15,
    direction: str | None = None,
    include_geometry: bool = True,
) -> dict[str, Any]:
    since = at - timedelta(minutes=window_minutes)
    segments = segments_in_bbox(session, bbox)
    ids = set(segments) if bbox is not None else None
    traffic = summarise(session, congestion_bins(session, since, at, ids), segments)
    if direction:
        traffic = [t for t in traffic if t.direction == direction]
    attributor = Attributor(session, since, at, segments)
    return {
        "window": {"from": iso(since), "to": iso(at)},
        "units": {
            "congestion_index": "0 free flow … 1 standstill",
            "delay_s_per_km": "seconds lost per km vs free flow",
        },
        "segments": [
            _row(t, segments.get(t.segment_id), attributor, include_geometry)
            for t in sorted(traffic, key=lambda t: -t.ci)
        ],
    }


def bottlenecks(
    session: Session,
    *,
    since: datetime,
    until: datetime,
    bbox: tuple[float, float, float, float] | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Ranked by passenger-hours lost, not by CI: a corridor costing 400 passenger-hours a
    day outranks a worse-CI lane costing 12."""
    cfg = get_settings()
    segments = segments_in_bbox(session, bbox)
    ids = set(segments) if bbox is not None else None
    traffic = summarise(session, congestion_bins(session, since, until, ids), segments)
    missing = {t.segment_id for t in traffic} - set(segments)
    if missing:
        segments.update(
            {
                s.segment_id: s
                for s in session.scalars(
                    select(RoadSegment).where(RoadSegment.segment_id.in_(missing))
                )
            }
        )
    attributor = Attributor(session, since, until, segments)
    ranked = []
    for t in traffic:
        seg = segments.get(t.segment_id)
        if seg is None:
            continue  # length unknown; cannot convert s/km into hours lost
        length_km = seg.length_m / 1000.0
        hours = t.delay_s_per_km * length_km * t.passes * cfg.assumed_bus_occupancy / 3600.0
        ranked.append((hours, t, seg))
    ranked.sort(key=lambda r: -r[0])
    return {
        "window": {"from": iso(since), "to": iso(until)},
        "occupancy_source": "assumed",
        "assumed_bus_occupancy": cfg.assumed_bus_occupancy,
        "items": [
            {
                **_row(t, seg, attributor, include_geometry=False),
                "length_m": round(seg.length_m, 1),
                "passenger_hours_lost": round(h, 2),
                "rank": i,
            }
            for i, (h, t, seg) in enumerate(ranked[:limit], start=1)
        ],
    }
