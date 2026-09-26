"""Operator identity: JWT with three roles (docs/04 "API surface", docs/08 §6).

    viewer    read
    engineer  work orders, reject, incident evidence (audited)
    admin     devices, thresholds, audit log, bulk export

Device identity is a different thing entirely — an X.509 client certificate at the MQTT
broker — and never passes through here.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from jose import JWTError, jwt

from argus_api.core.config import get_settings

ROLES = ("viewer", "engineer", "admin")
_PBKDF2_ROUNDS = 390_000


@dataclass(frozen=True)
class Principal:
    username: str
    role: str

    @property
    def named(self) -> bool:
        return self.username != "anonymous"

    def has(self, role: str) -> bool:
        return ROLES.index(self.role) >= ROLES.index(role)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    salt_b64, dk_b64 = base64.b64encode(salt).decode(), base64.b64encode(dk).decode()
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt_b64}${dk_b64}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, rounds, salt_b64, dk_b64 = encoded.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt_b64), int(rounds))
    return hmac.compare_digest(dk, base64.b64decode(dk_b64))


def issue_token(username: str, role: str, ttl: timedelta | None = None) -> str:
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}")
    s = get_settings()
    now = datetime.now(UTC)
    claims = {
        "sub": username,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + (ttl or timedelta(minutes=s.jwt_ttl_minutes))).timestamp()),
    }
    return jwt.encode(claims, s.jwt_secret, algorithm=s.jwt_algorithm)


class AuthError(Exception):
    pass


def decode_token(token: str) -> Principal:
    s = get_settings()
    try:
        claims = jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
    except JWTError as exc:
        raise AuthError(str(exc)) from exc
    role, sub = claims.get("role"), claims.get("sub")
    if role not in ROLES or not sub:
        raise AuthError("malformed token")
    return Principal(username=sub, role=role)
