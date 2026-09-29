"""SQLAlchemy models.

Two populations, never conflated (backend/README):

* **Raw** — ``observation``, ``segment_pass``, ``telemetry``. Immutable, append-only,
  hypertables on PostgreSQL, read only by the fusion worker and the aggregates. The single
  column anything writes after insert is ``observation.asset_id``, set once by fusion to
  record which asset the observation was fused into (docs/04 schema).
* **Fused** — ``asset`` and everything hanging off it. Mutable, lifecycle-managed, and the
  only defect data an API or UI ever reads.

Geometry is stored portably (lat/lon columns, GeoJSON for lines and polygons, bbox columns
for cheap window queries). On PostgreSQL the initial migration adds generated PostGIS
``geom`` columns with GIST indexes over the same data, so spatial SQL works there without
the ORM or the SQLite test database having to know about it.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from argus_api.db.types import JSONType, UTCDateTime, utcnow

AutoId = BigInteger().with_variant(Integer(), "sqlite")


class Base(DeclarativeBase):
    pass


# ─── Reference geography ─────────────────────────────────────────────────────


class Ward(Base):
    __tablename__ = "ward"

    ward_id: Mapped[str] = mapped_column(String, primary_key=True)  # 'bbmp:150'
    name: Mapped[str] = mapped_column(String, nullable=False)
    geometry: Mapped[dict] = mapped_column(JSONType, nullable=False)  # GeoJSON (Multi)Polygon
    population: Mapped[int | None] = mapped_column(Integer)
    engineer_of_record: Mapped[str | None] = mapped_column(String)
    min_lon: Mapped[float] = mapped_column(Float, nullable=False)
    min_lat: Mapped[float] = mapped_column(Float, nullable=False)
    max_lon: Mapped[float] = mapped_column(Float, nullable=False)
    max_lat: Mapped[float] = mapped_column(Float, nullable=False)


class RoadSegment(Base):
    __tablename__ = "road_segment"

    segment_id: Mapped[str] = mapped_column(String, primary_key=True)  # 'osm:way/23847561:3'
    osm_way_id: Mapped[int | None] = mapped_column(BigInteger)
    coordinates: Mapped[list] = mapped_column(JSONType, nullable=False)  # [[lon, lat], ...]
    name: Mapped[str | None] = mapped_column(String)
    highway: Mapped[str | None] = mapped_column(String)
    oneway: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    maxspeed_kmh: Mapped[int | None] = mapped_column(Integer)
    length_m: Mapped[float] = mapped_column(Float, nullable=False)
    ward_id: Mapped[str | None] = mapped_column(ForeignKey("ward.ward_id"))
    # Ends at a mapped signalised junction: stopped time is intersection delay, not link delay.
    has_signal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    freeflow_kmh: Mapped[float | None] = mapped_column(Float)
    min_lon: Mapped[float] = mapped_column(Float, nullable=False)
    min_lat: Mapped[float] = mapped_column(Float, nullable=False)
    max_lon: Mapped[float] = mapped_column(Float, nullable=False)
    max_lat: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (Index("ix_road_segment_bbox", "min_lon", "max_lon", "min_lat", "max_lat"),)


class SegmentFreeflow(Base):
    """Learned free-flow speed per segment per direction (docs/07 §1)."""

    __tablename__ = "segment_freeflow"

    segment_id: Mapped[str] = mapped_column(String, primary_key=True)
    direction: Mapped[str] = mapped_column(String, primary_key=True)
    freeflow_kmh: Mapped[float] = mapped_column(Float, nullable=False)
    n_passes: Mapped[int] = mapped_column(Integer, nullable=False)
    method: Mapped[str] = mapped_column(String, nullable=False)  # p85_offpeak | maxspeed | default
    computed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class ExpectedAsset(Base):
    """The Road Asset Ledger's expectation side: what should be here."""

    __tablename__ = "expected_asset"

    expected_id: Mapped[str] = mapped_column(String, primary_key=True)  # 'osm:node/1029384756'
    class_id: Mapped[str] = mapped_column(String, nullable=False)  # the missing_* it guards
    segment_id: Mapped[str | None] = mapped_column(ForeignKey("road_segment.segment_id"))
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(
        String, nullable=False
    )  # osm | historical_observation | municipal_import
    valid_from: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    last_positive_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class LedgerAbsence(Base):
    """One qualifying pass that looked for an expected asset and did not see it."""

    __tablename__ = "ledger_absence"

    id: Mapped[int] = mapped_column(AutoId, primary_key=True, autoincrement=True)
    expected_id: Mapped[str] = mapped_column(
        ForeignKey("expected_asset.expected_id"), nullable=False
    )
    segment_pass_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    device_id: Mapped[str] = mapped_column(String, nullable=False)
    at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (UniqueConstraint("expected_id", "segment_pass_id"),)


