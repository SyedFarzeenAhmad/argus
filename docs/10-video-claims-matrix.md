# 10 — Video claims matrix

**Purpose.** The submission video is made ~2 Oct. The system is finished in December. Anything
the video claims, we must be able to *show* at the finale. This table is the contract between
those two dates.

**Keep the status column current.** It is reviewed every Friday ([`docs/09`](09-build-plan.md)),
and it is the single place where "what we can honestly say" is tracked.

---

## The three tiers

| Tier | Meaning | How the video may phrase it |
|---|---|---|
| **BUILT** | Exists and runs today | *"ARGUS detects…"*, *"here it is running"* |
| **COMMITTED** | Designed, owned, scheduled, on the critical path | *"ARGUS is built to…"*, *"the platform…"* — present tense about the **design**, which is true |
| **ROADMAP** | Planned but cuttable | *"the architecture extends to…"*, *"planned for…"* — never present tense |
| **DO NOT SAY** | Not being built | — |

A judge at the finale who sees something the video claimed and the product doesn't do remembers
only that. A judge who sees something the video *under*-promised is pleasantly surprised. Bias
every phrasing one tier down.

---

## Claims about detection

| # | Claim | Tier | Evidence at the finale |
|---|---|---|---|
| D1 | Detects potholes from bus-mounted cameras | **COMMITTED** | Live inference on Bengaluru footage |
| D2 | Detects damaged road surface as **% distressed area**, not a crack count | **COMMITTED** | Segmentation output + segment condition score |
| D3 | Detects waterlogging, distinguished from wet road by reflectance + persistence | **COMMITTED** | Wet-weather capture footage |
| D4 | Detects damaged signboards with a condition classification | **COMMITTED** | Condition head output |
| D5 | Identifies **missing** zebra crossings and dividers via map-difference over repeated passes | **COMMITTED** | Ledger reasoning visible on the asset record |
| D6 | Detects debris and open manholes | ROADMAP | Phase 3 |
| D7 | Surfaces unknown hazards for **human triage** (open-vocabulary) | ROADMAP | Say "for human review", never "detects all hazards" |
| D8 | Vehicle detection, classification and counting including **auto-rickshaws and two-wheelers** | **COMMITTED** | Track counts per segment pass |
| D9 | Identifies vulnerable-pedestrian situations using pedestrian clustering + OSM school zones + school hours | **COMMITTED** | Say it this way — it is more impressive than "detects children", and true |
| D10 | Visually classifies children | **DO NOT SAY** | Not built. Deliberately. |
| D11 | Detects rash driving and hit-and-run from track kinematics + IMU | **COMMITTED** | Trigger values in the incident record |
| D12 | Extracts registration numbers **with a confidence score** | **COMMITTED** | Per-character vote margins |
| D13 | Per-character confidence exposed to the operator | **COMMITTED** | Incident detail view |
| D14 | Reads plates of all passing vehicles | **DO NOT SAY** | Deliberately not built — see [`docs/08`](08-privacy-and-compliance.md) |

## Claims about the edge

