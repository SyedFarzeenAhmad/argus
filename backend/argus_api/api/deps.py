"""FastAPI dependencies: database session, caller identity, role gates, time parsing."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from argus_api.core.auth import AuthError, Principal, decode_token
from argus_api.core.config import get_settings
from argus_api.core.geo import parse_bbox
from argus_api.db import session as db_session
from argus_api.db.models import AppUser
from argus_api.db.types import parse_ts, utcnow

oauth2 = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=False)


def get_db() -> Iterator[Session]:
    s = db_session.get_sessionmaker()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def principal_from_token(token: str | None, db: Session) -> Principal:
    if token:
        try:
            p = decode_token(token)
        except AuthError as exc:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "invalid token",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc
        user = db.get(AppUser, p.username)
        if user is not None and not user.active:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "account disabled")
        return p
    anon = get_settings().anonymous_role
    if anon:
        return Principal("anonymous", anon)
    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_principal(token: str | None = Depends(oauth2), db: Session = Depends(get_db)) -> Principal:
    return principal_from_token(token, db)


def require(role: str, *, named: bool = False) -> Callable[..., Principal]:
    """Role gate. ``named=True`` additionally refuses the anonymous kiosk identity, for
    every endpoint whose access must be audited against a real operator."""

    def dep(p: Principal = Depends(get_principal)) -> Principal:
        if not p.has(role):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"requires role {role}")
        if named and not p.named:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "this access is audited and needs a named operator"
            )
        return p

    return dep


def client_of(request: Request) -> str | None:
    return request.client.host if request.client else None


def bbox_param(bbox: str | None) -> tuple[float, float, float, float] | None:
    try:
        return parse_bbox(bbox)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def time_param(value: str | None, default: datetime | None = None) -> datetime | None:
    if value is None:
        return default
    try:
        return parse_ts(value)
    except ValueError as exc:
        raise HTTPException(422, f"not an RFC3339 timestamp: {value}") from exc


def window(since: str | None, until: str | None, default_days: float) -> tuple[datetime, datetime]:
    end = time_param(until, utcnow())
    start = time_param(since, end - timedelta(days=default_days))
    if start >= end:
        raise HTTPException(422, "since must precede until")
    return start, end
