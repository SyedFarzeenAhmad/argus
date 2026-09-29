from __future__ import annotations

import json

from sqlalchemy import func, select

from argus_api.db.models import (
    AuditLog,
    Device,
    EvidenceCheck,
    Incident,
    IngestRejection,
    Observation,
    SegmentPass,
    Telemetry,
)
from tests import factories as f


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def test_stores_valid_observation_and_wakes_fusion(ingestor, session, bus):
    r = ingestor.handle(
        "observation", json.dumps(f.observation()).encode(), topic_device="BLR-BUS-4417-EDGE"
    )
    assert r.status == "stored", r.reason
    obs = session.scalars(select(Observation)).one()
    assert obs.segment_id == f.SEGMENT and obs.asset_id is None
    assert obs.received_at is not None
    assert session.get(Device, "BLR-BUS-4417-EDGE") is not None


def test_duplicates_are_normal_operation(ingestor, session):
    msg = f.observation()
    assert ingestor.handle("observation", msg).status == "stored"
    assert ingestor.handle("observation", msg).status == "duplicate"
    assert count(session, Observation) == 1
    assert count(session, IngestRejection) == 0


def test_rejects_unknown_major_version(ingestor, session):
    msg = f.observation()
    msg["schema_version"] = "2.0.0"
    r = ingestor.handle("observation", msg)
    assert r.status == "rejected" and "major" in r.reason
    assert count(session, Observation) == 0


def test_rejects_schema_violation(ingestor):
    msg = f.observation()
    msg["confidence"] = 1.4
    r = ingestor.handle("observation", msg)
    assert r.status == "rejected" and "confidence" in r.reason


def test_rejects_invalid_json(ingestor, session):
    r = ingestor.handle("observation", b"{not json")
    assert r.status == "rejected"
    assert count(session, IngestRejection) == 1


def test_privacy_gate_refuses_unblurred_evidence_and_flags_device(ingestor, session):
    for flag in (False, None):
        r = ingestor.handle("observation", f.observation(faces_blurred=flag))
        assert r.status == "rejected" and "faces_blurred" in r.reason
    assert count(session, Observation) == 0
    dev = session.get(Device, "BLR-BUS-4417-EDGE")
    assert dev.flagged and "privacy" in dev.flag_reason


def test_edge_may_not_emit_missing_classes(ingestor):
    r = ingestor.handle("observation", f.observation(cls="missing_zebra"))
    assert r.status == "rejected" and "ledger-inferred" in r.reason


def test_topic_device_must_match_payload(ingestor, session):
    r = ingestor.handle("observation", f.observation(), topic_device="BLR-BUS-9999-EDGE")
    assert r.status == "rejected"


def test_revoked_device_is_refused(ingestor, session):
    session.add(Device(device_id="BLR-BUS-4417-EDGE", revoked=True))
    session.commit()
    assert ingestor.handle("observation", f.observation()).status == "rejected"


def test_hash_verified_against_store(ingestor, session, store):
    msg = f.observation()
    store.objects[msg["evidence"]["uri"]] = msg["observation_id"].encode()  # hashes to sha(oid)
    assert ingestor.handle("observation", msg).status == "stored"
    assert session.get(EvidenceCheck, msg["evidence"]["uri"]).status == "verified"


def test_hash_mismatch_is_rejected_and_flags_device(ingestor, session, store):
    msg = f.observation()
    store.objects[msg["evidence"]["uri"]] = b"tampered"
    r = ingestor.handle("observation", msg)
    assert r.status == "rejected" and "mismatch" in r.reason
    assert session.get(Device, msg["device_id"]).flagged


def test_missing_object_is_pending_in_best_effort_mode(ingestor, session):
    msg = f.observation()
    assert ingestor.handle("observation", msg).status == "stored"
    assert session.get(EvidenceCheck, msg["evidence"]["uri"]).status == "pending"


def test_segment_pass_stored(ingestor, session):
    assert ingestor.handle("segment_pass", f.segment_pass()).status == "stored"
    p = session.scalars(select(SegmentPass)).one()
    assert p.inspected_for == ["pothole", "faded_zebra"] and p.assessable_frac == 0.87


def test_incident_writes_anpr_audit_and_publishes_redacted(ingestor, session, bus):
    assert ingestor.handle("incident", f.incident()).status == "stored"
    assert count(session, Incident) == 1
    audit = session.scalars(select(AuditLog)).one()
    assert audit.action == "anpr.read"
    live = [e for e in bus.published if e["type"] == "incident"]
    assert live and "KA 05" not in json.dumps(live[0]["data"])


def test_telemetry_stored_and_published(ingestor, session, bus):
    assert ingestor.handle("telemetry", f.telemetry()).status == "stored"
    assert ingestor.handle("telemetry", f.telemetry()).status == "duplicate"
    assert count(session, Telemetry) == 1
    assert any(e["type"] == "telemetry" for e in bus.published)