| # | Claim | Tier | Evidence |
|---|---|---|---|
| E1 | Runs on-device; video never leaves the bus | **COMMITTED** | Architecture + live run. Video crosses only the bus's local Wi-Fi, camera phone → edge phone. |
| E1a | Runs on off-the-shelf Android phones: front + rear camera phones streaming to one edge phone; camera count is configurable | **COMMITTED** | The three phones running the APK |
| E2 | **~12.5 MB/bus/day vs ~43 GB of raw video from our two cameras — a ~3,500× reduction** (~6,900× against a four-camera fit-out) | **BUILT** (arithmetic) | The table in [`docs/03`](03-cv-pipeline.md#bandwidth-budget). Safe now; it's arithmetic. |
| E3 | One portable ONNX model — the same file can move to dedicated hardware without retraining | **COMMITTED** | The APK's model file; export pipeline |
| E4 | Phone setup measured under windscreen heat: FPS, temperature, battery | **COMMITTED** | Part 1 of [`docs/11`](11-hardware-benchmark.md) |
| E4a | Dedicated boards (Jetson Orin, Pi 5 + Hailo) compared against the measured requirement | ROADMAP | PPT "next step" slide. Say "next, we benchmark dedicated boards against this requirement" — never quote board figures. |
| E5 | Specific FPS or accuracy figures | **DO NOT SAY** — until measured | Quote **no** numbers you haven't run. Say "measured on the phone under soak", not "runs at 30 fps". |
| E6 | Bandwidth-aware uplink: severity-ranked, age-compensated, store-and-forward | **BUILT** (in the mock) | Queue depth as a live KPI |
| E7 | Distance-gated sampling — surveys per metre of road, not per second | **COMMITTED** | ~8% duty cycle |
| E8 | Faces blurred **on-device before storage** | **COMMITTED** | Privacy gate in the pipeline |

## Claims about the platform

| # | Claim | Tier | Evidence |
|---|---|---|---|
| P1 | Fuses observations from the whole fleet into deduplicated assets | **COMMITTED** | `evidence_count` / `distinct_devices` on every asset |
| P2 | **Confidence rises with independent corroboration; positional accuracy improves ~1/√N** | **COMMITTED** | ±5.2 m → ±2.2 m, visible on the asset record |
| P3 | **Work orders close themselves when the fleet stops seeing the defect** | **COMMITTED** | `closed_by: auto:fleet-verified` |
| P4 | Tracks whether a defect is **worsening** over weeks | **COMMITTED** | `severity_trend` |
| P5 | **Measures repair durability** — defects re-opening after a patch | **COMMITTED** | Ward scorecard durability sub-score |
| P6 | GIS dashboard: 3D live command + 2D analytics over real Bengaluru | **BUILT** (3D, mock) / **COMMITTED** (2D) | Both views |
| P7 | Congestion heat maps | **COMMITTED** | Directional, 15-min, per segment |
| P8 | Pedestrian density heat maps | **COMMITTED** | Say **"density"**, never "population count" |
| P9 | Ward-level infrastructure deficiency index, **normalised by kilometres surveyed** | **COMMITTED** | Scorecard |
| P10 | Shows what it has **not** surveyed | **COMMITTED** | Coverage layer, on by default |
| P11 | Route delay estimation and **delay attribution to specific segments** | ROADMAP (phase 3) | Say "planned" |
| P12 | Origin–destination analysis | ROADMAP — **and phrase carefully** | Say **"corridor flow and junction turn ratios"**. Do not say vehicle-level O–D. |
| P13 | Deployed on real BMTC buses | **DO NOT SAY** | We have no fleet access |
| P14 | Tested on real Bengaluru roads | **COMMITTED** | Our own capture. Say "our own footage of Bengaluru roads", not "BMTC trial". |

---

## Phrases to avoid, and what to say instead

| Don't say | Say |
|---|---|
| "99% accurate" | "benchmarked on held-out Bengaluru routes, with the reliability diagram published" |
| "real-time" (unqualified) | "detected on-board in under a second; alerts reach the control room in seconds" |
| "detects all road hazards" | "detects seven defect classes, and surfaces unknown hazards for human review" |
| "counts the city's population" | "estimates pedestrian density along bus corridors" |
| "tracks every vehicle" | "tracks vehicles within a segment for counting; no cross-city re-identification" |
| "AI-powered" (alone) | name the actual mechanism — it is more impressive and it is checkable |
| "deployed on BMTC buses" | "designed for retrofit to BMTC buses with off-the-shelf phones — no new wiring" |
| "replaces manual inspection" | "surveys continuously between inspections" |

**The general principle:** every specific, checkable claim is worth more than three superlatives.
*"Three independent 0.70 detections from three different buses outrank one isolated 0.95"* lands
harder with a technical judge than *"highly accurate AI"*, and it is falsifiable, which is why.

---

## Numbers you may quote on 2 October

These are arithmetic or design facts, true today, requiring no built software:

| Number | Source |
|---|---|
| ~6,400 BMTC buses, ~2,200 routes | Public BMTC figures — **verify against the current annual report before quoting** |
| ~43 GB/bus/day of raw video (2 × 1080p30, 16 h) — ~86 GB with four cameras | [`docs/03`](03-cv-pipeline.md#bandwidth-budget) |
| ~12.5 MB/bus/day of ARGUS findings | Same |
| ~3,500× bandwidth reduction (two cameras) | Same |
| ~276 TB/day fleet-wide raw vs ~80 GB/day ARGUS | Same |
| Single-pass positional accuracy ±5.2 m; fused ±2.2 m | [`docs/03`](03-cv-pipeline.md#geo-referencing) error budget |
| ~8% inference duty cycle from distance gating | [`docs/03`](03-cv-pipeline.md#frame-gating) |
| 13 detection classes across 4 categories | [`docs/02`](02-detection-taxonomy.md) |
| 28 problem-statement clauses, each mapped | [`docs/00`](00-problem-statement.md) |

**Verify the BMTC fleet figures** against a current published source before they go in the
video. A wrong number in the first thirty seconds costs credibility for the remaining four
minutes, and it is the easiest thing in this document to check.

---

## A suggested narrative arc

Not a script — a structure the material supports. Roughly 4 minutes.

**0:00 – 0:30 · The problem, concretely.**
Not "cities have potholes." A ward engineer finds out about a pothole when a citizen complains,
which is weeks after it formed and days after it became dangerous. Fixed CCTV sees junctions,
not road surface. Manual inspection covers a fraction of the network once a year.

**0:30 – 1:00 · The insight.**
The instrument already exists and already drives everywhere. 6,400 buses, every major road,
several times a day, cameras already fitted — used only to review accidents. *"Every bus is
already a survey vehicle. Nobody is reading the data."*

**1:00 – 2:30 · The three pillars.** One beat each, each with a visual.

1. *Absence is not detectable in a frame — but it is detectable across a fleet.* Twelve buses,
   good daylight, no crossing where the map says one should be. **That** is the detection.
2. *Fifteen buses hit the same pothole forty times a day.* Fusion turns that from noise into
   precision: position to ±2 m, confidence from corroboration, severity trend over weeks. And
   when the buses **stop** seeing it — the work order closes itself.
3. *Congestion needs no camera; the bus is the probe.* The camera's job is to say **why** —
   demand, or a stopped truck, or standing water.

**2:30 – 2:50 · The number.**
43 GB a day per bus from just two cameras, versus 12.5 MB. Hold the table on screen. Say it plainly and move on.

**2:50 – 3:50 · The demo.**
Bengaluru footage → live detection → the pin appearing on the 3D map → the asset record with 11
distinct devices and a worsening trend → the ward scorecard. Real footage of real Bengaluru
roads, and say so.

**3:50 – 4:20 · What changes.**
A ward engineer opens a ranked backlog on Monday with photographs and coordinates, instead of a
complaints inbox. Repairs verify themselves. Bad repairs become visible for the first time.

**Close on the honesty.** *"Buses drive bus routes, so we show you what we haven't surveyed too."*
Ending on a limitation you handled is a stronger close than ending on a superlative, and it is
the note that distinguishes a team that has thought about deployment from one that has thought
about a demo.

---

## Status log

| Date | Change |
|---|---|
| 2026-09-22 | Created. All tiers assigned from the design. Nothing BUILT except the mock frontend and the bandwidth arithmetic. |
| 2026-09-25 | D11: edge moves to Android phones (2 camera phones → 1 edge phone). E1a added. E2 recomputed for two cameras. E3/E4 re-scoped to the phone; dedicated-board comparison moved to E4a, ROADMAP (PPT next step). |
