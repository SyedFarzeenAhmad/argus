"""Live fleet, reference geography and the realtime socket."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import Session

from argus_api.analytics import transit
from argus_api.analytics.common import segments_in_bbox
from argus_api.api.deps import bbox_param, get_db, principal_from_token, require
from argus_api.core.auth import Principal
from argus_api.db import session as db_session
from argus_api.db.models import Ward
from argus_api.db.types import utcnow
from argus_api.realtime.bus import EVENT_TYPES, get_bus

router = APIRouter(tags=["fleet"])


@router.get("/api/v1/fleet/live")
def fleet_live(
    max_age_s: int = Query(300, ge=5, le=3600),
    db: Session = Depends(get_db),
    _: Principal = Depends(require("viewer")),
):
    """Current positions and health. A dirty lens shows up here, not as a crash."""
    return transit.fleet_live(db, now=utcnow(), max_age_s=max_age_s)


@router.get("/api/v1/fleet/summary")
def fleet_summary(db: Session = Depends(get_db), _: Principal = Depends(require("viewer"))):
    return transit.fleet_summary(db, now=utcnow())


@router.get("/api/v1/geo/wards")
def wards_geojson(db: Session = Depends(get_db), _: Principal = Depends(require("viewer"))):
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": w.ward_id,
                "geometry": w.geometry,
                "properties": {"ward_id": w.ward_id, "name": w.name, "population": w.population},
            }
            for w in db.scalars(select(Ward))
        ],
    }


@router.get("/api/v1/geo/segments")
def segments_geojson(
    bbox: str | None = None,
    db: Session = Depends(get_db),
    _: Principal = Depends(require("viewer")),
):
    b = bbox_param(bbox)
    segs = segments_in_bbox(db, b)
    if b is None and len(segs) > 50_000:
        raise HTTPException(422, "too many segments; pass a bbox")
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": s.segment_id,
                "geometry": {"type": "LineString", "coordinates": s.coordinates},
                "properties": {
                    "segment_id": s.segment_id,
                    "name": s.name,
                    "highway": s.highway,
                    "oneway": s.oneway,
                    "length_m": s.length_m,
                    "ward_id": s.ward_id,
                },
            }
            for s in segs.values()
        ],
    }


@router.websocket("/ws/live")
async def live(ws: WebSocket, token: str | None = None, types: str | None = None):
    """Event stream: ``asset.created | asset.updated | asset.resolved | incident | telemetry``.

    Browsers cannot set headers on a WebSocket, so the JWT rides in ``?token=``. ``types`` is
    an optional comma-separated filter (a prefix like ``asset`` matches all asset events).
    """
    try:
        with db_session.get_sessionmaker()() as db:
            principal_from_token(token, db)
    except HTTPException:
        await ws.close(code=4401)
        return
    wanted = {t.strip() for t in types.split(",")} if types else None
    await ws.accept()
    await ws.send_json({"type": "hello", "data": {"events": sorted(EVENT_TYPES)}})

    async def pump() -> None:
        async for msg in get_bus().subscribe():
            t = msg.get("type", "")
            if wanted and t not in wanted and t.split(".")[0] not in wanted:
                continue
            await ws.send_json(msg)

    task = asyncio.create_task(pump())
    try:
        while True:  # we only read to notice the disconnect; clients may send pings
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        task.cancel()