# ─── Devices and people ─────────────────────────────────────────────────────


class Device(Base):
    __tablename__ = "device"

    device_id: Mapped[str] = mapped_column(String, primary_key=True)
    bus_id: Mapped[str | None] = mapped_column(String)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    # Learned from the operator-rejection rate of assets this device contributed to.
    reliability: Mapped[float] = mapped_column(Float, nullable=False, default=0.9)
    confirmed_contributions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected_contributions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    flagged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    flag_reason: Mapped[str | None] = mapped_column(Text)
    flagged_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # A revoked certificate. Messages from a revoked device are refused at ingest.
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class AppUser(Base):
    __tablename__ = "app_user"

    username: Mapped[str] = mapped_column(String, primary_key=True)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)  # viewer | engineer | admin
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class AuditLog(Base):
    """Who looked at what. Queryable by the deploying authority (docs/08 §6)."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(AutoId, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    actor: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str | None] = mapped_column(String)
    action: Mapped[str] = mapped_column(String, nullable=False)
    resource_type: Mapped[str] = mapped_column(String, nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String)
    detail: Mapped[dict | None] = mapped_column(JSONType)
    client: Mapped[str | None] = mapped_column(String)

    __table_args__ = (Index("ix_audit_log_resource", "resource_type", "resource_id", "at"),)


# ─── Raw telemetry: immutable, append-only, never read by a UI ───────────────


class Observation(Base):
    __tablename__ = "observation"

    observation_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime, primary_key=True)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    uplinked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    device_id: Mapped[str] = mapped_column(String, nullable=False)
    bus_id: Mapped[str | None] = mapped_column(String)
    route_id: Mapped[str | None] = mapped_column(String)
    trip_id: Mapped[str | None] = mapped_column(String)
    segment_pass_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    camera_id: Mapped[str] = mapped_column(String, nullable=False)
    class_id: Mapped[str] = mapped_column(String, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    accuracy_m: Mapped[float] = mapped_column(Float, nullable=False)
    geo_source: Mapped[str] = mapped_column(String, nullable=False)
    segment_id: Mapped[str | None] = mapped_column(String)
    offset_m: Mapped[float | None] = mapped_column(Float)
    ward_id: Mapped[str | None] = mapped_column(String)
    map_match_confidence: Mapped[float | None] = mapped_column(Float)
    severity_score: Mapped[float | None] = mapped_column(Float)
    severity_band: Mapped[str | None] = mapped_column(String)
    area_m2: Mapped[float | None] = mapped_column(Float)
    range_m: Mapped[float | None] = mapped_column(Float)
    conditions: Mapped[dict] = mapped_column(JSONType, nullable=False)
    geometry_m: Mapped[dict | None] = mapped_column(JSONType)
    evidence: Mapped[dict | None] = mapped_column(JSONType)
    evidence_uri: Mapped[str | None] = mapped_column(String)
    evidence_sha256: Mapped[str | None] = mapped_column(String(64))
    model_version: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONType, nullable=False)  # the message as received
    asset_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # set once, by fusion

    __table_args__ = (
        Index("ix_observation_id", "observation_id"),
        Index("ix_observation_seg_class_time", "segment_id", "class_id", "captured_at"),
        Index("ix_observation_asset", "asset_id"),
    )


class SegmentPass(Base):
    __tablename__ = "segment_pass"

    segment_pass_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    entered_at: Mapped[datetime] = mapped_column(UTCDateTime, primary_key=True)
    exited_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    device_id: Mapped[str] = mapped_column(String, nullable=False)
    bus_id: Mapped[str | None] = mapped_column(String)
    route_id: Mapped[str | None] = mapped_column(String)
    trip_id: Mapped[str | None] = mapped_column(String)
    segment_id: Mapped[str] = mapped_column(String, nullable=False)
    ward_id: Mapped[str | None] = mapped_column(String)
    direction: Mapped[str] = mapped_column(String, nullable=False, default="forward")
    length_m: Mapped[float | None] = mapped_column(Float)
    mean_speed_kmh: Mapped[float] = mapped_column(Float, nullable=False)
    min_speed_kmh: Mapped[float | None] = mapped_column(Float)
    stopped_s: Mapped[float | None] = mapped_column(Float)
    dwell_excl_s: Mapped[float | None] = mapped_column(Float)
    vehicle_counts: Mapped[dict | None] = mapped_column(JSONType)
    occupancy_ratio: Mapped[float | None] = mapped_column(Float)
    pcu_estimate: Mapped[float | None] = mapped_column(Float)
    pedestrian: Mapped[dict | None] = mapped_column(JSONType)
    inspected_for: Mapped[list] = mapped_column(JSONType, nullable=False)
    found: Mapped[list] = mapped_column(JSONType, nullable=False)
    assessable_frac: Mapped[float] = mapped_column(Float, nullable=False)
    conditions: Mapped[dict] = mapped_column(JSONType, nullable=False)
    model_version: Mapped[str | None] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSONType, nullable=False)

    __table_args__ = (
        Index("ix_segment_pass_id", "segment_pass_id"),
        Index("ix_segment_pass_seg_dir_time", "segment_id", "direction", "entered_at"),
        Index("ix_segment_pass_route_time", "route_id", "entered_at"),
    )


class Telemetry(Base):
    __tablename__ = "telemetry"

    device_id: Mapped[str] = mapped_column(String, primary_key=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, primary_key=True)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    bus_id: Mapped[str | None] = mapped_column(String)
    route_id: Mapped[str | None] = mapped_column(String)
    trip_id: Mapped[str | None] = mapped_column(String)
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    accuracy_m: Mapped[float | None] = mapped_column(Float)
    speed_kmh: Mapped[float | None] = mapped_column(Float)
    heading: Mapped[float | None] = mapped_column(Float)
    next_stop_id: Mapped[str | None] = mapped_column(String)
    delay_s: Mapped[int | None] = mapped_column(Integer)
    headway_s: Mapped[int | None] = mapped_column(Integer)
    health: Mapped[dict] = mapped_column(JSONType, nullable=False)
    model: Mapped[dict | None] = mapped_column(JSONType)

    __table_args__ = (Index("ix_telemetry_route_time", "route_id", "at"),)


class Incident(Base):
    __tablename__ = "incident"

    incident_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    device_id: Mapped[str] = mapped_column(String, nullable=False)
    bus_id: Mapped[str | None] = mapped_column(String)
    route_id: Mapped[str | None] = mapped_column(String)
    incident_type: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    ended_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    severity: Mapped[str | None] = mapped_column(String)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    accuracy_m: Mapped[float] = mapped_column(Float, nullable=False)
    segment_id: Mapped[str | None] = mapped_column(String)
    ward_id: Mapped[str | None] = mapped_column(String)
    ego: Mapped[dict | None] = mapped_column(JSONType)
    triggers: Mapped[dict] = mapped_column(JSONType, nullable=False)
    subject_vehicle: Mapped[dict | None] = mapped_column(JSONType)
    evidence: Mapped[dict | None] = mapped_column(JSONType)
    model: Mapped[dict | None] = mapped_column(JSONType)
    payload: Mapped[dict] = mapped_column(JSONType, nullable=False)
    # Operator workflow. The platform produces evidence for an enforcement process; it
    # does not adjudicate (docs/08).
    review_status: Mapped[str] = mapped_column(String, nullable=False, default="new")
    reviewed_by: Mapped[str | None] = mapped_column(String)
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    case_closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    __table_args__ = (Index("ix_incident_started", "started_at"),)


class IngestRejection(Base):
    """Messages refused at the boundary, kept so a flagged device can be investigated."""

    __tablename__ = "ingest_rejection"

    id: Mapped[int] = mapped_column(AutoId, primary_key=True, autoincrement=True)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    device_id: Mapped[str | None] = mapped_column(String)
    topic: Mapped[str | None] = mapped_column(String)
    message_id: Mapped[str | None] = mapped_column(String)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSONType)


class EvidenceCheck(Base):
    """Chain of custody: the hash the edge claimed and what ingest found in the store."""

    __tablename__ = "evidence_check"

    uri: Mapped[str] = mapped_column(String, primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(
        String, nullable=False
    )  # observation | incident_clip | plate_crop
    message_id: Mapped[str] = mapped_column(String, nullable=False)
    device_id: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(
        String, nullable=False
    )  # verified | pending | mismatch | unchecked | expired
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


# ─── Fused truth: what every UI and API actually reads ───────────────────────


class Asset(Base):
    __tablename__ = "asset"

    asset_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    class_id: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False, default="candidate")
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    accuracy_m: Mapped[float] = mapped_column(Float, nullable=False)
    geo_source: Mapped[str] = mapped_column(String, nullable=False, default="gnss+ipm+mapmatch")
    segment_id: Mapped[str | None] = mapped_column(String)
    offset_m: Mapped[float | None] = mapped_column(Float)
    ward_id: Mapped[str | None] = mapped_column(String)
    map_match_confidence: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    log_odds: Mapped[float] = mapped_column(Float, nullable=False)
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    distinct_devices: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    negative_passes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    severity_score: Mapped[float | None] = mapped_column(Float)
    severity_band: Mapped[str | None] = mapped_column(String)
    severity_trend: Mapped[str | None] = mapped_column(String)
    physical: Mapped[dict | None] = mapped_column(JSONType)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    last_evidence_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    # Last time any qualifying pass or observation looked at this spot. Drives `stale`.
    last_inspected_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    reopen_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_reopened_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    canonical_evidence: Mapped[dict | None] = mapped_column(JSONType)
    evidence_gallery: Mapped[list | None] = mapped_column(JSONType)
    ledger: Mapped[dict | None] = mapped_column(JSONType)  # missing_* reasoning
    expected_id: Mapped[str | None] = mapped_column(String)  # missing_* only
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        Index("ix_asset_state_class_ward", "state", "class_id", "ward_id"),
        Index("ix_asset_seg_class", "segment_id", "class_id"),
        Index("ix_asset_latlon", "lat", "lon"),
    )


class AssetNegative(Base):
    """A qualifying pass that inspected an asset's class on its segment and found nothing."""

    __tablename__ = "asset_negative"

    id: Mapped[int] = mapped_column(AutoId, primary_key=True, autoincrement=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("asset.asset_id"), nullable=False)
    segment_pass_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    device_id: Mapped[str] = mapped_column(String, nullable=False)
    at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    quality: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (
        UniqueConstraint("asset_id", "segment_pass_id"),
        Index("ix_asset_negative_asset_at", "asset_id", "at"),
    )


