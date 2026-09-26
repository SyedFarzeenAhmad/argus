"""Initial schema.

Tables are generated from the ORM models. The PostgreSQL section at the bottom adds
what the ORM does not know about: PostGIS geometry kept in step with the portable lat/lon
and GeoJSON columns, TimescaleDB hypertables, the continuous aggregates every heat map
reads, and the retention policies from docs/08.

Revision ID: 0001
Revises:
Create Date: 2026-09-26 01:46:03.469977
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_user",
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("username"),
    )
    op.create_table(
        "asset",
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("class_id", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("accuracy_m", sa.Float(), nullable=False),
        sa.Column("geo_source", sa.String(), nullable=False),
        sa.Column("segment_id", sa.String(), nullable=True),
        sa.Column("offset_m", sa.Float(), nullable=True),
        sa.Column("ward_id", sa.String(), nullable=True),
        sa.Column("map_match_confidence", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("log_odds", sa.Float(), nullable=False),
        sa.Column("evidence_count", sa.Integer(), nullable=False),
        sa.Column("distinct_devices", sa.Integer(), nullable=False),
        sa.Column("negative_passes", sa.Integer(), nullable=False),
        sa.Column("severity_score", sa.Float(), nullable=True),
        sa.Column("severity_band", sa.String(), nullable=True),
        sa.Column("severity_trend", sa.String(), nullable=True),
        sa.Column(
            "physical",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_evidence_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_inspected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reopen_count", sa.Integer(), nullable=False),
        sa.Column("last_reopened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "canonical_evidence",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "evidence_gallery",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "ledger",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("expected_id", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("asset_id"),
    )
    with op.batch_alter_table("asset", schema=None) as batch_op:
        batch_op.create_index("ix_asset_latlon", ["lat", "lon"], unique=False)
        batch_op.create_index("ix_asset_seg_class", ["segment_id", "class_id"], unique=False)
        batch_op.create_index(
            "ix_asset_state_class_ward", ["state", "class_id", "ward_id"], unique=False
        )

    op.create_table(
        "audit_log",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("resource_type", sa.String(), nullable=False),
        sa.Column("resource_id", sa.String(), nullable=True),
        sa.Column(
            "detail",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("client", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("audit_log", schema=None) as batch_op:
        batch_op.create_index(
            "ix_audit_log_resource", ["resource_type", "resource_id", "at"], unique=False
        )

    op.create_table(
        "device",
        sa.Column("device_id", sa.String(), nullable=False),
        sa.Column("bus_id", sa.String(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reliability", sa.Float(), nullable=False),
        sa.Column("confirmed_contributions", sa.Integer(), nullable=False),
        sa.Column("rejected_contributions", sa.Integer(), nullable=False),
        sa.Column("flagged", sa.Boolean(), nullable=False),
        sa.Column("flag_reason", sa.Text(), nullable=True),
        sa.Column("flagged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("device_id"),
    )
    op.create_table(
        "evidence_check",
        sa.Column("uri", sa.String(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("message_id", sa.String(), nullable=False),
        sa.Column("device_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("uri"),
    )
    op.create_table(
        "gtfs_route",
        sa.Column("route_id", sa.String(), nullable=False),
        sa.Column("short_name", sa.String(), nullable=True),
        sa.Column("long_name", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("route_id"),
    )
    op.create_table(
        "gtfs_stop",
        sa.Column("stop_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("stop_id"),
    )
    op.create_table(
        "gtfs_stop_time",
        sa.Column("trip_id", sa.String(), nullable=False),
        sa.Column("stop_sequence", sa.Integer(), nullable=False),
        sa.Column("stop_id", sa.String(), nullable=False),
        sa.Column("arrival_s", sa.Integer(), nullable=True),
        sa.Column("departure_s", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("trip_id", "stop_sequence"),
    )
    with op.batch_alter_table("gtfs_stop_time", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_gtfs_stop_time_stop_id"), ["stop_id"], unique=False)

    op.create_table(
        "gtfs_trip",
        sa.Column("trip_id", sa.String(), nullable=False),
        sa.Column("route_id", sa.String(), nullable=False),
        sa.Column("service_id", sa.String(), nullable=True),
        sa.Column("direction_id", sa.Integer(), nullable=True),
        sa.Column("shape_id", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("trip_id"),
    )
    with op.batch_alter_table("gtfs_trip", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_gtfs_trip_route_id"), ["route_id"], unique=False)

    op.create_table(
        "incident",
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("device_id", sa.String(), nullable=False),
        sa.Column("bus_id", sa.String(), nullable=True),
        sa.Column("route_id", sa.String(), nullable=True),
        sa.Column("incident_type", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("severity", sa.String(), nullable=True),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("accuracy_m", sa.Float(), nullable=False),
        sa.Column("segment_id", sa.String(), nullable=True),
        sa.Column("ward_id", sa.String(), nullable=True),
        sa.Column(
            "ego",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "triggers",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "subject_vehicle",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "evidence",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "model",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("review_status", sa.String(), nullable=False),
        sa.Column("reviewed_by", sa.String(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("case_closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("incident_id"),
    )
    with op.batch_alter_table("incident", schema=None) as batch_op:
        batch_op.create_index("ix_incident_started", ["started_at"], unique=False)

    op.create_table(
        "ingest_rejection",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("device_id", sa.String(), nullable=True),
        sa.Column("topic", sa.String(), nullable=True),
        sa.Column("message_id", sa.String(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "observation",
        sa.Column("observation_id", sa.Uuid(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uplinked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("device_id", sa.String(), nullable=False),
        sa.Column("bus_id", sa.String(), nullable=True),
        sa.Column("route_id", sa.String(), nullable=True),
        sa.Column("trip_id", sa.String(), nullable=True),
        sa.Column("segment_pass_id", sa.Uuid(), nullable=True),
        sa.Column("camera_id", sa.String(), nullable=False),
        sa.Column("class_id", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("accuracy_m", sa.Float(), nullable=False),
        sa.Column("geo_source", sa.String(), nullable=False),
        sa.Column("segment_id", sa.String(), nullable=True),
        sa.Column("offset_m", sa.Float(), nullable=True),
        sa.Column("ward_id", sa.String(), nullable=True),
        sa.Column("map_match_confidence", sa.Float(), nullable=True),
        sa.Column("severity_score", sa.Float(), nullable=True),
        sa.Column("severity_band", sa.String(), nullable=True),
        sa.Column("area_m2", sa.Float(), nullable=True),
        sa.Column("range_m", sa.Float(), nullable=True),
        sa.Column(
            "conditions",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "geometry_m",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "evidence",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("evidence_uri", sa.String(), nullable=True),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=True),
        sa.Column("model_version", sa.String(), nullable=False),
        sa.Column(
            "payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("asset_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("observation_id", "captured_at"),
    )
    with op.batch_alter_table("observation", schema=None) as batch_op:
        batch_op.create_index("ix_observation_asset", ["asset_id"], unique=False)
        batch_op.create_index("ix_observation_id", ["observation_id"], unique=False)
        batch_op.create_index(
            "ix_observation_seg_class_time", ["segment_id", "class_id", "captured_at"], unique=False
        )

    op.create_table(
        "pass_processed",
        sa.Column("segment_pass_id", sa.Uuid(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("segment_pass_id"),
    )
    op.create_table(
        "segment_freeflow",
        sa.Column("segment_id", sa.String(), nullable=False),
        sa.Column("direction", sa.String(), nullable=False),
        sa.Column("freeflow_kmh", sa.Float(), nullable=False),
        sa.Column("n_passes", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("segment_id", "direction"),
    )
    op.create_table(
        "segment_pass",
        sa.Column("segment_pass_id", sa.Uuid(), nullable=False),
        sa.Column("entered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exited_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("device_id", sa.String(), nullable=False),
        sa.Column("bus_id", sa.String(), nullable=True),
        sa.Column("route_id", sa.String(), nullable=True),
        sa.Column("trip_id", sa.String(), nullable=True),
        sa.Column("segment_id", sa.String(), nullable=False),
        sa.Column("ward_id", sa.String(), nullable=True),
        sa.Column("direction", sa.String(), nullable=False),
        sa.Column("length_m", sa.Float(), nullable=True),
        sa.Column("mean_speed_kmh", sa.Float(), nullable=False),
        sa.Column("min_speed_kmh", sa.Float(), nullable=True),
        sa.Column("stopped_s", sa.Float(), nullable=True),
        sa.Column("dwell_excl_s", sa.Float(), nullable=True),
        sa.Column(
            "vehicle_counts",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("occupancy_ratio", sa.Float(), nullable=True),
        sa.Column("pcu_estimate", sa.Float(), nullable=True),
        sa.Column(
            "pedestrian",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "inspected_for",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "found",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("assessable_frac", sa.Float(), nullable=False),
        sa.Column(
            "conditions",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("model_version", sa.String(), nullable=True),
        sa.Column(
            "payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("segment_pass_id", "entered_at"),
    )
    with op.batch_alter_table("segment_pass", schema=None) as batch_op:
        batch_op.create_index("ix_segment_pass_id", ["segment_pass_id"], unique=False)
        batch_op.create_index(
            "ix_segment_pass_route_time", ["route_id", "entered_at"], unique=False
        )
        batch_op.create_index(
            "ix_segment_pass_seg_dir_time", ["segment_id", "direction", "entered_at"], unique=False
        )

    op.create_table(
        "telemetry",
        sa.Column("device_id", sa.String(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bus_id", sa.String(), nullable=True),
        sa.Column("route_id", sa.String(), nullable=True),
        sa.Column("trip_id", sa.String(), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column("accuracy_m", sa.Float(), nullable=True),
        sa.Column("speed_kmh", sa.Float(), nullable=True),
        sa.Column("heading", sa.Float(), nullable=True),
        sa.Column("next_stop_id", sa.String(), nullable=True),
        sa.Column("delay_s", sa.Integer(), nullable=True),
        sa.Column("headway_s", sa.Integer(), nullable=True),
        sa.Column(
            "health",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "model",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("device_id", "at"),
    )
    with op.batch_alter_table("telemetry", schema=None) as batch_op:
        batch_op.create_index("ix_telemetry_route_time", ["route_id", "at"], unique=False)

    op.create_table(
        "ward",
        sa.Column("ward_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "geometry",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("population", sa.Integer(), nullable=True),
        sa.Column("engineer_of_record", sa.String(), nullable=True),
        sa.Column("min_lon", sa.Float(), nullable=False),
        sa.Column("min_lat", sa.Float(), nullable=False),
        sa.Column("max_lon", sa.Float(), nullable=False),
        sa.Column("max_lat", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("ward_id"),
    )
    op.create_table(
        "asset_event",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("from_state", sa.String(), nullable=True),
        sa.Column("to_state", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["asset.asset_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("asset_event", schema=None) as batch_op:
        batch_op.create_index("ix_asset_event_asset_at", ["asset_id", "at"], unique=False)

    op.create_table(
        "asset_negative",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("segment_pass_id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.String(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quality", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["asset.asset_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asset_id", "segment_pass_id"),
    )
    with op.batch_alter_table("asset_negative", schema=None) as batch_op:
        batch_op.create_index("ix_asset_negative_asset_at", ["asset_id", "at"], unique=False)

    op.create_table(
        "hard_negative",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("class_id", sa.String(), nullable=False),
        sa.Column(
            "observation_ids",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "evidence",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("rejected_by", sa.String(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exported_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["asset.asset_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "road_segment",
        sa.Column("segment_id", sa.String(), nullable=False),
        sa.Column("osm_way_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "coordinates",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("highway", sa.String(), nullable=True),
        sa.Column("oneway", sa.Boolean(), nullable=False),
        sa.Column("maxspeed_kmh", sa.Integer(), nullable=True),
        sa.Column("length_m", sa.Float(), nullable=False),
        sa.Column("ward_id", sa.String(), nullable=True),
        sa.Column("has_signal", sa.Boolean(), nullable=False),
        sa.Column("freeflow_kmh", sa.Float(), nullable=True),
        sa.Column("min_lon", sa.Float(), nullable=False),
        sa.Column("min_lat", sa.Float(), nullable=False),
        sa.Column("max_lon", sa.Float(), nullable=False),
        sa.Column("max_lat", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["ward_id"],
            ["ward.ward_id"],
        ),
        sa.PrimaryKeyConstraint("segment_id"),
    )
    with op.batch_alter_table("road_segment", schema=None) as batch_op:
        batch_op.create_index(
            "ix_road_segment_bbox", ["min_lon", "max_lon", "min_lat", "max_lat"], unique=False
        )

    op.create_table(
        "work_order",
        sa.Column("reference", sa.String(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("ward_id", sa.String(), nullable=True),
        sa.Column("class_id", sa.String(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("department", sa.String(), nullable=False),
        sa.Column("severity_band", sa.String(), nullable=False),
        sa.Column("priority_score", sa.Float(), nullable=False),
        sa.Column("priority_rank", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sla_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["asset.asset_id"],
        ),
        sa.PrimaryKeyConstraint("reference"),
    )
    with op.batch_alter_table("work_order", schema=None) as batch_op:
        batch_op.create_index("ix_work_order_ward_status", ["ward_id", "status"], unique=False)

    op.create_table(
        "expected_asset",
        sa.Column("expected_id", sa.String(), nullable=False),
        sa.Column("class_id", sa.String(), nullable=False),
        sa.Column("segment_id", sa.String(), nullable=True),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_positive_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["segment_id"],
            ["road_segment.segment_id"],
        ),
        sa.PrimaryKeyConstraint("expected_id"),
    )
    op.create_table(
        "ledger_absence",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("expected_id", sa.String(), nullable=False),
        sa.Column("segment_pass_id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.String(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["expected_id"],
            ["expected_asset.expected_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("expected_id", "segment_pass_id"),
    )
    if op.get_bind().dialect.name == "postgresql":
        _postgres_upgrade()


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        _postgres_downgrade()
    op.drop_table("ledger_absence")
    op.drop_table("expected_asset")
    with op.batch_alter_table("work_order", schema=None) as batch_op:
        batch_op.drop_index("ix_work_order_ward_status")

    op.drop_table("work_order")
    with op.batch_alter_table("road_segment", schema=None) as batch_op:
        batch_op.drop_index("ix_road_segment_bbox")

    op.drop_table("road_segment")
    op.drop_table("hard_negative")
    with op.batch_alter_table("asset_negative", schema=None) as batch_op:
        batch_op.drop_index("ix_asset_negative_asset_at")

    op.drop_table("asset_negative")
    with op.batch_alter_table("asset_event", schema=None) as batch_op:
        batch_op.drop_index("ix_asset_event_asset_at")

    op.drop_table("asset_event")
    op.drop_table("ward")
    with op.batch_alter_table("telemetry", schema=None) as batch_op:
        batch_op.drop_index("ix_telemetry_route_time")

    op.drop_table("telemetry")
    with op.batch_alter_table("segment_pass", schema=None) as batch_op:
        batch_op.drop_index("ix_segment_pass_seg_dir_time")
        batch_op.drop_index("ix_segment_pass_route_time")
        batch_op.drop_index("ix_segment_pass_id")

    op.drop_table("segment_pass")
    op.drop_table("segment_freeflow")
    op.drop_table("pass_processed")
    with op.batch_alter_table("observation", schema=None) as batch_op:
        batch_op.drop_index("ix_observation_seg_class_time")
        batch_op.drop_index("ix_observation_id")
        batch_op.drop_index("ix_observation_asset")

    op.drop_table("observation")
    op.drop_table("ingest_rejection")
    with op.batch_alter_table("incident", schema=None) as batch_op:
        batch_op.drop_index("ix_incident_started")

    op.drop_table("incident")
    with op.batch_alter_table("gtfs_trip", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_gtfs_trip_route_id"))

    op.drop_table("gtfs_trip")
    with op.batch_alter_table("gtfs_stop_time", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_gtfs_stop_time_stop_id"))

    op.drop_table("gtfs_stop_time")
    op.drop_table("gtfs_stop")
    op.drop_table("gtfs_route")
    op.drop_table("evidence_check")
    op.drop_table("device")
    with op.batch_alter_table("audit_log", schema=None) as batch_op:
        batch_op.drop_index("ix_audit_log_resource")

    op.drop_table("audit_log")
    with op.batch_alter_table("asset", schema=None) as batch_op:
        batch_op.drop_index("ix_asset_state_class_ward")
        batch_op.drop_index("ix_asset_seg_class")
        batch_op.drop_index("ix_asset_latlon")

    op.drop_table("asset")
    op.drop_table("app_user")


# ─── PostgreSQL: PostGIS, TimescaleDB ───────────────────────────────────────

POINT_TABLES = ("observation", "telemetry", "incident", "asset", "expected_asset")
HYPERTABLES = (
    ("observation", "captured_at", "1 day"),
    ("segment_pass", "entered_at", "1 day"),
    ("telemetry", "at", "6 hours"),
)

# Must stay identical to argus_api.analytics.common.corrected_speed_kmh.
CORRECTED_SPEED = """
    LEAST(120.0, CASE
        WHEN length_m > 0
             AND EXTRACT(EPOCH FROM (exited_at - entered_at)) - COALESCE(dwell_excl_s, 0) > 1
        THEN length_m / (EXTRACT(EPOCH FROM (exited_at - entered_at))
                         - COALESCE(dwell_excl_s, 0)) * 3.6
        ELSE mean_speed_kmh END)
