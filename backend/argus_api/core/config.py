"""Runtime configuration. Every value is overridable with an ``ARGUS_`` environment variable.

Fusion thresholds live here rather than as constants because docs/04 commits to them being
configurable per deployment, and because the operator-rejection rate after week 6 is what
tunes ``confirm_probability``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ARGUS_", env_file=".env", extra="ignore")

    # ── Infrastructure ───────────────────────────────────────────────────────
    database_url: str = "postgresql+psycopg://argus:argus@127.0.0.1:5432/argus"
    # Empty means "use the in-process bus". Fine for a single process and for tests;
    # a split ingest/fusion/API deployment needs Redis so events cross processes.
    redis_url: str = ""
    mqtt_host: str = "127.0.0.1"
    mqtt_port: int = 1883
    mqtt_fleet: str = "+"
    mqtt_client_id: str = "argus-ingest"
    mqtt_tls: bool = False
    mqtt_ca_file: str | None = None
    mqtt_cert_file: str | None = None
    mqtt_key_file: str | None = None

    s3_endpoint_url: str | None = "http://127.0.0.1:9000"
    s3_access_key: str = "argus"
    s3_secret_key: str = "argusargus"
    s3_region: str = "us-east-1"
    signed_url_ttl_s: int = 300

    contracts_dir: Path = _REPO_ROOT / "contracts" / "schemas"
    supported_schema_major: int = 1

    # ── Auth ─────────────────────────────────────────────────────────────────
    jwt_secret: str = "change-me-in-any-real-deployment"
    jwt_algorithm: str = "HS256"
    jwt_ttl_minutes: int = 12 * 60
    # When set, unauthenticated requests act with this role (e.g. "viewer" for a kiosk
    # dashboard). Audited endpoints still demand a named identity regardless.
    anonymous_role: str | None = None
    # Run the fusion loop inside the API process. One command for a demo laptop; a real
    # deployment runs `python -m argus_api.fusion.worker` separately.
    embedded_fusion: bool = False
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://localhost:4173"]
    )

    # ── Ingest ───────────────────────────────────────────────────────────────
    # required: a crop that cannot be fetched is rejected.
    # best_effort: unfetchable evidence is stored as pending and re-checked by a job.
    # off: hashes are recorded but never recomputed (simulation / replay without MinIO).
    evidence_verification: str = "best_effort"

    # ── Fusion (docs/04) ─────────────────────────────────────────────────────
    cluster_eps_m: float = 8.0
    cluster_min_samples: int = 1
    correlation_discount: float = 0.5
    # No single observation may claim more than 9:1 odds. A single frame cannot rule out
    # the systematic errors (a shadow, a tar patch) that only corroboration can, and this
    # cap is what makes three independent 0.70s outrank one isolated 0.95.
    max_observation_probability: float = 0.90
    negative_evidence_probability: float = 0.75
    position_sigma_floor_m: float = 2.0
    confirm_distinct_devices: int = 2
    confirm_probability: float = 0.85
    resolve_negative_passes: int = 3
    resolve_distinct_devices: int = 2
    # Likelihood ratio a run of misses must reach before "gone" is asserted: 3 nats ≈ 20:1.
    # Three clean daylight passes from different buses clear it; murky ones need more.
    resolve_evidence_nats: float = 3.0
    # Per-pass recall of a present, in-view defect under ideal conditions (from the model
    # card's validation report). Scaled per pass by conditions and device reliability.
    detector_recall: float = 0.85
    stale_after_days: int = 30
    min_assessable_fraction: float = 0.7
    max_negative_occlusion: float = 0.6
    auto_issue_work_orders: bool = True
    reopen_window_days: int = 90
    default_device_reliability: float = 0.9

    # ── Road Asset Ledger ────────────────────────────────────────────────────
    ledger_min_absences: int = 10
    ledger_min_devices: int = 3
    ledger_presence_radius_m: float = 15.0

    # ── Analytics (docs/07) ──────────────────────────────────────────────────
    timezone: str = "Asia/Kolkata"
    freeflow_min_passes: int = 50
    freeflow_window_days: int = 30
    congestion_low_confidence_passes: int = 3
    # The contracts carry no cabin-occupancy figure yet, so passenger-hours lost uses an
    # assumed mean load and says so in every response.
    assumed_bus_occupancy: float = 40.0
    unsurveyed_after_days: int = 35

    # ── Retention (docs/08) ──────────────────────────────────────────────────
    retain_evidence_days: int = 90
    retain_incident_days: int = 365
    retain_observation_days: int = 180
    retain_segment_pass_days: int = 30
    retain_telemetry_days: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
