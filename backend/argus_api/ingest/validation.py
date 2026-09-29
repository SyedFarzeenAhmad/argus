"""Validate messages against ``contracts/schemas/*.json`` — the source of truth.

The schemas are loaded as-is (no hand-written copy in this folder), so a contract change that
ingest hasn't caught up with fails loudly here rather than being silently mis-parsed.
"""

from __future__ import annotations

import json
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from argus_api.core.config import get_settings
from argus_api.db.types import parse_ts

KINDS = {
    "observation": "observation.schema.json",
    "segment_pass": "segment-pass.schema.json",
    "incident": "incident.schema.json",
    "telemetry": "telemetry.schema.json",
    "asset": "asset.schema.json",
}

ID_FIELD = {
    "observation": "observation_id",
    "segment_pass": "segment_pass_id",
    "incident": "incident_id",
    "telemetry": None,
}


class ContractError(ValueError):
    pass


_formats = FormatChecker()


@_formats.checks("uuid", raises=ValueError)
def _is_uuid(value: object) -> bool:
    if isinstance(value, str):
        uuid.UUID(value)
    return True


@_formats.checks("date-time", raises=ValueError)
def _is_datetime(value: object) -> bool:
    if isinstance(value, str):
        if "T" not in value and "t" not in value:
            raise ValueError("date-time needs a T separator")
        parse_ts(value)
    return True


@lru_cache
def _validators(contracts_dir: str) -> dict[str, Draft202012Validator]:
    root = Path(contracts_dir)
    if not root.is_dir():
        raise RuntimeError(f"contracts directory not found: {root} (set ARGUS_CONTRACTS_DIR)")
    resources = []
    docs: dict[str, dict] = {}
    for path in root.glob("*.schema.json"):
        doc = json.loads(path.read_text(encoding="utf-8"))
        docs[path.name] = doc
        resources.append((doc["$id"], Resource.from_contents(doc)))
    registry = Registry().with_resources(resources)
    return {
        kind: Draft202012Validator(docs[fname], registry=registry, format_checker=_formats)
        for kind, fname in KINDS.items()
        if fname in docs
    }


def validator(kind: str) -> Draft202012Validator:
    v = _validators(str(get_settings().contracts_dir)).get(kind)
    if v is None:
        raise ContractError(f"no schema for message kind {kind!r}")
    return v


def check_version(msg: dict[str, Any]) -> None:
    version = msg.get("schema_version")
    if not isinstance(version, str) or version.count(".") != 2:
        raise ContractError("missing or malformed schema_version")
    major = version.split(".", 1)[0]
    supported = get_settings().supported_schema_major
    if not major.isdigit() or int(major) != supported:
        # Silent mis-parsing is worse than a hard failure (docs/06 rule 6).
        raise ContractError(f"unsupported schema major {major} (this ingest speaks {supported}.x)")


def validate(kind: str, msg: Any) -> None:
    if not isinstance(msg, dict):
        raise ContractError("message is not a JSON object")
    check_version(msg)
    errors = sorted(validator(kind).iter_errors(msg), key=lambda e: list(e.absolute_path))
    if errors:
        first = errors[0]
        where = "/".join(str(p) for p in first.absolute_path) or "(root)"
        more = f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""
        raise ContractError(f"{where}: {first.message}{more}")
