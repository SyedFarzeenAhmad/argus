"""Route delay, corridor flow and the live fleet (docs/07 §6–7).

Route delay needs no computer vision whatsoever — GPS against GTFS — which is worth saying
plainly. What makes it interesting is *attribution*: decomposing a route's lateness across the
segments that produced it, and naming the cause of each.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from argus_api.analytics.common import congestion_bins, corrected_speed_kmh, local_hour
from argus_api.analytics.congestion import Attributor, summarise
from argus_api.analytics.freeflow import freeflow_lookup
from argus_api.db.models import (
    Device,
    GtfsRoute,
    GtfsStop,
    GtfsStopTime,
    GtfsTrip,
    Incident,
    Observation,
    RoadSegment,
    SegmentPass,
    Telemetry,
)
from argus_api.db.types import iso

# ─── Route delay ─────────────────────────────────────────────────────────────


def _scheduled_headway_s(session: Session, route_id: str) -> float | None:
    """Median gap between consecutive scheduled departures at each trip's first stop."""
    route_ids = session.scalars(
        select(GtfsRoute.route_id).where(
            (GtfsRoute.route_id == route_id) | (GtfsRoute.short_name == route_id)
        )
    ).all()
    if not route_ids:
        return None
    trips = session.scalars(select(GtfsTrip.trip_id).where(GtfsTrip.route_id.in_(route_ids))).all()
    if len(trips) < 2:
        return None
    first = (
        select(GtfsStopTime.trip_id, func.min(GtfsStopTime.stop_sequence).label("seq"))
        .where(GtfsStopTime.trip_id.in_(trips))
        .group_by(GtfsStopTime.trip_id)
        .subquery()
    )
    deps = sorted(
        d
        for d in session.scalars(
            select(GtfsStopTime.departure_s).join(
                first,
                (GtfsStopTime.trip_id == first.c.trip_id)
                & (GtfsStopTime.stop_sequence == first.c.seq),
            )
        )
        if d is not None
    )
    gaps = [b - a for a, b in zip(deps, deps[1:], strict=False) if b > a]
    return float(statistics.median(gaps)) if gaps else None


def route_delay(session: Session, *, route_id: str, since: datetime, until: datetime) -> dict:
    tel = session.scalars(
        select(Telemetry).where(
            Telemetry.route_id == route_id, Telemetry.at >= since, Telemetry.at < until
        )
    ).all()
    delays = [t.delay_s for t in tel if t.delay_s is not None]
    headways = [t.headway_s for t in tel if t.headway_s is not None and t.headway_s > 0]
    sched = _scheduled_headway_s(session, route_id)

    by_stop: dict[str, list[int]] = defaultdict(list)
    for t in tel:
        if t.next_stop_id and t.delay_s is not None:
            by_stop[t.next_stop_id].append(t.delay_s)
    stop_names = (
        dict(
            session.execute(
                select(GtfsStop.stop_id, GtfsStop.name).where(GtfsStop.stop_id.in_(set(by_stop)))
            ).all()
        )
        if by_stop
        else {}
    )
    stops = sorted(
        (
            {
                "stop_id": s,
                "name": stop_names.get(s),
                "samples": len(v),
                "median_delay_s": float(statistics.median(v)),
            }
            for s, v in by_stop.items()
        ),
        key=lambda x: -x["median_delay_s"],
    )

    # Bunching: two buses arriving together after a long gap. Passengers feel it more sharply
    # than raw lateness, and average delay does not show it.
    bunch_threshold = max(120.0, 0.25 * sched) if sched else 120.0
    headway = {
        "scheduled_s": sched,
        "observed_median_s": float(statistics.median(headways)) if headways else None,
        "median_abs_deviation_s": float(statistics.median(abs(h - sched) for h in headways))
        if headways and sched
        else None,
        "bunching_samples": sum(1 for h in headways if h < bunch_threshold),
        "bunching_threshold_s": bunch_threshold,
        "samples": len(headways),
    }

    return {
        "route_id": route_id,
        "window": {"from": iso(since), "to": iso(until)},
        "method": "GNSS against GTFS stop_times; no computer vision involved",
        "delay": {
            "samples": len(delays),
            "median_s": float(statistics.median(delays)) if delays else None,
            "p90_s": float(np.percentile(delays, 90)) if delays else None,
        },
        "headway": headway,
        "stops": stops[:25],
        "attribution": delay_attribution(session, route_id=route_id, since=since, until=until),
    }


