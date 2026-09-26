"""Step 1 — clustering, incrementally.

Two stages per ``(segment_id, class_id)`` partition:

1. **Association.** Each new observation joins the nearest existing asset whose uncertainty
   gate contains it: ``max(eps, k·√(σ_asset² + σ_obs²))``, capped. A fixed 8 m gate is right
   between two observations but wrong between an observation and a *young* asset: two
   independent ±5.2 m fixes of the same pothole are more than 8 m apart about 30% of the time,
   which would split one pothole into several assets. As an asset's σ shrinks towards the
   multipath floor, its gate tightens back towards ``eps``.
2. **Seeding.** Observations no asset claimed are clustered among themselves with DBSCAN
   (``eps = 8 m``, ``min_samples = 1``), each cluster seeding one new candidate. A lone first
   sighting still becomes a candidate; discarding it would lose exactly the new defects.

Partitioning by the map-matched segment is what keeps two potholes 6 m apart on opposite
carriageways of a divided road from merging into one asset on neither carriageway.
"""

from __future__ import annotations

import math
from collections.abc import Hashable, Sequence
from dataclasses import dataclass, field

import numpy as np
from sklearn.cluster import DBSCAN

from argus_api.core.geo import LocalProjection

GATE_SIGMAS = 2.0  # ~86% of same-object sightings for a 2-D Gaussian error
GATE_MAX_M = 15.0


@dataclass
class Point:
    key: Hashable
    lat: float
    lon: float
    sigma_m: float = 5.2


@dataclass
class Assignment:
    # new-observation key -> existing asset key
    to_existing: dict[Hashable, Hashable] = field(default_factory=dict)
    # groups of new-observation keys that should seed one new asset each
    new_groups: list[list[Hashable]] = field(default_factory=list)


def gate_m(asset_sigma: float, obs_sigma: float, eps_m: float) -> float:
    return min(GATE_MAX_M, max(eps_m, GATE_SIGMAS * math.hypot(asset_sigma, obs_sigma)))


def assign(
    new: Sequence[Point], existing: Sequence[Point], eps_m: float, min_samples: int = 1
) -> Assignment:
    out = Assignment()
    if not new:
        return out
    proj = LocalProjection(new[0].lat, new[0].lon)
    exy = [proj.to_xy(p.lat, p.lon) for p in existing]

    unclaimed: list[Point] = []
    for p in new:
        x, y = proj.to_xy(p.lat, p.lon)
        best, best_score = None, 1.0
        for a, (ax, ay) in zip(existing, exy, strict=True):
            score = math.hypot(ax - x, ay - y) / gate_m(a.sigma_m, p.sigma_m, eps_m)
            if score <= best_score:
                best, best_score = a, score
        if best is not None:
            out.to_existing[p.key] = best.key
        else:
            unclaimed.append(p)

    if unclaimed:
        xy = np.array([proj.to_xy(p.lat, p.lon) for p in unclaimed])
        labels = DBSCAN(eps=eps_m, min_samples=min_samples).fit(xy).labels_
        groups: dict[int, list[Hashable]] = {}
        for i, (p, lab) in enumerate(zip(unclaimed, labels, strict=True)):
            # With min_samples > 1 noise is possible; it still seeds its own candidate.
            groups.setdefault(int(lab) if lab >= 0 else -(i + 1), []).append(p.key)
        out.new_groups = list(groups.values())
    return out
