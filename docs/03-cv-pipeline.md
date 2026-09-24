# 03 — The CV pipeline (`cv-pipeline/` + `edge-app/`)

**Owners:** CV–Perception (models, accuracy — `cv-pipeline/`) and CV–Edge (the Android app:
streaming, runtime, geometry, uplink — `edge-app/`).
**Interface between them:** one ONNX file and a class list. Nothing else.

> **Decision 2026-09-25 (D11 in the design of record).** The edge runs on **off-the-shelf
> Android phones**, not a dedicated board. Two camera phones (front, rear) stream video over
> the bus's local Wi-Fi to one edge phone, which runs every model and uplinks findings to the
> servers. Dedicated boards (Jetson, Pi + Hailo) are deferred until the phone MVP works; see
> [`docs/11`](11-hardware-benchmark.md).

---

## Design constraints, in priority order

1. **One model artefact, phones now, other hardware later.** The MVP runs on an Android edge
   phone. The model still ships as a single portable ONNX export, so that the dedicated-board
   comparison we will present after the MVP ([`docs/11`](11-hardware-benchmark.md)) measures
   the same model rather than a re-trained one.
2. **Input is a URI, never a device.** `rtsp://front-cam.local:8554/live` (a camera phone) and
   `file:///sdcard/argus/bengaluru.mp4` (recorded footage) go through the identical path. This
   is what makes the demo honest — the video file exercises production code, not a special
   case. It is also what makes the camera count **N, not 2**: a camera is a URI in config.
3. **Findings leave the bus; footage does not.** Video travels only over the bus's own local
   Wi-Fi, camera phone → edge phone, and never over cellular. One narrow, deliberate exception:
   incident clips, over depot wifi.
4. **Never block on the network.** Cellular in a moving bus drops constantly, and a camera
   phone's stream can drop too. Every uplink is fire-and-forget into a local spool, and a lost
   camera stream degrades the pass (it shows in `inspected_for` and telemetry) rather than
   stopping the edge.
5. **Degrade visibly, not silently.** A throttled phone, a camera phone that lost its stream or
   a fogged lens must show up in telemetry. The realistic failure of a 6,400-unit fleet is not
   a crash — it's a bus that quietly stops contributing and nobody notices for three weeks.

---

<a id="on-bus-topology"></a>
## On the bus — three phones

```
   ┌──────────────────┐                        ┌──────────────────┐
   │ FRONT CAMERA     │                        │ REAR CAMERA      │
   │ phone            │                        │ phone            │
   │ windscreen mount │                        │ rear window mount│
   │ CameraX → H.264  │                        │ CameraX → H.264  │
   │ (hardware enc.)  │                        │ (hardware enc.)  │
   │ → RTSP server    │                        │ → RTSP server    │
   └────────┬─────────┘                        └─────────┬────────┘
            │  ~3 Mbps                         ~3 Mbps   │
            │  local Wi-Fi only (WPA2/3, no internet)    │
            └──────────────────┐        ┌────────────────┘
                               ▼        ▼
                   ┌──────────────────────────────────┐
                   │ EDGE phone  (hosts the hotspot)  │
                   │  RTSP client ×N → decode         │
                   │  own GNSS + IMU = the bus's pose │
                   │  ONNX Runtime: every model       │
                   │  track · geo · privacy · queue   │
                   └────────────────┬─────────────────┘
                                    │  MQTT / TLS 1.3 over 4G/5G
                                    ▼  ~12.5 MB per bus per day
                              central platform
```

**One APK, two roles.** The same app is installed on all three phones and started in either
**Camera** or **Edge** mode. Camera mode does nothing but capture, hardware-encode and serve
one stream — so camera phones can be cheap, old handsets. Edge mode is where all the compute
lives, so the edge phone should be a mid-to-upper-range handset with an NPU.

