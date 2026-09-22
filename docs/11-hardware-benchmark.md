# 11 — Hardware benchmark

**The deployment board is a result, not an input.**

Most teams pick a board and fit a model to it. We build one portable model and measure it on
three candidates, then let the numbers answer the question a government evaluator actually
asks: *what does it cost to put this on 6,400 buses, and what do we get for the money?*

That table is an artifact almost no competing team will have, and it is cheap to produce once
the ONNX portability constraint from [`docs/03`](03-cv-pipeline.md) is respected.

---

## Candidates

| | **Jetson Orin Nano Super 8GB** | **Raspberry Pi 5 8GB + Hailo-8L** | **Android phone (mid-range, 2024+)** |
|---|---|---|---|
| Compute | ~67 TOPS INT8 (sparse) | 13 TOPS INT8 | ~15–25 TOPS NPU (varies) |
| Runtime | ONNX RT → TensorRT EP | ONNX → HailoRT | ONNX RT → NNAPI EP |
| Approx. unit cost | ₹25–45k | ₹12–16k | ₹15–25k |
| Power | 7–25 W | 5–12 W | 3–8 W |
| Enclosure / install | Needs enclosure, 12 V supply, cooling | Needs enclosure, 12 V supply | **Self-contained**: camera + GNSS + modem + battery in one unit |
| Fleet retrofit story | Best performance | Best ₹/TOPS | **Best deployability** — no new wiring |

**The Android candidate is not a novelty.** For a retrofit across an existing fleet it is
arguably the strongest option: it brings its own camera, GNSS, modem and battery backup, it
needs no 12 V tap or enclosure engineering, and a depot can swap a failed unit in two minutes.
If it can hold the required frame rate, the total-cost-of-deployment argument may beat raw
throughput. That's precisely the kind of conclusion a benchmark produces and an assumption does
not.

---

## Method

**Fairness rules, because an unfair benchmark is worse than none:**

1. **Identical ONNX file** across all targets. No per-target retraining, no architecture swaps.
2. **Identical input footage** — a fixed 10-minute Bengaluru clip, 1080p30, from our own capture.
3. **Identical pipeline** — decode, frame gate, inference, track, geo, encode, queue. End to end,
   not inference alone. Inference-only numbers are marketing; the pipeline is what runs on a bus.
4. **INT8 where the target supports it**, FP16 otherwise. Report which.
5. **Thermal soak** — measure after 30 minutes at ambient 40 °C, not from cold. A sealed box on a
   bus roof in a Bengaluru May is the real operating condition, and every one of these boards
   throttles.
6. **Three runs**, report median.

**Measure:**

| Metric | Why |
|---|---|
| End-to-end FPS (sustained, post-soak) | The only throughput figure that means anything |
| Inference latency p50 / p95 | p95 is what drops frames |
| Accuracy delta vs FP32 baseline (mAP) | Quantisation damage is silent and must be bounded |
| Peak power (W) | Bus 12 V supply budget |
| SoC temperature at steady state | Throttling headroom |
| Dropped frame % | Whether the pipeline keeps up |
| **Max concurrent camera streams at target FPS** | Decides whether 4-camera operation is affordable |
| Unit cost + enclosure + install (₹) | The procurement number |

---

## The table to fill

*Empty by design. Populated in Phase 3, weeks 8–9. **Do not quote any figure from this page in
the video until it is measured** — see [`docs/10`](10-video-claims-matrix.md) claim E5.*

| Metric | Orin Nano Super | Pi 5 + Hailo-8L | Android | Laptop (ref) |
|---|---|---|---|---|
| Precision | INT8 | INT8 | INT8 | FP16 |
| End-to-end FPS (1 cam, post-soak) | — | — | — | — |
| Inference p50 / p95 (ms) | — | — | — | — |
| mAP@50 delta vs FP32 | — | — | — | — |
| Max cameras @ ≥ 15 fps | — | — | — | — |
| Peak power (W) | — | — | — | — |
| Steady-state temp (°C) | — | — | — | — |
| Dropped frames (%) | — | — | — | — |
| Unit + install (₹) | — | — | — | — |
| **₹ per bus, fleet-wide** | — | — | — | — |

### Derived conclusions the table should support

- **Minimum viable board** for single-camera operation.
- **Cost of the 4-camera configuration** versus front-only, per bus and fleet-wide.
- **Recommended configuration** with the trade-off stated in one sentence.
- **Fleet capital estimate** for 6,400 buses at the recommended configuration.

That last figure is the one a transport authority cares about most, and producing it credibly
requires exactly this table and nothing more.

---

## Running it

```bash
cd cv-pipeline
uv run python -m benchmarks.run \
  --clip ./data/bench-bengaluru-10min.mp4 \
  --model ./models/argus-multitask-v0.4.2.onnx \
  --ep tensorrt-int8 \
  --soak-minutes 30 \
  --runs 3 \
  --out ./benchmarks/results/orin-int8.json

uv run python -m benchmarks.report --results ./benchmarks/results/ --out ../docs/11-table.md
```

The report generator writes the table above directly, so the document cannot drift from the
measurements.

## If a target is unavailable

Borrow, don't fabricate. If a board genuinely cannot be obtained, **leave its column empty and
say so in the video**. An honest two-column comparison is worth more than a three-column one
with an estimated column, and the estimate is the thing a judge will probe.
