# 00 — The problem statement, broken into testable clauses

SIH 2026 · PS **26124** · *AI-Powered Mobile Urban Intelligence Platform Using Public
Transport Fleet*

SIH is judged against the problem statement, so this document exists to make sure nothing in
it goes unanswered and nothing we build is unasked for. Every requirement below is quoted
from the brief, given an owner, a method, and a phase.

**Read the Phase column as a promise.** Phase 1–2 is what exists when the submission video is
made. Phase 3–4 is what exists by the December finale. [`docs/10`](10-video-claims-matrix.md)
governs what the video may therefore say.

---

## The brief, verbatim

> **Background.** Urban public transport buses traverse almost every major road in a city
> every day. Modern buses are increasingly equipped with multiple cameras covering the
> front, rear, sides, and passenger cabin. However, these cameras are primarily used for
> recording incidents and are not leveraged as intelligent sensing platforms. At the same
> time, city authorities rely on fixed CCTV cameras, manual inspections and citizen
> complaints to identify road defects, traffic congestion, missing infrastructure and unsafe
> driving behaviour. This results in delayed response, incomplete situational awareness and
> inefficient maintenance planning.

> **Description.** Develop an AI-powered onboard and centralized software platform that
> transforms public transport buses into mobile urban sensing units. The onboard software
> shall analyse video streams from multiple bus-mounted cameras to detect road defects such
> as potholes, damaged roads, missing road dividers, missing zebra crossings, damaged or
> missing traffic signboards, waterlogging and other road hazards. It shall estimate vehicle
> density through vehicle detection, classification and counting, identify traffic
> bottlenecks, and detect vulnerable pedestrian situations such as school children crossing
> roads. During incidents such as hit-and-run or rash driving, the system should detect and
> track the offending vehicle, extract the registration number with a confidence score,
> timestamp and GPS location, and securely share alerts with a central command system. The
> centralized platform shall aggregate information from the entire bus fleet, visualize
> events on a GIS map, generate congestion heat maps, identify infrastructure deficiencies,
> analyse origin–destination traffic patterns, estimate route delays and provide actionable
> insights for transport authorities.

> **Expected solution.** An edge-AI onboard processing framework integrated with a
> centralized urban intelligence platform. It should generate reliable alerts, GIS-based
> dashboards, road condition maps, traffic analytics and incident reports to support
> proactive road maintenance, improved traffic management, enhanced public safety and
> evidence-based decision making while minimizing bandwidth through intelligent edge
> processing.

---

## A. Onboard / edge requirements