"""
VEHICLE_CLASSES = ("car", "two_wheeler", "auto_rickshaw", "bus", "truck", "lcv", "bicycle")


def _postgres_upgrade() -> None:
    from argus_api.core.config import get_settings

    cfg = get_settings()
    for ext in ("postgis", "timescaledb"):
        op.execute(f"CREATE EXTENSION IF NOT EXISTS {ext}")

    # Point geometry generated from lat/lon, so the ORM never has to write it.
    for t in POINT_TABLES:
        op.execute(
            f"ALTER TABLE {t} ADD COLUMN geom geometry(Point, 4326) GENERATED ALWAYS AS "
            f"(ST_SetSRID(ST_MakePoint(lon, lat), 4326)) STORED"
        )
        op.execute(f"CREATE INDEX ix_geom_{t} ON {t} USING GIST (geom)")

    # Lines and polygons are maintained from the GeoJSON columns by trigger.
    op.execute("ALTER TABLE road_segment ADD COLUMN geom geometry(LineString, 4326)")
    op.execute("ALTER TABLE ward ADD COLUMN geom geometry(MultiPolygon, 4326)")
    op.execute("""
        CREATE FUNCTION argus_road_segment_geom() RETURNS trigger AS $$
        BEGIN
          NEW.geom := ST_SetSRID(ST_GeomFromGeoJSON(json_build_object(
                        'type', 'LineString', 'coordinates', NEW.coordinates)::text), 4326);
          RETURN NEW;
        END $$ LANGUAGE plpgsql""")
    op.execute("""
        CREATE FUNCTION argus_ward_geom() RETURNS trigger AS $$
        BEGIN
          NEW.geom := ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(NEW.geometry::text), 4326));
          RETURN NEW;
        END $$ LANGUAGE plpgsql""")
    op.execute(
        "CREATE TRIGGER trg_road_segment_geom BEFORE INSERT OR UPDATE OF coordinates "
        "ON road_segment FOR EACH ROW EXECUTE FUNCTION argus_road_segment_geom()"
    )
    op.execute(
        "CREATE TRIGGER trg_ward_geom BEFORE INSERT OR UPDATE OF geometry "
        "ON ward FOR EACH ROW EXECUTE FUNCTION argus_ward_geom()"
    )
    op.execute("CREATE INDEX ix_geom_road_segment ON road_segment USING GIST (geom)")
    op.execute("CREATE INDEX ix_geom_ward ON ward USING GIST (geom)")

    # Raw tables become hypertables: immutable, append-only, chunked by time.
    for table, col, chunk in HYPERTABLES:
        op.execute(
            f"SELECT create_hypertable('{table}', '{col}', "
            f"chunk_time_interval => INTERVAL '{chunk}', migrate_data => true)"
        )

    # Continuous aggregates: the single feature that keeps heat maps fast at fleet scale.
    # Sums and counts only — never a pre-divided rate, which could not be re-aggregated.
    vehicles = " + ".join(f"COALESCE((vehicle_counts->>'{c}')::int, 0)" for c in VEHICLE_CLASSES)
    op.execute(f"""
        CREATE MATERIALIZED VIEW congestion_15min
        WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
        SELECT time_bucket(INTERVAL '15 minutes', entered_at)                AS bucket,
               segment_id,
               direction,
               count(*)::int                                                  AS passes,
               sum({CORRECTED_SPEED})                                         AS speed_sum,
               COALESCE(sum(occupancy_ratio), 0)                              AS occupancy_sum,
               count(occupancy_ratio)::int                                    AS occupancy_n,
               COALESCE(sum(pcu_estimate), 0)                                 AS pcu_sum,
               count(pcu_estimate)::int                                       AS pcu_n,
               COALESCE(sum(stopped_s), 0)                                    AS stopped_sum,
               COALESCE(sum(dwell_excl_s), 0)                                 AS dwell_sum,
               COALESCE(sum((vehicle_counts->>'two_wheeler')::int), 0)::int   AS two_wheeler,
               COALESCE(sum({vehicles}), 0)::int                              AS vehicles
        FROM segment_pass
        GROUP BY 1, 2, 3
        WITH NO DATA""")
    op.execute("""
        CREATE MATERIALIZED VIEW pedestrian_density_hourly
        WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
        SELECT time_bucket(INTERVAL '1 hour', entered_at)                      AS bucket,
               segment_id,
               count(*)::int                                                 AS passes,
               COALESCE(sum((pedestrian->>'unique_tracks')::int), 0)::int    AS pedestrians,
               COALESCE(sum((pedestrian->>'observed_area_m2')::float8), 0)   AS observed_area_m2,
               COALESCE(sum((pedestrian->>'crossing_events')::int), 0)::int  AS crossings,
               COALESCE(max((pedestrian->>'cluster_max')::int), 0)::int      AS cluster_max,
               sum(CASE WHEN (pedestrian->>'in_school_zone')::boolean
                        THEN 1 ELSE 0 END)::int                              AS school_zone_passes
        FROM segment_pass
        WHERE pedestrian IS NOT NULL
        GROUP BY 1, 2
        WITH NO DATA""")
    for view in ("congestion_15min", "pedestrian_density_hourly"):
        op.execute(
            f"SELECT add_continuous_aggregate_policy('{view}', "
            f"start_offset => INTERVAL '3 days', end_offset => INTERVAL '15 minutes', "
            f"schedule_interval => INTERVAL '5 minutes')"
        )

    # Retention (docs/08 §5). Raw rows expire; the aggregates above hold no personal data
    # and are kept indefinitely.
    for table, days in (
        ("observation", cfg.retain_observation_days),
        ("segment_pass", cfg.retain_segment_pass_days),
        ("telemetry", cfg.retain_telemetry_days),
    ):
        op.execute(f"SELECT add_retention_policy('{table}', INTERVAL '{days} days')")


def _postgres_downgrade() -> None:
    for view in ("pedestrian_density_hourly", "congestion_15min"):
        op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {view} CASCADE")
    op.execute("DROP TRIGGER IF EXISTS trg_road_segment_geom ON road_segment")
    op.execute("DROP TRIGGER IF EXISTS trg_ward_geom ON ward")
    op.execute("DROP FUNCTION IF EXISTS argus_road_segment_geom()")
    op.execute("DROP FUNCTION IF EXISTS argus_ward_geom()")
