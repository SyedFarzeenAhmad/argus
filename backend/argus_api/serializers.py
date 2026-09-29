"""DB rows → contract-shaped dicts.

``asset_to_contract`` produces exactly ``contracts/schemas/asset.schema.json`` (which has
``additionalProperties: false``), and the test suite validates its output against that schema
so the frontend's generated types never drift from what the API actually sends.
"""

from __future__ import annotations

from typing import Any

from argus_api.db.models import Asset, Incident, Telemetry, WorkOrder
from argus_api.db.types import iso
from argus_api.fusion.actions import policy

SCHEMA_VERSION = "1.0.0"


def _clean(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


def asset_to_contract(
    asset: Asset, work_order: WorkOrder | None = None, ward_name: str | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "asset_id": str(asset.asset_id),
        "class_id": asset.class_id,
        "state": asset.state,
        "geo": {
            "lat": round(asset.lat, 7),
            "lon": round(asset.lon, 7),
            "accuracy_m": round(asset.accuracy_m, 2),
            "source": asset.geo_source,
        },
        "confidence": round(min(max(asset.confidence, 0.0), 1.0), 4),
        "evidence_count": max(1, asset.evidence_count),
        "distinct_devices": max(1, asset.distinct_devices),
        "negative_passes": asset.negative_passes,
        "severity": _clean(
            {
                "score": round(min(max(asset.severity_score or 0.0, 0.0), 1.0), 3),
                "band": asset.severity_band or "medium",
                "trend": asset.severity_trend or "unknown",
            }
        ),
        "first_seen_at": iso(asset.first_seen_at),
        "last_evidence_at": iso(asset.last_evidence_at),
        "confirmed_at": iso(asset.confirmed_at),
        "resolved_at": iso(asset.resolved_at),
    }
    if asset.segment_id:
        out["map_match"] = _clean(
            {
                "segment_id": asset.segment_id,
                "offset_m": round(max(asset.offset_m or 0.0, 0.0), 1),
                "ward_id": asset.ward_id,
                "confidence": round(asset.map_match_confidence or 0.0, 3),
            }
        )
    if asset.physical:
        out["physical"] = asset.physical
    if asset.canonical_evidence:
        out["canonical_evidence"] = asset.canonical_evidence
    if asset.evidence_gallery:
        out["evidence_gallery"] = asset.evidence_gallery
    if asset.ledger:
        out["ledger"] = asset.ledger
    if work_order is not None:
        out["work_order"] = _clean(
            {
                "reference": work_order.reference,
                "ward_id": work_order.ward_id,
                "ward_name": ward_name,
                "recommended_action": work_order.action or policy(asset.class_id).action,
                "priority_rank": max(1, work_order.priority_rank),
                "issued_at": iso(work_order.issued_at),
                "sla_due_at": iso(work_order.sla_due_at),
            }
        )
    return _clean(out)


def work_order_to_dict(wo: WorkOrder, asset: Asset | None = None) -> dict[str, Any]:
    d = {
        "reference": wo.reference,
        "asset_id": str(wo.asset_id),
        "ward_id": wo.ward_id,
        "class_id": wo.class_id,
        "recommended_action": wo.action,
        "department": wo.department,
        "severity_band": wo.severity_band,
        "priority_rank": wo.priority_rank,
        "priority_score": round(wo.priority_score, 4),
        "status": wo.status,
        "created_at": iso(wo.created_at),
        "issued_at": iso(wo.issued_at),
        "sla_due_at": iso(wo.sla_due_at),
        "closed_at": iso(wo.closed_at),
        "closed_by": wo.closed_by,
    }
    if asset is not None:
        d.update(
            lat=round(asset.lat, 7),
            lon=round(asset.lon, 7),
            accuracy_m=round(asset.accuracy_m, 2),
            asset_state=asset.state,
            confidence=round(asset.confidence, 4),
            distinct_devices=asset.distinct_devices,
            canonical_evidence_uri=(asset.canonical_evidence or {}).get("uri"),
        )
    return d


def incident_to_dict(inc: Incident, *, include_plate: bool) -> dict[str, Any]:
    """Incident summary. The plate (personal data) is included only for audited callers."""
    subject = dict(inc.subject_vehicle or {})
    if not include_plate and "plate" in subject:
        plate = subject.pop("plate") or {}
        subject["plate_read"] = bool(plate.get("text"))
    evidence = dict(inc.evidence or {})
    if not include_plate:
        evidence = {k: v for k, v in evidence.items() if k in ("duration_s", "cameras")}
    return _clean(
        {
            "incident_id": str(inc.incident_id),
            "incident_type": inc.incident_type,
            "device_id": inc.device_id,
            "bus_id": inc.bus_id,
            "route_id": inc.route_id,
            "started_at": iso(inc.started_at),
            "ended_at": iso(inc.ended_at),
            "confidence": inc.confidence,
            "severity": inc.severity,
            "geo": {"lat": inc.lat, "lon": inc.lon, "accuracy_m": inc.accuracy_m},
            "segment_id": inc.segment_id,
            "ward_id": inc.ward_id,
            "ego": inc.ego,
            "triggers": inc.triggers,
            "subject_vehicle": subject or None,
            "evidence": evidence or None,
            "review_status": inc.review_status,
            "reviewed_by": inc.reviewed_by,
            "reviewed_at": iso(inc.reviewed_at),
        }
    )


def telemetry_to_live(t: Telemetry) -> dict[str, Any]:
    return _clean(
        {
            "device_id": t.device_id,
            "bus_id": t.bus_id,
            "route_id": t.route_id,
            "at": iso(t.at),
            "lat": t.lat,
            "lon": t.lon,
            "speed_kmh": t.speed_kmh,
            "heading": t.heading,
            "delay_s": t.delay_s,
            "queue_depth": (t.health or {}).get("queue_depth"),
        }
    )
