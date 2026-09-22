# 06 — Data contracts, walked through

Schemas live in [`contracts/schemas/`](../contracts/schemas/) and that folder's
[README](../contracts/README.md) explains the generation and versioning rules. This document
walks a real example through each message so the shape is concrete before anyone writes code
against it.

---

## The five messages and who owns them

```
  cv-pipeline ──▶ observation    ──┐
              ──▶ segment_pass   ──┤──▶ backend ──▶ asset ──▶ frontend
              ──▶ incident       ──┤
              ──▶ telemetry      ──┘
```

Four go up, one comes down. The edge never sees an `asset`; the frontend never sees an
`observation`. That asymmetry is the Observation/Asset boundary from
[`docs/01`](01-architecture.md) expressed in the wire format.

---

## `observation` — one detection

```json
{
  "schema_version": "1.0.0",
  "observation_id": "8f3a91c2-4b7e-4d21-9a15-6c8e2f0b7d33",
  "device_id": "BLR-BUS-4417-EDGE",
  "bus_id": "KA01FA4417",
  "route_id": "500D",
  "trip_id": "500D-0740-WD",
  "segment_pass_id": "b2c1a904-77de-4f08-8a3e-11d9c4e5a620",
  "camera_id": "front",
  "class_id": "pothole",
  "captured_at": "2026-11-04T08:14:22.481Z",
  "uplinked_at": "2026-11-04T08:14:25.902Z",
  "confidence": 0.91,
  "geo": {
    "lat": 12.96183, "lon": 77.63094,
    "accuracy_m": 5.2,
    "source": "gnss+ipm+mapmatch"
  },
  "map_match": {
    "segment_id": "osm:way/23847561:3",
    "offset_m": 127.4, "lateral_m": 1.2,
    "ward_id": "bbmp:150",
    "confidence": 0.94
  },
  "ego": { "speed_kmh": 17.2, "heading_deg": 118.4, "accel_ms2": -0.3 },
  "geometry": {
    "bbox_px": [574, 702, 651, 774],
    "mask_area_m2": 0.38,
    "extent_m": [0.74, 0.52],
    "range_m": 11.4
  },
  "severity": { "score": 0.44, "band": "medium" },
  "conditions": {
    "illumination": "day", "weather": "clear",
    "occlusion": 0.08, "motion_blur": 0.12
  },
  "evidence": {
    "uri": "s3://argus-evidence/2026/11/04/obs-8f3a91c2.jpg",
    "sha256": "4d9f...c1a7", "bytes": 46218,
    "quality": 0.81, "faces_blurred": true
  },
  "model": {
    "name": "argus-road-defect", "version": "0.4.2",
    "runtime": "tensorrt-int8", "input_res": "960x544"
  }
}
```

**Things worth noticing.**

`geo.source` is `gnss+ipm+mapmatch`, so this is the **pothole's** position, not the bus's — IPM
projected it 11.4 m ahead and 1.2 m left before the map-matcher snapped it. `accuracy_m: 5.2`
is what the fusion worker weights by.

`severity.score` (0.44) and `confidence` (0.91) are deliberately unrelated. The detector is very
sure it's a pothole; the pothole is 0.38 m², which is medium. Conflating certainty with
seriousness is a common and consequential mistake.

`geometry.extent_m` exists because it is fusable. `bbox_px` is not — the same pothole at 22 m
would have a box about a seventh the area.

`captured_at → uplinked_at` is 3.4 s: the queue latency KPI.

**Size: ~1.9 KB JSON + ~46 KB JPEG.**

---

## `segment_pass` — one bus over one segment

```json
{
  "schema_version": "1.0.0",
  "segment_pass_id": "b2c1a904-77de-4f08-8a3e-11d9c4e5a620",
  "device_id": "BLR-BUS-4417-EDGE",
  "bus_id": "KA01FA4417", "route_id": "500D", "trip_id": "500D-0740-WD",
  "segment_id": "osm:way/23847561:3",
  "ward_id": "bbmp:150",
  "direction": "forward",
  "entered_at": "2026-11-04T08:14:09.220Z",
  "exited_at":  "2026-11-04T08:15:02.870Z",
  "length_m": 248.0,

  "traffic": {
    "mean_speed_kmh": 16.6,
    "min_speed_kmh": 0.0,
    "stopped_seconds": 11.4,
    "dwell_excluded_seconds": 8.2,
    "vehicle_counts": {
      "car": 31, "two_wheeler": 58, "auto_rickshaw": 14,
      "bus": 3, "truck": 2, "lcv": 4, "bicycle": 1
    },
    "occupancy_ratio": 0.42,
    "pcu_estimate": 79.6
  },

  "pedestrian": {
    "unique_tracks": 23,
    "observed_area_m2": 1840.0,
    "crossing_events": 4,
    "cluster_max": 7,
    "in_school_zone": false
  },

  "inspection": {
    "inspected_for": ["pothole", "damaged_road", "waterlogging",
                      "faded_zebra", "damaged_sign", "damaged_divider"],
    "found": ["pothole"],
    "assessable_fraction": 0.87
  },

  "conditions": {
    "illumination": "day", "weather": "clear",
    "occlusion": 0.14, "motion_blur": 0.10
  },
  "model": { "name": "argus-multitask", "version": "0.4.2", "runtime": "tensorrt-int8" }
}
```

