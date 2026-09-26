"""Assets — the fused truth, and the only defect objects a UI ever sees."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from argus_api.api.deps import bbox_param, get_db, require, time_param
from argus_api.core import audit
from argus_api.core.auth import Principal
from argus_api.db.models import Asset, AssetEvent, AssetNegative, Observation, Ward, WorkOrder
from argus_api.db.types import iso, utcnow
from argus_api.fusion.actions import BAND_ORDER
from argus_api.fusion.engine import FusionEngine
from argus_api.serializers import asset_to_contract, work_order_to_dict

router = APIRouter(prefix="/api/v1/assets", tags=["assets"])

# Candidates and rejected assets never appear on the default view: a single bus's mistake
# stays invisible until a second bus corroborates it.
DEFAULT_STATES = ("confirmed", "reported", "in_progress", "stale")
ALL_STATES = ("candidate", "confirmed", "reported", "in_progress", "resolved", "rejected", "stale")


def _csv(v: str | None) -> list[str] | None:
    return [x.strip() for x in v.split(",") if x.strip()] if v else None


def contracts_for(db: Session, assets: list[Asset]) -> list[dict]:
    if not assets:
        return []
    ids = [a.asset_id for a in assets]
    latest: dict[uuid.UUID, WorkOrder] = {}
    for wo in db.scalars(
        select(WorkOrder).where(WorkOrder.asset_id.in_(ids)).order_by(WorkOrder.created_at)
    ):
        latest[wo.asset_id] = wo
    ward_ids = {wo.ward_id for wo in latest.values() if wo.ward_id}
    names = (
        dict(db.execute(select(Ward.ward_id, Ward.name).where(Ward.ward_id.in_(ward_ids))).all())
        if ward_ids
        else {}
    )
    out = []
    for a in assets:
        wo = latest.get(a.asset_id)
        out.append(asset_to_contract(a, wo, names.get(wo.ward_id) if wo and wo.ward_id else None))
    return out


@router.get("")
def list_assets(
    bbox: str | None = Query(None, description="minLon,minLat,maxLon,maxLat"),
    cls: str | None = Query(None, alias="class", description="comma-separated class ids"),
    state: str | None = Query(None, description="comma-separated states, or 'all'"),
    ward: str | None = None,
    severity: str | None = Query(None, description="minimum band: low|medium|high|critical"),
    since: str | None = Query(None, description="last evidence at or after (RFC3339)"),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: Principal = Depends(require("viewer")),
):
    q = select(Asset)
    states = ALL_STATES if state == "all" else (_csv(state) or DEFAULT_STATES)
    q = q.where(Asset.state.in_(states))
    if (b := bbox_param(bbox)) is not None:
        q = q.where(Asset.lon.between(b[0], b[2]), Asset.lat.between(b[1], b[3]))
    if classes := _csv(cls):
        q = q.where(Asset.class_id.in_(classes))
    if ward:
        q = q.where(Asset.ward_id == ward)
    if severity:
        if severity not in BAND_ORDER:
            raise HTTPException(422, f"severity must be one of {BAND_ORDER}")
        q = q.where(Asset.severity_band.in_(BAND_ORDER[BAND_ORDER.index(severity) :]))
    if (t := time_param(since)) is not None:
        q = q.where(Asset.last_evidence_at >= t)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(Asset.last_evidence_at.desc()).limit(limit).offset(offset)).all()
    return {"total": total, "items": contracts_for(db, list(rows))}


def _get(db: Session, asset_id: uuid.UUID) -> Asset:
    a = db.get(Asset, asset_id)
    if a is None:
        raise HTTPException(404, "asset not found")
    return a


@router.get("/{asset_id}")
def get_asset(
    asset_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: Principal = Depends(require("viewer")),
):
    """Full evidence gallery plus the history that justifies the conclusion — a summary of
    each contributing sighting, never a map pin per observation."""
    a = _get(db, asset_id)
    obs = db.scalars(
        select(Observation)
        .where(Observation.asset_id == asset_id)
        .order_by(Observation.captured_at)
    ).all()
    negs = db.scalars(
        select(AssetNegative).where(AssetNegative.asset_id == asset_id).order_by(AssetNegative.at)
    ).all()
    events = db.scalars(
        select(AssetEvent)
        .where(AssetEvent.asset_id == asset_id)
        .order_by(AssetEvent.at, AssetEvent.id)
    ).all()
    wos = db.scalars(
        select(WorkOrder).where(WorkOrder.asset_id == asset_id).order_by(WorkOrder.created_at)
    ).all()
    return {
        "asset": contracts_for(db, [a])[0],
        "reopen_count": a.reopen_count,
        "observations": [
            {
                "observation_id": str(o.observation_id),
                "captured_at": iso(o.captured_at),
                "device_id": o.device_id,
                "camera_id": o.camera_id,
                "confidence": o.confidence,
                "accuracy_m": o.accuracy_m,
                "severity_score": o.severity_score,
                "area_m2": o.area_m2,
                "range_m": o.range_m,
                "conditions": o.conditions,
                "evidence": o.evidence,
                "model_version": o.model_version,
            }
            for o in obs
        ],
        "negative_evidence": [
            {
                "segment_pass_id": str(n.segment_pass_id),
                "at": iso(n.at),
                "device_id": n.device_id,
                "quality": round(n.quality, 3),
            }
            for n in negs
        ],
        "history": [
            {
                "at": iso(e.at),
                "from": e.from_state,
                "to": e.to_state,
                "reason": e.reason,
                "actor": e.actor,
            }
            for e in events
        ],
        "work_orders": [work_order_to_dict(w) for w in wos],
    }


class RejectBody(BaseModel):
    reason: str | None = Field(None, max_length=2000)


@router.post("/{asset_id}/reject")
def reject_asset(
    asset_id: uuid.UUID,
    body: RejectBody,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("engineer", named=True)),
):
    """Operator feedback: "not real". Writes a hard negative for retraining and lowers the
    reliability of every device that contributed."""
    a = _get(db, asset_id)
    engine = FusionEngine(db)
    engine.reject(a, p.username, body.reason, utcnow())
    audit.record(
        db,
        actor=p.username,
        role=p.role,
        action="asset.reject",
        resource_type="asset",
        resource_id=str(asset_id),
        detail={"reason": body.reason},
    )
    db.commit()
    engine.publish_events()
    return contracts_for(db, [a])[0]
