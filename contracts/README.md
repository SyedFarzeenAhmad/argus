# `contracts/` — the seam

This folder is the reason four people can work in three folders without blocking each other.

Every message that crosses a team boundary is defined here once, in JSON Schema, and the
Python and TypeScript types are **generated** from it. Nobody hand-writes a type that
describes someone else's data. A schema change that a consumer hasn't caught up with
fails CI rather than failing in November.

```
                    ┌──────────────────────────┐
  edge-app/ ──────▶ │  contracts/schemas/*.json│ ◀─── frontend/
   (produces)       │   THE SOURCE OF TRUTH    │      (consumes)
                    └────────────┬─────────────┘
                                 │  generated
                    ┌────────────┴─────────────┐
                    │ python/  argus_contracts │ ◀─── backend/
                    │ ts/      @argus/contracts│      (both)
                    │ kotlin/  argus.contracts │ ◀─── edge-app/
                    └──────────────────────────┘
```

## The five messages

| Schema | Direction | Volume | What it is |
|---|---|---|---|
| `observation` | edge → backend | ~200/bus/day | One detection, one camera, one instant. Never shown raw. |
| `segment-pass` | edge → backend | ~400/bus/day | One bus over one road segment. Traffic + pedestrians + **inspection coverage**. |
| `incident` | edge → backend | ~0–3/bus/day | A conclusion from a tracked trajectory. Pre-empts the queue. |
| `telemetry` | edge → backend | 1 per 5 s | Heartbeat: position, health, queue depth, achieved FPS. |
| `asset` | backend → frontend | — | The fused, deduplicated real-world thing. The only defect object a UI ever sees. |

## Two rules that carry the whole design

**1. The edge never emits a `missing_*` class.**
You cannot draw a bounding box around an absent zebra crossing. The edge reports what it
*sees*, plus — in `segment-pass.inspection` — what it *looked for and did not find*. The
backend turns repeated qualifying non-observations into a `missing_zebra` Asset. Absence is
a fleet-level inference, never a frame-level detection.

**2. `Observation` is evidence; `Asset` is truth.**
Fifteen buses hit the same pothole forty times a day. Observations are raw and noisy and
nobody looks at them. Assets are what the fusion worker builds out of them, and they only
reach `confirmed` once **distinct devices** have corroborated. One bus seeing something
forty times is one bus's opinion.

## Generating the types

```bash
# Python (Pydantic v2)
python contracts/generate.py --lang python --out contracts/python/argus_contracts

# TypeScript
node contracts/generate.mjs --out contracts/ts/src

# Kotlin (kotlinx.serialization) — for edge-app/
node contracts/generate.mjs --lang kotlin --out contracts/kotlin/argus/contracts
```

All three are wired into CI. A PR that edits `schemas/` without regenerating fails.

## Versioning

`schema_version` is SemVer and is **required on every message**.

- **Patch** — documentation, examples, description text. Safe.
- **Minor** — a new optional field. Consumers ignore what they don't know.
- **Major** — a removed or retyped field, or a new required one. Consumers **must** reject
  a major they don't recognise rather than silently mis-parse it.

**1.1.0 (2026-09-25):** `Observation.subclass` (optional) — the crack type for `damaged_road`.

During the build, stay on `1.x`. If you find yourself wanting a major bump before December,
that's a signal the model was wrong — raise it with the team rather than versioning around it.

## Working against the contract before the CV exists

`ops/replay/` ships a service that reads a recorded `.jsonl` of these exact messages and
publishes them over MQTT at wall-clock speed. Backend and frontend develop against it from
day one. It is also the demo-day panic button: if live inference fails on stage, the replay
path produces an identical dashboard from a stored log.