**This single message drives four different products.**

- **Congestion.** 16.6 km/h against a learned free-flow of 38 km/h → CI 0.56. Note
  `dwell_excluded_seconds: 8.2` — the bus stopped at a stop for 8.2 s of its 11.4 s stationary
  time, and that portion is removed before the speed maths. Without it this segment would read
  far more congested than it is.
- **Density.** 79.6 PCU and occupancy 0.42.
- **Crowd density.** 23 pedestrians over 1,840 m² observed → **1.25 persons per 100 m²**. The
  denominator travels with the numerator precisely so this division is valid.
- **Negative evidence.** Six classes inspected, one found. The other five accrue as
  non-observations on this segment — which is what will eventually resolve a repaired defect or
  raise a `missing_zebra`. `assessable_fraction: 0.87` says the pass was clean enough to count.

**Size: ~1.5 KB. This is the highest-value-per-byte message in the system.**

---

## `incident` — a conclusion from a trajectory

```json
{
  "schema_version": "1.0.0",
  "incident_id": "e71b0d55-2a94-4cc8-b0f7-9e3a1d6c8452",
  "device_id": "BLR-BUS-4417-EDGE",
  "bus_id": "KA01FA4417", "route_id": "500D",
  "incident_type": "rash_driving",
  "started_at": "2026-11-04T08:22:41.100Z",
  "ended_at":   "2026-11-04T08:22:45.700Z",
  "confidence": 0.88,
  "severity": "critical",
  "geo": { "lat": 12.95871, "lon": 77.63902, "accuracy_m": 4.8, "source": "gnss" },
  "map_match": { "segment_id": "osm:way/19238471:1", "offset_m": 61.0,
                 "ward_id": "bbmp:150", "confidence": 0.91 },
  "ego": { "speed_kmh": 31.5, "heading_deg": 122.0, "accel_ms2": -3.9 },

  "triggers": {
    "ttc_s": 0.62,
    "lateral_accel_ms2": 4.2,
    "lane_changes": 3,
    "ego_impact_g": 0.0,
    "track_discontinuity": false,
    "departure_speed_kmh": 58.0,
    "vru_proximity_m": 3.1
  },

  "subject_vehicle": {
    "track_id": "T-00418",
    "vehicle_class": "car",
    "colour": "white",
    "plate": {
      "text": "KA 05 MJ 7213",
      "confidence": 0.89,
      "per_character_confidence": [0.98,0.91,0.96,0.94,0.72,0.95,0.97,0.93,0.96,0.61],
      "frames_voted": 22,
      "state_code_valid": true
    }
  },

  "evidence": {
    "clip_uri": "s3://argus-evidence/incidents/e71b0d55.mp4",
    "sha256": "9b2e...44fd",
    "duration_s": 30.0,
    "cameras": ["front", "rear"],
    "plate_crop_uri": "s3://argus-evidence/incidents/e71b0d55-plate.jpg",
    "faces_blurred": true,
    "encrypted": true
  },
  "model": { "name": "argus-behaviour", "version": "0.3.1", "runtime": "tensorrt-fp16" }
}
```

**The `triggers` block is the point.** An operator does not have to take 0.88 on faith: TTC of
0.62 s, 4.2 m/s² lateral, three lane changes in 4.6 seconds. That is *why*, and it is
reviewable. Confidence without its reasoning is not evidence.

**The plate carries its own doubt.** Overall 0.89, but position 4 reads 0.72 (M vs N) and
position 9 reads 0.61 (3 vs 8). An operator checking a registry resolves that in seconds. A bare
0.89 would leave them with nothing to check.

