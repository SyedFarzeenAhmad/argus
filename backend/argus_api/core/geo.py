"""Small geodesy helpers.

Fusion works in metres, never degrees (docs/04). At city scale a local equirectangular
projection about a reference point is accurate to well under a centimetre per kilometre,
which is three orders of magnitude below the GNSS error being fused, so there is no reason
to pay for a full UTM transform on every observation.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

EARTH_RADIUS_M = 6_371_008.8


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


class LocalProjection:
    """Equirectangular projection about ``(lat0, lon0)``: degrees <-> east/north metres."""

    def __init__(self, lat0: float, lon0: float) -> None:
        self.lat0 = lat0
        self.lon0 = lon0
        self._ky = math.pi * EARTH_RADIUS_M / 180.0
        self._kx = self._ky * math.cos(math.radians(lat0))

    def to_xy(self, lat: float, lon: float) -> tuple[float, float]:
        return ((lon - self.lon0) * self._kx, (lat - self.lat0) * self._ky)

    def to_latlon(self, x: float, y: float) -> tuple[float, float]:
        return (self.lat0 + y / self._ky, self.lon0 + x / self._kx)


def line_length_m(coords: Sequence[Sequence[float]]) -> float:
    """Length of a GeoJSON-ordered ``[[lon, lat], ...]`` polyline."""
    return sum(
        haversine_m(a[1], a[0], b[1], b[0]) for a, b in zip(coords, coords[1:], strict=False)
    )


def interpolate_line(coords: Sequence[Sequence[float]], step_m: float) -> list[tuple[float, float]]:
    """Points every ``step_m`` along a ``[[lon, lat], ...]`` polyline, as ``(lat, lon)``.

    Used to split a segment's length across the H3 cells it crosses.
    """
    out: list[tuple[float, float]] = []
    carry = step_m / 2
    for a, b in zip(coords, coords[1:], strict=False):
        seg = haversine_m(a[1], a[0], b[1], b[0])
        if seg <= 0:
            continue
        d = carry
        while d < seg:
            t = d / seg
            out.append((a[1] + (b[1] - a[1]) * t, a[0] + (b[0] - a[0]) * t))
            d += step_m
        carry = d - seg
    if not out and coords:
        mid = coords[len(coords) // 2]
        out.append((mid[1], mid[0]))
    return out


def bbox_of(coords: Iterable[Sequence[float]]) -> tuple[float, float, float, float]:
    lons, lats = [], []
    for c in coords:
        lons.append(c[0])
        lats.append(c[1])
    return (min(lons), min(lats), max(lons), max(lats))


def parse_bbox(value: str | None) -> tuple[float, float, float, float] | None:
    """``minLon,minLat,maxLon,maxLat`` — the deck.gl / GeoJSON order."""
    if not value:
        return None
    parts = [float(p) for p in value.split(",")]
    if len(parts) != 4:
        raise ValueError("bbox must be minLon,minLat,maxLon,maxLat")
    min_lon, min_lat, max_lon, max_lat = parts
    if min_lon > max_lon or min_lat > max_lat:
        raise ValueError("bbox minimum exceeds maximum")
    return (min_lon, min_lat, max_lon, max_lat)
