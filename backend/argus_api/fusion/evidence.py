"""Multi-pass evidence fusion — the maths, with no database in sight.

Everything here is a pure function of its inputs so the invariants in backend/README can be
property-tested directly (tests/test_fusion_properties.py). This is the component where a bug
produces plausible-looking wrong answers rather than a crash, so it is kept small and
side-effect free on purpose.

    L  = logit(prior) + Σ_i  w_i · min(logit(p_i), cap)  −  Σ_j  v_j · logit(p_neg)
    w_i = r_device × q_conditions × a_range × γ^(n_d − 1)

See docs/04 "Multi-pass evidence fusion" for the reasoning behind each factor.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from argus_api.core.geo import LocalProjection

_EPS = 1e-9

ILLUMINATION_FACTOR = {"day": 1.0, "dusk": 0.75, "night": 0.45, "tunnel": 0.6, "glare": 0.5}
WEATHER_FACTOR = {"clear": 1.0, "rain": 0.8, "heavy-rain": 0.6, "fog": 0.6, "unknown": 0.9}


def logit(p: float) -> float:
    p = min(max(p, _EPS), 1 - _EPS)
    return math.log(p / (1 - p))


def sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def condition_quality(conditions: Mapping | None) -> float:
    """``q_conditions``: illumination × weather × (1 − occlusion) × (1 − motion_blur)."""
    c = conditions or {}
    q = ILLUMINATION_FACTOR.get(c.get("illumination", "day"), 0.7)
    q *= WEATHER_FACTOR.get(c.get("weather", "unknown"), 0.9)
    q *= 1.0 - float(c.get("occlusion") or 0.0)
    q *= 1.0 - float(c.get("motion_blur") or 0.0)
    return min(1.0, max(0.05, q))


def range_attenuation(range_m: float | None) -> float:
    """``a_range``: full weight inside 10 m, half at 25 m, floored at 0.3 beyond ~35 m."""
    if range_m is None or range_m <= 10.0:
        return 1.0
    return max(0.3, 1.0 - 0.5 * (range_m - 10.0) / 15.0)


@dataclass(frozen=True)
class FusionParams:
    correlation_discount: float = 0.5
    max_observation_probability: float = 0.90
    negative_evidence_probability: float = 0.75
    position_sigma_floor_m: float = 2.0
    detector_recall: float = 0.85

    @classmethod
    def from_settings(cls, s) -> FusionParams:
        return cls(
            correlation_discount=s.correlation_discount,
            max_observation_probability=s.max_observation_probability,
            negative_evidence_probability=s.negative_evidence_probability,
            position_sigma_floor_m=s.position_sigma_floor_m,
            detector_recall=s.detector_recall,
        )


@dataclass(frozen=True)
class PositiveEvidence:
    device_id: str
    captured_at: datetime
    confidence: float
    lat: float
    lon: float
    accuracy_m: float
    quality: float = 1.0  # q_conditions × a_range
    reliability: float = 1.0
    severity: float | None = None
    area_m2: float | None = None
    evidence: Mapping | None = None
    observation_id: str | None = None
    segment_id: str | None = None
    offset_m: float | None = None
    ward_id: str | None = None
    map_match_confidence: float | None = None


@dataclass(frozen=True)
class NegativeEvidence:
    device_id: str
    at: datetime
    quality: float  # q_conditions × assessable_fraction
    reliability: float = 1.0


@dataclass
class FusedState:
    log_odds: float
    confidence: float
    lat: float
    lon: float
    accuracy_m: float
    evidence_count: int
    distinct_devices: int
    negative_passes: int
    negative_devices: int
    # Evidence that the thing is gone, in nats: Σ γ^(n_d−1) · −ln(1 − recall · r · q) over
    # misses since the last sighting. See ``repair_evidence``.
    repair_evidence_nats: float
    severity_score: float | None
    severity_trend: str
    area_m2: float | None
    first_seen_at: datetime
    last_evidence_at: datetime
    canonical_evidence: dict | None
    evidence_gallery: list[dict] = field(default_factory=list)
    segment_id: str | None = None
    offset_m: float | None = None
    ward_id: str | None = None
    map_match_confidence: float | None = None


# ─── Confidence ──────────────────────────────────────────────────────────────


def positive_weights(evidence: Sequence[PositiveEvidence], gamma: float) -> list[float]:
    """Per-observation weights in time order, with the geometric same-device discount."""
    seen: Counter[str] = Counter()
    out = []
    for e in evidence:
        n_prior = seen[e.device_id]
        out.append(e.reliability * e.quality * gamma**n_prior)
        seen[e.device_id] += 1
    return out


def negative_weights(negatives: Sequence[NegativeEvidence], gamma: float) -> list[float]:
    seen: Counter[str] = Counter()
    out = []
    for n in sorted(negatives, key=lambda n: n.at):
        out.append(n.reliability * n.quality * gamma ** seen[n.device_id])
        seen[n.device_id] += 1
    return out


def repair_evidence(negatives: Sequence[NegativeEvidence], params: FusionParams) -> float:
    """Log-likelihood ratio, in nats, of "gone" over "still there" given a run of misses.

    A present defect is missed on a pass with probability 1 − recall·r·q, so each qualifying
    miss contributes −ln(1 − recall·r·q). A clean daylight pass is strong evidence of a
    repair; a night pass behind traffic is weak. Repeat misses from one device are
    discounted like repeat sightings, because one camera missing something is correlated.

    Counting misses instead (docs/04's "3 passes") false-resolves a real pothole whenever a
    ~80%-recall detector happens to miss it three times running — which, over the hundreds of
    passes a busy corridor gets, is most weeks — and every false resolution then shows up as a
    failed repair in the ward's durability score.
    """
    seen: Counter[str] = Counter()
    total = 0.0
    for n in sorted(negatives, key=lambda n: n.at):
        p_miss_if_present = 1.0 - min(0.99, params.detector_recall * n.reliability * n.quality)
        total += -math.log(p_miss_if_present) * params.correlation_discount ** seen[n.device_id]
        seen[n.device_id] += 1
    return total


def fuse_log_odds(
    prior: float,
    positives: Sequence[PositiveEvidence],
    negatives: Sequence[NegativeEvidence],
    params: FusionParams,
) -> float:
    cap = logit(params.max_observation_probability)
    ordered = sorted(positives, key=lambda e: e.captured_at)
    L = logit(prior)
    for e, w in zip(ordered, positive_weights(ordered, params.correlation_discount), strict=True):
        L += w * min(logit(e.confidence), cap)
    neg = logit(params.negative_evidence_probability)
    for v in negative_weights(negatives, params.correlation_discount):
        L -= v * neg
    return L


# ─── Position ────────────────────────────────────────────────────────────────


def fuse_position(
    points: Sequence[tuple[float, float, float]], sigma_floor_m: float
) -> tuple[float, float, float]:
    """Inverse-variance weighted centroid of ``(lat, lon, sigma_m)``; σ̂ floored.

    The floor exists because GNSS multipath under a flyover is spatially correlated: every bus
    inherits a similar bias, so repeated passes share the error instead of averaging it out.
    """
    if not points:
        raise ValueError("no points to fuse")
    proj = LocalProjection(points[0][0], points[0][1])
    wsum = xs = ys = 0.0
    for lat, lon, sigma in points:
        w = 1.0 / max(sigma, 0.1) ** 2
        x, y = proj.to_xy(lat, lon)
        wsum += w
        xs += w * x
        ys += w * y
    lat, lon = proj.to_latlon(xs / wsum, ys / wsum)
    return lat, lon, max(math.sqrt(1.0 / wsum), sigma_floor_m)


# ─── Severity ────────────────────────────────────────────────────────────────


def severity_trend(
    samples: Sequence[tuple[datetime, float]],
    *,
    min_samples: int = 4,
    min_span_days: float = 2.0,
    slope_threshold_per_day: float = 0.005,
) -> str:
    """Theil–Sen slope of severity against time → worsening / stable / improving / unknown.

    Median-of-slopes tolerates ~29% outliers, so one badly-ranged rainy observation cannot
    manufacture a trend. The 90% interval must exclude zero before a direction is claimed.
    """
    if len(samples) < min_samples:
        return "unknown"
    t0 = min(t for t, _ in samples)
    days = np.array([(t - t0).total_seconds() / 86400.0 for t, _ in samples])
    if days.max() - days.min() < min_span_days:
        return "unknown"
    from scipy.stats import theilslopes

    y = np.array([s for _, s in samples])
    slope, _, lo, hi = theilslopes(y, days, alpha=0.90)
    if slope > slope_threshold_per_day and lo > 0:
        return "worsening"
    if slope < -slope_threshold_per_day and hi < 0:
        return "improving"
    return "stable"


# ─── Evidence selection ──────────────────────────────────────────────────────


def _usable(ev: Mapping | None) -> bool:
    return bool(ev and ev.get("uri") and ev.get("faces_blurred") is True)


def select_evidence(
    positives: Sequence[PositiveEvidence], gallery_size: int = 6
) -> tuple[dict | None, list[dict]]:
    """Canonical crop = highest quality; gallery = best crop in each of N time bins."""
    usable = [p for p in positives if _usable(p.evidence)]
    if not usable:
        return None, []

    def q(p: PositiveEvidence) -> float:
        return float(p.evidence.get("quality") or 0.0)  # type: ignore[union-attr]

    best = max(usable, key=lambda p: (q(p), p.captured_at))
    others = sorted((p for p in usable if p is not best), key=lambda p: p.captured_at)
    gallery: list[dict] = []
    if others:
        t_first, t_last = others[0].captured_at, others[-1].captured_at
        span = max((t_last - t_first).total_seconds(), 1e-6)
        bins: dict[int, PositiveEvidence] = {}
        for p in others:
            b = min(
                int((p.captured_at - t_first).total_seconds() / span * gallery_size),
                gallery_size - 1,
            )
            if b not in bins or q(p) > q(bins[b]):
                bins[b] = p
        gallery = [dict(bins[b].evidence) for b in sorted(bins)]  # type: ignore[arg-type]
    return dict(best.evidence), gallery  # type: ignore[arg-type]


def _mode(values: Iterable[str | None]) -> str | None:
    c = Counter(v for v in values if v)
    return c.most_common(1)[0][0] if c else None


# ─── Putting it together ─────────────────────────────────────────────────────


def fuse(
    prior: float,
    positives: Sequence[PositiveEvidence],
    negatives: Sequence[NegativeEvidence],
    params: FusionParams,
) -> FusedState:
    """Fused asset state from its full evidence history.

    Only negative evidence *after* the last positive sighting counts: it expresses doubt
    about whether the thing is still there. Earlier negatives were answered by the later
    sighting.
    """
    if not positives:
        raise ValueError("an asset needs at least one positive observation")
    ordered = sorted(positives, key=lambda e: e.captured_at)
    last = ordered[-1].captured_at
    live_negatives = [n for n in negatives if n.at > last]

    L = fuse_log_odds(prior, ordered, live_negatives, params)
    lat, lon, sigma = fuse_position(
        [(p.lat, p.lon, p.accuracy_m) for p in ordered], params.position_sigma_floor_m
    )
    sev = [p.severity for p in ordered if p.severity is not None]
    areas = [p.area_m2 for p in ordered if p.area_m2 is not None]
    canonical, gallery = select_evidence(ordered)
    offsets = [p.offset_m for p in ordered if p.offset_m is not None]
    mmc = [p.map_match_confidence for p in ordered if p.map_match_confidence is not None]
    return FusedState(
        log_odds=L,
        confidence=sigmoid(L),
        lat=lat,
        lon=lon,
        accuracy_m=sigma,
        evidence_count=len(ordered),
        distinct_devices=len({p.device_id for p in ordered}),
        negative_passes=len(live_negatives),
        negative_devices=len({n.device_id for n in live_negatives}),
        repair_evidence_nats=repair_evidence(live_negatives, params),
        severity_score=statistics.median(sev) if sev else None,
        severity_trend=severity_trend(
            [(p.captured_at, p.severity) for p in ordered if p.severity is not None]
        ),
        area_m2=statistics.median(areas) if areas else None,
        first_seen_at=ordered[0].captured_at,
        last_evidence_at=last,
        canonical_evidence=canonical,
        evidence_gallery=gallery,
        segment_id=_mode(p.segment_id for p in ordered),
        offset_m=statistics.median(offsets) if offsets else None,
        ward_id=_mode(p.ward_id for p in ordered),
        map_match_confidence=statistics.fmean(mmc) if mmc else None,
    )
