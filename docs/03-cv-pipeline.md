# 03 — The CV pipeline (`cv-pipeline/`)

**Owners:** CV–Perception (models, accuracy) and CV–Edge (runtime, geometry, uplink).
**Interface between them:** one ONNX file and a class list. Nothing else.

---

## Design constraints, in priority order

1. **One model artefact, three hardware targets.** The deployment board is not chosen yet
   and choosing it is a *result*, not an input. So the pipeline must run unmodified on
   Jetson Orin, Raspberry Pi 5 + Hailo-8L, and an Android phone, from a single ONNX export.
2. **Input is a URI, never a device.** `rtsp://`, `/dev/video0` and `./bengaluru.mp4` go
   through the identical path. This is what makes the demo honest — the video file exercises
   production code, not a special case.
3. **Findings leave the bus; footage does not.** Except one narrow, deliberate exception
   (incident clips over depot wifi).
4. **Never block on the network.** Cellular in a moving bus drops constantly. Every uplink is
   fire-and-forget into a local spool.
5. **Degrade visibly, not silently.** A throttled SoC or a fogged lens must show up in
   telemetry. The realistic failure of a 6,400-unit fleet is not a crash — it's a bus that
   quietly stops contributing and nobody notices for three weeks.

---

## The runtime abstraction

```
             ┌────────────────────────────────────┐
             │   argus-road-defect-v0.4.2.onnx    │   ← ONE artefact
             └─────────────────┬──────────────────┘
                               │
        ┌──────────────┬───────┴────────┬──────────────────┐
        ▼              ▼                ▼                  ▼
  TensorRT EP     HailoRT           NNAPI EP          CUDA / CPU EP
  (Orin, INT8)  (Pi 5 + Hailo-8L)  (Android, INT8)   (dev laptop)
        │              │                │                  │
        └──────────────┴────────┬───────┴──────────────────┘
                                ▼
                     identical Observation output
```

`argus_edge/runtime/` exposes exactly one function — `infer(frames) -> Detections` — and the
execution provider is selected from config at startup. **No perception code anywhere else in
the repository knows which board it is running on.**

This is the constraint that makes the benchmark in [`docs/11`](11-hardware-benchmark.md)
meaningful. If any target needed its own retrained model, the comparison would be between
three different systems rather than one system on three boards, and the resulting
cost-per-bus table would be worthless.

### Why ONNX rather than each vendor's native path

Vendor toolchains give maybe 10–20% more throughput. They also fork your model three ways,
triple your validation matrix, and make "which board should the government buy" unanswerable.
For a 10-week project with two CV people, portability is worth more than the last 15%.

<a id="model-licensing"></a>
## Model licensing — decide this in week 1, not week 9

**Ultralytics YOLO (v5/v8/v11) is AGPL-3.0.** Under AGPL, software that users interact with
over a network must have its complete corresponding source offered to those users. For a
platform intended for government procurement and potential fleet-wide deployment, that is a
serious encumbrance, and "we'll sort out licensing later" is how a good project becomes
un-deployable.

**Plan:** prototype fast on Ultralytics (the tooling is genuinely the best), but validate and
ship on Apache-2.0 architectures.

| Job | Prototype | Ship on | Licence |
|---|---|---|---|
| Object detection | YOLO11n/s | **D-FINE-S** or **RT-DETRv2-S** | Apache-2.0 |
| Instance segmentation | YOLO11n-seg | **RTMDet-Ins-tiny** | Apache-2.0 |
| Semantic segmentation (road surface) | — | **PIDNet-S** / DDRNet-23-slim | MIT |
| Tracking | ByteTrack | ByteTrack | MIT |
| Plate OCR | PaddleOCR | PaddleOCR rec, fine-tuned | Apache-2.0 |
| Open-vocab triage | YOLO-World | YOLO-World (prototype only) | AGPL — **never shipped**, triage-only, runs offline on our own capture |

Record the decision and the date in the model card. A judge asking "can this actually be
deployed by a government body?" is asking a licensing question as much as a technical one, and
having the answer ready is cheap.

---

## The model stack

### One backbone, several heads

The naive build runs three networks: a defect detector, a vehicle detector, a road segmenter.
That is roughly **3× the compute for substantially overlapping feature extraction** — all
three are looking at the same road scene and need the same low-level features.

