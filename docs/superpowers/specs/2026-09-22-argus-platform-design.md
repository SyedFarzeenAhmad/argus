# ARGUS platform — design of record

**Date:** 2026-09-22
**Status:** Approved · amended 2026-09-25 (D11 supersedes D4)
**Scope:** Repository structure, technical architecture and build plan for SIH 2026 PS 26124.
**Supersedes nothing.** Extends `mock_frontend/docs/superpowers/specs/2026-09-08-argus-3d-visualiser-design.md`,
which covered the visualiser slice only.

---

## Problem

Build an edge-AI onboard processing framework plus a centralized urban intelligence platform
that turns public transport buses into mobile urban sensing units. Four people, ten weeks, two
deadlines: a submission video ~2 Oct 2026 and the SIH Grand Finale in Dec 2026.

The video constraint is load-bearing: **every technical claim made on 2 October must be
demonstrable in December.** The documentation therefore doubles as a commitment register
([`docs/10`](../../10-video-claims-matrix.md)).

## Starting position

`mock_frontend/` — a complete three.js urban telemetry visualiser built for the internal round.
Well-engineered, deterministic, and architected around an explicit substitution point: replace
the seeded event generator with a socket client and every view keeps working. It was, however,
excluded from version control by a root `.gitignore` containing the single line `mock_frontend`.

Its `DetectionEvent` shape and its detection taxonomy are the direct ancestors of this design's
contracts.

## Decisions

### D1 — Contract-first monorepo

`contracts/` holds JSON Schema as the single source of truth; Pydantic and TypeScript types are
generated. A schema change a consumer hasn't adopted breaks CI.

*Alternatives rejected:* three repos (version drift across four people in ten weeks);
hand-written types per folder (drifts within a week).

### D2 — Five messages, with `SegmentPass` as the keystone

`Observation`, `SegmentPass`, `Incident`, `Telemetry` go up; `Asset` comes down.

`SegmentPass` is new relative to the mock and carries the design. It records one bus traversing
one road segment, and crucially records **inspection coverage** — what the pipeline looked for
and did not find. It is the system's only source of negative evidence, and it also happens to
be the natural home for congestion and pedestrian-density measurements, which share its spatial
key. One message, four analytics products.

### D3 — The three technical pillars

1. **"Missing" infrastructure is a map-difference problem.** An object detector cannot bound-box
   an absence. The edge reports what it sees plus what it looked for; the backend compares
   against OSM expectation and fleet history. Twelve qualifying non-observations across nine
   devices is the detection.
2. **Multi-pass evidence fusion.** Clustering deduplicates; fusion *improves*. Position by
   inverse-variance weighting (±5.2 m → ±2.2 m, floored for correlated multipath), confidence by
   log-odds accumulation with a **correlation discount** for repeat observations from the same
   device, severity by median plus Theil–Sen trend, canonical evidence by best-frame selection.
   Negative evidence auto-resolves work orders — the fleet verifies its own repairs.
3. **Congestion is a speed-deficit signal; CV supplies attribution.** Base index from ego speed
   against a learned per-segment free-flow baseline, requiring no CV. Occupancy ratio and active
   assets then attribute cause: demand, drainage, incident or capacity loss.

### D4 — Hardware is a result, not an input  *(superseded by D11, 2026-09-25)*

One ONNX artefact, four execution providers. The deployment board is chosen by a measured
benchmark ([`docs/11`](../../11-hardware-benchmark.md)) across Jetson Orin, Pi 5 + Hailo-8L and
Android, reporting mAP, sustained post-thermal-soak FPS, watts and ₹/bus.

This inverts the usual order at the user's direction, and it makes portability a hard constraint:
if any target needs its own retrained model, the comparison is between three systems rather than
one system on three boards.

### D11 — The edge is Android phones: two camera phones, one edge phone  *(2026-09-25)*

For the MVP the bus carries three off-the-shelf phones. The **front** and **rear** camera phones
only capture, hardware-encode H.264 and serve RTSP over a local Wi-Fi hotspot (no internet).
The **edge** phone hosts that hotspot, ingests the N streams, runs every model, uses its own
GNSS + IMU as the bus's pose, and uplinks findings over MQTT/TLS. One Kotlin APK, started in
Camera or Edge mode; the versioned release APK is committed to `edge-app/release/`.

- The camera count is **N by config** (a camera is an RTSP URI); the MVP fits two. Side and
  cabin cameras are roadmap.
- One edge phone = one `device_id`, so fusion's distinct-device corroboration still means
  distinct buses. Cameras are `camera_id`s, not devices.
- Frames carry **capture** time from the camera phone, synced to the edge phone to ≤ 20 ms;
  arrival time would misplace detections by metres at speed.