def delay_attribution(session: Session, *, route_id: str, since: datetime, until: datetime) -> dict:
    """delay_contribution(s) = t_actual(s) − length(s) / v_ff(s), summed per segment."""
    passes = session.scalars(
        select(SegmentPass).where(
            SegmentPass.route_id == route_id,
            SegmentPass.entered_at >= since,
            SegmentPass.entered_at < until,
        )
    ).all()
    if not passes:
        return {"segments": [], "statement": None}
    seg_ids = {p.segment_id for p in passes}
    segments = {
        s.segment_id: s
        for s in session.scalars(select(RoadSegment).where(RoadSegment.segment_id.in_(seg_ids)))
    }
    ff = freeflow_lookup(session, {(p.segment_id, p.direction) for p in passes}, segments)
    contrib: dict[str, float] = defaultdict(float)
    per_trip: dict[str, list[float]] = defaultdict(list)
    trips = {p.trip_id or str(p.segment_pass_id) for p in passes}
    for p in passes:
        length = p.length_m or (
            segments[p.segment_id].length_m if p.segment_id in segments else None
        )
        if not length:
            continue
        v_ff = ff[(p.segment_id, p.direction)][0]
        t_actual = (p.exited_at - p.entered_at).total_seconds() - (p.dwell_excl_s or 0.0)
        c = max(0.0, t_actual - length / (v_ff / 3.6))
        contrib[p.segment_id] += c
        per_trip[p.segment_id].append(c)
    total = sum(contrib.values())
    attributor = Attributor(session, since, until, segments)
    traffic = {
        t.segment_id: t
        for t in summarise(session, congestion_bins(session, since, until, seg_ids), segments)
    }
    items = []
    for seg_id, c in sorted(contrib.items(), key=lambda kv: -kv[1])[:10]:
        seg = segments.get(seg_id)
        t = traffic.get(seg_id)
        cause = attributor.cause(t) if t else None
        items.append(
            {
                "segment_id": seg_id,
                "name": seg.name if seg else None,
                "share": round(c / total, 4) if total else None,
                "median_loss_s_per_trip": round(statistics.median(per_trip[seg_id]), 1),
                "length_m": round(seg.length_m, 1) if seg else None,
                "attributed_cause": cause["cause"] if cause else None,
            }
        )
    statement = None
    if items and items[0]["share"]:
        top = items[0]
        statement = (
            f"{top['share'] * 100:.0f}% of route {route_id}'s delay in this window "
            f"accrues on {top['name'] or top['segment_id']}; median loss "
            f"{top['median_loss_s_per_trip'] / 60:.1f} min per trip"
            + (f"; attributed cause: {top['attributed_cause']}" if top["attributed_cause"] else "")
            + "."
        )
    return {
        "trips_observed": len(trips),
        "total_delay_s": round(total, 1),
        "segments": items,
        "statement": statement,
    }


# ─── Corridor flow (docs/07 §6) ──────────────────────────────────────────────