class AssetEvent(Base):
    """Lifecycle history. Durability (re-opening after repair) is read from here."""

    __tablename__ = "asset_event"

    id: Mapped[int] = mapped_column(AutoId, primary_key=True, autoincrement=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("asset.asset_id"), nullable=False)
    at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    from_state: Mapped[str | None] = mapped_column(String)
    to_state: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str] = mapped_column(String, nullable=False)
    actor: Mapped[str] = mapped_column(String, nullable=False, default="fusion")

    __table_args__ = (Index("ix_asset_event_asset_at", "asset_id", "at"),)


class WorkOrder(Base):
    __tablename__ = "work_order"

    reference: Mapped[str] = mapped_column(String, primary_key=True)  # 'ARG-BBMP-150-004417'
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("asset.asset_id"), nullable=False)
    ward_id: Mapped[str | None] = mapped_column(String)
    class_id: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    department: Mapped[str] = mapped_column(String, nullable=False)
    severity_band: Mapped[str] = mapped_column(String, nullable=False)
    priority_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    priority_rank: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # draft | issued | in_progress | awaiting_verification | closed
    status: Mapped[str] = mapped_column(String, nullable=False, default="issued")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    issued_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    sla_due_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    closed_by: Mapped[str | None] = mapped_column(String)  # 'auto:fleet-verified' | operator id

    __table_args__ = (Index("ix_work_order_ward_status", "ward_id", "status"),)


