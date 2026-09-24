# 01 — Architecture

## The shape of the system

```
╔═══════════════════ ON THE BUS (× 6,400) ═══════════════════╗
║                                                             ║
║  CAMERA PHONES   (MVP: front + rear; N by config)           ║
║    front phone ──┐   H.264 / RTSP over the bus's own        ║
║    rear phone  ──┤   local Wi-Fi hotspot, no internet       ║
║  ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄│┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄  ║
║  EDGE PHONE      │                   GNSS 1Hz   IMU 100Hz   ║
║                  ▼                   (edge phone's own)     ║
║      RTSP ingest ×N, frames             │          │        ║
║      stamped at CAPTURE time            └────┬─────┘        ║
║              │                               │              ║
║              ▼                               ▼              ║
║      ┌───────────────┐              ┌──────────────────┐    ║
║      │ FRAME GATE    │              │ POSE ESTIMATOR   │    ║
║      │ distance-     │              │ interpolate GNSS │    ║
║      │ triggered,    │◀─────────────│ to frame time,   │    ║
║      │ not time-     │   ego speed  │ heading, IMU     │    ║
║      │ triggered     │              └────────┬─────────┘    ║
║      └───────┬───────┘                       │              ║
║              ▼                                │              ║
║      ┌───────────────────────────┐            │              ║
║      │ PERCEPTION  (ONNX Runtime)│            │              ║
║      │  ├ defect det + seg       │            │              ║
║      │  ├ vehicle/VRU det        │            │              ║
║      │  ├ road-surface seg       │            │              ║
║      │  └ plate det → OCR        │            │              ║
║      └───────┬───────────────────┘            │              ║
║              ▼                                │              ║
║      ┌───────────────┐   ┌──────────────┐     │              ║
║      │ TRACKER       │──▶│ BEHAVIOUR    │     │              ║
║      │ ByteTrack     │   │ kinematics → │     │              ║
║      │ (vehicles,VRU)│   │ incidents    │     │              ║
║      └───────┬───────┘   └──────┬───────┘     │              ║
║              │                   │             │              ║
║              ▼                   │             ▼              ║
║      ┌──────────────────────────────────────────────┐        ║
║      │ GEO-REFERENCE                                │        ║
║      │  IPM: pixel → ground metres → lat/lon        │        ║
║      │  map-match → segment_id, ward_id             │        ║
║      └───────────────┬──────────────────────────────┘        ║
║                      ▼                                        ║
║      ┌──────────────────────────────────────────────┐        ║
║      │ PRIVACY GATE  — faces blurred BEFORE any      │        ║
║      │ frame is written to disk or queued            │        ║
║      └───────────────┬──────────────────────────────┘        ║
║                      ▼                                        ║
║      ┌──────────────────────────────────────────────┐        ║
║      │ UPLINK QUEUE                                  │        ║
║      │  severity-ranked, age-compensated             │        ║
║      │  SQLite store-and-forward spool               │        ║
║      │  incidents pre-empt everything                │        ║
║      └───────────────┬──────────────────────────────┘        ║
╚══════════════════════╪═══════════════════════════════════════╝
                       │  MQTT / TLS 1.3, per-device X.509
                       │  ~12.5 MB per bus per day
                       ▼
╔═══════════════════ CENTRAL PLATFORM ═══════════════════════════╗
║   ┌──────────────┐                                             ║
║   │ MQTT broker  │  argus/v1/{fleet}/{device}/{obs|pass|       ║
║   │ (EMQX)       │                          incident|tlm}      ║
║   └──────┬───────┘                                             ║
║          ▼                                                     ║
║   ┌──────────────┐   schema-validate, dedupe by id,            ║
║   │ INGEST       │   verify evidence hash, stamp receipt       ║
║   └──────┬───────┘                                             ║
║          ▼                                                     ║
║   ┌─────────────────────────────────────────┐                  ║
║   │ TimescaleDB hypertables                 │  RAW, immutable  ║
║   │  observation · segment_pass · telemetry │  nobody queries  ║
║   └──────┬──────────────────────────────────┘  these for a UI  ║
║          ▼                                                     ║
║   ┌─────────────────────────────────────────┐                  ║
║   │ FUSION WORKER                           │                  ║
║   │  ├ spatial cluster (DBSCAN, per class)  │                  ║
║   │  ├ log-odds confidence accumulation     │                  ║
║   │  ├ inverse-variance position estimate   │                  ║
║   │  ├ negative evidence → missing_* / resolve                 ║
║   │  └ lifecycle transitions                │                  ║
║   └──────┬──────────────────────────────────┘                  ║
║          ▼                                                     ║
║   ┌─────────────────────────────────────────┐                  ║
║   │ PostGIS: asset · road_segment · ward    │  TRUTH           ║
║   │ Continuous aggregates: congestion,      │  everything a UI ║
║   │ pedestrian density, ward index          │  reads           ║
║   └──────┬──────────────────────────────────┘                  ║
║          ├──────────────┬───────────────────┐                  ║
║          ▼              ▼                   ▼                  ║
║   ┌───────────┐  ┌────────────┐     ┌──────────────┐          ║
║   │ REST API  │  │ Redis      │     │ MinIO / S3   │          ║
║   │ FastAPI   │  │ pub/sub    │     │ evidence     │          ║
║   └─────┬─────┘  └─────┬──────┘     └──────┬───────┘          ║
╚═════════╪══════════════╪═══════════════════╪══════════════════╝
          │              │ WebSocket         │ signed URLs
          ▼              ▼                   ▼
   ┌──────────────────────────────────────────────────┐
   │ FRONTEND                                          │
   │  ┌────────────────────┐  ┌─────────────────────┐ │
   │  │ LIVE COMMAND (3D)  │  │ ANALYTICS (2D)      │ │
   │  │ three.js, real OSM │  │ MapLibre + deck.gl  │ │
   │  │ Bengaluru geometry │  │ heat maps, scorecard│ │
   │  └────────────────────┘  └─────────────────────┘ │
   └──────────────────────────────────────────────────┘
```

