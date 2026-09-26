"""The one-page incident packet (docs/04 "Incident reports").

The triggers block is what makes the report reviewable rather than merely assertive, and the
per-character plate confidence tells the reviewer exactly which character to check.
"""

from __future__ import annotations

from argus_api.db.models import EvidenceCheck, Incident
from argus_api.db.types import iso, utcnow
from argus_api.reports.pdf import Document

TRIGGER_LABELS = {
    "ttc_s": ("Minimum time-to-collision", "s"),
    "lateral_accel_ms2": ("Peak lateral acceleration", "m/s²"),
    "lane_changes": ("Lane changes in window", ""),
    "ego_impact_g": ("Bus IMU impact", "g"),
    "track_discontinuity": ("Track discontinuity", ""),
    "departure_speed_kmh": ("Departure speed", "km/h"),
    "vru_proximity_m": ("Closest approach to pedestrian/cyclist", "m"),
}


def render(inc: Incident, custody: list[EvidenceCheck], generated_for: str) -> bytes:
    doc = Document()
    p = doc.page
    p.text("ARGUS — Incident report", size=16, bold=True)
    p.text(
        f"Generated {iso(utcnow())} for {generated_for}. Access to this report is logged.", size=8
    )
    p.rule()

    p.text("Event", size=12, bold=True)
    p.kv("Incident ID", inc.incident_id)
    p.kv("Type", inc.incident_type.replace("_", " "))
    p.kv("Window", f"{iso(inc.started_at)} to {iso(inc.ended_at)}")
    p.kv("Classifier confidence", f"{inc.confidence:.2f}")
    p.kv("Severity", inc.severity or "—")
    p.kv("Location", f"{inc.lat:.6f}, {inc.lon:.6f} (±{inc.accuracy_m:.1f} m)")
    p.kv("Road segment", inc.segment_id or "—")
    p.kv("Ward", inc.ward_id or "—")
    p.kv(
        "Reporting unit", f"{inc.device_id} on bus {inc.bus_id or '—'}, route {inc.route_id or '—'}"
    )
    p.gap()

    p = doc.page
    p.text("Why the system concluded this (kinematic triggers)", size=12, bold=True)
    for key, value in (inc.triggers or {}).items():
        label, unit = TRIGGER_LABELS.get(key, (key, ""))
        p.kv(label, f"{value} {unit}".strip())
    p.gap()

    subject = inc.subject_vehicle or {}
    plate = subject.get("plate") or {}
    p = doc.page
    p.text("Subject vehicle", size=12, bold=True)
    p.kv("Class", subject.get("vehicle_class", "—"))
    p.kv("Colour", subject.get("colour", "—"))
    if plate:
        p.kv("Registration (ANPR, per-character vote)", plate.get("text", "—"))
        p.kv("Overall plate confidence", plate.get("confidence", "—"))
        pcc = plate.get("per_character_confidence") or []
        chars = [c for c in (plate.get("text") or "") if c != " "]
        if pcc:
            cells = [f"{chars[i] if i < len(chars) else '?'}={c:.2f}" for i, c in enumerate(pcc)]
            p.kv("Per-character confidence", "  ".join(cells))
            weak = [f"position {i + 1}" for i, c in enumerate(pcc) if c < 0.8]
            if weak:
                p.text(
                    "Verify against the registry: " + ", ".join(weak) + " below 0.80.",
                    size=9,
                    indent=10,
                )
        p.kv("Frames voted", plate.get("frames_voted", "—"))
        p.kv("State code valid", plate.get("state_code_valid", "—"))
    else:
        p.text("No registration read.", size=10)
    p.gap()

    p = doc.page
    p.text("Evidence and chain of custody", size=12, bold=True)
    ev = inc.evidence or {}
    p.kv("Clip", ev.get("clip_uri", "—"))
    p.kv("Clip SHA-256 (computed on-device)", ev.get("sha256", "—"))
    p.kv("Plate crop", ev.get("plate_crop_uri", "—"))
    p.kv("Faces blurred on-device", ev.get("faces_blurred", "—"))
    p.kv("Encrypted at rest", ev.get("encrypted", "—"))
    for c in custody:
        p.kv(
            f"Store verification ({c.kind})",
            f"{c.status}" + (f" at {iso(c.checked_at)}" if c.checked_at else ""),
        )
    p.gap()
    p = doc.page
    p.rule()
    p.text(
        "ARGUS produces evidence for an enforcement process; it does not adjudicate. "
        "Review status: " + inc.review_status + ".",
        size=8,
    )
    return doc.render()