class HardNegative(Base):
    """An operator-rejected asset with its evidence, destined for the retraining set."""

    __tablename__ = "hard_negative"

    id: Mapped[int] = mapped_column(AutoId, primary_key=True, autoincrement=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("asset.asset_id"), nullable=False)
    class_id: Mapped[str] = mapped_column(String, nullable=False)
    observation_ids: Mapped[list] = mapped_column(JSONType, nullable=False)
    evidence: Mapped[list] = mapped_column(JSONType, nullable=False)
    rejected_by: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    exported_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class PassProcessed(Base):
    """Fusion's bookkeeping of which passes it has applied as negative evidence."""

    __tablename__ = "pass_processed"

    segment_pass_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


# ─── GTFS (route delay, docs/07 §7) ──────────────────────────────────────────


class GtfsRoute(Base):
    __tablename__ = "gtfs_route"

    route_id: Mapped[str] = mapped_column(String, primary_key=True)
    short_name: Mapped[str | None] = mapped_column(String)
    long_name: Mapped[str | None] = mapped_column(String)


class GtfsStop(Base):
    __tablename__ = "gtfs_stop"

    stop_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str | None] = mapped_column(String)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)


class GtfsTrip(Base):
    __tablename__ = "gtfs_trip"

    trip_id: Mapped[str] = mapped_column(String, primary_key=True)
    route_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    service_id: Mapped[str | None] = mapped_column(String)
    direction_id: Mapped[int | None] = mapped_column(Integer)
    shape_id: Mapped[str | None] = mapped_column(String)


class GtfsStopTime(Base):
    __tablename__ = "gtfs_stop_time"

    trip_id: Mapped[str] = mapped_column(String, primary_key=True)
    stop_sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    stop_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    arrival_s: Mapped[int | None] = mapped_column(Integer)  # seconds after service-day midnight
    departure_s: Mapped[int | None] = mapped_column(Integer)