| | Camera phone (× N, MVP N = 2) | Edge phone (× 1) |
|---|---|---|
| Runs | Capture → H.264 encode → RTSP server | RTSP ingest, all models, tracking, geo, privacy gate, uplink |
| Sensors used | Camera only | **GNSS + IMU** (it *is* the bus's pose sensor), cellular modem |
| Mounting | Front windscreen / rear window, fixed pitch, measured height | **Rigidly** fixed to the bus body (so its IMU measures the bus, not a wobbling holder), sky view for GNSS, shaded |
| Power | USB-C from the bus 12 V supply | USB-C from the bus 12 V supply |
| Identity in `contracts/` | A `camera_id` (`front`, `rear`) | The `device_id`. One edge phone = one device, so fusion's "distinct devices" still means distinct buses |

**The local network.** The edge phone hosts a WPA2/WPA3 hotspot that only the camera phones
join, and shares its cellular link with no one. Two 1080p streams need ~6 Mbps; phone Wi-Fi
sustains many times that, so the local link is not the bottleneck — edge-phone compute is.

**Time sync across phones — the easy-to-miss part.** Geo-referencing interpolates the edge
phone's GNSS/IMU to the moment a frame was *captured*, not the moment it *arrived*. Stream
latency is 100–300 ms; at 60 km/h, using arrival time would misplace every detection by up to
5 m. So the edge phone is the time master (GNSS-disciplined), each camera phone runs an
NTP-style offset handshake with it on connect and every 30 s, and every frame carries its
capture timestamp through RTP/RTCP sender reports mapped through that offset. Target: ≤ 20 ms.

**Candidate libraries** (licence-checked in week 1, same discipline as the models): CameraX +
MediaCodec for capture and encode, an RTSP server library on the camera side (e.g.
RootEncoder), AndroidX Media3's RTSP client on the edge side, ONNX Runtime Android, and an
MQTT client (e.g. HiveMQ MQTT Client). Kotlin throughout.

---

## The runtime abstraction

```
             ┌────────────────────────────────────┐
             │   argus-road-defect-v0.4.2.onnx    │   ← ONE artefact
             └─────────────────┬──────────────────┘
                               │
        ┌──────────────────────┼─────────────────────────────────┐
        ▼                      ▼                                  ▼
   NNAPI EP              CPU EP (XNNPACK)              later, same file:
   (edge phone NPU,      (fallback on phones           TensorRT (Orin),
    INT8)                 without a usable NPU)         HailoRT (Pi + Hailo)
        │                      │                       — docs/11, after MVP
        └──────────┬───────────┘
                   ▼
        identical Observation output
```

`edge-app/.../runtime/` exposes exactly one function — `infer(frames) -> Detections` — and the
execution provider is selected from config at startup, with a CPU fallback if the NPU path
fails to load. **No perception code anywhere else in the repository knows which chip it is
running on.**

The laptop is not an edge target any more. It is where CV–Perception trains, exports and
evaluates, and where the Python reference post-processing produces the golden frames the app
is tested against (see [Validation gates](#validation-gates)).

### Why ONNX rather than each vendor's native path

Vendor toolchains give maybe 10–20% more throughput. They also fork your model per chip,
multiply your validation matrix, and make the post-MVP question "which dedicated board, if
any, should the government buy" unanswerable. For a 10-week project with two CV people,
portability is worth more than the last 15%.

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

The brief says front, rear, sides and cabin. The architecture takes **N camera phones** — each
is one RTSP URI in the edge phone's config — but the MVP fits **two**, and every camera is
paid for in *edge-phone* compute, because that is where every model runs.

| Camera | MVP | Rate on the edge phone | Runs | Why |
|---|---|---|---|---|
| **Front** | ✅ phone | full (distance-gated + 15 Hz track) | everything | The primary sensor. Road ahead, traffic, VRUs, incidents. |
| **Rear** | ✅ phone | 5 Hz, **escalates to full on incident** | vehicle detect, plate | The tailgater's plate is behind the bus. Idle most of the time; the incident trigger wakes it. |
| Left / right | roadmap | 1 Hz | encroachment, footpath, stop-area crowd | Side content changes slowly and is about static infrastructure, not events. |
| Cabin | roadmap | 0.2 Hz | occupancy count only, **faces blurred pre-storage** | Occupancy for load analytics. See [`08`](08-privacy-and-compliance.md). |

Camera phones always stream at their full encoded rate; the **edge phone decides what to
decode and infer**. The rear stream is decoded at 5 Hz until an incident trigger, then at full
rate for twenty seconds. Event-driven escalation is what makes a second camera cost well under
2× — the rear camera is cheap until the moment it matters.

**The compute budget is the tightest constraint in the MVP.** One phone decoding two streams
and running a 15 Hz vehicle path, a distance-gated defect path and a 5 Hz rear path is a lot to
ask, and it will get hot. It is measured, not assumed ([`docs/11`](11-hardware-benchmark.md)).
If it doesn't fit, the levers, in order: vehicle path 15 → 10 Hz, rear idle rate 5 → 2 Hz,
defect path to INT8 first. Adding a third camera is a config line plus a measurement.

---

## Tracking

**ByteTrack**, chosen for one specific property: it associates *low-confidence* detections in
a second pass rather than discarding them. Footage from a moving bus — motion blur, rain,
partial occlusion behind an auto — produces exactly that population of weak detections, and a
tracker that throws them away fragments tracks constantly.

It also uses **no appearance embedding network**, which means no second model in the edge
compute budget. On one edge phone already decoding two streams, that difference decides whether
the pipeline fits at all.

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

1. **IMU-derived pitch correction** per frame — the edge phone's accelerometer already tells
   us the body attitude, so use it rather than assuming the calibrated value. This only works
   because the edge phone is **rigidly** mounted to the bus body; the camera phones are
   calibrated relative to it once, at install.
2. **Range gating.** Detections beyond 25 m carry reduced fusion weight; beyond 35 m they are
   dropped. Error grows roughly with range squared, so the far field is nearly worthless for
   localisation even though it's fine for *detection*.
3. **Reject frames under high longitudinal acceleration** — don't survey while braking hard.

### Map matching

Raw lat/lon is snapped to the OSM road graph with an HMM map-matcher — on the phone, our own
Viterbi over candidate segments of a Bengaluru road graph baked into the APK (the same OSM
extent the frontend bakes; Valhalla is too heavy to embed). This yields `segment_id`, `offset_m` and `ward_id`.

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
| Frame/GNSS time sync (camera-phone capture time → edge-phone pose, ≤ 20 ms; see [topology](#on-bus-topology)) | ±0.3 m |
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
  2 cameras (front + rear) × 1080p30 × H.264 @ ~3 Mbps   =   6 Mbps
  × 16 operating hours                                   =  43.2 GB per bus per day
  × 6,400 BMTC buses                                     ≈  276 TB per day
```

Roughly 100 PB a year — and that is only the MVP's two cameras. The brief's full fit-out of
front, rear, sides and cabin roughly doubles it (4 cameras ≈ 86 GB/bus/day, ≈ 553 TB/day).
There is no cellular plan, no backhaul and no storage budget in any municipal corporation for
which that is a real option. This is *why* the problem statement demands edge processing, and
it's worth showing the number rather than repeating the phrase.

Those 6 Mbps do exist on the bus — but only on the local Wi-Fi between the camera phones and
the edge phone, which costs nothing and leaves nothing behind.

### What ARGUS sends over cellular

| Message | Volume/bus/day | Size | Total |
|---|---:|---:|---:|
| `observation` (JSON + JPEG crop) | ~200 | ~47 KB | 9.4 MB |
| `segment_pass` | ~400 | ~1.5 KB | 0.6 MB |
| `telemetry` (5 s moving, 30 s idle) | ~9,000 | ~250 B | 2.3 MB |
| `incident` metadata + plate crop | ~2 | ~120 KB | 0.2 MB |
| **Total over cellular** | | | **≈ 12.5 MB/bus/day** |
| **× 6,400 buses** | | | **≈ 80 GB/day** |

**Reduction factor: ~3,500×** against our two cameras (~6,900× against a four-camera fit-out).

### The exception, and why it's designed this way

A 30-second 1080p incident clip is ~11 MB. At 1–3 incidents per bus per day that is
**11–33 MB — more than everything else combined.** Sending them over cellular would blow the
entire budget on the rarest message type.

So incident clips **spool locally and upload over depot wifi overnight**, on an unmetered link.
The edge phone keeps a rolling 30-second buffer of each incoming stream *as received* — it is
already H.264, so buffering costs memory, not re-encoding — and writes it, encrypted, only when
an incident fires.
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
- **SQLite spool** on the edge phone's storage, so a cellular dropout or a power cycle loses nothing. `queue_depth`
  and `queue_oldest_s` ride in telemetry, making uplink health a visible fleet KPI rather than
  an invisible failure.

---

## The privacy gate

Faces are blurred **before any frame is written to disk or entered into the queue** — not at
the server, not at display time. Camera phones never write frames to disk at all; they only
stream, over a hotspot that has no route to the internet. A lightweight face detector runs on every crop destined for
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

- **Rig:** the same arrangement as the product — a front phone on the windscreen at ~1.4 m and a
  rear phone, fixed pitch, with a GNSS logger (or the edge phone recording its own GNSS + IMU).
  Note the height difference from a real bus windscreen (~2–3 m) — the IPM calibration differs
  and must be measured, not assumed. This is a known, documented gap between demo rig and
  deployment geometry, not something to paper over.
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
cv-pipeline/                 CV–Perception: Python, runs on a laptop / GPU box
├── models/                  model cards + export scripts. Weights live in releases, not git.
├── scripts/                 train, export-onnx, calibrate-confidence, label-assist
├── reference/               Python reference pre/post-processing → golden frames for the app
└── tests/                   export checks, quantisation delta, calibration report

edge-app/                    CV–Edge: Kotlin Android app, one APK, two roles
├── app/src/main/java/.../
│   ├── camera/              CAMERA role: CameraX → MediaCodec H.264 → RTSP server, time-sync client
│   ├── ingest/              EDGE role: source adapters — rtsp:// | file://, capture-time stamping
│   ├── runtime/             ONNX Runtime + EP selection. The ONLY hardware-aware code.
│   ├── perception/          pre/post-processing, NMS, mask decode, calibration
│   ├── tracking/            ByteTrack, track lifecycle, kinematics
│   ├── geo/                 pose interpolation, IPM, map matching over the baked road graph
│   ├── fusion/              per-pass aggregation: unique counts, occupancy, inspected_for
│   ├── privacy/             face blur before anything touches storage
│   ├── uplink/              priority queue, SQLite spool, MQTT client
│   └── config/              per-camera calibration, camera URIs + policy, thresholds
├── app/src/main/assets/     the ONNX model(s) + class list + baked road graph, packed into the APK
├── release/                 the committed, versioned APK: argus-edge-<version>.apk
└── app/src/test/            golden-frame regression, geometry unit tests, schema conformance
```

## Running it

Install `edge-app/release/argus-edge-<version>.apk` on all three phones.

1. **Edge phone:** open ARGUS → *Edge* mode. It starts the local hotspot, shows its SSID, and
   waits for cameras. Set `device_id`, bus and route once in settings.
2. **Each camera phone:** join that hotspot → ARGUS → *Camera* mode → pick `front` or `rear`.
   It announces itself; the edge phone shows both streams, their FPS and clock offset.
3. **Calibrate** each camera once per mounting (a printed checkerboard on the road, guided in
   the app). Calibration is stored per `camera_id`.
4. Edge phone → **Start**. Findings flow to the broker configured in settings
   (`mqtts://…` in the field, `mqtt://<laptop-ip>:1883` for development).

**Demo path — no bus required.** In Edge mode, point a camera at a file instead of a phone:
`file:///sdcard/argus/bengaluru-orr-morning.mp4` (with its GNSS track alongside). Same code,
same models, real MQTT.

**Replay log.** Settings → *Record messages to file* writes every outgoing message to a
`.jsonl` on the phone as well as publishing it. Pull it with `adb pull` into
`ops/replay/logs/`; that is how the demo-day fallback log is produced — the same run, same
code, output to a file as well as a broker.

## Validation gates

Nothing merges to `main` without:

- **Golden-frame regression** — a fixed set of frames with known expected outputs, produced by
  `cv-pipeline/reference/` and asserted by the app's tests. Catches a post-processing change —
  or a Kotlin port that disagrees with the Python the model was trained against — that silently
  shifts every mask by two pixels.
- **Geometry unit tests** — synthetic camera, known ground truth, assert IPM recovers the
  planted position within tolerance. Geometry bugs are invisible in a demo and fatal in a map.
- **Schema conformance** — every emitted message validated against `contracts/`.
- **Quantisation delta** — INT8 mAP must be within 2 points of FP32, checked at export.
  Silent quantisation damage is the classic edge-deployment failure.
- **Calibration report** — reliability diagram and ECE for every released model, because
  log-odds fusion downstream is only valid on calibrated confidences.
