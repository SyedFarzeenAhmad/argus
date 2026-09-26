"""Road condition map, ward scorecard and coverage (docs/07 §4–5, docs/00 "Fleet coverage").

All three are normalised by what the fleet actually surveyed. Without that, the "worst ward"
is reliably the best-connected one and an undriven road scores a perfect 100.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from argus_api.analytics.common import congestion_bins, last_pass_by_segment, segments_in_bbox
from argus_api.core.config import get_settings
from argus_api.db.models import Asset, AssetEvent, RoadSegment, Ward, WorkOrder
from argus_api.db.types import iso
from argus_api.fusion.actions import POLICIES, policy
from argus_api.fusion.work_orders import FLEET_VERIFIED, OPEN_STATUSES

UNRESOLVED = ("confirmed", "reported", "in_progress")
SURFACE_CLASSES = tuple(c for c, p in POLICIES.items() if p.surface)


# ─── Road condition (docs/07 §4) ─────────────────────────────────────────────


def road_condition(
    session: Session, *, bbox: tuple[float, float, float, float] | None, now: datetime
) -> dict[str, Any]:
    """condition(s) = 100 − clamp(Σ w × severity × min(1, area/A_ref) × 100 / L_hm, 0, 100)

    with the segment length ``L_hm`` in hectometres (100 m), so four potholes on a 1.5 km
    segment cost far less than the same four on 200 m. On a 0–100 scale deliberately
    analogous to a Pavement Condition Index.
    """
    cfg = get_settings()
    segments = segments_in_bbox(session, bbox)
    ids = set(segments)
    last = last_pass_by_segment(session, ids)
    recent_since = now - timedelta(days=cfg.unsurveyed_after_days)
    passes: dict[str, int] = defaultdict(int)
    for b in congestion_bins(session, recent_since, now, ids):
        passes[b.segment_id] += b.passes

    penalty: dict[str, float] = defaultdict(float)
    defects: dict[str, int] = defaultdict(int)
    if ids:
        for a in session.scalars(
            select(Asset).where(
                Asset.segment_id.in_(ids),
                Asset.state.in_(UNRESOLVED),
                Asset.class_id.in_(SURFACE_CLASSES),
            )
        ):
            p = policy(a.class_id)
            area = (a.physical or {}).get("area_m2")
            area_factor = 1.0 if area is None else min(1.0, area / p.area_ref_m2)
            penalty[a.segment_id] += p.weight * (a.severity_score or 0.5) * area_factor
            defects[a.segment_id] += 1

    items = []
    for seg_id, seg in segments.items():
        last_at = last.get(seg_id)
        surveyed = last_at is not None and last_at >= recent_since
        score = None
        if surveyed:
            hm = max(seg.length_m / 100.0, 0.1)
            score = round(100.0 - min(100.0, max(0.0, penalty[seg_id] * 100.0 / hm)), 1)
        items.append(
            {
                "segment_id": seg_id,
                "name": seg.name,
                "ward_id": seg.ward_id,
                "status": "surveyed" if surveyed else "unsurveyed",
                "condition": score,
                "open_surface_defects": defects[seg_id],
                "last_surveyed_at": iso(last_at),
                "pass_count": passes[seg_id],
                "length_m": round(seg.length_m, 1),
                "path": seg.coordinates,
            }
        )
    return {
        "scale": "0 (failed) … 100 (no open defects)",
        "unsurveyed_after_days": cfg.unsurveyed_after_days,
        "segments": items,
    }


# ─── Coverage ────────────────────────────────────────────────────────────────


def coverage(
    session: Session,
    *,
    bbox: tuple[float, float, float, float] | None,
    since: datetime,
    now: datetime,
) -> dict[str, Any]:
    """What we have NOT surveyed. The endpoint that stops the platform lying by omission."""
    segments = segments_in_bbox(session, bbox)
    ids = set(segments)
    last = last_pass_by_segment(session, ids)
    counts: dict[str, int] = defaultdict(int)
    for b in congestion_bins(session, since, now, ids):
        counts[b.segment_id] += b.passes
    items, km_total, km_surveyed = [], 0.0, 0.0
    for seg_id, seg in segments.items():
        last_at = last.get(seg_id)
        status = "never" if last_at is None else ("surveyed" if last_at >= since else "stale")
        km_total += seg.length_m / 1000.0
        if status == "surveyed":
            km_surveyed += seg.length_m / 1000.0
        items.append(
            {
                "segment_id": seg_id,
                "name": seg.name,
                "status": status,
                "last_pass_at": iso(last_at),
                "passes": counts[seg_id],
                "path": seg.coordinates,
            }
        )
    return {
        "since": iso(since),
        "summary": {
            "km_total": round(km_total, 3),
            "km_surveyed": round(km_surveyed, 3),
            "fraction_surveyed": round(km_surveyed / km_total, 4) if km_total else None,
            "segments": len(items),
        },
        "segments": items,
    }


# ─── Ward scorecard (docs/07 §5) ─────────────────────────────────────────────


def _km_surveyed_by_ward(session: Session, since: datetime, until: datetime) -> dict[str, float]:
    seg_ids = {b.segment_id for b in congestion_bins(session, since, until) if b.passes}
    out: dict[str, float] = defaultdict(float)
    if seg_ids:
        for seg in session.scalars(select(RoadSegment).where(RoadSegment.segment_id.in_(seg_ids))):
            if seg.ward_id:
                out[seg.ward_id] += seg.length_m / 1000.0
    return out


def _reopen_pairs(session: Session) -> list[tuple[str, datetime, datetime]]:
    """(asset_id, resolved_at, reopened_at) for every re-opening after a resolution."""
    events = session.scalars(
        select(AssetEvent)
        .where(AssetEvent.to_state.in_(("resolved", "candidate")))
        .order_by(AssetEvent.asset_id, AssetEvent.at, AssetEvent.id)
    ).all()
    pairs, last_resolved = [], {}
    for e in events:
        if e.to_state == "resolved":
            last_resolved[e.asset_id] = e.at
        elif e.from_state == "resolved" and e.asset_id in last_resolved:
            pairs.append((str(e.asset_id), last_resolved.pop(e.asset_id), e.at))
    return pairs


def ward_scorecard(
    session: Session, *, since: datetime, until: datetime, now: datetime
) -> dict[str, Any]:
    """Four sub-scores, deliberately not collapsed into one gameable number.

    deficiency     Σ w × severity × confidence / km surveyed      how bad
    responsiveness median age of open work orders                   how fast
    closure        fleet-verified resolutions ÷ issued orders       how much gets fixed
    durability     re-openings within 90 days of a resolution       repair quality
    """
    cfg = get_settings()
    wards = {w.ward_id: w for w in session.scalars(select(Ward)).all()}
    km_surv = _km_surveyed_by_ward(session, since, until)
    km_total: dict[str, float] = defaultdict(float)
    for seg in session.scalars(select(RoadSegment).where(RoadSegment.ward_id.is_not(None))):
        km_total[seg.ward_id] += seg.length_m / 1000.0

    burden: dict[str, float] = defaultdict(float)
    open_assets: dict[str, int] = defaultdict(int)
    critical: dict[str, int] = defaultdict(int)
    for a in session.scalars(
        select(Asset).where(Asset.state.in_(UNRESOLVED), Asset.ward_id.is_not(None))
    ):
        burden[a.ward_id] += policy(a.class_id).weight * (a.severity_score or 0.5) * a.confidence
        open_assets[a.ward_id] += 1
        critical[a.ward_id] += a.severity_band == "critical"

    open_ages: dict[str, list[float]] = defaultdict(list)
    issued: dict[str, int] = defaultdict(int)
    verified: dict[str, int] = defaultdict(int)
    for wo in session.scalars(select(WorkOrder).where(WorkOrder.ward_id.is_not(None))):
        if wo.status in OPEN_STATUSES and wo.issued_at:
            open_ages[wo.ward_id].append((now - wo.issued_at).total_seconds() / 86400.0)
        if wo.issued_at and since <= wo.issued_at < until:
            issued[wo.ward_id] += 1
            if wo.closed_by == FLEET_VERIFIED:
                verified[wo.ward_id] += 1

    asset_ward = dict(session.execute(select(Asset.asset_id, Asset.ward_id)).all())
    resolved: dict[str, int] = defaultdict(int)
    for e in session.scalars(
        select(AssetEvent).where(
            AssetEvent.to_state == "resolved", AssetEvent.at >= since, AssetEvent.at < until
        )
    ):
        w = asset_ward.get(e.asset_id)
        if w:
            resolved[w] += 1
    reopened: dict[str, int] = defaultdict(int)
    window = timedelta(days=cfg.reopen_window_days)
    for asset_id, r_at, o_at in _reopen_pairs(session):
        w = asset_ward.get(_uuid(asset_id))
        if w and since <= r_at < until and o_at - r_at <= window:
            reopened[w] += 1

    ward_ids = set(wards) | set(open_assets) | set(km_surv) | set(issued)
    items = []
    for wid in sorted(ward_ids):
        ward = wards.get(wid)
        km = km_surv.get(wid, 0.0)
        items.append(
            {
                "ward_id": wid,
                "name": ward.name if ward else None,
                "engineer_of_record": ward.engineer_of_record if ward else None,
                "km_surveyed": round(km, 3),
                "km_total": round(km_total.get(wid, 0.0), 3),
                "deficiency_index": round(burden[wid] / km, 4) if km > 0 else None,
                "open_assets": open_assets[wid],
                "open_critical": critical[wid],
                "responsiveness_median_open_days": round(statistics.median(open_ages[wid]), 2)
                if open_ages[wid]
                else None,
                "open_work_orders": len(open_ages[wid]),
                "work_orders_issued": issued[wid],
                "fleet_verified_resolutions": verified[wid],
                "closure_rate": round(verified[wid] / issued[wid], 4) if issued[wid] else None,
                "resolutions": resolved[wid],
                "reopened_within_window": reopened[wid],
                "durability_reopen_rate": round(reopened[wid] / resolved[wid], 4)
                if resolved[wid]
                else None,
            }
        )
    ranked = sorted(
        (i for i in items if i["deficiency_index"] is not None),
        key=lambda i: -i["deficiency_index"],
    )
    for rank, i in enumerate(ranked, start=1):
        i["deficiency_rank"] = rank
    return {
        "period": {"from": iso(since), "to": iso(until)},
        "durability_window_days": cfg.reopen_window_days,
        "notes": {
            "deficiency_index": "weighted open-defect burden per km actually surveyed; null "
            "when the fleet did not survey the ward in this period",
            "closure_rate": "fleet-verified resolutions ÷ work orders issued in the period",
        },
        "wards": items,
    }


def _uuid(s: str):
    import uuid

    return uuid.UUID(s)