## Why the boundaries are where they are

### The edge/central split is drawn at "evidence, not footage"

Everything that needs a pixel happens on the bus. Everything that needs *more than one bus*
happens centrally. That single rule settles most of the design arguments before they start:

| Question | Answer | Because |
|---|---|---|
| Where does detection run? | Bus | Needs pixels |
| Where does tracking run? | Bus | Needs consecutive frames |
| Where does ANPR run? | Bus | Needs the full-resolution crop |
| Where does dedup run? | Central | Needs other buses' data |
| Where is "missing zebra" decided? | Central | Needs many passes over time |
| Where is congestion computed? | Central | Needs fleet-wide aggregation |
| Where does a work order close? | Central | Needs *absence* across the fleet |

### The Observation → Asset boundary is the most important one

There are two populations of data in this system and conflating them is the classic failure:

- **Observations** are raw, noisy, high-volume, immutable, and **nobody ever looks at them.**
  They are append-only sensor readings.
- **Assets** are fused, corroborated, low-volume, mutable, and **they are the entire product.**

A dashboard that renders observations shows forty pins for one pothole and loses the user's
trust in about eight seconds. A dashboard that renders assets shows one pin that says
"confirmed by 12 buses over 9 days, worsening" — and that is a thing a ward engineer will act
on.

This boundary is also what makes a **single false positive harmless.** One bus mis-detecting
a tar patch as a pothole produces a `candidate` asset that never reaches `confirmed`, never
becomes a work order, and never appears on the default dashboard view.

### The contract boundary is enforced by code generation

`contracts/schemas/*.json` is the source of truth; Pydantic models and TypeScript types are
generated. A schema edit that a consumer hasn't adopted **breaks CI**, which is the only
form of interface discipline that survives a hackathon deadline.

## Data flow, end to end, for one pothole

```
 t+0.000  Bus KA01FA4417 at 17 km/h. Frame gate fires — 5 m since last inference.
 t+0.012  Detector: pothole, 0.91, mask 1,840 px, bbox bottom-centre at (612, 738).
 t+0.014  IPM: ground range 11.4 m, lateral +1.2 m, area 0.38 m².
 t+0.015  Pose: GNSS interpolated to frame time; heading 118.4°.
 t+0.016  Detection lat/lon = bus position + 11.4 m @ 118.4° + lateral offset.
 t+0.018  Map-match → osm:way/23847561:3, offset 127 m, ward bbmp:150.
 t+0.021  Severity from physical area, not confidence → 0.44, band "medium".
 t+0.024  Crop written, face-blur pass (no faces), SHA-256 computed.
 t+0.026  Observation enqueued. Queue depth 7.
 t+3.400  Uplink: severity+age ranked it first. 1.9 KB JSON + 46 KB JPEG.
 t+3.9    Ingest validates, verifies hash, writes to hypertable.
 t+5.0    Fusion: DBSCAN finds 3 prior observations within 8 m, same class,
          from 2 other devices. Cluster now 4 observations / 3 devices.
          → log-odds confidence 0.87 → state candidate → CONFIRMED.
          → position re-estimated, σ drops 5.9 m → 3.1 m.
 t+5.1    Redis publish → WebSocket → pin appears on both frontend views.
 t+5.1    Work order ARG-BBMP-150-004417 drafted, SLA 7 days, ward engineer notified.
  ...
 D+14     Road resurfaced. Buses keep passing.
 D+17     3 qualifying passes, good light, unoccluded, inspected_for includes pothole,
          found does not. negative_passes = 3 → state RESOLVED, automatically.
```

That last step is the one to put in the video. **Nobody had to tell the system the road was
fixed.**

## Failure modes the architecture takes seriously

