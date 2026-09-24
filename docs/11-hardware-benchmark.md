# 11 — Edge performance: the phone now, dedicated hardware later

**Decision 2026-09-25 (D11).** The MVP edge is **Android phones only**: two camera phones
streaming to one edge phone ([`docs/03`](03-cv-pipeline.md#on-bus-topology)). We do not
benchmark Jetson or Raspberry Pi boards before the MVP works.

That splits this document into two parts:

1. **Now — measure the phone setup.** Does one edge phone keep up with two streams, on a
   windscreen, in Bengaluru heat? This is on the critical path; the MVP is not done without it.
2. **After the MVP — hardware requirements.** From the phone numbers, derive what a bus unit
   actually needs, and compare dedicated boards against that. **Presented to the judges in the
   PPT as the next step**, clearly labelled as such.

The model stays a single portable ONNX file throughout ([`docs/03`](03-cv-pipeline.md)), so that
part 2 compares one system on different hardware rather than different systems.

---

## Part 1 — the phone setup (MVP)

### What is being measured

| Unit | Role | What can go wrong |
|---|---|---|
| Edge phone | Decodes N streams, runs every model, geo, queue, uplink | **Compute and heat.** The whole budget lives here. |
| Camera phone × 2 | Capture + hardware H.264 encode + RTSP | Heat on a sunlit windscreen; encoder throttling; stream drops |
| Local Wi-Fi link | ~6 Mbps, camera → edge | Latency and drops, especially the rear phone at the far end of the bus |

### Method — fairness rules

1. **Identical ONNX file** as the release APK. No special "benchmark model".
2. **Identical input footage** — a fixed 10-minute Bengaluru clip per camera, 1080p30, from our
   own capture, fed through the real RTSP path (a camera phone playing the clip as its source),
   not read locally on the edge phone. The stream path is part of what's being measured.
3. **Identical pipeline** — stream decode, frame gate, inference, track, geo, privacy gate,
   queue, uplink. End to end, not inference alone. Inference-only numbers are marketing; the
   pipeline is what runs on a bus.
4. **INT8 where the NPU path supports it**, FP16/FP32 on CPU otherwise. Report which.
5. **Thermal soak** — measure after 30 minutes **on a sunlit windscreen mount, charging**, not
   from cold on a desk. A phone behind glass in a Bengaluru May is the real operating
   condition, and every phone throttles.
6. **Three runs**, report median.

### Measure

| Metric | Why |
|---|---|
| End-to-end FPS per path (front vehicle, front defect, rear), sustained post-soak | The only throughput figure that means anything |
| Inference latency p50 / p95 | p95 is what drops frames |
| Accuracy delta vs FP32 baseline (mAP) | Quantisation damage is silent and must be bounded |
| Dropped frames % | Whether the pipeline keeps up |
| Edge-phone temperature + throttling events | Throttling headroom; Android reports thermal status |
| Net battery drain **while charging** | If it drains while plugged in, it dies mid-shift |
| Camera-phone temperature + encoder FPS | Cheap camera phones may throttle first |
| Stream latency and clock offset (camera → edge) | Geo accuracy depends on ≤ 20 ms sync |
| **Max camera streams at target FPS** | Decides whether a third camera is affordable |

### The table to fill

*Empty by design. **Do not quote any figure from this page in the video until it is
measured** — see [`docs/10`](10-video-claims-matrix.md) claim E5.*

| Metric | Edge phone A | Edge phone B (if available) |
|---|---|---|
| Phone model / SoC | — | — |
| Precision (NPU INT8 / CPU) | — | — |
| FPS: front vehicle / front defect / rear (post-soak) | — | — |
| Inference p50 / p95 (ms) | — | — |
| mAP@50 delta vs FP32 | — | — |
| Max cameras @ target rates | — | — |
| Steady-state temp (°C), throttling events | — | — |
| Net battery drain while charging (%/h) | — | — |
| Dropped frames (%) | — | — |
| Camera phone temp (°C) / encoder FPS | — | — |
| Stream latency p95 (ms) / clock offset (ms) | — | — |
| Unit cost (₹): edge + 2 cameras + mounts + power | — | — |

---

## Part 2 — hardware requirements (after the MVP, for the PPT)

Once part 1 is filled, the phone numbers turn into a **requirement**: frames per second per
camera at the target rates, the compute that took, and the thermal envelope it needed. That is
the spec any bus unit must meet, whatever it is.

Then compare candidate dedicated units against that requirement. **Not measured before the
MVP.** In the PPT, show this as the next step: the requirement (measured) and the candidates
(to be benchmarked), never estimated figures presented as results.

| | **Phone setup (MVP)** | **Jetson Orin Nano Super 8GB** | **Raspberry Pi 5 8GB + Hailo-8L** |
|---|---|---|---|
| Compute | ~15–25 TOPS NPU (varies) | ~67 TOPS INT8 (sparse) | 13 TOPS INT8 |
| Runtime | ONNX RT → NNAPI / CPU | ONNX RT → TensorRT EP | ONNX → HailoRT |
| Approx. unit cost | ₹15–25k edge + 2 cheap camera phones | ₹25–45k + cameras | ₹12–16k + cameras |
| Enclosure / install | **Self-contained phones**: own camera, GNSS, modem, battery; USB-C power | Enclosure, 12 V supply, cooling, wired cameras | Enclosure, 12 V supply, wired cameras |
| Fleet retrofit story | **Best deployability** — no new wiring, two-minute swap | Best performance | Best ₹/TOPS |

**Why the phone went first.** For a retrofit across an existing fleet it is arguably the
strongest option: it brings its own camera, GNSS, modem and battery backup, needs no 12 V tap
or enclosure engineering, and a depot can swap a failed unit in two minutes. If it holds the
required frame rate, the total-cost-of-deployment argument may beat raw throughput. Part 2
exists to test that conclusion with numbers, not to assume it.

### Derived conclusions the PPT should show

- **Measured:** what the phone setup sustains, with how many cameras, at what temperature.
- **Measured:** the per-bus requirement derived from that.
- **Next step:** candidate boards against the requirement, and when they will be benchmarked.
- **Fleet capital estimate** for 6,400 buses at the phone configuration — labelled an estimate.

---

## Running it

The benchmark is a mode of the edge app, so it runs the release code path:

```
Edge phone → Settings → Benchmark
  clip source:   rtsp://front-cam.local:8554/live, rtsp://rear-cam.local:8554/live
                 (camera phones in Camera mode, source = the fixed 10-min clip)
  soak:          30 min
  runs:          3
  → writes /sdcard/argus/bench/<phone-model>-<date>.json
```

```bash
adb pull /sdcard/argus/bench/ cv-pipeline/benchmarks/results/
cd cv-pipeline && uv run python -m benchmarks.report \
  --results ./benchmarks/results/ --out ../docs/11-table.md
```

The report generator writes the table above directly, so the document cannot drift from the
measurements.

## If a phone is unavailable

Borrow, don't fabricate. If a second edge phone cannot be obtained, **leave its column empty and
say so**. An honest one-column table is worth more than a two-column one with an estimated
column, and the estimate is the thing a judge will probe.
