"""Auth, administration and the HTTP ingest path."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from argus_api.api.deps import get_db, require, time_param
from argus_api.core import audit
from argus_api.core.auth import ROLES, Principal, hash_password, issue_token, verify_password
from argus_api.core.config import get_settings
from argus_api.db.models import AppUser, AuditLog, Device, HardNegative, IngestRejection
from argus_api.db.types import iso, utcnow
from argus_api.ingest.pipeline import Ingestor

router = APIRouter(tags=["admin"])

# ─── Auth ────────────────────────────────────────────────────────────────────


@router.post("/api/v1/auth/token")
def token(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.get(AppUser, form.username)
    if user is None or not user.active or not verify_password(form.password, user.password_hash):
        raise HTTPException(401, "invalid credentials", headers={"WWW-Authenticate": "Bearer"})
    return {
        "access_token": issue_token(user.username, user.role),
        "token_type": "bearer",
        "role": user.role,
    }


@router.get("/api/v1/auth/me")
def me(p: Principal = Depends(require("viewer"))):
    return {"username": p.username, "role": p.role}


class UserBody(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8)
    role: Literal["viewer", "engineer", "admin"]


@router.post("/api/v1/admin/users", status_code=201)
def create_user(
    body: UserBody,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("admin", named=True)),
):
    if db.get(AppUser, body.username) is not None:
        raise HTTPException(409, "user exists")
    db.add(
        AppUser(username=body.username, password_hash=hash_password(body.password), role=body.role)
    )
    audit.record(
        db,
        actor=p.username,
        role=p.role,
        action="user.create",
        resource_type="user",
        resource_id=body.username,
        detail={"role": body.role},
    )
    return {"username": body.username, "role": body.role}


# ─── Devices ─────────────────────────────────────────────────────────────────


def _device(d: Device) -> dict[str, Any]:
    return {
        "device_id": d.device_id,
        "bus_id": d.bus_id,
        "first_seen_at": iso(d.first_seen_at),
        "last_seen_at": iso(d.last_seen_at),
        "reliability": round(d.reliability, 4),
        "confirmed_contributions": d.confirmed_contributions,
        "rejected_contributions": d.rejected_contributions,
        "flagged": d.flagged,
        "flag_reason": d.flag_reason,
        "flagged_at": iso(d.flagged_at),
        "revoked": d.revoked,
    }


@router.get("/api/v1/admin/devices")
def devices(
    flagged: bool | None = None,
    db: Session = Depends(get_db),
    _: Principal = Depends(require("admin")),
):
    q = select(Device).order_by(Device.device_id)
    if flagged is not None:
        q = q.where(Device.flagged.is_(flagged))
    return {"items": [_device(d) for d in db.scalars(q)]}


class DeviceAction(BaseModel):
    action: Literal["flag", "unflag", "revoke", "reinstate"]
    reason: str | None = None


@router.post("/api/v1/admin/devices/{device_id}")
def device_action(
    device_id: str,
    body: DeviceAction,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("admin", named=True)),
):
    d = db.get(Device, device_id)
    if d is None:
        raise HTTPException(404, "device not found")
    if body.action == "flag":
        d.flagged, d.flag_reason, d.flagged_at = True, body.reason, utcnow()
    elif body.action == "unflag":
        d.flagged, d.flag_reason = False, None
    elif body.action == "revoke":
        # Mirrors certificate revocation at the broker; ingest refuses this device from now.
        d.revoked = True
    else:
        d.revoked = False
    audit.record(
        db,
        actor=p.username,
        role=p.role,
        action=f"device.{body.action}",
        resource_type="device",
        resource_id=device_id,
        detail={"reason": body.reason},
    )
    return _device(d)


# ─── Oversight ───────────────────────────────────────────────────────────────


@router.get("/api/v1/admin/audit")
def audit_log(
    actor: str | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    since: str | None = None,
    limit: int = Query(500, ge=1, le=10000),
    db: Session = Depends(get_db),
    _: Principal = Depends(require("admin")),
):
    q = select(AuditLog)
    for col, v in (
        (AuditLog.actor, actor),
        (AuditLog.action, action),
        (AuditLog.resource_type, resource_type),
        (AuditLog.resource_id, resource_id),
    ):
        if v:
            q = q.where(col == v)
    if (t := time_param(since)) is not None:
        q = q.where(AuditLog.at >= t)
    rows = db.scalars(q.order_by(AuditLog.at.desc()).limit(limit))
    return {
        "items": [
            {
                "at": iso(r.at),
                "actor": r.actor,
                "role": r.role,
                "action": r.action,
                "resource_type": r.resource_type,
                "resource_id": r.resource_id,
                "detail": r.detail,
                "client": r.client,
            }
            for r in rows
        ]
    }


@router.get("/api/v1/admin/rejections")
def rejections(
    device_id: str | None = None,
    limit: int = Query(200, ge=1, le=5000),
    db: Session = Depends(get_db),
    _: Principal = Depends(require("admin")),
):
    q = select(IngestRejection)
    if device_id:
        q = q.where(IngestRejection.device_id == device_id)
    rows = db.scalars(q.order_by(IngestRejection.received_at.desc()).limit(limit))
    return {
        "items": [
            {
                "received_at": iso(r.received_at),
                "kind": r.kind,
                "device_id": r.device_id,
                "topic": r.topic,
                "message_id": r.message_id,
                "reason": r.reason,
            }
            for r in rows
        ]
    }


@router.get("/api/v1/admin/hard-negatives")
def hard_negatives(
    unexported: bool = True,
    mark_exported: bool = False,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("admin", named=True)),
):
    """Operator-rejected evidence for the retraining set: the model improves from use."""
    q = select(HardNegative).order_by(HardNegative.at)
    if unexported:
        q = q.where(HardNegative.exported_at.is_(None))
    rows = list(db.scalars(q))
    if mark_exported:
        now = utcnow()
        for r in rows:
            r.exported_at = now
    audit.record(
        db,
        actor=p.username,
        role=p.role,
        action="hard_negatives.export",
        resource_type="hard_negative",
        detail={"rows": len(rows)},
    )
    return {
        "items": [
            {
                "asset_id": str(r.asset_id),
                "class_id": r.class_id,
                "observation_ids": r.observation_ids,
                "evidence": r.evidence,
                "rejected_by": r.rejected_by,
                "reason": r.reason,
                "at": iso(r.at),
            }
            for r in rows
        ]
    }


@router.get("/api/v1/admin/config")
def config(_: Principal = Depends(require("admin"))):
    s = get_settings()
    keys = [
        "cluster_eps_m",
        "correlation_discount",
        "max_observation_probability",
        "negative_evidence_probability",
        "position_sigma_floor_m",
        "confirm_distinct_devices",
        "confirm_probability",
        "resolve_negative_passes",
        "resolve_distinct_devices",
        "resolve_evidence_nats",
        "detector_recall",
        "stale_after_days",
        "min_assessable_fraction",
        "max_negative_occlusion",
        "ledger_min_absences",
        "ledger_min_devices",
        "reopen_window_days",
        "evidence_verification",
        "retain_evidence_days",
        "retain_incident_days",
        "retain_observation_days",
        "retain_segment_pass_days",
        "retain_telemetry_days",
        "assumed_bus_occupancy",
    ]
    return {"roles": list(ROLES), **{k: getattr(s, k) for k in keys}}


# ─── HTTP ingest ─────────────────────────────────────────────────────────────


@router.post("/api/v1/ingest/{kind}")
def ingest_http(
    kind: Literal["observation", "segment_pass", "incident", "telemetry"],
    body: Any = Body(...),
    p: Principal = Depends(require("admin", named=True)),
):
    """The same pipeline as MQTT, over HTTP, for replaying logs and for integration tests.
    Accepts one message or a list. Fleet devices use MQTT with their certificates."""
    msgs = body if isinstance(body, list) else [body]
    if len(msgs) > 5000:
        raise HTTPException(413, "at most 5000 messages per request")
    ing = Ingestor()
    results = [ing.handle(kind, m) for m in msgs]
    return {
        "stored": sum(r.status == "stored" for r in results),
        "duplicate": sum(r.status == "duplicate" for r in results),
        "rejected": [
            {"message_id": r.message_id, "reason": r.reason}
            for r in results
            if r.status == "rejected"
        ],
    }