| Failure | Consequence if ignored | Mitigation |
|---|---|---|
| Cellular dropout | Silent data loss | SQLite spool, store-and-forward, QoS 1 |
| One bad camera | Persistent false positives from one device | Per-device reliability weight in fusion; distinct-device corroboration |
| Thermal throttling of a phone behind a sunlit windscreen | Silently reduced FPS, quiet coverage loss | `inference_fps` + `temp_c` in telemetry; fleet-health panel; measured under soak in [`11`](11-hardware-benchmark.md) |
| A camera phone drops off the local Wi-Fi | That camera's classes silently uninspected | Edge keeps running on the remaining streams; the camera leaves `cameras_online` and its classes leave `inspected_for`, so no false negative evidence |
| Camera/edge clocks disagree | Every detection misplaced by metres at speed | Capture-time stamping + offset handshake, ≤ 20 ms ([`03`](03-cv-pipeline.md#on-bus-topology)) |
| Bus stop dwell | Every stop reads as a traffic jam | `dwell_excluded_seconds` excluded from congestion maths |
| Occluded pass behind a truck | Counted as a clean inspection; false "missing" | `assessable_fraction` gates negative evidence |
| GNSS multipath under a flyover | Detections snap to the wrong road | Map-matching with an HMM over the road graph; accuracy-weighted fusion |
| Model upgrade mid-deployment | Incomparable history | `ModelProvenance` on every message; re-scoring is possible |
| Operator sees a false positive | No feedback loop | `rejected` state feeds a hard-negative set for retraining |

## Technology choices, with the reason

| Layer | Choice | Why this and not the obvious alternative |
|---|---|---|
| Edge platform | **Android phones** — 2 camera phones → 1 edge phone | Self-contained (camera, GNSS, IMU, modem, battery), no wiring or enclosure, cheap, swappable in two minutes. Dedicated boards are compared *after* the MVP ([`11`](11-hardware-benchmark.md)). |
| Edge app | **Kotlin**, native Android, one APK with Camera and Edge roles | Direct access to CameraX, the hardware H.264 encoder/decoder and the NPU. Cross-platform frameworks reach all three through plugins, which is where real-time CV gets slow and fiddly. |
| Edge inference | **ONNX Runtime Android** + swappable EP (NNAPI, CPU) | One portable artefact: the same file runs on the phone now and on any dedicated board we compare later, so that comparison is one model on different hardware, not different models. |
| Detector | RT-DETR / D-FINE (Apache-2.0) | Ultralytics YOLO is AGPL-3.0 — a poor fit for government deployment. Prototype on YOLO, ship on Apache. See [`03`](03-cv-pipeline.md#model-licensing). |
| Tracker | **ByteTrack** | No appearance model, so no second network on the edge budget. Robust to the low-confidence detections a moving bus produces. |
| Uplink | **MQTT** | Designed for exactly this: intermittent cellular, small messages, QoS tiers, retained state. HTTP polling from 6,400 buses is not a plan. |
| Time-series | **TimescaleDB** | Continuous aggregates give pre-computed 15-minute congestion rollups for free. This is the single feature that keeps heat-map queries fast at fleet scale. |
| Spatial | **PostGIS** | Road-segment joins, ward containment and hexbin aggregation belong in SQL with a spatial index, not in Python. |
| API | **FastAPI** | Same language and venv as the CV folder; Pydantic models generate directly from our JSON Schema. |
| 3D | **three.js** | Already proven in `mock_frontend/`; the visual language exists and works. |
| 2D GIS | **MapLibre + deck.gl** | `HeatmapLayer`, `H3HexagonLayer`, `TripsLayer`, `ArcLayer` are exactly our four analytics products, GPU-rendered, already written. |
| Motion | **anime.js** | HUD counters, panel transitions, feed entry. Cheap, small, and it keeps motion logic out of the render loop. |

## What runs where, on demo day

```
  3 phones (APK)                 laptop (docker)              browser
 ┌────────────────┐            ┌──────────────────┐        ┌───────────┐
 │ front + rear   │            │ EMQX             │        │ frontend  │
 │ camera phones  │            │ backend (uvicorn)│ ── WS ─▶│ :5173     │
 │   │ local wifi │            │ postgres+timescale│        │           │
 │   ▼            │ ── MQTT ─▶ │ redis · minio    │        └───────────┘
 │ edge phone     │            └──────────────────┘
 └────────────────┘
 no bus on stage? the camera phones play recorded Bengaluru footage as their source,
                  or the edge phone reads file:// directly — same code path.
 fallback: ops/replay/replay.py --log bengaluru-mgroad.jsonl
           publishes the identical messages; the dashboard cannot tell.
```

The replay fallback exists because a venue is a hostile environment and the dashboard is the
thing judges actually watch. It is not a shortcut — it is the same substitution point the
mock frontend was built around, kept honest.
