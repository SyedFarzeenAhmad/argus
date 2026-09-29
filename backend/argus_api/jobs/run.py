"""Maintenance jobs. Run from cron or a scheduler:

    uv run python -m argus_api.jobs.run retention [--dry-run]
    uv run python -m argus_api.jobs.run verify-evidence
    uv run python -m argus_api.jobs.run freeflow
    uv run python -m argus_api.jobs.run stale

On PostgreSQL, raw-row retention is also enforced by TimescaleDB retention policies (migration
0001); this job covers what those cannot: evidence objects in the store, incident clips whose
clock starts at case closure, and non-Timescale databases.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta

import structlog
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from argus_api.core.config import get_settings
from argus_api.core.logging import configure_logging
from argus_api.core.storage import ObjectMissing, StoreUnavailable, get_store
from argus_api.db import session as db_session
from argus_api.db.models import (
    EvidenceCheck,
    Incident,
    Observation,
    SegmentPass,
    Telemetry,
)
from argus_api.db.types import utcnow

log = structlog.get_logger()


def retention(session: Session, now: datetime, dry_run: bool = False) -> dict[str, int]:
    """Tiered retention (docs/08 §5). Everything that could identify a person expires; the
    aggregates, which contain no personal data, are untouched."""
    cfg = get_settings()
    store = get_store()
    out: dict[str, int] = {}

    # Evidence crops: 90 days. The observation row stays (it is immutable and holds no
    # imagery); the object is deleted and its custody record marked expired.
    crop_cutoff = now - timedelta(days=cfg.retain_evidence_days)
    crops = session.scalars(
        select(EvidenceCheck).where(
            EvidenceCheck.kind == "observation",
            EvidenceCheck.status != "expired",
            EvidenceCheck.created_at < crop_cutoff,
        )
    ).all()
    # Incident clips: one year, or case closure — whichever is later for an open case.
    clip_cutoff = now - timedelta(days=cfg.retain_incident_days)
    expired_incidents = session.scalars(
        select(Incident).where(
            Incident.started_at < clip_cutoff,
            or_(Incident.case_closed_at.is_(None), Incident.case_closed_at < clip_cutoff),
            Incident.review_status != "escalated",
        )
    ).all()
    clip_uris = set()
    for inc in expired_incidents:
        ev = inc.evidence or {}
        clip_uris |= {u for u in (ev.get("clip_uri"), ev.get("plate_crop_uri")) if u}
    clips = (
        session.scalars(
            select(EvidenceCheck).where(
                EvidenceCheck.uri.in_(clip_uris), EvidenceCheck.status != "expired"
            )
        ).all()
        if clip_uris
        else []
    )

    out["evidence_objects"] = len(crops) + len(clip_uris)
    if not dry_run:
        for uri in [c.uri for c in crops] + sorted(clip_uris):
            try:
                store.delete(uri)
            except Exception as exc:  # keep going; the next run retries
                log.warning("retention.delete_failed", uri=uri, error=str(exc))
                continue
        for c in [*crops, *clips]:
            c.status = "expired"
            c.checked_at = now
        for inc in expired_incidents:
            # Keep the record (kinematics, type, place); drop the pointer to the footage and
            # the plate, which is the personal data.
            sv = dict(inc.subject_vehicle or {})
            sv.pop("plate", None)
            inc.subject_vehicle = sv or None
            inc.evidence = {"expired_at": now.isoformat()}
    out["incidents_redacted"] = len(expired_incidents)

    for model, col, days, key in (
        (Observation, Observation.captured_at, cfg.retain_observation_days, "observations"),
        (SegmentPass, SegmentPass.entered_at, cfg.retain_segment_pass_days, "segment_passes"),
        (Telemetry, Telemetry.at, cfg.retain_telemetry_days, "telemetry"),
    ):
        cutoff = now - timedelta(days=days)
        if dry_run:
            out[key] = session.scalar(select(func.count()).select_from(model).where(col < cutoff))
        else:
            out[key] = session.execute(delete(model).where(col < cutoff)).rowcount or 0
    return out


def verify_pending(session: Session, now: datetime, max_attempts: int = 48) -> dict[str, int]:
    """Re-check evidence that was not in the store at ingest time (incident clips arrive over
    depot wifi overnight). A mismatch found now is as serious as one found at ingest."""
    from argus_api.db.models import Device

    store = get_store()
    stats = {"verified": 0, "mismatch": 0, "still_pending": 0}
    for c in session.scalars(
        select(EvidenceCheck).where(
            EvidenceCheck.status == "pending", EvidenceCheck.attempts < max_attempts
        )
    ):
        c.attempts += 1
        c.checked_at = now
        try:
            actual = store.sha256(c.uri)
        except (ObjectMissing, StoreUnavailable, ValueError):
            stats["still_pending"] += 1
            continue
        if actual == c.sha256:
            c.status = "verified"
            stats["verified"] += 1
        else:
            c.status = "mismatch"
            stats["mismatch"] += 1
            dev = session.get(Device, c.device_id)
            if dev is not None:
                dev.flagged, dev.flag_reason, dev.flagged_at = True, f"hash mismatch {c.uri}", now
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description="ARGUS maintenance jobs")
    ap.add_argument("job", choices=["retention", "verify-evidence", "freeflow", "stale"])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    configure_logging()
    now = utcnow()
    with db_session.session_scope() as s:
        if args.job == "retention":
            result = retention(s, now, dry_run=args.dry_run)
        elif args.job == "verify-evidence":
            result = verify_pending(s, now)
        elif args.job == "freeflow":
            from argus_api.analytics.freeflow import relearn_all

            result = {"segments": relearn_all(s, now)}
        else:
            from argus_api.fusion.engine import FusionEngine

            engine = FusionEngine(s)
            result = {"stale": engine.sweep_stale(now)}
            s.commit()
            engine.publish_events()
        if args.dry_run:
            s.rollback()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