**Transmission is split** — see [`docs/03`](03-cv-pipeline.md#bandwidth-budget). This message
plus the plate crop go over cellular immediately (~120 KB). The 30-second clip spools and
uploads over depot wifi overnight (~11 MB), because sending clips over cellular would cost more
than every other message type in the system combined.

---

## `telemetry` — the heartbeat

```json
{
  "schema_version": "1.0.0",
  "device_id": "BLR-BUS-4417-EDGE",
  "bus_id": "KA01FA4417", "route_id": "500D", "trip_id": "500D-0740-WD",
  "at": "2026-11-04T08:14:25.000Z",
  "geo": { "lat": 12.96190, "lon": 77.63081, "accuracy_m": 4.1, "source": "gnss" },
  "ego": { "speed_kmh": 17.2, "heading_deg": 118.4 },
  "schedule": { "next_stop_id": "bmtc:4412", "delay_s": 214, "headway_s": 640 },
  "health": {
    "uptime_s": 18442, "queue_depth": 7, "queue_oldest_s": 12,
    "cpu_pct": 58, "gpu_pct": 71, "temp_c": 68.4,
    "inference_fps": 14.2, "dropped_frames_pct": 0.6,
    "cameras_online": ["front", "rear", "left", "right", "cabin"],
    "camera_quality": { "front": 0.93, "rear": 0.88, "left": 0.61, "right": 0.90, "cabin": 0.85 },
    "gnss_fix": "3d", "link": "4g", "uplink_kb_session": 3184
  },
  "model": { "name": "argus-multitask", "version": "0.4.2", "runtime": "tensorrt-int8" }
}
```

`camera_quality.left: 0.61` is a dirty lens, and it is the realistic failure mode of a
6,400-unit fleet — not a crash, but a bus that quietly stops contributing while everything still
looks green. `temp_c: 68.4` and `inference_fps: 14.2` together catch thermal throttling, which
manifests as silently reduced coverage.

**Size: ~250 B.** At 5 s while moving and 30 s while idle, ~2.3 MB per bus per day.

---

## `asset` — what the fleet concluded

Backend → frontend only. The same pothole after nine days of passes:

```json
{
  "schema_version": "1.0.0",
  "asset_id": "c4e18a70-5d92-4f1b-bb03-2e7c9a4f1d88",
  "class_id": "pothole",
  "state": "confirmed",
  "geo": { "lat": 12.961845, "lon": 77.630928, "accuracy_m": 2.2,
           "source": "gnss+ipm+mapmatch" },
  "map_match": { "segment_id": "osm:way/23847561:3", "offset_m": 127.9,
                 "ward_id": "bbmp:150", "confidence": 0.96 },

  "confidence": 0.97,
  "evidence_count": 34,
  "distinct_devices": 11,
  "negative_passes": 2,

  "severity": { "score": 0.61, "band": "high", "trend": "worsening" },
  "physical": { "area_m2": 0.52 },

  "first_seen_at": "2026-10-26T07:41:10Z",
  "last_evidence_at": "2026-11-04T08:14:22Z",
  "confirmed_at": "2026-10-26T18:03:55Z",

  "canonical_evidence": {
    "uri": "s3://argus-evidence/2026/10/29/obs-1c77e0a3.jpg",
    "sha256": "7e01...9bb2", "quality": 0.94, "faces_blurred": true
  },

  "work_order": {
    "reference": "ARG-BBMP-150-004417",
    "ward_id": "bbmp:150", "ward_name": "Domlur",
    "recommended_action": "Raise patching work order to ward engineer",
    "priority_rank": 3,
    "issued_at": "2026-10-27T09:00:00Z",
    "sla_due_at": "2026-11-03T09:00:00Z"
  }
}
```

**Read the fusion fields together and the argument makes itself.** 34 observations from 11
distinct buses. Accuracy improved from ±5.2 m on a single pass to ±2.2 m. Severity rose from
0.44 to 0.61 and the trend is `worsening` — it is physically growing, which nine days of
repeated passes established and which no single inspection could. Two negative passes are on
record and haven't triggered resolution because the threshold is three.

`accuracy_m: 2.2` is the floor described in [`docs/04`](04-backend.md#multi-pass-evidence-fusion),
not 5.2/√34 = 0.9 m, because GNSS multipath is spatially correlated and does not average away.

When the road is patched, three qualifying passes with `pothole` in `inspected_for` and absent
from `found` flip `state` to `resolved`, stamp `resolved_at`, and close the work order with
`closed_by: 'auto:fleet-verified'`. **Nobody tells the system.**

---

## Rules that are easy to break and expensive to fix

1. **The edge never emits `missing_*`.** Those classes exist only as backend conclusions. An
   edge emitting `missing_zebra` means somebody trained a detector on empty road.
2. **Never render `observation` in a UI.** One pothole, forty pins, zero trust.
3. **`confidence` must be calibrated.** Log-odds fusion on raw softmax scores compounds error
   rather than reducing it. Every released model ships a reliability diagram.
4. **Physical quantities in metres, never pixels.** Pixel areas are not fusable across passes.
5. **Keep rate numerators and denominators separate.** Pre-divided rates cannot be
   re-aggregated correctly.
6. **`schema_version` on every message, and reject unknown majors.** Silent mis-parsing is
   worse than a hard failure.
