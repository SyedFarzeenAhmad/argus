"""Audit log. Every incident-evidence access and every ANPR read lands here (docs/08 §3, §6).

A platform that *could* read every plate it passes and is configured not to should be able to
prove it didn't.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from argus_api.db.models import AuditLog
from argus_api.db.types import utcnow


def record(
    session: Session,
    *,
    actor: str,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    role: str | None = None,
    detail: dict[str, Any] | None = None,
    client: str | None = None,
) -> None:
    session.add(
        AuditLog(
            at=utcnow(),
            actor=actor,
            role=role,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            detail=detail,
            client=client,
        )
    )
