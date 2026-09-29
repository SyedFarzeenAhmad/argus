"""Work orders — the difference between a dashboard and a tool someone uses on a Tuesday."""

from __future__ import annotations

import csv
import io
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from argus_api.api.deps import client_of, get_db, require
from argus_api.core import audit
from argus_api.core.auth import Principal
from argus_api.db.models import Asset, WorkOrder
from argus_api.db.types import utcnow
from argus_api.fusion.engine import FusionEngine
from argus_api.fusion.work_orders import OPEN_STATUSES
from argus_api.serializers import work_order_to_dict

router = APIRouter(prefix="/api/v1/work-orders", tags=["work-orders"])

CSV_FIELDS = [
    "reference",
    "ward_id",
    "class_id",
    "recommended_action",
    "department",
    "severity_band",
    "priority_rank",
    "status",
    "issued_at",
    "sla_due_at",
    "sla_breached",
    "lat",
    "lon",
    "accuracy_m",
    "confidence",
    "distinct_devices",
    "canonical_evidence_uri",
    "closed_at",
    "closed_by",
    "asset_id",
]


def _query(db: Session, ward: str | None, state: str | None, limit: int):
    q = select(WorkOrder, Asset).join(Asset, Asset.asset_id == WorkOrder.asset_id)
    if ward:
        q = q.where(WorkOrder.ward_id == ward)
    if state == "open" or state is None:
        q = q.where(WorkOrder.status.in_(OPEN_STATUSES))
    elif state != "all":
        q = q.where(WorkOrder.status.in_([s.strip() for s in state.split(",")]))
    q = q.order_by(WorkOrder.ward_id, WorkOrder.priority_rank, WorkOrder.created_at).limit(limit)
    now = utcnow()
    out = []
    for wo, asset in db.execute(q):
        d = work_order_to_dict(wo, asset)
        d["sla_breached"] = wo.status in OPEN_STATUSES and wo.sla_due_at < now
        out.append(d)
    return out


@router.get("")
def list_work_orders(
    ward: str | None = None,
    state: str | None = Query(None, description="open (default) | all | comma-separated statuses"),
    limit: int = Query(1000, ge=1, le=10000),
    db: Session = Depends(get_db),
    _: Principal = Depends(require("viewer")),
):
    items = _query(db, ward, state, limit)
    return {"total": len(items), "items": items}


@router.get("/export.csv")
def export_csv(
    request: Request,
    ward: str | None = None,
    state: str | None = None,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("engineer", named=True)),
):
    """CSV for a works department. Defect evidence only — never incident evidence."""
    items = _query(db, ward, state, 100_000)
    audit.record(
        db,
        actor=p.username,
        role=p.role,
        action="work_orders.export",
        resource_type="work_order",
        detail={"ward": ward, "state": state, "rows": len(items)},
        client=client_of(request),
    )
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore")
    w.writeheader()
    w.writerows(items)
    name = f"argus-work-orders-{(ward or 'all').replace(':', '-')}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


class StatusBody(BaseModel):
    status: Literal["issued", "in_progress", "completed"]


@router.post("/{reference}/status")
def set_status(
    reference: str,
    body: StatusBody,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("engineer", named=True)),
):
    """Engineer progress. ``completed`` means *awaiting fleet verification*: only the buses
    can close a work order as fixed."""
    wo = db.get(WorkOrder, reference)
    if wo is None:
        raise HTTPException(404, "work order not found")
    if wo.status not in OPEN_STATUSES:
        raise HTTPException(409, f"work order is {wo.status}")
    asset = db.get(Asset, wo.asset_id)
    engine = FusionEngine(db)
    try:
        engine.set_work_order_status(asset, body.status, p.username, utcnow())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    audit.record(
        db,
        actor=p.username,
        role=p.role,
        action="work_order.status",
        resource_type="work_order",
        resource_id=reference,
        detail={"status": body.status},
    )
    db.commit()
    engine.publish_events()
    return work_order_to_dict(wo, asset)