```
                  ┌──────────────────────────┐
   frame ────────▶│ shared backbone (CSP/HG) │
                  └────┬──────┬──────┬───────┘
                       │      │      │
          ┌────────────┘      │      └──────────────┐
          ▼                   ▼                     ▼
   ┌─────────────┐    ┌──────────────┐     ┌────────────────┐
   │ DET head    │    │ SEG head     │     │ DEFECT-INS head│
   │ vehicles,   │    │ road surface,│     │ pothole masks, │
   │ VRU, signs, │    │ footpath,    │     │ surface distress│
   │ plates      │    │ markings     │     │                │
   └─────────────┘    └──────────────┘     └────────────────┘
      15 Hz              2–5 Hz                2–5 Hz
```

Measured saving on comparable multi-task setups is 35–45% of total inference cost. It also
guarantees the three heads see *the same frame*, which matters: the segmentation head's
drivable-area mask is the denominator for the detection head's occupancy ratio, and computing
them from different frames at different times introduces an error that is annoying to debug.

**Risk, stated honestly:** multi-task training is harder to balance than three independent
models — heads compete, and one task's loss can dominate. Mitigation: train heads
independently first to establish per-task baselines, then jointly with uncertainty-weighted
loss, and *keep the independent models* as the fallback if joint training underperforms by
week 6. This is a real fork in the plan, not a formality.

### Input resolution

`960 × 544` for the defect/segmentation path. Potholes are small objects and dropping to 640
costs real recall on anything beyond ~15 m. `640 × 384` for the vehicle path, which runs more
often and where objects are larger.

Both are multiples of 32 and both quantise cleanly to INT8.

---

<a id="frame-gating"></a>
## Frame gating — sample by distance, not by time

This is the single largest compute saving in the pipeline and it costs nothing in quality.

**The problem with time-based sampling.** A bus stopped at Silk Board for four minutes at
30 fps runs 7,200 inferences on the same three metres of road. A bus at 60 km/h on Outer Ring
Road covers 33 m between two frames sampled at 0.5 Hz and misses defects entirely. Time is
simply the wrong axis for a road-surveying task.

**Distance gating.** Trigger defect inference every **5 m of travel**, derived from GNSS and
wheel-speed/IMU:

| Bus speed | Defect inference rate |
|---|---|
| 0 km/h (stopped) | **0 Hz** — nothing new to see |
| 15 km/h | 0.8 Hz |
| 30 km/h | 1.7 Hz |
| 60 km/h | 3.3 Hz |
| capped at | 10 Hz |

Effective duty cycle over a typical BMTC route profile: **roughly 8% of frames**. The vehicle/
tracking path is separate and runs at a fixed 15 Hz, because tracking needs temporal
continuity and would break under distance gating.

A pleasant side effect: distance gating gives near-uniform *spatial* sampling of the road
regardless of traffic, which makes coverage statistics meaningful. "This segment was sampled
every 5 m" is a statement about survey quality; "this segment was sampled at 2 Hz" is not.

<a id="multi-camera-policy"></a>
## Multi-camera policy

The brief says front, rear, sides and cabin. Running four full pipelines is neither necessary
nor affordable.

| Camera | Rate | Runs | Why |
|---|---|---|---|
| **Front** | full (distance-gated + 15 Hz track) | everything | The primary sensor. Road ahead, traffic, VRUs, incidents. |
| **Rear** | 5 Hz, **escalates to full on incident** | vehicle detect, plate | The tailgater's plate is behind the bus. Idle most of the time; the incident trigger wakes it. |
| **Left / right** | 1 Hz | encroachment, footpath, stop-area crowd | Side content changes slowly and is about static infrastructure, not events. |
| **Cabin** | 0.2 Hz | occupancy count only, **faces blurred pre-storage** | Occupancy for load analytics. Nothing else. See [`08`](08-privacy-and-compliance.md). |

Total cost is roughly **1.4× the front camera alone**, not 4×. Event-driven escalation is what
buys that: the rear camera is cheap until the moment it matters, and then it is instantly
expensive for twenty seconds.

---

## Tracking

**ByteTrack**, chosen for one specific property: it associates *low-confidence* detections in
a second pass rather than discarding them. Footage from a moving bus — motion blur, rain,
partial occlusion behind an auto — produces exactly that population of weak detections, and a
tracker that throws them away fragments tracks constantly.

It also uses **no appearance embedding network**, which means no second model in the edge
compute budget. On a Hailo-8L that difference decides whether the pipeline fits at all.

Tracks are what make three otherwise impossible things possible:

- **Counting without double-counting.** `vehicle_counts` is unique track IDs per segment pass,
  not summed per-frame detections. Per-frame sums would scale with bus speed, which is
  nonsense as a density measure.
- **Kinematics.** Lateral acceleration, TTC and lane-change rate exist only for a track.
  Incidents are undetectable without them.
- **Multi-frame plate voting.** See below.

Track state is local to a segment pass and is discarded at the segment boundary. We do **not**
attempt vehicle re-identification across buses — it is infeasible on this hardware and it is a
surveillance capability we are choosing not to build.

<a id="anpr"></a>
## ANPR — three stages and a vote

Runs **only** on an incident's subject track. Not continuously, not on every passing vehicle.

```
  1. DETECT     small detector on the subject's crop → plate quadrilateral
  2. RECTIFY    perspective warp to a canonical 96×32 strip
                (a plate at 30° off-axis is unreadable until it isn't)
  3. READ       PaddleOCR rec, fine-tuned on Indian plate fonts
  4. VOTE       ← the part that actually makes it work
```

### Why the vote is the whole thing

A single 1080p frame from a bus gives a plate maybe 60 px wide, motion-blurred, at an angle.
Single-frame OCR accuracy on that is poor and, worse, *unreliably* poor — you cannot tell a
good read from a bad one.

A 20-frame track gives 20 independent reads. Vote **per character position**:

```
  pos:      0    1    2    3    4    5    6    7    8    9
  frame 1:  K    A    0    5    M    J    7    2    1    3
  frame 2:  K    A    0    5    M    J    7    2    1    8
  frame 3:  K    4    0    5    N    J    7    2    1    3
  ...
  vote:     K    A    0    5    M    J    7    2    1    3
  margin:  .98  .91  .96  .94  .72  .95  .97  .93  .96  .61
                                  ▲                        ▲
                          M vs N ambiguous       3 vs 8 ambiguous
```

Three things fall out of this, all of them required by the brief or useful to an operator:

1. **The plate is more accurate** than any single frame produced.
2. **`confidence` is the product of vote margins** — a genuine, interpretable probability.
   This is the *"registration number with a confidence score"* the problem statement asks for,
   and it is derived rather than asserted.
3. **`per_character_confidence` ships with it**, so a control-room operator sees that the read
   is solid except for one digit. A human can resolve "KA 05 MJ 721**3** or 721**8**" against a
   vehicle registry in seconds. A single scalar 0.94 gives them nothing to work with.

**Plus a free sanity filter:** the leading two characters must be a real RTO state code
(`state_code_valid`). Anything else is an OCR failure, not a rare plate.

<a id="geo-referencing"></a>
## Geo-referencing — putting the pothole where the pothole is

A detection's position is **not** the bus's position. A pothole seen 18 m ahead and 2 m to the
left is 18 m ahead and 2 m to the left, and pinning it at the GNSS antenna would put it on the
wrong part of the road — sometimes the wrong road.

### Inverse perspective mapping

For a calibrated camera at known height `h` and orientation `R_cv`, the ground point under a
detection's bottom-centre pixel `(u,v)` is:

```
  p_cam   = K⁻¹ · [u, v, 1]ᵀ            ray in camera frame
  p_veh   = R_cv · p_cam                rotate into vehicle frame
  t       = −h / p_veh.z                intersect ground plane z = −h
  ground  = t · p_veh                   → (x_forward, y_lateral) in metres
```

Then rotate by the bus heading and offset from the GNSS fix to get lat/lon.

This is about twenty lines of code and it is the difference between a map of *where buses were*
and a map of *where defects are*.

### The error that actually dominates: suspension pitch

Calibration error is small — a 0.5° pitch error costs ~0.3 m at 10 m range and ~1.2 m at 20 m.

**Bus body pitch under braking is several degrees**, and it is not small. A bus decelerating
into a stop can pitch 2–3°, which at 20 m range is a 5–7 m range error. Three mitigations:

1. **IMU-derived pitch correction** per frame — the accelerometer already tells us the body
   attitude, so use it rather than assuming the calibrated value.
2. **Range gating.** Detections beyond 25 m carry reduced fusion weight; beyond 35 m they are
   dropped. Error grows roughly with range squared, so the far field is nearly worthless for
   localisation even though it's fine for *detection*.
3. **Reject frames under high longitudinal acceleration** — don't survey while braking hard.

### Map matching

