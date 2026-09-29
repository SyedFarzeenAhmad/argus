"""Ingest: validate, verify, store, notify. Boring on purpose (docs/04 "Ingest").

Per message:

1. **Validate** against the contract; reject an unknown major version.
2. **Authenticate the source**: the device in the MQTT topic must be the device in the
   payload, and a revoked device is refused.
3. **Privacy gate**: evidence whose ``faces_blurred`` is not ``true`` is refused and the
   device flagged. The edge runs a face blur on every crop destined for disk, so the flag
   must always be set; this enforces it at the boundary rather than trusting it.
4. **Deduplicate** by message id — MQTT QoS 1 is at-least-once, so duplicates are normal.
5. **Verify the evidence hash** against the object store (chain of custody).
6. **Store** in the raw append-only tables, stamping receipt time.
7. **Notify** fusion (observations, passes) or the live feed (incidents, telemetry).

No interpretation happens here. Anything clever belongs in fusion or analytics.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from argus_api.core import audit
from argus_api.core.config import Settings, get_settings
from argus_api.core.storage import EvidenceStore, ObjectMissing, StoreUnavailable, get_store
from argus_api.db import session as db_session
from argus_api.db.models import (
    Device,
    EvidenceCheck,
    Incident,
    IngestRejection,
    Observation,
    SegmentPass,
    Telemetry,
)
from argus_api.db.types import parse_ts, utcnow
from argus_api.fusion.actions import LEDGER_CLASSES
from argus_api.ingest.validation import ContractError, validate
from argus_api.realtime.bus import EventBus, get_bus
from argus_api.serializers import incident_to_dict, telemetry_to_live

log = structlog.get_logger()

MAX_CLOCK_SKEW = timedelta(minutes=10)


class Rejected(Exception):
    def __init__(self, reason: str, *, flag_device: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.flag_device = flag_device


@dataclass
class IngestResult:
    status: str  # stored | duplicate | rejected
    kind: str
    message_id: str | None = None
    reason: str | None = None


class Ingestor:
    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
        settings: Settings | None = None,
        bus: EventBus | None = None,
        store: Callable[[], EvidenceStore] | None = None,
    ) -> None:
        self.sessions = session_factory or db_session.get_sessionmaker()
        self.cfg = settings or get_settings()
        self.bus = bus or get_bus()
        self._store = store or get_store

    # ─── Entry point ────────────────────────────────────────────────────────

    def handle(
        self,
        kind: str,
        payload: bytes | str | dict[str, Any],
        *,
        topic: str | None = None,
        topic_device: str | None = None,
    ) -> IngestResult:
        msg: Any = payload
        device_id: str | None = None
        message_id: str | None = None
        try:
            if isinstance(payload, bytes | str):
                try:
                    msg = json.loads(payload)
                except json.JSONDecodeError as exc:
                    raise Rejected(f"invalid JSON: {exc.msg}") from exc
            if isinstance(msg, dict):
                device_id = msg.get("device_id")
                message_id = _message_id(kind, msg)
            try:
                validate(kind, msg)
            except ContractError as exc:
                raise Rejected(f"contract: {exc}") from exc

            with self.sessions() as s:
                self._check_source(s, msg, topic_device)
                self._check_privacy(kind, msg)
                self._check_semantics(kind, msg)
                if self._is_duplicate(s, kind, msg):
                    return IngestResult("duplicate", kind, message_id)
                self._verify_evidence(s, kind, msg)
                row = self._store_row(s, kind, msg)
                self._touch_device(s, msg)
                if kind == "incident" and (msg.get("subject_vehicle") or {}).get("plate"):
                    # Every ANPR invocation is on the record (docs/08 §3).
                    audit.record(
                        s,
                        actor=msg["device_id"],
                        role="device",
                        action="anpr.read",
                        resource_type="incident",
                        resource_id=msg["incident_id"],
                        detail={
                            "frames_voted": msg["subject_vehicle"]["plate"].get("frames_voted")
                        },
                    )
                try:
                    s.commit()
                except IntegrityError:
                    # The same message racing itself through two consumers: still a duplicate.
                    s.rollback()
                    return IngestResult("duplicate", kind, message_id)
                self._notify(kind, row)
            return IngestResult("stored", kind, message_id)
        except Rejected as r:
            self._record_rejection(
                kind, msg, r, topic=topic, device_id=device_id, message_id=message_id
            )
            log.warning("ingest.rejected", kind=kind, device=device_id, reason=r.reason)
            return IngestResult("rejected", kind, message_id, r.reason)

    # ─── Checks ─────────────────────────────────────────────────────────────

    def _check_source(self, s: Session, msg: dict, topic_device: str | None) -> None:
        if topic_device is not None and topic_device != msg["device_id"]:
            raise Rejected(
                f"topic device {topic_device} does not match payload device {msg['device_id']}",
                flag_device=True,
            )
        dev = s.get(Device, msg["device_id"])
        if dev is not None and dev.revoked:
            raise Rejected("device certificate revoked")

    def _check_privacy(self, kind: str, msg: dict) -> None:
        ev = msg.get("evidence")
        if ev is not None and ev.get("faces_blurred") is not True:
            raise Rejected(
                "evidence without faces_blurred=true refused at the privacy gate", flag_device=True
            )

    def _check_semantics(self, kind: str, msg: dict) -> None:
        now = utcnow()
        if kind == "observation":
            if msg["class_id"] in LEDGER_CLASSES:
                # An edge emitting missing_* means someone trained a detector on empty road.
                raise Rejected(
                    f"{msg['class_id']} is ledger-inferred and never edge-emitted", flag_device=True
                )
            t = parse_ts(msg["captured_at"])
        elif kind == "segment_pass":
            t = parse_ts(msg["entered_at"])
            if parse_ts(msg["exited_at"]) < t:
                raise Rejected("exited_at precedes entered_at")
            missing = [c for c in msg["inspection"]["found"] if c in LEDGER_CLASSES]
            if missing:
                raise Rejected(f"inspection.found contains ledger-inferred {missing}")
        elif kind == "incident":
            t = parse_ts(msg["started_at"])
            if parse_ts(msg["ended_at"]) < t:
                raise Rejected("ended_at precedes started_at")
        else:
            t = parse_ts(msg["at"])
        if t > now + MAX_CLOCK_SKEW:
            raise Rejected(f"timestamp {t.isoformat()} is in the future; edge clock unsynchronised")

    def _is_duplicate(self, s: Session, kind: str, msg: dict) -> bool:
        if kind == "observation":
            q = (
                select(func.count())
                .select_from(Observation)
                .where(Observation.observation_id == uuid.UUID(msg["observation_id"]))
            )
        elif kind == "segment_pass":
            q = (
                select(func.count())
                .select_from(SegmentPass)
                .where(SegmentPass.segment_pass_id == uuid.UUID(msg["segment_pass_id"]))
            )
        elif kind == "incident":
            q = (
                select(func.count())
                .select_from(Incident)
                .where(Incident.incident_id == uuid.UUID(msg["incident_id"]))
            )
        else:
            q = (
                select(func.count())
                .select_from(Telemetry)
                .where(Telemetry.device_id == msg["device_id"], Telemetry.at == parse_ts(msg["at"]))
            )
        return bool(s.scalar(q))

    # ─── Chain of custody ───────────────────────────────────────────────────

    def _verify_evidence(self, s: Session, kind: str, msg: dict) -> None:
        items: list[tuple[str, str, str]] = []  # (uri, sha256, kind)
        ev = msg.get("evidence") or {}
        if kind == "observation" and ev:
            items.append((ev["uri"], ev["sha256"], "observation"))
        elif kind == "incident" and ev:
            # The clip rides depot wifi overnight; it is expected to be missing now.
            items.append((ev["clip_uri"], ev["sha256"], "incident_clip"))
        msg_id = _message_id(kind, msg) or ""
        for uri, sha, ekind in items:
            status = self._check_hash(uri, sha, ekind)
            if s.get(EvidenceCheck, uri) is None:
                s.add(
                    EvidenceCheck(
                        uri=uri,
                        sha256=sha,
                        kind=ekind,
                        message_id=msg_id,
                        device_id=msg["device_id"],
                        status=status,
                        attempts=1,
                        checked_at=utcnow() if status != "unchecked" else None,
                    )
                )

    def _check_hash(self, uri: str, sha: str, ekind: str) -> str:
        mode = self.cfg.evidence_verification
        if mode == "off":
            return "unchecked"
        try:
            actual = self._store().sha256(uri)
        except ObjectMissing as exc:
            if mode == "required" and ekind != "incident_clip":
                raise Rejected(f"evidence object missing: {uri}") from exc
            return "pending"
        except (StoreUnavailable, ValueError) as exc:
            if mode == "required":
                raise Rejected(f"evidence not verifiable: {exc}") from exc
            return "pending"
        if actual != sha:
            raise Rejected(f"evidence hash mismatch for {uri}", flag_device=True)
        return "verified"

    # ─── Storage ────────────────────────────────────────────────────────────

    def _store_row(self, s: Session, kind: str, m: dict) -> Any:
        now = utcnow()
        if kind == "observation":
            geo, mm = m["geo"], m.get("map_match") or {}
            geom, sev, ev = m.get("geometry") or {}, m.get("severity") or {}, m.get("evidence")
            row: Any = Observation(
                observation_id=uuid.UUID(m["observation_id"]),
                captured_at=parse_ts(m["captured_at"]),
                received_at=now,
                uplinked_at=parse_ts(m["uplinked_at"]) if m.get("uplinked_at") else None,
                device_id=m["device_id"],
                bus_id=m.get("bus_id"),
                route_id=m.get("route_id"),
                trip_id=m.get("trip_id"),
                segment_pass_id=uuid.UUID(m["segment_pass_id"])
                if m.get("segment_pass_id")
                else None,
                camera_id=m["camera_id"],
                class_id=m["class_id"],
                confidence=m["confidence"],
                lat=geo["lat"],
                lon=geo["lon"],
                accuracy_m=geo["accuracy_m"],
                geo_source=geo["source"],
                segment_id=mm.get("segment_id"),
                offset_m=mm.get("offset_m"),
                ward_id=mm.get("ward_id"),
                map_match_confidence=mm.get("confidence"),
                severity_score=sev.get("score"),
                severity_band=sev.get("band"),
                area_m2=geom.get("mask_area_m2"),
                range_m=geom.get("range_m"),
                conditions=m["conditions"],
                geometry_m=geom or None,
                evidence=ev,
                evidence_uri=(ev or {}).get("uri"),
                evidence_sha256=(ev or {}).get("sha256"),
                model_version=f"{m['model']['name']}@{m['model']['version']}",
                payload=m,
            )
        elif kind == "segment_pass":
            t, insp = m["traffic"], m["inspection"]
            row = SegmentPass(
                segment_pass_id=uuid.UUID(m["segment_pass_id"]),
                entered_at=parse_ts(m["entered_at"]),
                exited_at=parse_ts(m["exited_at"]),
                received_at=now,
                device_id=m["device_id"],
                bus_id=m.get("bus_id"),
                route_id=m.get("route_id"),
                trip_id=m.get("trip_id"),
                segment_id=m["segment_id"],
                ward_id=m.get("ward_id"),
                direction=m.get("direction", "forward"),
                length_m=m.get("length_m"),
                mean_speed_kmh=t["mean_speed_kmh"],
                min_speed_kmh=t.get("min_speed_kmh"),
                stopped_s=t.get("stopped_seconds"),
                dwell_excl_s=t.get("dwell_excluded_seconds"),
                vehicle_counts=t.get("vehicle_counts"),
                occupancy_ratio=t.get("occupancy_ratio"),
                pcu_estimate=t.get("pcu_estimate"),
                pedestrian=m.get("pedestrian"),
                inspected_for=list(insp["inspected_for"]),
                found=list(insp["found"]),
                # Absent means the edge made no claim about assessability; treat it as not
                # assessable rather than as a clean inspection.
                assessable_frac=insp.get("assessable_fraction", 0.0),
                conditions=m["conditions"],
                model_version=(
                    f"{m['model']['name']}@{m['model']['version']}" if m.get("model") else None
                ),
                payload=m,
            )
        elif kind == "incident":
            geo, mm = m["geo"], m.get("map_match") or {}
            row = Incident(
                incident_id=uuid.UUID(m["incident_id"]),
                received_at=now,
                device_id=m["device_id"],
                bus_id=m.get("bus_id"),
                route_id=m.get("route_id"),
                incident_type=m["incident_type"],
                started_at=parse_ts(m["started_at"]),
                ended_at=parse_ts(m["ended_at"]),
                confidence=m["confidence"],
                severity=m.get("severity"),
                lat=geo["lat"],
                lon=geo["lon"],
                accuracy_m=geo["accuracy_m"],
                segment_id=mm.get("segment_id"),
                ward_id=mm.get("ward_id"),
                ego=m.get("ego"),
                triggers=m["triggers"],
                subject_vehicle=m.get("subject_vehicle"),
                evidence=m.get("evidence"),
                model=m.get("model"),
                payload=m,
            )
        else:
            geo, ego, sch = m["geo"], m.get("ego") or {}, m.get("schedule") or {}
            row = Telemetry(
                device_id=m["device_id"],
                at=parse_ts(m["at"]),
                received_at=now,
                bus_id=m.get("bus_id"),
                route_id=m.get("route_id"),
                trip_id=m.get("trip_id"),
                lat=geo["lat"],
                lon=geo["lon"],
                accuracy_m=geo["accuracy_m"],
                speed_kmh=ego.get("speed_kmh"),
                heading=ego.get("heading_deg"),
                next_stop_id=sch.get("next_stop_id"),
                delay_s=sch.get("delay_s"),
                headway_s=sch.get("headway_s"),
                health=m["health"],
                model=m.get("model"),
            )
        s.add(row)
        return row

    def _touch_device(self, s: Session, m: dict) -> None:
        dev = s.get(Device, m["device_id"])
        now = utcnow()
        if dev is None:
            s.add(
                Device(
                    device_id=m["device_id"],
                    bus_id=m.get("bus_id"),
                    first_seen_at=now,
                    last_seen_at=now,
                    reliability=self.cfg.default_device_reliability,
                )
            )
        else:
            dev.last_seen_at = now
            if m.get("bus_id"):
                dev.bus_id = m["bus_id"]

    def _notify(self, kind: str, row: Any) -> None:
        if kind in ("observation", "segment_pass"):
            self.bus.wake_fusion()
        elif kind == "incident":
            self.bus.publish("incident", incident_to_dict(row, include_plate=False))
        elif kind == "telemetry":
            self.bus.publish("telemetry", telemetry_to_live(row))

    def _record_rejection(
        self,
        kind: str,
        msg: Any,
        r: Rejected,
        *,
        topic: str | None,
        device_id: str | None,
        message_id: str | None,
    ) -> None:
        payload = msg if isinstance(msg, dict) else {"raw": str(msg)[:2000]}
        with self.sessions() as s:
            s.add(
                IngestRejection(
                    kind=kind,
                    device_id=device_id,
                    topic=topic,
                    message_id=message_id,
                    reason=r.reason[:2000],
                    payload=payload,
                )
            )
            if r.flag_device and isinstance(device_id, str):
                dev = s.get(Device, device_id)
                if dev is None:
                    dev = Device(
                        device_id=device_id, reliability=self.cfg.default_device_reliability
                    )
                    s.add(dev)
                dev.flagged = True
                dev.flag_reason = r.reason[:500]
                dev.flagged_at = utcnow()
            s.commit()


def _message_id(kind: str, msg: dict) -> str | None:
    key = {
        "observation": "observation_id",
        "segment_pass": "segment_pass_id",
        "incident": "incident_id",
    }.get(kind)
    if key is None:
        return f"{msg.get('device_id')}@{msg.get('at')}"
    v = msg.get(key)
    return v if isinstance(v, str) else None