| # | Clause | How ARGUS answers it | Where | Phase |
|---|---|---|---|---|
| A1 | *"analyse video streams from multiple bus-mounted cameras"* | Camera phones stream over the bus's local Wi-Fi to one edge phone, which ingests N RTSP streams; the MVP fits **front + rear**. Front at full rate, rear at a reduced rate that escalates on incidents. Input is a URI, never a device, so a video file exercises the identical path. | [`03`](03-cv-pipeline.md#on-bus-topology) | 1 |
| A2 | *"potholes"* | Instance segmentation. Mask area is projected to ground metres by IPM, so severity is physical extent rather than pixel count. | [`02`](02-detection-taxonomy.md#pothole) | 1 |
| A3 | *"damaged roads"* | Semantic segmentation of surface distress → **% distressed area per 10 m**, a PCI-style engineering metric. Counting cracks would not be one. | [`02`](02-detection-taxonomy.md#damaged_road) | 2 |
| A4 | *"missing road dividers"* | **Ledger inference.** Detector reports dividers present; backend raises `missing_divider` where OSM or fleet history expects one and qualifying passes repeatedly don't see it. | [`02`](02-detection-taxonomy.md#missing-classes) | 2 |
| A5 | *"missing zebra crossings"* | Same ledger mechanism, plus a graded `faded_zebra` condition class — real crossings degrade before they vanish. | [`02`](02-detection-taxonomy.md#missing-classes) | 2 |
| A6 | *"damaged or missing traffic signboards"* | Sign detector + condition classifier (intact / bent / occluded / faded / defaced) for *damaged*; ledger for *missing*. Both halves of the clause. | [`02`](02-detection-taxonomy.md#damaged_sign) | 2 |
| A7 | *"waterlogging"* | Segmentation + specular-reflectance cue + **temporal persistence**, because wet road and standing water look nearly identical in one frame and not at all alike across ten. | [`02`](02-detection-taxonomy.md#waterlogging) | 2 |
| A8 | *"other road hazards"* | Trained classes for debris and open manholes; open-vocabulary fallback (YOLO-World) for the long tail, flagged for human triage rather than auto-ticketed. | [`02`](02-detection-taxonomy.md#long-tail) | 3 |
| A9 | *"estimate vehicle density through vehicle detection, classification and counting"* | Detection + ByteTrack. **Unique tracks per segment pass**, not per-frame counts, plus a scale-invariant occupancy ratio and a PCU estimate. Indian classes include auto-rickshaw and two-wheeler, which COCO lacks. | [`07`](07-analytics-methods.md#congestion) | 2 |
| A10 | *"identify traffic bottlenecks"* | Speed deficit against a per-segment free-flow baseline, aggregated fleet-wide into a ranked bottleneck list with CV-supplied causal attribution. | [`07`](07-analytics-methods.md#congestion) | 2 |
| A11 | *"vulnerable pedestrian situations such as school children crossing roads"* | Pedestrian clustering + crossing-trajectory detection, **gated by OSM school zones and school hours** — not by visually classifying children, which is both unreliable and a privacy hazard. | [`02`](02-detection-taxonomy.md#pedestrian_risk) | 2 |
| A12 | *"during incidents such as hit-and-run or rash driving, detect and track the offending vehicle"* | Temporal classifier over track kinematics (TTC, lateral acceleration, lane-change rate) plus ego IMU impact. Incidents are conclusions from trajectories, structurally unlike per-frame detections. | [`02`](02-detection-taxonomy.md#incidents) | 3 |
| A13 | *"extract the registration number with a confidence score"* | Plate detect → perspective rectify → OCR → **per-character majority vote across the whole track**. The vote margin *is* the confidence score, and per-character confidence is exposed so an operator sees which digit is doubtful. | [`03`](03-cv-pipeline.md#anpr) | 3 |
| A14 | *"timestamp and GPS location"* | NTP-disciplined edge clock; GNSS interpolated from 1 Hz to frame rate; IPM gives the *detection's* ground position, not the bus's; map-matching snaps it to a road segment. Error budget stated. | [`03`](03-cv-pipeline.md#geo-referencing) | 1 |
| A15 | *"securely share alerts with a central command system"* | MQTT over TLS 1.3 with per-device X.509 certificates, QoS 2 for incidents, store-and-forward spool across cellular dropouts. | [`04`](04-backend.md#ingest) | 1 |
| A16 | *"minimizing bandwidth through intelligent edge processing"* | Video never leaves the bus except as incident evidence. Video crosses only the bus's local Wi-Fi, camera phone → edge phone. ~12.5 MB/bus/day against ~43 GB from two cameras. Severity-aged priority queue governs transmission order. | [`03`](03-cv-pipeline.md#bandwidth-budget) | 1 |

## B. Centralized platform requirements

| # | Clause | How ARGUS answers it | Where | Phase |
|---|---|---|---|---|
| B1 | *"aggregate information from the entire bus fleet"* | Multi-pass evidence fusion: cluster → corroborate across **distinct devices** → promote Observation to Asset. The aggregation is the product, not a side effect. | [`04`](04-backend.md#multi-pass-evidence-fusion) | 1 |
| B2 | *"visualize events on a GIS map"* | Two views over one store: a three.js Live Command view on real Bengaluru geometry, and a MapLibre + deck.gl Analytics view. | [`05`](05-frontend.md) | 1 |
| B3 | *"generate congestion heat maps"* | Directional, per-segment, 15-minute-binned congestion index, served from TimescaleDB continuous aggregates. | [`07`](07-analytics-methods.md#congestion) | 2 |
| B4 | *"identify infrastructure deficiencies"* | Ward-level **Infrastructure Deficiency Index**, normalised by road-km actually surveyed — without that denominator the index is just a coverage map. | [`07`](07-analytics-methods.md#ward-scorecard) | 2 |
| B5 | *"analyse origin–destination traffic patterns"* | Corridor flow and junction turn-ratio estimation from tracked trajectories. Named honestly: true vehicle O–D needs re-identification we will not have. | [`07`](07-analytics-methods.md#od) | 3 |
| B6 | *"estimate route delays"* | GPS against GTFS `stop_times`. **No CV involved**, and worth saying so. Plus delay *attribution*: which segments consumed the lateness. | [`07`](07-analytics-methods.md#route-delay) | 3 |
| B7 | *"actionable insights for transport authorities"* | Every class carries a recommended action; confirmed Assets become work orders with a ward, an SLA and an exportable packet. | [`04`](04-backend.md#work-orders) | 2 |
| B8 | *"reliable alerts"* | Reliability is engineered, not asserted: corroboration thresholds, condition gating, calibrated confidence, and an operator reject path that feeds retraining. | [`04`](04-backend.md#asset-lifecycle) | 2 |
| B9 | *"road condition maps"* | Segment-level condition scoring from fused surface-damage assets. | [`07`](07-analytics-methods.md#road-condition) | 2 |
| B10 | *"incident reports"* | Per-incident packet: clip, plate with per-character confidence, kinematic triggers, timestamp, GPS, chain-of-custody hash. | [`04`](04-backend.md#incident-reports) | 3 |
| B11 | *"proactive road maintenance"* | Severity **trend** (Theil–Sen slope) flags defects that are worsening before they become failures. Only a repeatedly-passing fleet can produce a trend. | [`04`](04-backend.md#multi-pass-evidence-fusion) | 3 |
| B12 | *"evidence-based decision making"* | Auto-resolution closes the loop: buses stop seeing a repaired defect, the work order closes itself, and the ward gets a verified completion rate rather than a self-reported one. | [`04`](04-backend.md#asset-lifecycle) | 3 |

---

## Clauses we are deliberately answering narrowly

Being explicit about this is worth more than quietly over-claiming.

**Side and cabin cameras.** The brief lists front, rear, sides and cabin. The MVP fits front
and rear camera phones; a camera is one more RTSP URI in config, so sides and cabin are a
roadmap item bounded by edge-phone compute, not a redesign. When fitted, cabin cameras are
used for occupancy estimation and in-cabin safety only, with **on-device face blurring before any frame is
persisted or transmitted**. We do not do passenger identification, behaviour scoring or
emotion inference. See [`docs/08`](08-privacy-and-compliance.md).

**"Origin–destination traffic patterns" (B5).** True vehicle-level O–D requires
re-identifying individual vehicles across buses and across the city, which is neither
feasible on this hardware nor appropriate given the surveillance implications. We deliver
corridor-level directional flow and junction turn ratios, which is what a traffic engineer
actually plans against, and we name it accurately.

**Fleet coverage.** Buses cover bus routes. They do not cover residential lanes. Every map
in the platform therefore carries a **coverage layer**, and the ward index is normalised by
surveyed kilometres. A system that silently reports "no defects" for roads it has never
driven would be worse than no system.

---

## Where the internal-round demo sits

`mock_frontend/` is the visualiser built for the internal round. It is deliberately frozen
and kept in the repository: it demonstrates B2 standalone with no backend, it is the
rehearsed fallback if live inference fails on stage, and its `DetectionEvent` shape is the
direct ancestor of `contracts/schemas/observation.schema.json`. Its own README is candid
about what was real and what was simulated, and that candour is a feature — carry it into
the video.