Raw lat/lon is snapped to the OSM road graph with an HMM map-matcher (Valhalla Meili, or our
own Viterbi over candidate segments). This yields `segment_id`, `offset_m` and `ward_id`.

Map matching is not cosmetic. It is what makes **every aggregation in the platform possible** —
per-segment congestion, ward rollups, the asset ledger, coverage statistics. Without a stable
spatial key, fusion would be clustering free-floating points in the plane and the analytics
would have nothing to group by.

### Error budget, stated honestly

| Source | 1σ |
|---|---|
| GNSS horizontal (consumer, urban) | ±5.0 m |
| IPM at 10–20 m, with IMU pitch correction | ±1.0 m |
| Heading error 3° projected at 15 m | ±0.8 m |
| Frame/GNSS time sync (interpolated, 1 Hz → frame) | ±0.3 m |
| **Single observation, combined** | **≈ ±5.2 m** |

After fusing N passes, error falls roughly as 1/√N — **but not indefinitely.** GNSS multipath
in an urban canyon is *spatially correlated*, so repeated passes share a bias rather than
averaging it away. Realistic floor is **±2–3 m**, reached at around 10–12 passes. We state that
rather than claiming √N forever, and the honest floor is still well inside a lane width, which
is the accuracy a patching crew actually needs.

<a id="bandwidth-budget"></a>
## Bandwidth budget — the arithmetic

### What raw video would cost

```
  4 cameras × 1080p30 × H.264 @ ~3 Mbps      =  12 Mbps
  × 16 operating hours                        =  86.4 GB per bus per day
  × 6,400 BMTC buses                          ≈  553 TB per day
```

Roughly 200 PB a year. There is no cellular plan, no backhaul and no storage budget in any
municipal corporation for which that is a real option. This is *why* the problem statement
demands edge processing, and it's worth showing the number rather than repeating the phrase.

### What ARGUS sends over cellular

| Message | Volume/bus/day | Size | Total |
|---|---:|---:|---:|
| `observation` (JSON + JPEG crop) | ~200 | ~47 KB | 9.4 MB |
| `segment_pass` | ~400 | ~1.5 KB | 0.6 MB |
| `telemetry` (5 s moving, 30 s idle) | ~9,000 | ~250 B | 2.3 MB |
| `incident` metadata + plate crop | ~2 | ~120 KB | 0.2 MB |
| **Total over cellular** | | | **≈ 12.5 MB/bus/day** |
| **× 6,400 buses** | | | **≈ 80 GB/day** |

**Reduction factor: ~6,900×.**

### The exception, and why it's designed this way

A 30-second 1080p incident clip is ~11 MB. At 1–3 incidents per bus per day that is
**11–33 MB — more than everything else combined.** Sending them over cellular would blow the
entire budget on the rarest message type.

So incident clips **spool locally and upload over depot wifi overnight**, on an unmetered link.
What goes over cellular immediately is the alert itself: type, location, timestamp, plate with
confidence, kinematic triggers, and a single plate crop. The control room is notified in
seconds; the full evidence file arrives before the next morning's shift.

This is a better design than "send everything immediately" on every axis except one, and the
one — evidentiary latency — does not matter for a maintenance or enforcement workflow that
operates on daily cycles. Where it *does* matter (an active pursuit), the clip can be pulled
on demand over cellular by an explicit operator request.

### The uplink queue

Inherited directly from the internal-round mock, which got this right:

- **Severity-ranked**, so the most serious pending finding transmits first.
- **Age-compensated** — a waiting item gains ~0.3 severity ranks per second, so after about
  seven seconds a low-severity signboard outranks a freshly-found high-severity defect.
  Without this, a busy road starves the queue and low-severity findings never transmit.
- **Incidents pre-empt entirely**, bypassing the queue.
- **SQLite spool** on disk, so a cellular dropout or a power cycle loses nothing. `queue_depth`
  and `queue_oldest_s` ride in telemetry, making uplink health a visible fleet KPI rather than
  an invisible failure.

---

## The privacy gate

Faces are blurred **before any frame is written to disk or entered into the queue** — not at
the server, not at display time. A lightweight face detector runs on every crop destined for
storage, and `Evidence.faces_blurred` is set accordingly. Ingest rejects any evidence crop
containing people with that flag unset.

Full reasoning and the DPDP Act position: [`docs/08`](08-privacy-and-compliance.md).

---

## Datasets and the labelling plan