def corridor_flow(
    session: Session,
    *,
    bbox: tuple[float, float, float, float] | None,
    since: datetime,
    until: datetime,
) -> dict[str, Any]:
    """Directional PCU per segment by hour of day, and the AM/PM tidal ratio.

    PCU here is what a passing bus observed, per pass — a relative flow index for comparing
    directions and hours, not a calibrated vehicles-per-hour count.
    """
    from argus_api.analytics.common import segments_in_bbox

    segments = segments_in_bbox(session, bbox)
    ids = set(segments) if bbox is not None else None
    hourly: dict[tuple[str, str], dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    for b in congestion_bins(session, since, until, ids):
        if b.pcu_n:
            hourly[(b.segment_id, b.direction)][int(local_hour(b.bucket))].append(
                b.pcu_sum / b.pcu_n
            )
    out = []
    for (seg_id, direction), hours in hourly.items():
        profile = {h: round(float(np.mean(v)), 2) for h, v in sorted(hours.items())}
        am = [v for h, v in profile.items() if 7 <= h < 11]
        pm = [v for h, v in profile.items() if 16 <= h < 21]
        seg = segments.get(seg_id)
        out.append(
            {
                "segment_id": seg_id,
                "direction": direction,
                "name": seg.name if seg else None,
                "pcu_per_pass_by_hour": profile,
                "am_peak_pcu": round(float(np.mean(am)), 2) if am else None,
                "pm_peak_pcu": round(float(np.mean(pm)), 2) if pm else None,
                "path": seg.coordinates if seg else None,
            }
        )
    for row in out:
        am, pm = row["am_peak_pcu"], row["pm_peak_pcu"]
        row["tidal_ratio_am_pm"] = round(am / pm, 3) if am and pm else None
    return {
        "window": {"from": iso(since), "to": iso(until)},
        "measure": "IRC:106 PCU observed per bus pass (relative flow index)",
        "not_claimed": "vehicle-level origin–destination; that needs re-identification we "
        "deliberately do not build",
        "segments": out,
    }


# ─── Fleet ───────────────────────────────────────────────────────────────────

DIRTY_LENS = 0.7
HOT_C = 80.0
LOW_FPS = 8.0


def health_flags(health: dict) -> list[str]:
    flags = []
    q = health.get("camera_quality") or {}
    dirty = [cam for cam, v in q.items() if v < DIRTY_LENS]
    if dirty:
        flags.append("camera_quality:" + ",".join(sorted(dirty)))
    if (health.get("temp_c") or 0) >= HOT_C:
        flags.append("thermal")
    fps = health.get("inference_fps")
    if fps is not None and fps < LOW_FPS:
        flags.append("low_fps")
    if (health.get("queue_oldest_s") or 0) > 600 or (health.get("queue_depth") or 0) > 500:
        flags.append("uplink_backlog")
    if health.get("gnss_fix") in ("none",):
        flags.append("no_gnss")
    return flags


def _latest_telemetry(session: Session, since: datetime) -> list[Telemetry]:
    latest = (
        select(Telemetry.device_id, func.max(Telemetry.at).label("at"))
        .where(Telemetry.at >= since)
        .group_by(Telemetry.device_id)
        .subquery()
    )
    return list(
        session.scalars(
            select(Telemetry).join(
                latest, (Telemetry.device_id == latest.c.device_id) & (Telemetry.at == latest.c.at)
            )
        )
    )


def fleet_live(session: Session, *, now: datetime, max_age_s: int = 300) -> dict[str, Any]:
    rows = _latest_telemetry(session, now - timedelta(seconds=max_age_s))
    flagged = {d.device_id for d in session.scalars(select(Device).where(Device.flagged.is_(True)))}
    return {
        "at": iso(now),
        "vehicles": [
            {
                "device_id": t.device_id,
                "bus_id": t.bus_id,
                "route_id": t.route_id,
                "trip_id": t.trip_id,
                "at": iso(t.at),
                "age_s": round((now - t.at).total_seconds(), 1),
                "lat": t.lat,
                "lon": t.lon,
                "speed_kmh": t.speed_kmh,
                "heading": t.heading,
                "delay_s": t.delay_s,
                "headway_s": t.headway_s,
                "next_stop_id": t.next_stop_id,
                "health": t.health,
                "flags": health_flags(t.health or {}),
                "device_flagged": t.device_id in flagged,
            }
            for t in rows
        ],
    }


def fleet_summary(session: Session, *, now: datetime) -> dict[str, Any]:
    """The HUD numbers, including the bandwidth figure that is the case for edge processing."""
    day = now - timedelta(days=1)
    live = _latest_telemetry(session, now - timedelta(minutes=5))
    uplink_kb = sum(float((t.health or {}).get("uplink_kb_session") or 0) for t in live)

    def count(model, col):
        return session.scalar(select(func.count()).select_from(model).where(col >= day)) or 0

    flags = defaultdict(int)
    for t in live:
        for f in health_flags(t.health or {}):
            flags[f.split(":")[0]] += 1
    return {
        "at": iso(now),
        "devices_registered": session.scalar(select(func.count()).select_from(Device)) or 0,
        "devices_online": len(live),
        "devices_flagged": session.scalar(
            select(func.count()).select_from(Device).where(Device.flagged.is_(True))
        )
        or 0,
        "health_flags": dict(flags),
        "queue_depth_total": sum(int((t.health or {}).get("queue_depth") or 0) for t in live),
        "uplink_mb_session_online": round(uplink_kb / 1024.0, 2),
        "last_24h": {
            "observations": count(Observation, Observation.received_at),
            "segment_passes": count(SegmentPass, SegmentPass.received_at),
            "incidents": count(Incident, Incident.received_at),
        },
    }


__all__ = ["route_delay", "corridor_flow", "fleet_live", "fleet_summary", "corrected_speed_kmh"]
