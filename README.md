# ARGUS

**Turning a public bus fleet into a continuously-sensing instrument for the city.**

Smart India Hackathon 2026 · Problem Statement **26124** — *AI-Powered Mobile Urban
Intelligence Platform Using Public Transport Fleet*

---

## The one-paragraph version

Bengaluru's BMTC runs roughly 6,400 buses over roughly 2,200 routes, and between them they
drive nearly every arterial road in the city, several times a day, every day. Those buses
already carry cameras — used today only to review incidents after the fact. ARGUS treats
that fleet as a distributed sensor array. On each bus, off-the-shelf phones do the work: front
and rear camera phones stream over the bus's own Wi-Fi to an edge phone that analyses the
streams in place, transmit **findings rather than footage**, and a central platform fuses the
findings from every pass by every bus into a live picture of road condition, congestion,
pedestrian pressure and unsafe driving — with the evidence attached.

## What makes this different from a dashcam with a neural net

Three ideas do the real work. Each solves a problem that looks like a detail and isn't.

**1. "Missing" infrastructure is a map-difference problem, not a detection problem.**
You cannot draw a bounding box around a zebra crossing that isn't there. So the edge
reports what it *sees*, and also what it *looked for and did not find*. The backend keeps a
**Road Asset Ledger** of what OpenStreetMap says should exist and what the fleet has
historically observed. When twelve buses pass a junction in good daylight and none of them
sees the crossing that used to be there, *that* is the detection. One bus missing it is
occlusion. Twelve is a fact. → [`docs/02`](docs/02-detection-taxonomy.md)