| Source | What it gives | Licence note |
|---|---|---|
| **RDD2022** (Road Damage Detection) | ~47k images, 6 countries **including India**. Cracking, potholes, ravelling. | Research-friendly; check terms |
| **IDD** (Indian Driving Dataset, IIIT-H) | Indian road scenes with **auto-rickshaws**, unstructured traffic, Bengaluru/Hyderabad. The most important single source. | Research use |
| **BDD100K** | 100k driving images for backbone pretraining and vehicle/VRU classes | BSD-3 |
| **Cityscapes / Mapillary Vistas** | Semantic segmentation pretraining for road surface and footpath | Research / CC-BY-SA |
| **Roboflow Universe** pothole + waterlogging sets | Class-specific top-up | Mixed — audit per-set |
| **Our own Bengaluru capture** | Domain match, and the demo footage | Ours |

### Our own capture — the plan

- **Rig:** phone or action camera, windscreen-mounted at ~1.4 m, fixed pitch, recorded with a
  GNSS logger. Note the height difference from a real bus roof (~3 m) — the IPM calibration
  differs and must be measured, not assumed. This is a known, documented gap between demo rig
  and deployment geometry, not something to paper over.
- **Routes:** 4–6 real BMTC corridors, chosen for defect variety. Outer Ring Road, Hosur Road,
  an inner-city stretch (Shivajinagar/Shantinagar), and a residential arterial.
- **Volume:** ~3–4 hours raw, in dry and wet conditions and at least one dusk run.
- **Labelling:** ~2,000–3,000 frames, in Label Studio or CVAT, split between both CV members.
  Distance-gated extraction means the frames are spatially spread rather than 3,000
  near-duplicates from one junction — sample at 10 m intervals.
- **Split discipline:** split **by route, not by frame.** Random frame splits leak — adjacent
  frames of the same pothole land in train and test and the reported mAP becomes fiction. A
  held-out *route* is the only honest test set.

That last point is worth being firm about internally. It is the most common way a hackathon CV
project ends up reporting a number it cannot reproduce on stage.

---

## Folder layout

```
cv-pipeline/
├── argus_edge/
│   ├── ingest/        source adapters: rtsp | v4l2 | file | image-dir
│   ├── runtime/       ONNX Runtime wrapper + EP selection. The ONLY hardware-aware code.
│   ├── perception/    pre/post-processing, NMS, mask decode, calibration
│   ├── tracking/      ByteTrack, track lifecycle, kinematics
│   ├── geo/           pose interpolation, IPM, map matching, error propagation
│   ├── fusion/        per-pass aggregation: unique counts, occupancy, inspected_for
│   ├── uplink/        priority queue, SQLite spool, MQTT client
│   └── config/        per-device calibration, camera policy, thresholds
├── models/            model cards + export scripts. Weights live in releases, not git.
├── scripts/           train, export-onnx, calibrate-camera, label-assist
├── benchmarks/        the cross-hardware harness → docs/11
└── tests/             golden-frame regression, geometry unit tests, schema conformance
```

## Running it

```bash
cd cv-pipeline && uv sync

# demo path: a video file, live inference, real MQTT
uv run argus-edge \
  --source ./data/bengaluru-orr-morning.mp4 \
  --calib  ./argus_edge/config/rig-phone-1.4m.yaml \
  --device-id BLR-DEMO-01 \
  --route 500D \
  --broker mqtt://localhost:1883

# swap hardware by swapping the execution provider, nothing else
--ep tensorrt-fp16 | hailo-int8 | nnapi-int8 | onnxrt-cuda | onnxrt-cpu

# offline: write a replay log instead of publishing
--sink file://./out/bengaluru-orr.jsonl
```

That last flag is how the demo-day fallback log gets produced: the same run, same code, output
to a file instead of a broker.

## Validation gates

Nothing merges to `main` without:

- **Golden-frame regression** — a fixed set of frames with known expected outputs. Catches a
  post-processing change that silently shifts every mask by two pixels.
- **Geometry unit tests** — synthetic camera, known ground truth, assert IPM recovers the
  planted position within tolerance. Geometry bugs are invisible in a demo and fatal in a map.
- **Schema conformance** — every emitted message validated against `contracts/`.
- **Quantisation delta** — INT8 mAP must be within 2 points of FP32, checked at export.
  Silent quantisation damage is the classic edge-deployment failure.
- **Calibration report** — reliability diagram and ECE for every released model, because
  log-odds fusion downstream is only valid on calibrated confidences.