- The model stays one portable ONNX file. The Jetson / Pi + Hailo comparison of D4 is
  **deferred until the phone MVP works**: the phone setup is measured first, a per-bus
  hardware requirement is derived from it, and dedicated boards are presented to the judges in
  the PPT as the next step ([`docs/11`](../../11-hardware-benchmark.md)).
- CV–Edge moves from a Python service in `cv-pipeline/` to a Kotlin app in `edge-app/`.
  `cv-pipeline/` becomes CV–Perception only: training, export, calibration, and the Python
  reference that produces the app's golden frames.

*Why:* phones are self-contained (camera, GNSS, IMU, modem, battery), need no wiring or
enclosure, and are the strongest fleet-retrofit story; building the MVP on them first removes
hardware procurement from the critical path. *Cost:* all compute sits on one phone, so the
compute and thermal budget is the MVP's tightest constraint and must be measured under soak.

### D5 — Apache-2.0 model architectures for shipping

Ultralytics YOLO is AGPL-3.0, which is a poor fit for government procurement. Prototype on
Ultralytics; ship on D-FINE / RT-DETRv2 / RTMDet-Ins. Decided in week 1, not week 9.

### D6 — Hybrid frontend: 3D Live Command + 2D Analytics

three.js over real OSM Bengaluru geometry for the live view; MapLibre + deck.gl for heat maps,
scorecards and flows. One store, one socket, two views. The transport is an interface, so
`MockTransport` preserves the internal-round demo and provides the demo-day fallback.

Bengaluru extent `12.93–13.01 N, 77.56–77.65 E`, baked to static JSON at build time — nothing is
fetched from the internet at runtime.

### D7 — Privacy designed in

On-device face blurring before any frame touches disk, enforced at the ingest boundary. ANPR only
on incident subject tracks, audit-logged. No face recognition, no demographic inference, no
cross-city vehicle re-identification — the school-children clause is answered with OSM school
zones and the clock instead. Tiered retention; the indefinitely-retained analytics contain no
personal data.

### D8 — Honesty as a design property

A `stale` asset state, a default-on coverage layer, separate rate numerators and denominators,
`assessable_fraction` gating, and an explicit "what these numbers are not" table. A platform that
silently reports "no defects" for roads it has never driven would be worse than no platform.

### D9 — Vertical slice before breadth

One class end to end by week 4, before any breadth work. Integration is where the unknowns live;
discovering them in week 9 leaves no room to respond.

### D10 — `mock_frontend/` frozen and committed

Removed from `.gitignore`. Kept as internal-round evidence, as the ancestor of the contracts, and
as the rehearsed fallback demo. `frontend/` is seeded from it and evolves separately.

## Deferred, with reasons

| Item | Reason |
|---|---|
| O–D patterns, route delay | Not selected as must-have; PS names both, so retained as Phase 3 rather than cut. Route delay is nearly free — GPS against GTFS, no CV. |
| Vehicle re-identification across buses | Infeasible on this hardware; a surveillance capability we decline to build. Corridor flow and junction turn ratios instead. |
| Red-light violation detection | Requires signal-state detection. Phase 4, flagged as hard. |
| Passenger O–D | Correct source is fare-collection data, not cameras. Documented as an integration. |

## Risks

| Risk | Mitigation |
|---|---|
| Multi-task backbone training fails to balance | Keep three independent models live until it wins; decision point week 6 |
| Own footage capture slips | Scheduled day 1–2 of week 1; it is the only task with a hard physical dependency and it feeds both the video and training |
| Frame rate unverified on presentation hardware | Inherited open item from the internal round; close in week 1 |
| One edge phone can't sustain two streams post-soak | Measured in Phase 1, not Phase 3; rate levers (vehicle 15 → 10 Hz, rear idle 5 → 2 Hz, INT8) decided in advance in [`docs/03`](../../03-cv-pipeline.md#multi-camera-policy) |
| Fusion bugs produce plausible wrong answers | Property-based tests on invariants; the component gets the most test attention |
| Demo-day infrastructure failure | Replay service maintained all ten weeks, rehearsed as a mid-sentence switch |

## Deliverables of this session

- Repository scaffolded: `contracts/`, `cv-pipeline/`, `backend/`, `frontend/`, `ops/`, `docs/`
- Five JSON Schemas written in full
- Twelve documents: problem-statement clause mapping, architecture, taxonomy, CV pipeline,
  backend, frontend, contracts walkthrough, analytics methods, privacy, build plan, video claims
  matrix, hardware benchmark
- Per-folder READMEs with non-negotiables
- `docker-compose` stack, project manifests
- Root `.gitignore` corrected to stop excluding `mock_frontend/`

No implementation code. Everything in `docs/` is a commitment to build, not a description of
built software.
