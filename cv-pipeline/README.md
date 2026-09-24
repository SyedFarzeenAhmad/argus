# `cv-pipeline/` — CV–Perception

**Footage in, one ONNX file out.** Datasets, labelling, training, export, confidence
calibration and validation. Runs on a laptop or GPU box — **not on the bus.** The bus runs the
Android app in [`edge-app/`](../edge-app/README.md), which packs this folder's ONNX file into
its APK.

Full design: [`docs/03-cv-pipeline.md`](../docs/03-cv-pipeline.md).
What gets detected and why: [`docs/02-detection-taxonomy.md`](../docs/02-detection-taxonomy.md).

## Two owners, one interface

| Owner | Folder | Scope | Deliverable |
|---|---|---|---|
| **CV-Perception** | `cv-pipeline/` | Datasets, labelling, training, accuracy, calibration | An ONNX file + class list + validation report |
| **CV-Edge** | `edge-app/` | Camera streaming, ingest, runtime, tracking, geometry, fusion-per-pass, uplink | An APK: camera phones stream in, schema-valid messages out of the edge phone |

The interface between them is **the ONNX file and nothing else**. CV-Edge starts on a
COCO-pretrained model in week 1 and swaps in the real one later without touching app code.

## Layout

```
cv-pipeline/
├── models/       model cards + export scripts. Weights live in releases, not git.
├── scripts/      train, export-onnx, calibrate-confidence, label-assist
├── reference/    Python reference pre/post-processing (NMS, mask decode, calibration).
│                 Produces the golden frames the edge app's tests must match.
├── benchmarks/   report generator for the phone measurements → docs/11
└── tests/        export checks, quantisation delta, calibration report
```

## Run it

```bash
uv sync --extra train

# train, export, check
uv run python scripts/train.py --config configs/pothole-seg.yaml
uv run python scripts/export_onnx.py --weights runs/pothole-seg/best.pt --imgsz 960 544
uv run pytest                       # export checks, quantisation delta, calibration

# hand the model to CV-Edge
cp models/argus-road-defect-<ver>.onnx ../edge-app/app/src/main/assets/
```

## Non-negotiables

1. **The model never emits a `missing_*` class.** You cannot bound-box an absence. Absence is a
   backend conclusion drawn from `SegmentPass.inspection`.
2. **Exports must run on ONNX Runtime Android** (NNAPI and CPU). Check operator support at
   export, not when the app crashes on a phone.
3. **Physical quantities in metres, never pixels.** Pixel areas aren't fusable across passes.
4. **Confidence is calibrated.** Log-odds fusion downstream is only valid on calibrated scores;
   every released model ships a reliability diagram.
5. **One portable artefact.** No phone-specific retraining, so the post-MVP dedicated-board
   comparison ([`docs/11`](../docs/11-hardware-benchmark.md)) measures the same model.

## Gates before handing a model to CV-Edge

- Golden frames regenerated from `reference/` for the new model version
- INT8 mAP within 2 points of FP32
- Calibration report (reliability diagram + ECE)
- Loads and runs in ONNX Runtime Android on the edge phone

## Dataset discipline

**Split by route, never by frame.** Adjacent frames of the same pothole landing in both train
and test makes the reported mAP fiction. A held-out *route* is the only honest test set, and
this is the single most common way a hackathon CV result fails to reproduce on stage.