**2. Multi-pass evidence fusion makes every field better, not just deduplicated.**
Fifteen buses hit the same pothole forty times a day. Naively that's forty dashboard pins.
ARGUS clusters them into one **Asset** and combines the evidence: position error shrinks as
~1/√N, confidence accumulates in log-odds so three independent 0.70 detections outrank one
isolated 0.95, severity becomes a robust median, and the best-lit frame across all passes
becomes the canonical photograph. And when buses keep passing and **stop** seeing it, the
work order closes itself — **the fleet verifies its own repairs.**
→ [`docs/04`](docs/04-backend.md#multi-pass-evidence-fusion)

**3. Congestion is a speed-deficit signal that CV explains rather than produces.**
Counting vehicles from a moving windscreen is scale-dependent and fragile. The base
congestion index needs no CV at all: the bus's own speed against that segment's free-flow
baseline — which is how TomTom and Google actually do it. Computer vision then supplies the
layer neither of them has: *why*. Dense traffic, or a stopped truck, or 200 mm of standing
water. → [`docs/07`](docs/07-analytics-methods.md#congestion)

## The number that makes the case

| | Per bus per day | BMTC fleet (~6,400 buses) |
|---|---:|---:|
| Raw video, 2 cameras (front + rear) @ 1080p30 | ~43 GB | **~276 TB / day** |
| ARGUS events + evidence crops, over cellular | ~12.5 MB | **~80 GB / day** |

A **~3,500×** reduction from just two cameras (~6,900× against the brief's four-camera
fit-out), and it's arithmetic rather than a claim. (Incident video clips are
the one exception and they ride depot wifi overnight rather than cellular — sending them live
would have cost more than every other message type combined.)

This is the problem statement's *"minimising bandwidth through intelligent edge processing"*
clause, evidenced. Full working in [`docs/03`](docs/03-cv-pipeline.md#bandwidth-budget).

---

## Repository map

```
argus/
├── contracts/        JSON Schema for every cross-team message + generated Py/TS types.
│                     THE SEAM. Read contracts/README.md first.
├── cv-pipeline/      CV–Perception. Datasets, training, ONNX export, calibration, and the
│                     Python reference the app's golden frames come from.
├── edge-app/         CV–Edge. Kotlin Android app, one APK: Camera mode (front/rear phones
│                     stream RTSP) and Edge mode (runs the models, sends findings).
│                     Release APK committed in edge-app/release/.
├── backend/          FastAPI + PostGIS + TimescaleDB + MQTT. Ingest, fusion, analytics,
│                     WebSocket fan-out.
├── frontend/         Two views over one event store: a three.js Live Command view and a
│                     MapLibre + deck.gl Analytics view. Real Bengaluru geometry.
├── mock_frontend/    FROZEN. The internal-round demo. Still runs, still the fallback.
├── ops/              docker-compose, migrations, seeds, the replay service.
└── docs/             The write-up. Start at docs/01.
```

## The documents

| | |
|---|---|
| [`00-problem-statement.md`](docs/00-problem-statement.md) | PS 26124 broken into 28 testable clauses, each mapped to where it is answered |
| [`01-architecture.md`](docs/01-architecture.md) | System architecture, data flow, and why the boundaries fall where they do |
| [`02-detection-taxonomy.md`](docs/02-detection-taxonomy.md) | **Every class we detect**, how, why not the obvious way, and what action it triggers |
| [`03-cv-pipeline.md`](docs/03-cv-pipeline.md) | Models, datasets, tracking, ANPR, geo-referencing, the bandwidth budget |
| [`04-backend.md`](docs/04-backend.md) | Ingest, **multi-pass fusion**, the asset lifecycle, database schema, APIs |
| [`05-frontend.md`](docs/05-frontend.md) | The two views, the Bengaluru map pipeline, the visual system |
| [`06-data-contracts.md`](docs/06-data-contracts.md) | Message-by-message walkthrough with worked examples |
| [`07-analytics-methods.md`](docs/07-analytics-methods.md) | **The maths.** Congestion, crowd density, ward scorecard, O–D, route delay |
| [`08-privacy-and-compliance.md`](docs/08-privacy-and-compliance.md) | DPDP Act 2023, on-device blurring, retention, audit |
| [`09-build-plan.md`](docs/09-build-plan.md) | 10 weeks, 4 people, named owners, phase exit criteria |
| [`10-video-claims-matrix.md`](docs/10-video-claims-matrix.md) | **Every claim the submission video may make**, and its evidence status |
| [`11-hardware-benchmark.md`](docs/11-hardware-benchmark.md) | Phone setup measured under soak now; dedicated boards (Orin, Pi+Hailo) after the MVP |

## Team

| Owner | Folder | Deliverable |
|---|---|---|
| CV — Perception | `cv-pipeline/` | An ONNX file, a class list, a validation report |
| CV — Edge | `edge-app/` | An APK: camera phones stream in, schema-valid messages out of the edge phone |
| Backend | `backend/`, `ops/` | Ingest → fusion → analytics → API → WebSocket |
| Frontend | `frontend/` | Live Command (3D) + Analytics (2D), over real Bengaluru |

Interfaces between them are in `contracts/`. Nothing else crosses a boundary.

## Quick start

```bash
# infrastructure: postgres+postgis+timescale, mosquitto, minio, redis
docker compose -f ops/compose/docker-compose.yml up -d

# backend
cd backend && uv sync && uv run alembic upgrade head && uv run fastapi dev

# frontend
cd frontend && npm install && npm run dev

# edge: install edge-app/release/argus-edge-<version>.apk on 3 phones —
# two in Camera mode (front, rear), one in Edge mode pointed at mqtt://<laptop-ip>:1883

# no CV yet? replay a recorded event log into MQTT at wall-clock speed
python ops/replay/replay.py --log ops/replay/logs/bengaluru-mgroad.jsonl --speed 1.0
```

The frozen internal-round demo still runs standalone and needs no backend at all:

```bash
cd mock_frontend && npm install && npm run build && npm run preview   # :4173
```

## Status

The **design of record** and the contracts, plus the first working component: the edge app
([`edge-app/`](edge-app/README.md), APK in `edge-app/release/`) — camera phones stream to a
processing phone that detects potholes and hands contract-format findings to the backend. The
backend, frontend and CV-Perception folders are scaffolded. Everything asserted in
`docs/` is a commitment to build, not a description of built software — see
[`docs/10`](docs/10-video-claims-matrix.md), which exists specifically so the submission
video never claims more than this table supports.

## Licence and attribution notes

Model architecture licensing is a live constraint, not an afterthought: Ultralytics YOLO is
**AGPL-3.0**, which is a poor fit for software intended for government deployment. The plan
is to prototype fast and ship on an Apache-2.0 architecture. Reasoning in
[`docs/03`](docs/03-cv-pipeline.md#model-licensing).

No real vehicle, person or registration mark is depicted in any mock data in this repository.
