"""Analytics endpoints (docs/07). Each is a thin wrapper; the maths lives in analytics/."""

from __future__ import annotations

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from argus_api.analytics import congestion as cong
from argus_api.analytics import infrastructure as infra
from argus_api.analytics import pedestrian as ped
from argus_api.analytics import transit
from argus_api.api.deps import bbox_param, get_db, require, time_param, window
from argus_api.core.auth import Principal
from argus_api.db.types import utcnow

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])
Viewer = Depends(require("viewer"))


@router.get("/congestion")
def congestion(
    bbox: str | None = None,
    at: str | None = Query(None, description="end of window (RFC3339); default now"),
    window_minutes: int = Query(15, alias="window", ge=15, le=7 * 24 * 60),
    direction: Literal["forward", "reverse"] | None = None,
    geometry: bool = True,
    db: Session = Depends(get_db),
    _: Principal = Viewer,
):
    return cong.congestion(
        db,
        bbox=bbox_param(bbox),
        at=time_param(at, utcnow()),
        window_minutes=window_minutes,
        direction=direction,
        include_geometry=geometry,
    )


@router.get("/bottlenecks")
def bottlenecks(
    bbox: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    _: Principal = Viewer,
):
    s, u = window(since, until, default_days=1)
    return cong.bottlenecks(db, since=s, until=u, bbox=bbox_param(bbox), limit=limit)


@router.get("/pedestrian-density")
def pedestrian_density(
    bbox: str | None = None,
    since: str | None = None,
    until: str | None = None,
    preset: str | None = Query(None, description=", ".join(ped.PRESETS)),
    resolution: str = Query("10", description="segment | 9 | 10 | 11"),
    db: Session = Depends(get_db),
    _: Principal = Viewer,
):
    s, u = window(since, until, default_days=7)
    try:
        return ped.pedestrian_density(
            db, bbox=bbox_param(bbox), since=s, until=u, preset=preset, resolution=resolution
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/ward-scorecard")
def ward_scorecard(
    period: int = Query(30, ge=1, le=366, description="days, ending now (or at `until`)"),
    until: str | None = None,
    db: Session = Depends(get_db),
    _: Principal = Viewer,
):
    end = time_param(until, utcnow())
    return infra.ward_scorecard(db, since=end - timedelta(days=period), until=end, now=utcnow())


@router.get("/road-condition")
def road_condition(bbox: str | None = None, db: Session = Depends(get_db), _: Principal = Viewer):
    return infra.road_condition(db, bbox=bbox_param(bbox), now=utcnow())


@router.get("/coverage")
def coverage(
    bbox: str | None = None,
    since: str | None = Query(None, description="default: 30 days ago"),
    db: Session = Depends(get_db),
    _: Principal = Viewer,
):
    now = utcnow()
    return infra.coverage(
        db, bbox=bbox_param(bbox), since=time_param(since, now - timedelta(days=30)), now=now
    )


@router.get("/route-delay")
def route_delay(
    route: str,
    since: str | None = None,
    until: str | None = None,
    db: Session = Depends(get_db),
    _: Principal = Viewer,
):
    s, u = window(since, until, default_days=1)
    return transit.route_delay(db, route_id=route, since=s, until=u)


@router.get("/corridor-flow")
def corridor_flow(
    bbox: str | None = None,
    since: str | None = None,
    until: str | None = None,
    db: Session = Depends(get_db),
    _: Principal = Viewer,
):
    s, u = window(since, until, default_days=7)
    return transit.corridor_flow(db, bbox=bbox_param(bbox), since=s, until=u)
