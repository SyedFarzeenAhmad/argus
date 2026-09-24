# `edge-app/` — CV–Edge, the Android app

**Camera phones stream in; the processing client finds potholes and prepares what the servers
need.** One Kotlin APK, installed on every phone on the bus. On first launch the home screen
asks what this phone is:

| Role | Phones | Does |
|---|---|---|
| **Camera** | 1…N (MVP: `front`, `rear`) | Live preview. Pick a position, link to a processing client, stream. Stores nothing. |
| **Processing client** | 1 per bus | Accepts any number of cameras, runs pothole detection on every stream, keeps the full record in `processed/`, puts only what the servers need in `helpful/`, and serves `helpful/` to the backend. |

```
  camera phone (front) ─┐  JPEG frames over TCP :7070        ┌───────────────────────┐
  camera phone (rear)  ─┼──────────────────────────────────▶ │  processing client    │
  camera phone (…N)    ─┘  same Wi-Fi / hotspot, no internet │  pothole model · GPS  │
                                                              │  processed/  helpful/ │
                                                              └──────────┬────────────┘
                                          backend pulls helpful/ over    │ HTTP :8080
                                          GET … then DELETE once stored  ▼
```

Design: [`docs/03`](../docs/03-cv-pipeline.md#on-bus-topology). Backend hand-off:
[`docs/04`](../docs/04-backend.md#edge-helpful-consumer). Decision record: D11 in
[the design of record](../docs/superpowers/specs/2026-09-22-argus-platform-design.md).

## Install and run

The release APK is committed: **`release/argus-edge-0.1.0.apk`** (arm64-v8a + armeabi-v7a,
Android 8.0+). It contains the pothole model, so nothing else is needed on the phone.

```bash
adb install -r release/argus-edge-0.1.0.apk     # or copy the APK to the phone and open it
```

1. Put all phones on **one network** — the processing phone's hotspot, or the bus Wi-Fi.
2. **Processing phone:** open ARGUS → *Processing client*. Allow location. It shows its
   address, and advertises itself on the network.
3. **Each camera phone:** open ARGUS → *Camera* → pick *Front* / *Rear* / … → tap the processing
   client in the list (or type its address). It streams, and after a reboot it re-links by
   itself.
4. **Processing phone:** press ▶. Every camera tile shows live frames; detected potholes get
   boxes, a thumbnail in *Recent potholes*, and a record in `processed/` + `helpful/`.

**No second phone? No bus?** *Test video* on the processing screen plays any video file as if it
were one more linked camera — same frame path, same models. Sampling is **every 5 m of travel**
(a stopped bus runs no inference); switch to *Every 0.5 s (test)* for a bench test without
moving.

## The two folders on the processing client

`/sdcard/Android/data/com.argus.edge/files/argus/` — app storage, no permission needed,
`adb pull`-able.

| Folder | What | Lifetime |
|---|---|---|
| `processed/<session>/` | `session.json`; `detections.jsonl` — one line per inference, every camera, with GNSS, latency and every box; `frames/` — annotated full frames with potholes; `crops/` — every pothole crop | Stays on the phone |
| `helpful/` | `<utc>-obs-<id>.json` + `.jpg` — one contract `Observation` + evidence crop per pothole; `<utc>-tlm.json` — contract `Telemetry` every 30 s | Until the backend consumes and **deletes** it |

Everything written to either folder is **face-blurred first** (ML Kit, on-device, fails closed:
no blur → no image). A pothole seen without a GPS fix goes to `processed/` only — a pothole
without a position is not useful to the server.

## Local API (processing client, port 8080)

`Authorization: Bearer <token>` — the token and endpoint are on the processing screen, tap to
copy.

| | |
|---|---|
| `GET /api/v1/status` | device, session, cameras (fps, latency, clock offset), GNSS, pending |
| `GET /api/v1/helpful` | list, oldest first |
| `GET /api/v1/helpful/{name}` | one file |
| `DELETE /api/v1/helpful/{name}` | consumed → delete (an observation also deletes its JPEG) |

The backend consumer that uses this is specified in
[`docs/04`](../docs/04-backend.md#edge-helpful-consumer).

## What is in the MVP, and what is not

| In | Not yet |
|---|---|
| Camera / processing roles, N cameras, discovery + manual address, auto re-link | Foreground service (the processing screen keeps itself awake instead) |
| Streaming with capture-time clock sync (NTP-style) and flow control | H.264/RTSP transport (MVP sends JPEG frames — simpler, fine on local Wi-Fi) |
| Pothole detection (YOLO11n, ONNX Runtime CPU) on every camera | Other classes; NPU/INT8 execution |
| Distance-gated sampling, GNSS jump rejection | IPM: `geo` is the **bus's** position (`source: "gnss"`), not the pothole's |
| On-device face blur before any write | Calibrated confidence — scores are the model's raw output |
| `processed/` + `helpful/`, contract-valid JSON, local API | MQTT uplink (the backend pulls instead, for now) |

**The model is a prototype.** `tahaUgan/pothole-yolo11n` from Hugging Face (weights CC-BY-4.0,
trained with Ultralytics, which is AGPL-3.0 — see [`docs/03`](../docs/03-cv-pipeline.md#model-licensing)).
It was not trained on Bengaluru roads and its accuracy here is unmeasured. It is replaced by
CV-Perception's model by dropping a new `pothole.onnx` into `app/src/main/assets/models/`
(same YOLO detect output, class 0 = pothole).

## Build

```bash
scripts/fetch_model.sh                 # downloads + exports pothole.onnx (weights are not in git)
./gradlew assembleRelease              # needs JDK 17 + Android SDK 35
cp app/build/outputs/apk/release/app-release.apk release/argus-edge-<version>.apk
```

The release build is signed with the **debug key** so the committed APK installs anywhere.
Replace it with a real signing key before any fleet deployment.

## Tests

```bash
./gradlew testDebugUnitTest                                           # 13 JVM tests
uv run --with jsonschema --with referencing python scripts/validate_contracts.py
```

- Detection maths: letterbox, YOLO decode, NMS
- Wire protocol round-trip, clock-sync estimator, distance/time gate
- Contract shape: sample `Observation` / `Telemetry` validated against `contracts/schemas/`

Verified end to end on two Android emulators (processing + camera): streaming with ≤ 5 ms
clock offset, detection on a pothole test video, `helpful/` files validating against the
schemas, and the API's list / fetch / hash / delete behaviour.

## Layout

```
app/src/main/java/com/argus/edge/
├── MainActivity.kt, ArgusApp.kt
├── core/         prefs, time formats, network helpers
├── link/         wire protocol + clock sync, discovery (mDNS), frame server, frame client
├── camera/       CameraX analyzer → JPEG → frame client
├── processing/   detector (ONNX), decode + NMS, face blur, GNSS tracker, frame gate,
│                 storage (processed/ + helpful/), contract messages, local API,
│                 video test source, EdgeEngine (the loop)
└── ui/           theme, components, role / camera / processing screens, settings
```
