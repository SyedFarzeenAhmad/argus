"""Simulated fleet history, pushed through the real ingest and fusion path.

    uv run python -m argus_api.seeds.simulate --days 30 --devices 15
    uv run python -m argus_api.seeds.simulate --days 7 --write-jsonl sim.jsonl   # replayable

A platform whose value is accumulation has nothing to show on a database created five
minutes ago. This generates contract-valid messages for a synthetic grid of roads laid over
central Bengaluru and feeds them through ``Ingestor`` and ``FusionEngine`` exactly as live
traffic would, so every asset, work order and heat map it produces came out of the real code.

**Everything it creates is labelled simulated**: segment ids ``sim:way/…``, ward ids
``sim:ward/…``, names prefixed ``SIM``, device ids ``BLR-SIM-…``. Say so when presenting it
(ops/README): seeded history passed off as real fleet data is the one dishonesty in this
project that would do real damage.

Scenarios planted so each headline behaviour is visible:

* potholes that worsen week on week (severity trend), and one repaired mid-period that the
  fleet auto-resolves (``closed_by = auto:fleet-verified``)
* one repair that fails and re-opens within 90 days (the durability sub-score)
* a device with a dirty lens that hallucinates potholes — they stay candidates
* an OSM-expected crossing that vanishes, raising ``missing_zebra`` by map difference
* a waterlogging hotspot on an evening-congested segment (drainage attribution)
* a school zone with morning and afternoon pedestrian peaks
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session, sessionmaker

from argus_api.core.config import get_settings
from argus_api.core.geo import LocalProjection, bbox_of, line_length_m
from argus_api.db import session as db_session
from argus_api.db.models import Base, ExpectedAsset, RoadSegment, Ward
from argus_api.db.types import iso, utcnow
from argus_api.fusion.engine import FusionEngine
from argus_api.ingest.pipeline import Ingestor

IST = ZoneInfo("Asia/Kolkata")
LAT0, LON0 = 12.955, 77.600  # south-west corner of the synthetic grid
SPACING = 0.008  # ~870 m between parallel roads
N_LINES = 4
SUB = 3  # sub-segments between junctions (~290 m each)

SURFACE = [
    "pothole",
    "damaged_road",
    "waterlogging",
    "faded_zebra",
    "damaged_sign",
    "damaged_divider",
    "encroachment",
]
VISUAL_ONLY = {"faded_zebra", "damaged_sign", "damaged_divider"}  # not assessable at night


# ─── Network ─────────────────────────────────────────────────────────────────


@dataclass
class Seg:
    id: str
    coords: list[list[float]]
    length: float
    freeflow: float
    school: bool = False
    bottleneck: float = 0.0  # extra peak slowdown, 0..1


def build_network(rng: random.Random) -> tuple[list[Seg], list[Ward], dict[str, list[Seg]]]:
    segs: list[Seg] = []
    lines: dict[str, list[Seg]] = {}
    total = SPACING * (N_LINES - 1)
    for axis in ("ew", "ns"):
        for i in range(N_LINES):
            name = f"{axis}{i}"
            line: list[Seg] = []
            for j in range(N_LINES - 1):
                for k in range(SUB):
                    t0 = (j + k / SUB) * SPACING
                    t1 = (j + (k + 1) / SUB) * SPACING
                    if axis == "ew":
                        lat = LAT0 + i * SPACING
                        coords = [[LON0 + t0, lat], [LON0 + t1, lat]]
                    else:
                        lon = LON0 + i * SPACING
                        coords = [[lon, LAT0 + t0], [lon, LAT0 + t1]]
                    sid = f"sim:way/{name}:{j * SUB + k}"
                    line.append(
                        Seg(
                            sid,
                            coords,
                            line_length_m(coords),
                            freeflow=rng.choice([32.0, 36.0, 40.0, 45.0]),
                        )
                    )
            lines[name] = line
            segs.extend(line)
    # Hand-placed character: a bottleneck corridor and a school zone.
    for s in lines["ew1"][3:6]:
        s.bottleneck = 0.55
    lines["ns2"][4].school = True
    lines["ns2"][5].school = True

    wards = []
    mid_lat, mid_lon = LAT0 + total / 2, LON0 + total / 2
    quads = [
        ("NW", LON0 - 0.002, mid_lat, mid_lon, LAT0 + total + 0.002),
        ("NE", mid_lon, mid_lat, LON0 + total + 0.002, LAT0 + total + 0.002),
        ("SW", LON0 - 0.002, LAT0 - 0.002, mid_lon, mid_lat),
        ("SE", mid_lon, LAT0 - 0.002, LON0 + total + 0.002, mid_lat),
    ]
    for n, (q, w, s, e, nth) in enumerate(quads, start=1):
        ring = [[w, s], [e, s], [e, nth], [w, nth], [w, s]]
        wards.append(
            Ward(
                ward_id=f"sim:ward/{n}",
                name=f"SIM Ward {q}",
                engineer_of_record=f"SIM AEE {q}",
                geometry={"type": "Polygon", "coordinates": [ring]},
                population=rng.randint(30_000, 60_000),
                min_lon=w,
                min_lat=s,
                max_lon=e,
                max_lat=nth,
            )
        )
    return segs, wards, lines


def ward_of(wards: list[Ward], lat: float, lon: float) -> str | None:
    for w in wards:
        if w.min_lon <= lon <= w.max_lon and w.min_lat <= lat <= w.max_lat:
            return w.ward_id
    return None


# ─── Ground truth ────────────────────────────────────────────────────────────


@dataclass
class Defect:
    cls: str
    seg: Seg
    frac: float  # position along the segment
    severity: float
    growth_per_day: float = 0.0
    active: list[tuple[datetime, datetime | None]] = field(default_factory=list)
    evening_only: bool = False  # waterlogging after the evening rain
    area: float = 0.4

    def is_active(self, t: datetime) -> bool:
        if self.evening_only and not 17 <= t.astimezone(IST).hour < 22:
            return False
        return any(a <= t and (b is None or t < b) for a, b in self.active)

    def latlon(self) -> tuple[float, float]:
        (lon0, lat0), (lon1, lat1) = self.seg.coords[0], self.seg.coords[-1]
        return lat0 + (lat1 - lat0) * self.frac, lon0 + (lon1 - lon0) * self.frac

    def sev_at(self, t: datetime, start: datetime) -> float:
        return min(1.0, self.severity + self.growth_per_day * (t - start).total_seconds() / 86400)


def plant(
    rng: random.Random, segs: list[Seg], lines: dict[str, list[Seg]], start: datetime, end: datetime
) -> tuple[list[Defect], list[tuple[str, Seg, float, datetime | None]]]:
    days = (end - start).days
    defects: list[Defect] = []
    for _ in range(22):
        s = rng.choice(segs)
        d = Defect(
            "pothole",
            s,
            rng.uniform(0.1, 0.9),
            rng.uniform(0.2, 0.55),
            growth_per_day=rng.choice([0.0, 0.0, 0.006, 0.012]),
            active=[(start + timedelta(days=rng.uniform(0, days * 0.5)), None)],
            area=rng.uniform(0.15, 1.2),
        )
        defects.append(d)
    # A repair that holds: the fleet stops seeing it and closes the work order itself.
    fixed = Defect(
        "pothole",
        lines["ns1"][2],
        0.5,
        0.5,
        active=[(start, start + timedelta(days=days * 0.5))],
        area=0.8,
    )
    # A repair that fails: patched mid-period, re-appears a week later.
    failed = Defect(
        "pothole",
        lines["ew2"][4],
        0.4,
        0.45,
        active=[
            (start, start + timedelta(days=days * 0.4)),
            (start + timedelta(days=days * 0.4 + 7), None),
        ],
        area=0.6,
    )
    defects += [fixed, failed]
    for s in lines["ew1"][3:5]:
        defects.append(
            Defect(
                "waterlogging", s, 0.5, 0.7, evening_only=True, active=[(start, None)], area=12.0
            )
        )
    defects.append(
        Defect("damaged_road", lines["ns3"][1], 0.5, 0.4, active=[(start, None)], area=15.0)
    )
    defects.append(Defect("encroachment", lines["ew0"][6], 0.3, 0.35, active=[(start, None)]))
    defects.append(Defect("faded_zebra", lines["ns2"][4], 0.5, 0.5, active=[(start, None)]))
    defects.append(Defect("damaged_sign", lines["ew3"][2], 0.6, 0.3, active=[(start, None)]))

    # Expected crossings from "OSM": one healthy-but-faded (seen), one that vanishes.
    crossings = [
        ("sim:osm/node/1", lines["ns2"][4], 0.5, None),
        ("sim:osm/node/2", lines["ew2"][1], 0.5, start + timedelta(days=days * 0.3)),
    ]
    vanishing = crossings[1]
    defects.append(
        Defect("faded_zebra", vanishing[1], vanishing[2], 0.6, active=[(start, vanishing[3])])
    )
    return defects, crossings


# ─── Messages ────────────────────────────────────────────────────────────────


def _sha(*parts: Any) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()


def _conditions(rng: random.Random, t: datetime) -> dict[str, Any]:
    h = t.astimezone(IST).hour
    illum = "day" if 7 <= h < 18 else ("dusk" if h in (6, 18) else "night")
    weather = "rain" if rng.random() < (0.25 if 17 <= h < 21 else 0.05) else "clear"
    return {
        "illumination": illum,
        "weather": weather,
        "occlusion": round(min(0.95, rng.betavariate(1.5, 8)), 3),
        "motion_blur": round(rng.uniform(0.02, 0.2), 3),
    }


def _speed(rng: random.Random, seg: Seg, t: datetime) -> float:
    h = t.astimezone(IST).hour + t.astimezone(IST).minute / 60
    peak = math.exp(-((h - 9.5) ** 2) / 2.0) + 1.2 * math.exp(-((h - 18.5) ** 2) / 2.5)
    slow = min(0.85, 0.35 * peak + seg.bottleneck * peak)
    return max(3.0, seg.freeflow * (1 - slow) * rng.uniform(0.85, 1.05))


@dataclass
class Device:
    device_id: str
    bus_id: str
    route_id: str
    route: list[tuple[Seg, str]]
    dirty_lens: bool = False
    delay_s: float = 0.0


def make_routes(lines: dict[str, list[Seg]]) -> dict[str, list[tuple[Seg, str]]]:
    routes = {}
    for n, key in enumerate(["ew0", "ew1", "ew2", "ns1", "ns2", "ns3"], start=1):
        fwd = [(s, "forward") for s in lines[key]]
        back = [(s, "reverse") for s in reversed(lines[key])]
        routes[f"SIM-{n}"] = fwd + back
    return routes


class Simulator:
    def __init__(self, days: int, n_devices: int, seed: int, end: datetime) -> None:
        self.rng = random.Random(seed)
        self.end = end
        self.start = end - timedelta(days=days)
        self.segs, self.wards, self.lines = build_network(self.rng)
        self.defects, self.crossings = plant(self.rng, self.segs, self.lines, self.start, end)
        routes = make_routes(self.lines)
        rids = sorted(routes)
        self.devices = [
            Device(
                f"BLR-SIM-{i:04d}-EDGE",
                f"KA01SIM{i:04d}",
                rids[i % len(rids)],
                routes[rids[i % len(rids)]],
                dirty_lens=(i == 3),
            )
            for i in range(1, n_devices + 1)
        ]

    def reference_rows(self) -> list[Any]:
        rows: list[Any] = list(self.wards)
        for s in self.segs:
            lat, lon = s.coords[0][1], s.coords[0][0]
            mn = bbox_of(s.coords)
            rows.append(
                RoadSegment(
                    segment_id=s.id,
                    coordinates=s.coords,
                    length_m=s.length,
                    name=f"SIM {'Avenue' if 'ew' in s.id else 'Road'} {s.id.split('/')[1]}",
                    highway="primary" if s.bottleneck else "secondary",
                    maxspeed_kmh=50,
                    ward_id=ward_of(self.wards, lat, lon),
                    has_signal=s.id.endswith((":2", ":5")),
                    min_lon=mn[0],
                    min_lat=mn[1],
                    max_lon=mn[2],
                    max_lat=mn[3],
                )
            )
        for cid, seg, frac, _gone in self.crossings:
            (lon0, lat0), (lon1, lat1) = seg.coords[0], seg.coords[-1]
            rows.append(
                ExpectedAsset(
                    expected_id=cid,
                    class_id="missing_zebra",
                    segment_id=seg.id,
                    lat=lat0 + (lat1 - lat0) * frac,
                    lon=lon0 + (lon1 - lon0) * frac,
                    source="osm",
                    valid_from=self.start - timedelta(days=365),
                )
            )
        return rows

    def day_messages(self, day: datetime) -> list[tuple[str, dict[str, Any]]]:
        """All messages for one simulated day, time-ordered."""
        out: list[tuple[datetime, str, dict[str, Any]]] = []
        for dev in self.devices:
            local0 = day.astimezone(IST).replace(hour=6, minute=0, second=0, microsecond=0)
            for trip in range(8):
                t = local0.astimezone(UTC) + timedelta(minutes=trip * 120 + self.rng.uniform(0, 40))
                if t >= self.end:
                    break
                trip_id = f"{dev.route_id}-{t.astimezone(IST):%H%M}-{dev.device_id[8:12]}"
                for seg, direction in dev.route:
                    out.extend(self._pass(dev, seg, direction, t, trip_id))
                    t = out[-1][0]
                if self.rng.random() < 0.03:
                    out.append(self._incident(dev, t))
        out.sort(key=lambda x: x[0])
        return [(k, m) for _, k, m in out]

    def _pass(
        self, dev: Device, seg: Seg, direction: str, t0: datetime, trip_id: str
    ) -> list[tuple[datetime, str, dict[str, Any]]]:
        rng = self.rng
        v = _speed(rng, seg, t0)
        dwell = rng.uniform(5, 25) if rng.random() < 0.35 else 0.0
        dur = seg.length / (v / 3.6) + dwell
        t1 = t0 + timedelta(seconds=dur)
        cond = _conditions(rng, t0)
        ward = ward_of(self.wards, seg.coords[0][1], seg.coords[0][0])
        pass_id = str(uuid.uuid4())
        msgs: list[tuple[datetime, str, dict[str, Any]]] = []

        inspected = [
            c for c in SURFACE if not (cond["illumination"] == "night" and c in VISUAL_ONLY)
        ]
        if cond["occlusion"] > 0.6:
            inspected = []
        found: set[str] = set()
        p_detect = {"day": 0.95, "dusk": 0.8, "night": 0.45}[cond["illumination"]]
        for d in self.defects:
            if d.seg is not seg or not d.is_active(t0):
                continue
            if d.cls in VISUAL_ONLY and cond["illumination"] == "night":
                continue
            if rng.random() < p_detect * (1 - cond["occlusion"]):
                found.add(d.cls)
                msgs.append(
                    self._obs(
                        dev, d, t0 + timedelta(seconds=dur * d.frac), cond, pass_id, trip_id, ward
                    )
                )
        if dev.dirty_lens and rng.random() < 0.25:
            # A smudge that looks like a pothole, somewhere different every time.
            fake = Defect(
                "pothole",
                seg,
                rng.uniform(0, 1),
                rng.uniform(0.2, 0.4),
                active=[(self.start, None)],
            )
            found.add("pothole")
            msgs.append(
                self._obs(
                    dev,
                    fake,
                    t0 + timedelta(seconds=dur / 2),
                    cond,
                    pass_id,
                    trip_id,
                    ward,
                    confidence=rng.uniform(0.55, 0.75),
                )
            )
        inspected = sorted(set(inspected) | found)

        h = t0.astimezone(IST).hour
        ped_rate = (
            0.4
            + (2.5 if seg.school and h in (7, 8, 15, 16) else 0.0)
            + 0.6 * math.exp(-((h - 18) ** 2) / 4)
        )
        area = seg.length * rng.uniform(4, 8) * (1 - cond["occlusion"])
        peds = sum(1 for _ in range(int(area / 100 * ped_rate * 3)) if rng.random() < 1 / 3)
        occupancy = min(0.95, max(0.02, 0.15 + 0.6 * (1 - v / seg.freeflow) + rng.gauss(0, 0.05)))
        counts = {
            "car": rng.randint(5, 40),
            "two_wheeler": rng.randint(10, 70),
            "auto_rickshaw": rng.randint(2, 20),
            "bus": rng.randint(0, 5),
            "truck": rng.randint(0, 4),
            "lcv": rng.randint(0, 6),
            "bicycle": rng.randint(0, 3),
        }
        pcu = (
            counts["car"]
            + 0.5 * counts["two_wheeler"]
            + 0.8 * counts["auto_rickshaw"]
            + 2.2 * counts["bus"]
            + 2.2 * counts["truck"]
            + 1.4 * counts["lcv"]
            + 0.4 * counts["bicycle"]
        )
        stopped = dwell + (rng.uniform(5, 40) if seg.id.endswith((":2", ":5")) else 0.0)
        msg = {
            "schema_version": "1.0.0",
            "segment_pass_id": pass_id,
            "device_id": dev.device_id,
            "bus_id": dev.bus_id,
            "route_id": dev.route_id,
            "trip_id": trip_id,
            "segment_id": seg.id,
            "direction": direction,
            "entered_at": iso(t0),
            "exited_at": iso(t1),
            "length_m": round(seg.length, 1),
            "traffic": {
                "mean_speed_kmh": round(seg.length / dur * 3.6, 2),
                "min_speed_kmh": 0.0 if stopped else round(v * 0.7, 2),
                "stopped_seconds": round(stopped, 1),
                "dwell_excluded_seconds": round(dwell, 1),
                "vehicle_counts": counts,
                "occupancy_ratio": round(occupancy, 3),
                "pcu_estimate": round(pcu, 1),
            },
            "pedestrian": {
                "unique_tracks": peds,
                "observed_area_m2": round(area, 1),
                "crossing_events": sum(1 for _ in range(peds) if rng.random() < 0.15),
                "cluster_max": min(peds, rng.randint(0, 6) + (8 if seg.school else 0)),
                "in_school_zone": seg.school,
            },
            "inspection": {
                "inspected_for": inspected,
                "found": sorted(found),
                "assessable_fraction": round(1 - cond["occlusion"] * 1.2, 3)
                if cond["occlusion"] < 0.8
                else 0.0,
            },
            "conditions": cond,
            "model": {"name": "argus-multitask", "version": "0.4.2", "runtime": "tensorrt-int8"},
        }
        if ward:
            msg["ward_id"] = ward
        msgs.append((t1, "segment_pass", msg))

        dev.delay_s = max(
            -120.0,
            min(
                1800.0,
                dev.delay_s + (dur - seg.length / (seg.freeflow / 3.6)) * 0.5 + rng.gauss(0, 10),
            ),
        )
        lat, lon = seg.coords[-1][1], seg.coords[-1][0]
        msgs.append((t1, "telemetry", self._telemetry(dev, t1, lat, lon, v)))
        return msgs

    def _obs(
        self,
        dev: Device,
        d: Defect,
        t: datetime,
        cond: dict,
        pass_id: str,
        trip_id: str,
        ward: str | None,
        confidence: float | None = None,
    ) -> tuple[datetime, str, dict]:
        rng = self.rng
        lat, lon = d.latlon()
        proj = LocalProjection(lat, lon)
        sigma = max(2.5, rng.gauss(5.2, 1.0) * (1.4 if cond["illumination"] == "night" else 1.0))
        lat, lon = proj.to_latlon(rng.gauss(0, sigma * 0.7), rng.gauss(0, sigma * 0.7))
        sev = min(1.0, max(0.0, d.sev_at(t, self.start) + rng.gauss(0, 0.04)))
        oid = str(uuid.uuid4())
        band = (
            "low" if sev < 0.25 else "medium" if sev < 0.5 else "high" if sev < 0.75 else "critical"
        )
        range_m = rng.uniform(6, 26)
        return (
            t,
            "observation",
            {
                "schema_version": "1.0.0",
                "observation_id": oid,
                "device_id": dev.device_id,
                "bus_id": dev.bus_id,
                "route_id": dev.route_id,
                "trip_id": trip_id,
                "segment_pass_id": pass_id,
                "camera_id": "front",
                "class_id": d.cls,
                "captured_at": iso(t),
                "uplinked_at": iso(t + timedelta(seconds=rng.uniform(1, 8))),
                "confidence": round(confidence or min(0.99, max(0.5, rng.gauss(0.82, 0.07))), 3),
                "geo": {
                    "lat": round(lat, 7),
                    "lon": round(lon, 7),
                    "accuracy_m": round(sigma, 2),
                    "source": "gnss+ipm+mapmatch",
                },
                "map_match": {
                    "segment_id": d.seg.id,
                    "offset_m": round(d.frac * d.seg.length, 1),
                    "confidence": round(rng.uniform(0.85, 0.99), 3),
                    **({"ward_id": ward} if ward else {}),
                },
                "ego": {
                    "speed_kmh": round(rng.uniform(8, 35), 1),
                    "heading_deg": round(rng.uniform(0, 359.9), 1),
                },
                "geometry": {
                    "mask_area_m2": round(
                        d.area * (1 + (sev - d.severity)) * rng.uniform(0.85, 1.15), 3
                    ),
                    "extent_m": [
                        round(math.sqrt(d.area) * 1.2, 2),
                        round(math.sqrt(d.area) * 0.8, 2),
                    ],
                    "range_m": round(range_m, 1),
                },
                "severity": {"score": round(sev, 3), "band": band},
                "conditions": cond,
                "evidence": {
                    "uri": f"s3://argus-evidence/sim/{t:%Y/%m/%d}/obs-{oid}.jpg",
                    "sha256": _sha(oid),
                    "bytes": rng.randint(30_000, 60_000),
                    "quality": round(rng.uniform(0.4, 0.97), 3),
                    "faces_blurred": True,
                },
                "model": {
                    "name": "argus-road-defect",
                    "version": "0.4.2",
                    "runtime": "tensorrt-int8",
                },
            },
        )

    def _telemetry(self, dev: Device, t: datetime, lat: float, lon: float, v: float) -> dict:
        rng = self.rng
        front_q = 0.55 if dev.dirty_lens else round(rng.uniform(0.85, 0.97), 3)
        return {
            "schema_version": "1.0.0",
            "device_id": dev.device_id,
            "bus_id": dev.bus_id,
            "route_id": dev.route_id,
            "at": iso(t),
            "geo": {
                "lat": round(lat, 7),
                "lon": round(lon, 7),
                "accuracy_m": 4.0,
                "source": "gnss",
            },
            "ego": {"speed_kmh": round(v, 1), "heading_deg": round(rng.uniform(0, 359.9), 1)},
            "schedule": {
                "next_stop_id": f"sim:stop/{rng.randint(1, 40)}",
                "delay_s": int(dev.delay_s),
                "headway_s": int(rng.gauss(600, 180)),
            },
            "health": {
                "uptime_s": rng.randint(1000, 60000),
                "queue_depth": rng.randint(0, 20),
                "queue_oldest_s": rng.randint(0, 30),
                "cpu_pct": rng.randint(35, 80),
                "gpu_pct": rng.randint(40, 90),
                "temp_c": round(rng.uniform(55, 78), 1),
                "inference_fps": round(rng.uniform(12, 16), 1),
                "dropped_frames_pct": round(rng.uniform(0, 2), 2),
                "cameras_online": ["front", "rear"],
                "camera_quality": {"front": front_q, "rear": round(rng.uniform(0.8, 0.95), 3)},
                "gnss_fix": "3d",
                "link": "4g",
                "uplink_kb_session": rng.randint(500, 9000),
            },
            "model": {"name": "argus-multitask", "version": "0.4.2", "runtime": "tensorrt-int8"},
        }

    def _incident(self, dev: Device, t: datetime) -> tuple[datetime, str, dict]:
        rng = self.rng
        seg = rng.choice([s for s, _ in dev.route])
        iid = str(uuid.uuid4())
        typ = rng.choice(
            ["rash_driving", "rash_driving", "dangerous_overtake", "near_miss_vru", "wrong_way"]
        )
        # Plates are synthetic and use the unallocated "ZZ" state code so they can never
        # match a real registration mark.
        series = rng.choice("ABCDEFGH") + rng.choice("JKLMNP")
        plate = f"ZZ {rng.randint(1, 99):02d} {series} {rng.randint(1000, 9999)}"
        chars = [c for c in plate if c != " "]
        return (
            t,
            "incident",
            {
                "schema_version": "1.0.0",
                "incident_id": iid,
                "device_id": dev.device_id,
                "bus_id": dev.bus_id,
                "route_id": dev.route_id,
                "incident_type": typ,
                "started_at": iso(t),
                "ended_at": iso(t + timedelta(seconds=rng.uniform(2, 8))),
                "confidence": round(rng.uniform(0.7, 0.95), 3),
                "severity": rng.choice(["high", "critical"]),
                "geo": {
                    "lat": seg.coords[0][1],
                    "lon": seg.coords[0][0],
                    "accuracy_m": 4.8,
                    "source": "gnss",
                },
                "map_match": {"segment_id": seg.id, "offset_m": 10.0, "confidence": 0.9},
                "triggers": {
                    "ttc_s": round(rng.uniform(0.4, 1.2), 2),
                    "lateral_accel_ms2": round(rng.uniform(2.5, 5.0), 2),
                    "lane_changes": rng.randint(0, 4),
                },
                "subject_vehicle": {
                    "track_id": f"T-{rng.randint(1, 99999):05d}",
                    "vehicle_class": rng.choice(["car", "two_wheeler", "auto_rickshaw"]),
                    "plate": {
                        "text": plate,
                        "confidence": round(rng.uniform(0.6, 0.95), 3),
                        "per_character_confidence": [
                            round(rng.uniform(0.55, 0.99), 2) for _ in chars
                        ],
                        "frames_voted": rng.randint(8, 30),
                        "state_code_valid": False,
                    },
                },
                "evidence": {
                    "clip_uri": f"s3://argus-evidence/sim/incidents/{iid}.mp4",
                    "sha256": _sha(iid),
                    "duration_s": 30.0,
                    "cameras": ["front", "rear"],
                    "plate_crop_uri": f"s3://argus-evidence/sim/incidents/{iid}-plate.jpg",
                    "faces_blurred": True,
                    "encrypted": True,
                },
                "model": {
                    "name": "argus-behaviour",
                    "version": "0.3.1",
                    "runtime": "tensorrt-fp16",
                },
            },
        )


def run(
    days: int,
    n_devices: int,
    seed: int,
    factory: sessionmaker[Session],
    jsonl: Path | None = None,
    create_tables: bool = False,
    quiet: bool = False,
) -> dict:
    end = utcnow().replace(microsecond=0) - timedelta(minutes=5)
    sim = Simulator(days, n_devices, seed, end)
    if create_tables:
        Base.metadata.create_all(factory.kw["bind"])
    with factory() as s:
        for row in sim.reference_rows():
            s.merge(row)
        s.commit()

    cfg = get_settings().model_copy(update={"evidence_verification": "off"})
    ingestor = Ingestor(session_factory=factory, settings=cfg)
    stats = {"stored": 0, "duplicate": 0, "rejected": 0}
    out = jsonl.open("w", encoding="utf-8") if jsonl else None
    suffix = {
        "observation": "obs",
        "segment_pass": "pass",
        "incident": "incident",
        "telemetry": "tlm",
    }
    try:
        day = sim.start
        while day < end:
            for kind, msg in sim.day_messages(day):
                if out:
                    out.write(
                        json.dumps(
                            {
                                "topic": f"argus/v1/sim/{msg['device_id']}/{suffix[kind]}",
                                "kind": kind,
                                "payload": msg,
                            }
                        )
                        + "\n"
                    )
                r = ingestor.handle(kind, msg, topic_device=msg["device_id"])
                stats[r.status] += 1
            day_end = min(day + timedelta(days=1), end)
            with factory() as s:
                engine = FusionEngine(s, settings=cfg)
                while True:
                    step = engine.run_once(batch=5000, now=day_end)
                    if step["observations"] < 5000 and step["passes"] < 5000:
                        break
                s.commit()
                engine._events.clear()  # history, not live news
            if not quiet:
                print(f"  {day.astimezone(IST):%Y-%m-%d}  stored={stats['stored']}")
            day = day_end
    finally:
        if out:
            out.close()
    from argus_api.analytics.freeflow import relearn_all

    with factory() as s:
        relearn_all(s, end)
        s.commit()
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description="Simulate fleet history through ingest + fusion")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--devices", type=int, default=15)
    ap.add_argument("--seed", type=int, default=26124)
    ap.add_argument("--write-jsonl", type=Path, help="also write a replayable message log")
    ap.add_argument(
        "--create-tables",
        action="store_true",
        help="create tables first (SQLite demo without Alembic)",
    )
    args = ap.parse_args()
    print(f"Simulating {args.days} days x {args.devices} devices. All data is SIMULATED.")
    stats = run(
        args.days,
        args.devices,
        args.seed,
        db_session.get_sessionmaker(),
        args.write_jsonl,
        args.create_tables,
    )
    print(json.dumps(stats))


if __name__ == "__main__":
    main()
