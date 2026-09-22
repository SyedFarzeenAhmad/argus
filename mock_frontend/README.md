# ARGUS — 3D Urban Telemetry Visualiser

**Mock-up for SIH 2026, problem statement 26124** — *AI-Powered Mobile Urban Intelligence Platform Using Public Transport Fleet*

A navigable 3D city in which public-transport buses drive their routes and, as they pass road defects, drop geolocated markers that stream into a live event feed and KPI header. This is the GIS view of the centralised platform, built to show what the product looks like.

> **This is an illustrative mock-up.** There is no computer vision, no real video, no real GPS and no backend. Every detection is synthetic. See [What is real vs. mocked](#what-is-real-vs-mocked).

---

## Running it

```bash
npm install
npm run dev          # http://localhost:5173
```

For the demo itself, prefer the production build — it starts faster and has no dev-server overhead:

```bash
npm run build
npm run preview      # http://localhost:4173
```

Everything is vendored locally. **No internet connection is required at any point**, which is deliberate: venue wifi cannot be relied on.

## Controls

| Input | Action |
|---|---|
| Left drag | Orbit |
| Right drag / two-finger | Pan |
| Scroll | Zoom |
| Click a marker | Open its evidence card |
| Click a feed row | Fly the camera to that marker |
| `Space` | Pause / resume the simulation |
| `R` | Reset the camera to the survey view |
| `B` | Toggle bloom (turn it off if the demo machine struggles) |
| `Esc` | Close the evidence card |

The camera drifts on its own, yields the instant you grab it, and resumes after six seconds of stillness — so it demos itself unattended but never fights a presenter.

## The 90-second cycle

The demo loops. Timing is identical on every machine and every run (see [Determinism](#determinism)), so it can be rehearsed to the second.

| Time | Beat |
|---|---|
| 0–5s | Establishing drift; KPI counters climb from zero while the uplink queue fills |
| 5.5s | First transmission — highest-severity finding available leads |
| 5–60s | Detections arrive roughly every 2 seconds; markers accumulate across the map |
| **62s** | **Incident beat** — rash-driving event, red critical marker, offending bus flashes, feed row pulses, camera pulls to it if nobody is driving |
| 62–90s | Detections continue, then markers fade and the cycle restarts |

Jump straight to a beat from the browser console:

```js
ARGUS.skipTo(62)   // advance to the incident
ARGUS.stats()      // current network figures
ARGUS.select(12)   // open the evidence card for event #12
```

## Determinism

The city layout, every building height, every defect location and the entire event timeline come from one seeded PRNG (`mulberry32`). The same city, the same detections, in the same order, every launch.

The simulation is stepped at a **fixed 60 Hz** through an accumulator, decoupled from render delta. A 144 Hz desktop and a struggling 30 fps laptop produce byte-identical event timing — the pothole lands at 0:23 on both.

## Architecture

The organising idea: **the mock-up's architecture is the production architecture with a fake transport substituted.**

```
generator.js  --emits DetectionEvent-->  event bus  -->  markers.js  (3D beacons)
(seeded, fixed-timestep                             -->  feed.js     (live ticker)
 proximity simulation)                              -->  hud.js      (KPI counters)
      ^
      |
      in production this is a WebSocket from the fleet ingest service
```

Nothing downstream of the event bus knows the data is synthetic. Replacing `generator.js` with a socket client leaves every view untouched. That is the honest answer to *"is this just a rendered video?"* — and the reason this mock-up is worth building on rather than throwing away.

```
src/
  main.js                    boot, wiring, keyboard, presenter handle
  core/
    theme.js                 colour + spatial tokens (feeds both 3D and CSS)
    viewer.js                renderer, camera, controls, bloom, frame loop
  city/
    layout.js                seeded PRNG; road graph, blocks, river
    roads.js                 carriageways, centre-line dashes, zebra crossings
    buildings.js             instanced blocks, rooftop lights, lit windows
    environment.js           ground, reference grid, river, parks, lighting
  fleet/
    routes.js                closed route curves derived from the road graph
    buses.js                 bus meshes, curve following, camera FOV cones
  events/
    catalog.js               detection taxonomy, plate + lat/lon helpers
    generator.js             defect sites, proximity detection, uplink queue
    markers.js               beacons, drop animation, picking
  ui/
    hud.js  feed.js  detail.js  styles.css
```

### Decisions worth knowing about

**Detections are proximity-triggered, not scripted.** Defect sites are seeded along each route; a bus firing a detection means it actually drove within sensing range and had the site inside its forward cone. The causality is real — the bus arrives, *then* the pin drops.

**Detections then enter a bandwidth-limited uplink queue.** Fifteen buses sweeping 45 sites find everything in about twenty seconds; a demo that goes silent at 0:20 is useless. Rather than fake the timing, the edge holds findings and transmits roughly one every two seconds. This is the problem statement's own requirement — *"minimising bandwidth through intelligent edge processing"* — and it makes queue depth a live KPI.

**The queue is an aging priority queue.** Severity leads, so the most serious finding available goes first, as a genuinely bandwidth-constrained device should behave. An age term prevents starvation: after about seven seconds of waiting, a low-severity signboard outranks a freshly-found high-severity defect. Critical incidents pre-empt the queue entirely.

**Detection classes are allocated by stratified quota, not independent weighted draws.** Weighted sampling is correct in expectation but lumpy at N=45 — one seed produced 15 signboards and a single divider, which made the feed monotonous and under-sold the breadth of detection. Exact quotas by largest remainder, then shuffled, guarantee a representative mix every run.

**Evidence stills are drawn with Canvas 2D at display time.** No image assets ship. Each class draws its own defect — an ellipse for a pothole, jagged strokes for cracking, ghost stripes for a worn-out crossing — under a labelled bounding box with corner ticks. Frames are seeded from the event id, so a given marker always shows the same still.

**Buildings, windows, road dashes and zebra bars are instanced.** 203 draw calls and ~19,000 triangles at 1920×900 for the whole scene, which is what holds 60fps on the integrated graphics you will probably be presenting on.

## What is real vs. mocked

| Real | Mocked |
|---|---|
| The rendering, camera and interaction | The city — procedurally generated, not a real place |
| Bus path-following along route curves | Bus positions are simulated, not GPS |
| Proximity-based detection geometry | The "detection" — no CV model is running |
| The event-bus architecture and all views | The transport — a seeded generator, not a socket |
| Confidence scores are per-class ranges | ...but they are sampled, not inferred |
| Lat/lon maths (anchored near Bengaluru) | The coordinates describe a fictional street grid |
| The uplink queue and its priority policy | Bandwidth figures are illustrative |

Registration marks, bus IDs and route names are fabricated. No real vehicle, person or location is depicted.

## Deferred to v2

Listed so the mock-up can be presented with a roadmap rather than appearing feature-complete:

- Congestion heat map tinting road segments by vehicle density
- Per-category layer toggles
- Timeline scrubber to replay a full day
- Origin–destination flow ribbons
- Route-delay overlay

## Design record

The approved design lives in [`docs/superpowers/specs/2026-09-08-argus-3d-visualiser-design.md`](docs/superpowers/specs/2026-09-08-argus-3d-visualiser-design.md).

## Stack

Vite 8 · three.js 0.185 · vanilla ES modules, no UI framework — so this drops into whatever the final application ends up using.
