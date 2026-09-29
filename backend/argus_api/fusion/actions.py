"""Per-class policy: what a municipality is supposed to *do* about each detection.

Adding a class (docs/02, "Adding a class") means adding a row here: its recommended action,
the department that owns the remedy, its weight in the ward Infrastructure Deficiency Index,
and whether it is edge-emitted or ledger-inferred.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class ClassPolicy:
    class_id: str
    action: str
    department: str
    # Weight in the ward IDI and the road-condition score (docs/07 §4–5).
    weight: float
    # Prior probability that a location the detector fired on really has the thing.
    prior: float = 0.5
    # Surface classes feed the segment road-condition score.
    surface: bool = False
    # Reference area for the condition score's min(1, area / A_ref) term.
    area_ref_m2: float = 1.0
    # Ledger-inferred classes are never accepted from the edge.
    ledger_inferred: bool = False
    # For missing_*: the edge class whose inspection and observations are presence evidence.
    presence_class: str | None = None
    # Severity floor. open_manhole is always critical.
    min_band: str | None = None


POLICIES: dict[str, ClassPolicy] = {
    p.class_id: p
    for p in [
        ClassPolicy(
            "pothole",
            "Raise patching work order to ward engineer",
            "roads",
            weight=1.0,
            surface=True,
            area_ref_m2=1.0,
        ),
        ClassPolicy(
            "damaged_road",
            "Schedule resurfacing survey for this segment",
            "roads",
            weight=0.8,
            surface=True,
            area_ref_m2=10.0,
        ),
        ClassPolicy(
            "waterlogging",
            "Dispatch de-silting crew; flag drain blockage",
            "drainage",
            weight=1.0,
            surface=True,
            area_ref_m2=10.0,
        ),
        ClassPolicy("road_debris", "Dispatch clearance crew", "solid_waste", weight=0.6),
        ClassPolicy(
            "open_manhole",
            "Cordon and cover immediately; notify water board",
            "water_board",
            weight=2.0,
            min_band="critical",
        ),
        ClassPolicy("damaged_divider", "Raise divider repair order", "roads", weight=0.6),
        ClassPolicy(
            "missing_divider",
            "Survey and reinstate median divider",
            "roads",
            weight=0.8,
            ledger_inferred=True,
            presence_class="damaged_divider",
        ),
        ClassPolicy(
            "faded_zebra",
            "Raise repainting order for pedestrian crossing",
            "traffic_engineering",
            weight=0.7,
        ),
        ClassPolicy(
            "missing_zebra",
            "Urgent: reinstate pedestrian crossing markings",
            "traffic_engineering",
            weight=1.2,
            ledger_inferred=True,
            presence_class="faded_zebra",
        ),
        ClassPolicy(
            "damaged_sign",
            "Raise signage repair order (or pruning, if occluded)",
            "traffic_engineering",
            weight=0.5,
        ),
        ClassPolicy(
            "missing_sign",
            "Reinstate traffic sign",
            "traffic_engineering",
            weight=0.8,
            ledger_inferred=True,
            presence_class="damaged_sign",
        ),
        ClassPolicy(
            "encroachment",
            "Refer to enforcement for carriageway encroachment",
            "enforcement",
            weight=0.5,
        ),
        ClassPolicy(
            "pedestrian_risk",
            "Assess for warden posting during school hours",
            "traffic_police",
            weight=0.9,
        ),
    ]
}

LEDGER_CLASSES = {c for c, p in POLICIES.items() if p.ledger_inferred}
# edge class -> the missing_* class its absence can raise
PRESENCE_TO_MISSING = {p.presence_class: c for c, p in POLICIES.items() if p.presence_class}

# docs/04 "Work orders": SLA by severity band.
SLA_BY_BAND: dict[str, timedelta] = {
    "critical": timedelta(hours=24),
    "high": timedelta(hours=72),
    "medium": timedelta(days=7),
    "low": timedelta(days=30),
}

BAND_ORDER = ["low", "medium", "high", "critical"]


def policy(class_id: str) -> ClassPolicy:
    try:
        return POLICIES[class_id]
    except KeyError:  # a class added to the contract but not here yet
        return ClassPolicy(class_id, "Review manually", "unassigned", weight=0.5)


def band_for(score: float | None, class_id: str) -> str:
    """Severity band from a 0–1 score, respecting the class's floor."""
    if score is None:
        band = "medium"
    elif score < 0.25:
        band = "low"
    elif score < 0.5:
        band = "medium"
    elif score < 0.75:
        band = "high"
    else:
        band = "critical"
    floor = policy(class_id).min_band
    if floor and BAND_ORDER.index(floor) > BAND_ORDER.index(band):
        band = floor
    return band


def band_at_least(band: str | None, minimum: str) -> bool:
    return band is not None and BAND_ORDER.index(band) >= BAND_ORDER.index(minimum)
