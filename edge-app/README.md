# `edge-app/` — CV–Edge, the Android app

**Camera phones stream in; findings go out.** One Kotlin APK, installed on every phone on the
bus and started in one of two roles:

| Role | Phones (MVP) | Does |
|---|---|---|
| **Camera** | 2 — `front`, `rear` | CameraX capture → hardware H.264 → RTSP server over the local hotspot. Stamps capture time. Stores nothing. |
| **Edge** | 1 | Hosts the hotspot. Ingests N `rtsp://` (or `file://`) streams, runs every model, tracks, geo-references with its own GNSS + IMU, blurs faces, queues and uplinks over MQTT/TLS. |

```
  front phone ─┐  RTSP over local Wi-Fi          ┌──────────────┐  MQTT/TLS, 4G/5G
               ├───────────────────────────────▶ │  edge phone  │ ────────────────▶ servers
  rear phone ──┘  (hotspot, no internet)         └──────────────┘  ~12.5 MB/bus/day
```

Full design: [`docs/03-cv-pipeline.md`](../docs/03-cv-pipeline.md#on-bus-topology).
Decision record: D11 in [the design of record](../docs/superpowers/specs/2026-09-22-argus-platform-design.md).

## The APK

The versioned release APK is committed at **`edge-app/release/argus-edge-<version>.apk`**.
It contains the ONNX model(s), the class list and the baked Bengaluru road graph, so a phone
needs nothing else installed.

```bash
./gradlew assembleRelease
cp app/build/outputs/apk/release/app-release.apk release/argus-edge-<version>.apk
adb install -r release/argus-edge-<version>.apk
```

## Layout

```
edge-app/
├── app/src/main/java/.../
│   ├── camera/       CAMERA role: CameraX → MediaCodec H.264 → RTSP server, time-sync client
│   ├── ingest/       EDGE role: rtsp:// | file:// sources, capture-time stamping
│   ├── runtime/      ONNX Runtime Android + EP selection (NNAPI → CPU fallback).
│   │                 THE ONLY HARDWARE-AWARE CODE.
│   ├── perception/   pre/post-processing, NMS, mask decode, confidence calibration
│   ├── tracking/     ByteTrack, track lifecycle, kinematics for incidents
│   ├── geo/          pose interpolation, IPM, map matching over the baked road graph
│   ├── fusion/       per-pass aggregation → unique counts, occupancy, inspected_for
│   ├── privacy/      face blur before anything touches storage
│   ├── uplink/       severity-aged priority queue, SQLite spool, MQTT client
│   └── config/       per-camera calibration, camera URIs + rate policy, thresholds
├── app/src/main/assets/   models, class list, road graph
├── app/src/test/          golden frames, geometry, schema conformance
└── release/               committed APKs
```

Message types come from `contracts/` (generated Kotlin) — never hand-written.

## Non-negotiables

1. **Frames carry capture time, not arrival time.** Camera clocks are synced to the edge phone
   to ≤ 20 ms. Stream latency is 100–300 ms; using arrival time misplaces detections by metres.
2. **Camera phones store nothing.** They capture and stream, over a hotspot with no internet.
3. **Faces are blurred before any frame touches the edge phone's disk.** Not at the server, not
   at display time.
4. **The edge never emits a `missing_*` class.** Absence is a backend conclusion from
   `SegmentPass.inspection`.
5. **A lost camera degrades, never stops.** Its classes leave `inspected_for` and it leaves
   `cameras_online`; the other streams keep running.
6. **Distance-gated sampling, not time-gated.** A stopped bus infers nothing; a fast bus doesn't
   skip road.
7. **Never block on the network.** Every uplink is fire-and-forget into the local spool.
8. **The edge phone is mounted rigidly.** Its IMU is the bus's pitch and impact sensor.

## Gates before a release APK is committed

- Golden-frame regression against `cv-pipeline/reference/`
- Geometry unit tests — synthetic camera, planted ground truth, assert IPM recovers it
- Schema conformance against `contracts/`
- Two-stream soak on the edge phone, results logged per [`docs/11`](../docs/11-hardware-benchmark.md)
