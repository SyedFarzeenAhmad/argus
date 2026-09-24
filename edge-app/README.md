# `edge-app/` — CV–Edge, the Android app

**Camera phones stream in; the processing client finds potholes and road cracks, counts
vehicles and pedestrians, keeps the record, and collects training data.** One Kotlin APK, installed on every phone on the bus. On first launch
the home screen asks what this phone is:

| Role | Phones | Does |
|---|---|---|
| **Camera** | 1…N (MVP: `front`, `rear`) | Live preview. Pick a position, link to a processing client, stream. Stores nothing. |
| **Processing client** | 1 per bus | Accepts any number of cameras, runs three detectors on every stream, writes findings to `processed/<category>/`, optionally records training frames to `dataset/`, and serves `processed/` to the backend (read-only). |

```
  camera phone (front) ─┐  JPEG frames over TCP :7070        ┌────────────────────────┐
  camera phone (rear)  ─┼──────────────────────────────────▶ │  processing client     │
  camera phone (…N)    ─┘  same Wi-Fi / hotspot, no internet │  pothole model · GPS   │
                                                              │  processed/  dataset/  │
                                                              └──────────┬─────────────┘
                                   backend reads processed/ over         │ HTTP :8080, GET only
                                   the read-only API (never deletes)     ▼
```

## What it detects (v0.3.0)

| Detector | Model (prototype) | Runs | Writes |
|---|---|---|---|
| **Potholes** | YOLO11n — `tahaUgan/pothole-yolo11n` | every 5 m | `processed/pothole/` — contract `Observation` |
| **Road cracks** — longitudinal, transverse, alligator | YOLOv8n on RDD2022 — `dronefreak/rdd2022-yolov8n` (test mAP@50 58.8%) | every 5 m | `processed/damaged_road/` — `Observation`, `class_id: damaged_road` + `subclass` |
| **Traffic** — car, two-wheeler, bus, truck, bicycle, pedestrian | YOLO11n COCO + IoU tracker | 4 fps per camera | `processed/traffic_counting/` — unique counts per camera per 30 s |

