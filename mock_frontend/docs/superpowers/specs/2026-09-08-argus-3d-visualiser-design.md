# ARGUS 3D Urban Telemetry Visualiser — Design

**Date:** 2026-09-08
**Context:** SIH 2026, problem statement 26124 — *AI-Powered Mobile Urban Intelligence Platform Using Public Transport Fleet*
**Scope of this document:** the 3D map visualiser **mockup only** — the internal-demo artefact used to show judges the intended shape of the centralised platform's GIS view. Not production code, not the edge-AI pipeline.

## 1. Purpose

Give a viewer a navigable 3D city in which public-transport buses drive their routes and, as they pass road defects, drop geolocated markers that stream into a live event feed and KPI header.

The mockup must answer one question a judge will ask: *what does your product actually look like?* It is explicitly **not** trying to demonstrate working computer vision.

### Success criteria

1. Opens and runs at ~60fps on integrated graphics with no network access.
2. A viewer can orbit, pan and zoom the city freely with no instructions.
3. Within 30 seconds of loading, unprompted, the viewer sees a bus reach a location and a marker appear there.
4. Clicking any marker reveals a plausible evidence record: class, confidence, timestamp, GPS, source bus.
5. The 90-second loop is identical on every run, so the pitch can be rehearsed.

### Explicit non-goals

- No real computer vision, no real video, no real GPS.
- No real city geography (see section 3).
- No backend, no persistence, no auth.
- Congestion heatmaps and per-category layer toggles are **deferred to v2** (see section 8).

## 2. Approved decisions

| Decision | Choice | Rationale |
|---|---|---|
| Build tooling | Vite + npm `three`, vanilla ES modules | Real project structure that survives into the final app; hot-reload for visual tuning; `three` vendored locally so the demo needs no internet; no framework commitment made prematurely |
| City geometry | Procedurally generated, seeded | Instant load, no data pipeline, tunable density, cannot fail offline |
| Art direction | Dark 3D scene, clean professional UI chrome | Dark ground makes emissive markers legible on a bright projector; restrained UI reads as shippable government software rather than sci-fi |
| Marker inspection | Included | The brief requires users to see what is flagged and marked; a non-interactive dot does not satisfy that |

## 3. Why a fake city is the right call

A recognisable real city core would earn a stronger first reaction, but it requires an OSM extraction step, ships a large geometry payload, and real road networks contain sparse or malformed regions that look broken when extruded naively. A seeded procedural city is dense and well-framed everywhere, loads instantly, and costs nothing to regenerate with different parameters.

The trade-off accepted: the map is not anyone's actual city. Mitigated by presenting it as a representative urban grid and keeping coordinates displayed as plausible lat/lon.

## 4. Architecture

The organising principle: **the mockup's architecture is the production architecture with a fake transport substituted.**

```
generator.js  --emits DetectionEvent-->  event bus  -->  markers.js  (3D beacons)
(seeded, fixed-timestep                             -->  feed.js     (live ticker)
 proximity simulation)                              -->  hud.js      (KPI counters)
      ^
      |
      in production this is a WebSocket from the fleet ingest service
```

No consumer downstream of the event bus knows the data is synthetic. Replacing `generator.js` with a real socket client leaves every view untouched. This is both an honest engineering choice and the answer to *is this just a rendered video?*

### Module layout

```
src/
  main.js                    boot, wiring, keyboard, intro camera move
  core/
    theme.js                 single source of colour + material tokens
    viewer.js                renderer, camera, OrbitControls, bloom, resize, RAF loop
  city/
    layout.js                seeded PRNG; road graph, blocks, river
    roads.js                 road surfaces + centre-line dashes
    buildings.js             instanced extruded blocks
    environment.js           ground, grid, fog, lights, water
  fleet/
    routes.js                closed route curves derived from the road graph
    buses.js                 bus meshes, curve following, ground-projected camera FOV cones
  events/
    catalog.js               detection taxonomy: label, colour, severity, frequency
    generator.js             defect sites + fixed-timestep proximity detection + scripted incident
    markers.js               beacon meshes, drop animation, raycast picking
  ui/
    hud.js                   KPI stat row with count-up
    feed.js                  live event ticker, click-to-focus
    detail.js                evidence card incl. procedurally drawn camera frame
    styles.css
```

### Key technical choices

- **Seeded PRNG (mulberry32)** for city layout *and* defect placement — identical scene every launch.
- **Fixed 60Hz simulation accumulator**, decoupled from render delta — event timing is frame-rate independent and therefore rehearsable.
- **InstancedMesh for buildings** — several hundred blocks in one draw call, which is what holds 60fps on integrated graphics.
- **Vertical gradient baked into building vertex colours**, multiplied by per-instance colour — atmospheric depth without per-building materials.
- **Proximity-triggered detection**: defect sites are placed along routes; a bus passing within threshold fires the event. Causality is real, not scripted.
- **Raycast against invisible sphere proxies** rather than beacon geometry — cheap, reliable picking with a generous hit target.
- **Procedurally drawn evidence frame** (canvas 2D: scanlines, bounding box, label, crosshair, timestamp burn-in) — a convincing mock camera still with zero image assets to ship.

## 5. Detection taxonomy

Drawn from the problem statement. Each type carries a label, colour, severity and relative frequency.

| Type | Colour | Severity |
|---|---|---|
| Pothole | amber | medium |
| Damaged road surface | orange | medium |
| Waterlogging | cyan | high |
| Missing road divider | yellow | medium |
| Missing zebra crossing | pink | high |
| Damaged/missing signboard | violet | low |
| Traffic incident (rash driving / hit-and-run) | red | critical |

Incident events additionally carry a mock registration number with its own confidence score, matching the brief's requirement to extract a plate with confidence, timestamp and GPS.

## 6. Demo choreography (~90s, looping)

| Time | Beat |
|---|---|
| 0-5s | Slow establishing orbit; KPI counters animate up from zero |
| 5-30s | Buses trace routes; first pothole detection drops with expanding ping ring and a new feed row |
| 30-60s | Detections accumulate across categories; camera drifts gently |
| 60-75s | Scripted incident beat — red critical marker, source bus highlighted, feed row flashes |
| 75-90s | Settle, then reset and loop |

## 7. Interaction

| Input | Action |
|---|---|
| Left drag | Orbit |
| Right drag / two-finger | Pan |
| Scroll | Zoom |
| Click marker | Open evidence card |
| Click feed row | Fly camera to that marker |
| `Space` | Pause / resume simulation |
| `R` | Reset camera |
| `B` | Toggle bloom |

## 8. Deferred to v2

- Congestion heatmap tinting road segments by vehicle density.
- Per-category layer toggle sidebar.
- Timeline scrubber to replay the day.
- Origin-destination flow ribbons.
- Route-delay overlay.

These are listed here so the mockup can be presented with a credible roadmap rather than appearing feature-complete.

## 9. Risks

| Risk | Mitigation |
|---|---|
| No internet on demo machine | `three` installed locally; no CDN, no runtime fetches |
| Weak GPU on demo machine | Instanced buildings, modest bloom, `B` to disable bloom entirely |
| Scene reads as empty or toy-like | Dense procedural blocks, fog depth falloff, restrained emissive palette |
| Judge assumes it is a video | Free camera control is immediately obvious; architecture slide explains the transport swap |
