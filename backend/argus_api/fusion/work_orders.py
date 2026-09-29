"""Work orders: the municipal hand-off (docs/04 "Work orders").

Priority within a ward is severity × confidence × exposure, where exposure is how many fleet
passes the segment sees per day — a proxy, measured for free, for how many road users meet
the defect. Two identical potholes are not equally urgent if one is on a corridor 40 buses
use per hour and the other on a lane used by two.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from argus_api.db.models import Asset, SegmentPass, WorkOrder
from argus_api.fusion.actions import SLA_BY_BAND, policy

OPEN_STATUSES = ("draft", "issued", "in_progress", "awaiting_verification")
FLEET_VERIFIED = "auto:fleet-verified"


def open_work_order(session: Session, asset_id) -> WorkOrder | None:
    return session.scalars(
        select(WorkOrder)
        .where(WorkOrder.asset_id == asset_id, WorkOrder.status.in_(OPEN_STATUSES))
        .order_by(WorkOrder.created_at.desc())
    ).first()


def latest_work_order(session: Session, asset_id) -> WorkOrder | None:
    return session.scalars(
        select(WorkOrder)
        .where(WorkOrder.asset_id == asset_id)
        .order_by(WorkOrder.created_at.desc())
    ).first()


def exposure_per_day(session: Session, segment_id: str | None, at: datetime) -> float:
    if not segment_id:
        return 0.0
    n = session.scalar(
        select(func.count())
        .select_from(SegmentPass)
        .where(
            SegmentPass.segment_id == segment_id,
            SegmentPass.entered_at > at - timedelta(days=7),
            SegmentPass.entered_at <= at,
        )
    )
    return (n or 0) / 7.0


def priority_score(session: Session, asset: Asset, at: datetime) -> float:
    return (
        (asset.severity_score or 0.5)
        * asset.confidence
        * (1.0 + exposure_per_day(session, asset.segment_id, at))
    )


def _next_reference(session: Session, ward_id: str | None) -> str:
    prefix = "ARG-" + (ward_id or "unassigned").upper().replace(":", "-").replace("/", "-")
    n = session.scalar(
        select(func.count()).select_from(WorkOrder).where(WorkOrder.reference.like(f"{prefix}-%"))
    )
    return f"{prefix}-{(n or 0) + 1:06d}"


def rerank_ward(session: Session, ward_id: str | None) -> None:
    session.flush()
    q = select(WorkOrder).where(WorkOrder.status.in_(OPEN_STATUSES))
    q = q.where(WorkOrder.ward_id.is_(None) if ward_id is None else WorkOrder.ward_id == ward_id)
    orders = session.scalars(q).all()
    orders.sort(key=lambda w: (-w.priority_score, w.created_at))
    for rank, wo in enumerate(orders, start=1):
        wo.priority_rank = rank


def create_work_order(session: Session, asset: Asset, at: datetime, auto_issue: bool) -> WorkOrder:
    p = policy(asset.class_id)
    band = asset.severity_band or "medium"
    wo = WorkOrder(
        reference=_next_reference(session, asset.ward_id),
        asset_id=asset.asset_id,
        ward_id=asset.ward_id,
        class_id=asset.class_id,
        action=p.action,
        department=p.department,
        severity_band=band,
        priority_score=priority_score(session, asset, at),
        status="issued" if auto_issue else "draft",
        created_at=at,
        issued_at=at if auto_issue else None,
        sla_due_at=at + SLA_BY_BAND.get(band, SLA_BY_BAND["medium"]),
    )
    session.add(wo)
    rerank_ward(session, asset.ward_id)
    return wo


def close_work_order(session: Session, wo: WorkOrder, closed_by: str, at: datetime) -> None:
    wo.status = "closed"
    wo.closed_at = at
    wo.closed_by = closed_by
    rerank_ward(session, wo.ward_id)