Each can be switched off on the processing screen (Potholes / Cracks / Traffic chips) to save
compute. Known gaps: **no auto-rickshaw class** (COCO lacks it), the simple tracker can
over-count fast oncoming traffic, cracks are boxes rather than % distressed area, and all three
models are untested on Bengaluru roads — see [`docs/02`](../docs/02-detection-taxonomy.md#mvp-status).

Design: [`docs/03`](../docs/03-cv-pipeline.md#on-bus-topology). Backend hand-off:
[`docs/04`](../docs/04-backend.md#edge-processed-consumer). Decision record: D11 in
[the design of record](../docs/superpowers/specs/2026-09-22-argus-platform-design.md).

## Install and run

The release APK is committed: **`release/argus-edge-0.3.0.apk`** (arm64-v8a + armeabi-v7a,
Android 8.0+, 78 MB). It contains all three models, so nothing else is needed on the phone.

```bash
adb install -r release/argus-edge-0.3.0.apk     # or copy the APK to the phone and open it
```

1. Put all phones on **one network** — the processing phone's hotspot, or the bus Wi-Fi.
2. **Processing phone:** open ARGUS → *Processing client*. Allow location. Set device, bus and
   **route** in settings (the route names the dataset folder).
3. **Each camera phone:** open ARGUS → *Camera* → pick *Front* / *Rear* / … → tap the processing
   client in the list (or type its address). It streams, and after a reboot it re-links by
   itself.
4. **Processing phone:** press ▶. Camera tiles show live frames with boxes — potholes amber,
   cracks pink, vehicles blue, pedestrians green. Road defects also get a thumbnail in *Recent
   road defects* and a record in `processed/`.
5. **Collecting training data:** switch on *Record training frames*. Camera phones switch to
   1080p immediately (their screen shows *Dataset · 1080p*).

**No second phone? No bus?** *Test video* plays any video file as one more linked camera —
same frame path, same models. Sampling is **distance-gated** (detection every 5 m, dataset
every 10 m; a stopped bus does neither); *Every 0.5 s (test)* is for bench tests without moving.

## What the processing client stores

`/sdcard/Android/data/com.argus.edge/files/argus/` — app storage, no permission, `adb pull`-able.

```
processed/
├── pothole/          <utc>-<id>.json         contract Observation (only with a GPS fix)
│                     <utc>-<id>.jpg          evidence crop
│                     <utc>-<camera>-frame.jpg  full frame, every box drawn, for review
├── damaged_road/     same layout, class_id damaged_road + subclass (crack type)
├── traffic_counting/ <utc>-<camera>.json     unique vehicles + pedestrians per 30 s (interim format)
├── telemetry/        <utc>.json              contract Telemetry, every 30 s
└── log/         <session>.jsonl        every inference on every camera, incl. nothing-found
dataset/
└── <session>_<route>/
    ├── session.json
    ├── manifest.jsonl                  per frame: time, camera, GNSS, speed, heading, light
    ├── frames/<camera>/<utc>_<seq>.jpg clean frame exactly as streamed, nothing drawn
    ├── prelabels/<camera>/<utc>_<seq>.txt  prototype road-defect boxes, YOLO format, for CVAT
    └── classes.txt                     0 pothole, 1 longitudinal, 2 transverse, 3 alligator crack
```

- **Categories appear as models land.** Today: `pothole`, `damaged_road`, `traffic_counting`,
  `telemetry`. Next: `incidents`, … with the same file conventions.
- **Nothing is deleted by the backend.** Clear the phone by hand (or `adb shell rm`) when needed.
- **Dataset capture** saves a frame every 10 m from every camera **whether or not a pothole was
  seen**, stops itself below 1 GB free, and is pulled with
  `adb pull /sdcard/Android/data/com.argus.edge/files/argus/dataset/`. See
  [`docs/03`](../docs/03-cv-pipeline.md#dataset-capture).
- **Face blurring is deferred** — frames are stored as captured; blurring will be done later
  ([`docs/08`](../docs/08-privacy-and-compliance.md#face-blurring-deferred)).

## Local API (processing client, port 8080, GET only)

`Authorization: Bearer <token>` — the token and endpoint are on the processing screen, tap to
copy.

| | |
|---|---|
| `GET /api/v1/status` | device, session, cameras (fps, latency, clock offset), GNSS, record counts |
| `GET /api/v1/processed` | categories and record counts |
| `GET /api/v1/processed/{category}?after=<name>&limit=<n>` | files, oldest first, after a cursor |
| `GET /api/v1/processed/{category}/{name}` | one file |

The backend consumer (cursor per device and category, no deletes) is specified in
[`docs/04`](../docs/04-backend.md#edge-processed-consumer).

## What is in the MVP, and what is not

| In | Not yet |
|---|---|
| Camera / processing roles, N cameras, discovery + manual address, auto re-link | Foreground service (the processing screen keeps itself awake instead) |
| Streaming with capture-time clock sync and flow control; 1080p switch for capture | H.264/RTSP transport (MVP sends JPEG frames) |
| Potholes, road cracks, vehicle + pedestrian counting (ONNX Runtime CPU) on every camera | Other classes, auto-rickshaws; NPU/INT8 execution |
| Distance-gated detection and dataset capture; GNSS jump rejection | IPM: `geo` is the **bus's** position (`source: "gnss"`), not the pothole's |
| `processed/<category>/`, contract-valid JSON, read-only API | Face blurring — deferred ([`docs/08`](../docs/08-privacy-and-compliance.md#face-blurring-deferred)) |
| Dataset capture with manifest and YOLO pre-labels | Calibrated confidence; MQTT uplink |

**The models are prototypes.** All three are public weights trained with Ultralytics, which is
AGPL-3.0 (see [`docs/03`](../docs/03-cv-pipeline.md#model-licensing)); none was trained on
Bengaluru roads, and accuracy here is unmeasured. CV-Perception replaces them file by file in
`app/src/main/assets/models/` — `pothole.onnx`, `road_damage.onnx`, `traffic.onnx` — keeping the
YOLO detect output and the class indices in `processing/Models.kt`.

## Build

```bash
scripts/fetch_models.sh                # downloads + exports the three models (weights are not in git)
./gradlew assembleRelease              # needs JDK 17 + Android SDK 35
cp app/build/outputs/apk/release/app-release.apk release/argus-edge-<version>.apk
```

The release build is signed with the **debug key** so the committed APK installs anywhere.
Replace it with a real signing key before any fleet deployment.

## Tests

```bash
./gradlew testDebugUnitTest                                           # 20 JVM tests
uv run --with jsonschema --with referencing python scripts/validate_contracts.py
```

- Detection maths: letterbox, YOLO decode, NMS
- Wire protocol round-trip, stream config, clock-sync estimator, distance/time gate
- Tracker counting: one vehicle counted once, class flicker, leave-and-return, crowds
- Contract shape: sample `Observation` / `Telemetry` validated against `contracts/schemas/`

Verified end to end on two Android emulators (processing + camera): streaming with clock sync,
detection on a mixed test video (potholes, Japanese RDD2022 crack scenes, a street with a bus
and pedestrians), live `processed/` records validating against the schemas,
the read-only API and its cursor, and dataset capture (clean frames with and without potholes,
manifest, YOLO pre-labels matching the pixel boxes). **Not yet run on physical phones.**

## Layout

```
app/src/main/java/com/argus/edge/
├── MainActivity.kt, ArgusApp.kt
├── core/         prefs, time formats, network helpers
├── link/         wire protocol, stream config, clock sync, discovery, frame server + client
├── camera/       CameraX analyzer → JPEG → frame client
├── processing/   models + YOLO detector (ONNX), decode + NMS, IoU tracker, GNSS tracker, frame gate, storage,
│                 contract messages, dataset recorder, local API, video test source,
│                 EdgeEngine (the loop)
└── ui/           theme, components, role / camera / processing screens, settings
```
