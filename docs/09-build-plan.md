# 09 — Build plan

**Team:** 4 — CV-Perception, CV-Edge, Backend, Frontend.
**Edge platform:** Android phones — front + rear camera phones streaming to one edge phone
(D11, 2026-09-25). CV-Edge ships an APK from `edge-app/`.
**Two deadlines:** submission video ~**2 Oct 2026**; SIH Grand Finale **Dec 2026**.

---

## The organising principle: vertical slice before breadth

The tempting order is to build each folder well and integrate at the end. It fails, every time,
because integration is where the unknowns are and discovering them in week 9 leaves no room to
respond.

So: **one detection class, end to end, working, by the end of week 4.** Video file → real
inference → MQTT → ingest → fusion → API → both frontend views. One class. Potholes.

Everything after that is breadth on a proven spine. If the spine is wrong, week 4 is when you
want to find out.

```
  WRONG                                 RIGHT
  ─────                                 ─────
  wk1-6  CV in isolation                wk1    contracts + scaffolding
  wk1-6  backend in isolation           wk2-4  ONE class, all the way through
  wk1-6  frontend in isolation          wk5-7  every other class, on the proven path
  wk7-9  integrate, discover, panic     wk8-9  analytics, benchmark, polish
```

---

## Phase 0 — Week 1 · 22 Sep – 2 Oct · *Unblock everyone, then make the video*

This week has one job beyond the video: make it impossible for anyone to be blocked in week 2.

