"""Incidents and evidence access.

Listing is open to viewers with the plate redacted. Anything that reveals a registration mark
or incident footage needs a named engineer and is written to the audit log first (docs/08).
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from argus_api.api.deps import client_of, get_db, require, time_param
from argus_api.core import audit
from argus_api.core.auth import Principal
from argus_api.core.config import get_settings
from argus_api.core.storage import get_store
from argus_api.db.models import EvidenceCheck, Incident, Observation
from argus_api.db.types import utcnow
from argus_api.reports import incident_report
from argus_api.serializers import incident_to_dict

router = APIRouter(tags=["incidents"])


def _get(db: Session, incident_id: uuid.UUID) -> Incident:
    inc = db.get(Incident, incident_id)
    if inc is None:
        raise HTTPException(404, "incident not found")
    return inc


@router.get("/api/v1/incidents")
def list_incidents(
    since: str | None = None,
    until: str | None = None,
    type: str | None = Query(None, description="comma-separated incident types"),
    status: str | None = Query(
        None, description="review status: new|confirmed|dismissed|escalated"
    ),
    limit: int = Query(200, ge=1, le=2000),
    db: Session = Depends(get_db),
    _: Principal = Depends(require("viewer")),
):
    q = select(Incident)
    if (t := time_param(since)) is not None:
        q = q.where(Incident.started_at >= t)
    if (t := time_param(until)) is not None:
        q = q.where(Incident.started_at < t)
    if type:
        q = q.where(Incident.incident_type.in_([x.strip() for x in type.split(",")]))
    if status:
        q = q.where(Incident.review_status == status)
    rows = db.scalars(q.order_by(Incident.started_at.desc()).limit(limit)).all()
    return {"items": [incident_to_dict(i, include_plate=False) for i in rows]}


@router.get("/api/v1/incidents/{incident_id}")
def get_incident(
    incident_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("engineer", named=True)),
):
    inc = _get(db, incident_id)
    audit.record(
        db,
        actor=p.username,
        role=p.role,
        action="incident.view",
        resource_type="incident",
        resource_id=str(incident_id),
        client=client_of(request),
    )
    custody = db.scalars(
        select(EvidenceCheck).where(EvidenceCheck.message_id == str(incident_id))
    ).all()
    return {
        **incident_to_dict(inc, include_plate=True),
        "chain_of_custody": [
            {"uri": c.uri, "kind": c.kind, "status": c.status, "sha256": c.sha256} for c in custody
        ],
    }


@router.get("/api/v1/incidents/{incident_id}/report.pdf")
def incident_report_pdf(
    incident_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("engineer", named=True)),
):
    inc = _get(db, incident_id)
    audit.record(
        db,
        actor=p.username,
        role=p.role,
        action="incident.report",
        resource_type="incident",
        resource_id=str(incident_id),
        client=client_of(request),
    )
    custody = list(
        db.scalars(select(EvidenceCheck).where(EvidenceCheck.message_id == str(incident_id))).all()
    )
    pdf = incident_report.render(inc, custody, generated_for=p.username)
    return Response(
        pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="argus-incident-{incident_id}.pdf"'},
    )


@router.get("/api/v1/incidents/{incident_id}/evidence")
def incident_evidence(
    incident_id: uuid.UUID,
    request: Request,
    item: Literal["clip", "plate"] = "clip",
    db: Session = Depends(get_db),
    p: Principal = Depends(require("engineer", named=True)),
):
    """Redirect to a short-lived signed URL. Logged before the URL is minted."""
    inc = _get(db, incident_id)
    ev = inc.evidence or {}
    uri = ev.get("clip_uri") if item == "clip" else ev.get("plate_crop_uri")
    if not uri:
        raise HTTPException(404, f"no {item} evidence for this incident")
    audit.record(
        db,
        actor=p.username,
        role=p.role,
        action=f"incident.evidence.{item}",
        resource_type="incident",
        resource_id=str(incident_id),
        detail={"uri": uri},
        client=client_of(request),
    )
    db.commit()
    return RedirectResponse(get_store().signed_url(uri, get_settings().signed_url_ttl_s), 307)


class ReviewBody(BaseModel):
    review_status: Literal["confirmed", "dismissed", "escalated"] | None = None
    case_closed: bool | None = None


@router.patch("/api/v1/incidents/{incident_id}")
def review_incident(
    incident_id: uuid.UUID,
    body: ReviewBody,
    db: Session = Depends(get_db),
    p: Principal = Depends(require("engineer", named=True)),
):
    inc = _get(db, incident_id)
    now = utcnow()
    if body.review_status:
        inc.review_status, inc.reviewed_by, inc.reviewed_at = body.review_status, p.username, now
    if body.case_closed is not None:
        # Case closure starts the retention clock on the clip (docs/08 §5).
        inc.case_closed_at = now if body.case_closed else None
    audit.record(
        db,
        actor=p.username,
        role=p.role,
        action="incident.review",
        resource_type="incident",
        resource_id=str(incident_id),
        detail=body.model_dump(exclude_none=True),
    )
    return incident_to_dict(inc, include_plate=False)


@router.get("/api/v1/evidence")
def defect_evidence(
    uri: str,
    db: Session = Depends(get_db),
    _: Principal = Depends(require("viewer")),
):
    """Signed URL for a defect crop. Only URIs that an observation actually references are
    signed, so this is not a general-purpose signing oracle for the bucket."""
    known = db.scalar(
        select(Observation.observation_id).where(Observation.evidence_uri == uri).limit(1)
    )
    if known is None:
        raise HTTPException(404, "unknown evidence")
    return RedirectResponse(get_store().signed_url(uri, get_settings().signed_url_ttl_s), 307)
