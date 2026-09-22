# `cv-pipeline/` — edge AI

**Video in, `Observation`s out.** Runs unmodified on Jetson Orin, Raspberry Pi 5 + Hailo-8L,
an Android phone, or a dev laptop, from one ONNX file.

Full design: [`docs/03-cv-pipeline.md`](../docs/03-cv-pipeline.md).
What gets detected and why: [`docs/02-detection-taxonomy.md`](../docs/02-detection-taxonomy.md).

## Two owners, one interface

| Owner | Scope | Deliverable |
|---|---|---|
| **CV-Perception** | Datasets, labelling, training, accuracy, calibration | An ONNX file + class list + validation report |
| **CV-Edge** | Ingest, runtime, tracking, geometry, fusion-per-pass, uplink, benchmarks | A URI in → schema-valid messages out |

The interface between them is **the ONNX file and nothing else**. CV-Edge starts on a
COCO-pretrained model in week 1 and swaps in the real one later without touching pipeline code.

## Layout

```
argus_edge/
├── ingest/       source adapters — rtsp:// | v4l2:// | file:// | dir://
├── runtime/      ONNX Runtime + execution-provider selection.
│                 THE ONLY HARDWARE-AWARE CODE IN THE REPOSITORY.
├── perception/   pre/post-processing, NMS, mask decode, confidence calibration
├── tracking/     ByteTrack, track lifecycle, kinematics for incidents
├── geo/          pose interpolation, IPM, map matching, error propagation
├── fusion/       per-pass aggregation → unique counts, occupancy, inspected_for
├── uplink/       severity-aged priority queue, SQLite spool, MQTT client
└── config/       per-device camera calibration, camera policy, thresholds
```

## Run it

```bash
uv sync

uv run argus-edge \
  --source ./data/bengaluru-orr-morning.mp4 \
  --calib  ./argus_edge/config/rig-phone-1.4m.yaml \
  --device-id BLR-DEMO-01 --route 500D \
  --broker mqtt://localhost:1883

# swap hardware by swapping ONE flag
--ep tensorrt-int8 | hailo-int8 | nnapi-int8 | onnxrt-cuda | onnxrt-cpu

# write a replay log instead of publishing (this is how the demo fallback is produced)
--sink file://./out/bengaluru-orr.jsonl
```

## Non-negotiables

1. **The edge never emits a `missing_*` class.** You cannot bound-box an absence. Absence is a
   backend conclusion drawn from `SegmentPass.inspection`.
2. **Faces are blurred before any frame touches disk.** Not at the server, not at display time.
3. **Physical quantities in metres, never pixels.** Pixel areas aren't fusable across passes.
4. **Confidence is calibrated.** Log-odds fusion downstream is only valid on calibrated scores;
   every released model ships a reliability diagram.
5. **Distance-gated sampling, not time-gated.** A stopped bus infers nothing; a fast bus doesn't
   skip road.
6. **Never block on the network.** Every uplink is fire-and-forget into the local spool.

## Gates before merge

- Golden-frame regression (catches silent post-processing drift)
- Geometry unit tests — synthetic camera, planted ground truth, assert IPM recovers it
- Schema conformance against `contracts/`
- INT8 mAP within 2 points of FP32
- Calibration report (reliability diagram + ECE)

## Dataset discipline

**Split by route, never by frame.** Adjacent frames of the same pothole landing in both train
and test makes the reported mAP fiction. A held-out *route* is the only honest test set, and
this is the single most common way a hackathon CV result fails to reproduce on stage.