| Owner | Deliverable | Done when |
|---|---|---|
| **All** | **Capture Bengaluru footage** — 3–4 h, 4–6 BMTC corridors, dry + wet + one dusk run, GNSS logged | Footage exists. Everything downstream depends on it and it cannot be compressed later. |
| Backend | `contracts/` generators wired; `ops/compose` up; schema CI green | `uv run pytest` validates all five schemas in both languages |
| Backend | **Replay service** publishing recorded `.jsonl` to MQTT at wall-clock speed | Frontend can develop with zero CV |
| CV-Edge | **Edge app skeleton** (Kotlin): Edge mode reads `file://` footage → COCO-pretrained YOLO exported to ONNX, on ONNX Runtime Android → stub geo (edge phone's own GNSS) → MQTT. Debug APK committed to `edge-app/release/`. | The APK on a phone emits schema-valid `Observation`s from the Bengaluru footage to the laptop broker |
| CV-Perception | Dataset assembly (RDD2022 + IDD), labelling started in CVAT, **split by route** | 500 frames labelled, split discipline documented |
| Frontend | `bake-bangalore.mjs` producing real OSM geometry; mock frontend re-skinned onto it | Real Bengaluru roads render in the 3D view at 60 fps **on the presentation machine** |
| **All** | Video script from [`docs/10`](10-video-claims-matrix.md) | Every claim traced to a row in that table |

### The week-1 trap to avoid

Do not spend this week training a good model. Spend it making a **bad** model flow through the
whole system. A COCO YOLO that mistakes drain covers for potholes is fine — it produces
schema-valid observations, and that is what unblocks three other people.

### Capture the footage first

It is the only task that has a hard physical dependency on daylight, weather and a vehicle, and
it feeds both the video and the training set. Everything else can be done at a desk at 2 a.m.
This is the long-pole item of week 1 and it should happen on day one or two.

**Exit criteria:** replay service drives the frontend end to end. CV emits valid messages from
real Bengaluru footage. Video submitted with every claim backed by [`docs/10`](10-video-claims-matrix.md).

---

## Phase 1 — Weeks 2–4 · 3 Oct – 23 Oct · *The vertical slice*

**One class. All the way. Really working.**

| Owner | Weeks 2–4 |
|---|---|
| **CV-Perception** | Train pothole instance segmentation on RDD2022 + IDD + our capture. Export ONNX. **Calibrate confidence** (temperature scaling, reliability diagram). Target mAP@50 ≥ 0.65 on a held-out *route*. |
| **CV-Edge** | **Week 2: Camera mode** — CameraX → H.264 → RTSP; Edge mode ingests two `rtsp://` streams over the hotspot, capture-time sync ≤ 20 ms. **First soak test** of two streams on the edge phone. Weeks 3–4: camera calibration + IPM + geometry unit tests. Pose interpolation. Map matching over the baked road graph. Distance-gated frame sampling. Uplink queue + SQLite spool. `SegmentPass` emission including `inspected_for`. |
| **Backend** | Ingest worker with validation and hash verification. PostGIS + Timescale schema, migrations. **Fusion worker**: clustering, log-odds, position weighting, lifecycle. `/assets`, `/ws/live`. |
| **Frontend** | `LiveTransport` against the real WebSocket. Markers, evidence card, HUD driven by real assets. Assets table. Both views sharing one store. |

### Milestone, end of week 4 — the one that matters

> Play `bengaluru-orr-morning.mp4` through the front camera phone into the edge phone. Watch a
> real pothole get detected, geo-referenced, uplinked,
> fused with prior observations from a second simulated device, promoted to `confirmed`, and
> appear as a pin in both frontend views with a work order attached — **without anyone touching
> anything.**

If that works on 23 October, the remaining six weeks are additive and low-risk. If it doesn't,
you have six weeks to fix it, which is enough. That asymmetry is the entire reason for this
ordering.

**Exit criteria:** the milestone above, demonstrated twice on different footage. Geometry unit
tests green. Fusion property tests green.

---

## Phase 2 — Weeks 5–7 · 24 Oct – 13 Nov · *Breadth on a proven spine*

| Owner | Weeks 5–7 |
|---|---|
| **CV-Perception** | All remaining defect classes. Road-surface segmentation. Vehicle + VRU detection **with auto-rickshaw**. Condition heads for sign/divider/zebra. Multi-task backbone — **with the three-independent-models fallback kept live until week 6.** |
| **CV-Edge** | ByteTrack integration (Kotlin). Unique-track counting, occupancy ratio, PCU. Pedestrian counting with `observed_area_m2`. Multi-camera policy: rear at 5 Hz with incident escalation. Privacy gate (face blur). INT8 quantisation + delta check on the phone NPU. Camera-dropout handling (stream loss → `inspected_for`). |
| **Backend** | **Road Asset Ledger**: OSM expectation loading, negative-evidence accumulation, `missing_*` inference, auto-resolution. Congestion + pedestrian-density continuous aggregates. Ward scorecard. Coverage endpoint. Work orders + CSV export. |
| **Frontend** | Analytics view: MapLibre + deck.gl, congestion `PathLayer`, density `H3HexagonLayer`, ward choropleth, coverage layer. Time scrubber. Legends with units. Ward scorecard panel. |

### The week-6 decision point

Multi-task training either beats three independent models by week 6 or it is abandoned. It is
the only genuinely risky technical bet in the plan — heads compete during training and balancing
them is finicky. **Keep the independent models working until the joint model wins.** Do not
delete the fallback in optimism.

**Exit criteria:** every taxonomy class detected end to end. A `missing_zebra` raised from real
ledger reasoning. An asset auto-resolved from negative evidence. Both heat maps live.

---

## Phase 3 — Weeks 8–9 · 14 Nov – 27 Nov · *Evidence, analytics, hardware*

| Owner | Weeks 8–9 |
|---|---|
| **CV-Perception** | Model cards. Reliability diagrams. Final validation on held-out routes. Retraining on operator-`rejected` hard negatives. |
| **CV-Edge** | **Phone performance measurement** (windscreen soak, battery, both streams), filling part 1 of [`docs/11`](11-hardware-benchmark.md), then the derived **hardware requirement** and the dedicated-board next-step slide for the PPT (part 2). ANPR with per-character voting. Incident behaviour classifier. Release APK. |
| **Backend** | Route delay + delay attribution (GTFS join). Corridor flow + junction turn ratios. Incident report PDF. Audit log. Retention jobs. |
| **Frontend** | Incident detail view with plate + per-character confidence + triggers. Flow ribbons. Route-delay panel. Performance pass. `prefers-reduced-motion`. |

**Exit criteria:** the phone table in [`docs/11`](11-hardware-benchmark.md) is filled with
measured numbers, and the release APK is committed. A complete incident flows from video to report. Every clause in
[`docs/00`](00-problem-statement.md) is demonstrable or explicitly deferred with a reason.

---

## Phase 4 — Weeks 10–11 · 28 Nov – finale · *Harden and rehearse*

Feature freeze on 28 November. Nothing new after that date; the remaining time is for making
what exists not break.

- **Rehearse the demo end to end, ten times.** Not the happy path — with the wifi off, with the
  broker restarted mid-run, on a cold machine.
- **Verify the replay fallback** produces an identical dashboard, and practise switching to it
  mid-sentence.
- **Confirm 60 fps on the actual presentation machine**, bloom on and off.
- Pre-seed the database with 30 days of plausible history, so heat maps and trends have
  something to show. A live demo of a system whose value is *accumulation* needs accumulated
  data; generate it from replayed logs, and say that it was generated.
- Prepare answers to the questions in [`docs/07`](07-analytics-methods.md#what-none-of-these-numbers-are).
- Print the one-page architecture diagram and the bandwidth table.

---

## Interfaces — who blocks whom, and how we stop it

```
  CV-Perception ──ONNX file + class list──▶ CV-Edge (packed into the APK)
  CV-Edge ──────────contracts/────────────▶ Backend
  Backend ──────────contracts/────────────▶ Frontend
                         ▲
                         └── replay service means Frontend and Backend
                             are NEVER blocked on CV
```

Three rules:

1. **Nothing crosses a boundary except through `contracts/`.** A schema change needs a PR that
   regenerates both languages.
2. **The replay service is maintained, not abandoned after week 2.** It is the reason two people
   can work while the CV is mid-retrain, and it is the demo-day fallback.
3. **`main` always demos.** Broken work lives on a branch. At any point in these ten weeks,
   someone should be able to clone, compose up, and see something.

---

## If we fall behind — the cut order

Decided now, calmly, rather than at 2 a.m. in week 9. Cut from the bottom:

| Cut | Cost | Why it's cuttable |
|---|---|---|
| 1. Corridor flow / turn ratios | B5 partially unanswered | Demonstrable as a method with a static figure |
| 2. Dedicated-board slide in the PPT | No hardware next-step slide | The phone setup is the product; the board comparison was always post-MVP |
| 3. Incident report PDF | Screen-only incident view | Format, not capability |
| 4. Multi-task backbone | ~40% more edge compute | Three independent models work; it's a cost, not a failure |
| 5. Rear camera phone | Front only; no rear plate for tailgating incidents | Front does the heavy lifting; N-camera support is config, and the policy is documented |
| 6. Long-tail open-vocab triage | Trained classes only | Was always human-triage, never automated |

**Never cut:** the fusion worker, negative evidence, the coverage layer, on-device face blurring,
or the honesty caveats in [`docs/07`](07-analytics-methods.md). Those are the difference between
this platform and a dashcam with a dashboard, and every one of them is cheap to keep.

---

## Weekly cadence

- **Monday, 20 min** — each owner: what shipped, what's blocked, what changed in `contracts/`.
- **Thursday, 30 min** — integration check on `main`. Compose up, run the demo, note what broke.
- **Friday** — update [`docs/10`](10-video-claims-matrix.md) status column. It is the single
  place where "what we can honestly say" is tracked, and it is only useful if it stays current.
