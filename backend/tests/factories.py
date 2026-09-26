"""Contract-valid message builders for tests. Defaults mirror the worked examples in docs/06."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from argus_api.db.types import iso

BASE_TIME = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
SEGMENT = "osm:way/23847561:3"
WARD = "bbmp:150"
LAT, LON = 12.96183, 77.63094


def sha(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def observation(
    *,
    device: str = "BLR-BUS-4417-EDGE",
    cls: str = "pothole",
    at: datetime = BASE_TIME,
    confidence: float = 0.9,
    lat: float = LAT,
    lon: float = LON,
    accuracy: float = 5.2,
    segment: str | None = SEGMENT,
    ward: str | None = WARD,
    severity: float = 0.44,
    area: float = 0.38,
    quality: float = 0.8,
    faces_blurred: bool | None = True,
    evidence: bool = True,
    conditions: dict | None = None,
    pass_id: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    oid = str(uuid.uuid4())
    m: dict[str, Any] = {
        "schema_version": "1.0.0",
        "observation_id": oid,
        "device_id": device,
        "bus_id": "KA01FA" + device[8:12] if len(device) > 12 else "KA01FA0000",
        "route_id": "500D",
        "camera_id": "front",
        "class_id": cls,
        "captured_at": iso(at),
        "uplinked_at": iso(at + timedelta(seconds=3)),
        "confidence": confidence,
        "geo": {"lat": lat, "lon": lon, "accuracy_m": accuracy, "source": "gnss+ipm+mapmatch"},
        "ego": {"speed_kmh": 17.2, "heading_deg": 118.4},
        "geometry": {"mask_area_m2": area, "extent_m": [0.7, 0.5], "range_m": 11.4},
        "severity": {"score": severity, "band": "medium"},
        "conditions": conditions
        or {"illumination": "day", "weather": "clear", "occlusion": 0.08, "motion_blur": 0.12},
        "model": {"name": "argus-road-defect", "version": "0.4.2", "runtime": "tensorrt-int8"},
    }
    if segment:
        m["map_match"] = {"segment_id": segment, "offset_m": 127.4, "confidence": 0.94}
        if ward:
            m["map_match"]["ward_id"] = ward
    if pass_id:
        m["segment_pass_id"] = pass_id
    if evidence:
        ev: dict[str, Any] = {
            "uri": f"s3://argus-evidence/test/obs-{oid}.jpg",
            "sha256": sha(oid),
            "bytes": 46218,
            "quality": quality,
        }
        if faces_blurred is not None:
            ev["faces_blurred"] = faces_blurred
        m["evidence"] = ev
    m.update(extra)
    return m


def segment_pass(
    *,
    device: str = "BLR-BUS-4417-EDGE",
    segment: str = SEGMENT,
    ward: str | None = WARD,
    at: datetime = BASE_TIME,
    duration_s: float = 53.6,
    length_m: float = 248.0,
    speed: float = 16.6,
    inspected: list[str] | None = None,
    found: list[str] | None = None,
    assessable: float = 0.87,
    occlusion: float = 0.14,
    illumination: str = "day",
    direction: str = "forward",
    dwell: float = 0.0,
    stopped: float = 0.0,
    pedestrians: int | None = 23,
    area_m2: float = 1840.0,
    occupancy: float | None = 0.42,
    route: str = "500D",
    counts: dict | None = None,
    school: bool = False,
) -> dict[str, Any]:
    m: dict[str, Any] = {
        "schema_version": "1.0.0",
        "segment_pass_id": str(uuid.uuid4()),
        "device_id": device,
        "route_id": route,
        "segment_id": segment,
        "direction": direction,
        "entered_at": iso(at),
        "exited_at": iso(at + timedelta(seconds=duration_s)),
        "length_m": length_m,
        "traffic": {
            "mean_speed_kmh": speed,
            "stopped_seconds": stopped,
            "dwell_excluded_seconds": dwell,
            "vehicle_counts": counts
            or {"car": 31, "two_wheeler": 58, "auto_rickshaw": 14, "bus": 3},
            "pcu_estimate": 79.6,
        },
        "inspection": {
            "inspected_for": inspected if inspected is not None else ["pothole", "faded_zebra"],
            "found": found if found is not None else [],
            "assessable_fraction": assessable,
        },
        "conditions": {
            "illumination": illumination,
            "weather": "clear",
            "occlusion": occlusion,
            "motion_blur": 0.1,
        },
    }
    if occupancy is not None:
        m["traffic"]["occupancy_ratio"] = occupancy
    if ward:
        m["ward_id"] = ward
    if pedestrians is not None:
        m["pedestrian"] = {
            "unique_tracks": pedestrians,
            "observed_area_m2": area_m2,
            "crossing_events": 4,
            "cluster_max": 7,
            "in_school_zone": school,
        }
    return m


def incident(
    *, device: str = "BLR-BUS-4417-EDGE", at: datetime = BASE_TIME, plate: bool = True
) -> dict[str, Any]:
    iid = str(uuid.uuid4())
    m: dict[str, Any] = {
        "schema_version": "1.0.0",
        "incident_id": iid,
        "device_id": device,
        "bus_id": "KA01FA4417",
        "route_id": "500D",
        "incident_type": "rash_driving",
        "started_at": iso(at),
        "ended_at": iso(at + timedelta(seconds=4.6)),
        "confidence": 0.88,
        "severity": "critical",
        "geo": {"lat": 12.95871, "lon": 77.63902, "accuracy_m": 4.8, "source": "gnss"},
        "map_match": {"segment_id": SEGMENT, "offset_m": 61.0, "ward_id": WARD, "confidence": 0.91},
        "triggers": {"ttc_s": 0.62, "lateral_accel_ms2": 4.2, "lane_changes": 3},
        "evidence": {
            "clip_uri": f"s3://argus-evidence/incidents/{iid}.mp4",
            "sha256": sha(iid),
            "duration_s": 30.0,
            "cameras": ["front", "rear"],
            "plate_crop_uri": f"s3://argus-evidence/incidents/{iid}-plate.jpg",
            "faces_blurred": True,
            "encrypted": True,
        },
    }
    if plate:
        m["subject_vehicle"] = {
            "track_id": "T-00418",
            "vehicle_class": "car",
            "colour": "white",
            "plate": {
                "text": "KA 05 MJ 7213",
                "confidence": 0.89,
                "per_character_confidence": [
                    0.98,
                    0.91,
                    0.96,
                    0.94,
                    0.72,
                    0.95,
                    0.97,
                    0.93,
                    0.96,
                    0.61,
                ],
                "frames_voted": 22,
                "state_code_valid": True,
            },
        }
    return m


def telemetry(
    *,
    device: str = "BLR-BUS-4417-EDGE",
    at: datetime = BASE_TIME,
    lat: float = 12.9619,
    lon: float = 77.63081,
    delay: int = 214,
    headway: int = 640,
    route: str = "500D",
    stop: str = "bmtc:4412",
    quality: float = 0.93,
    temp: float = 68.4,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "device_id": device,
        "bus_id": "KA01FA4417",
        "route_id": route,
        "at": iso(at),
        "geo": {"lat": lat, "lon": lon, "accuracy_m": 4.1, "source": "gnss"},
        "ego": {"speed_kmh": 17.2, "heading_deg": 118.4},
        "schedule": {"next_stop_id": stop, "delay_s": delay, "headway_s": headway},
        "health": {
            "uptime_s": 18442,
            "queue_depth": 7,
            "cpu_pct": 58,
            "temp_c": temp,
            "inference_fps": 14.2,
            "cameras_online": ["front", "rear"],
            "camera_quality": {"front": quality, "rear": 0.88},
            "gnss_fix": "3d",
            "link": "4g",
            "uplink_kb_session": 3184,
        },
    }
