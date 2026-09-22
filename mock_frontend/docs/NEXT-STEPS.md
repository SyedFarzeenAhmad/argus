# Where this stands, and how to pick it up

Last worked: 2026-09-08. Commit `ee6b15a`.

## Status

The 3D visualiser mock-up is **complete and committed**. It runs, loops, and is ready to demo.

```bash
cd C:\Users\syedf\argus
npm run build && npm run preview     # http://localhost:4173  <- use this to demo
npm run dev                          # http://localhost:5173  <- use this to edit
```

Read [`README.md`](../README.md) for controls, the 90-second beat sheet, and the real-vs-mocked table. The approved design is in [`specs/2026-09-08-argus-3d-visualiser-design.md`](superpowers/specs/2026-09-08-argus-3d-visualiser-design.md).

## Do this first, next session

**Confirm the frame rate on the machine you will actually present from.** This is the one thing that was never verified — the browser-automation tab used during the build does not composite, so `requestAnimationFrame` throttled to ~1fps there and wall-clock FPS could not be measured. Scene complexity is genuinely light (203 draw calls, ~19k triangles at 1920×900) so it *should* hold 60fps on integrated graphics, but confirm it rather than trust it. If it struggles, press `B` to drop bloom, which is the single biggest cost.

## Tuning knobs, by file

Nearly all the visual and pacing behaviour is a named constant. Where to reach when something needs adjusting:

| Want to change | File | Constant |
|---|---|---|
| Any colour, anywhere (3D **and** the HTML chrome) | `src/core/theme.js` | `COLOR` |
| Cycle length, incident timing, sim step | `src/core/theme.js` | `TIMING` (`loop: 90`, `incidentAt: 62`) |
| City size, fog distance, layer heights | `src/core/theme.js` | `WORLD` |
| A different city (same generator, new layout) | `src/city/layout.js` | `buildLayout(seed)` — default `20261124` |
| Building density | `src/city/buildings.js` | `SUBDIVIDE_ABOVE` (18) |
| Skyline height profile | `src/city/buildings.js` | `(11 + core * 72)` |
| Window light density | `src/city/buildings.js` | `windowCount` clamp |
| Home camera framing, bloom strength | `src/core/viewer.js` | `HOME`, `UnrealBloomPass` args |
| How many buses, how fast | `src/fleet/buses.js` + `main.js` | `buildFleet(..., perRoute)`, `speed` range |
| Bus size on screen | `src/fleet/buses.js` | `SCALE` (1.4, deliberately oversized) |
| Sensing cone reach | `src/fleet/buses.js` | `FOV_RANGE` (30) |
| Number of defects per route | `src/events/generator.js` | `SITES_PER_ROUTE` (9) |
| **Feed pacing** | `src/events/generator.js` | `UPLINK_START` (5.5), `UPLINK_INTERVAL` ([1.5, 2.6]) |
| Severity-vs-fairness in the queue | `src/events/generator.js` | `AGING_RATE` (0.3) |
| Detection classes, colours, actions | `src/events/catalog.js` | `DETECTION_TYPES` |
| Marker beacon height, ping size | `src/events/markers.js` | `SHAFT_HEIGHT` (34), `PING_MAX_RADIUS` (11) |

Several of these were tuned against measurement rather than taste — the commit message records which and why. If you change `SUBDIVIDE_ABOVE` or the height formula, re-check density the same way (the sweep scripts are in the session history, but they are three lines of `buildLayout()` plus a percentile print).

## Deferred v2 features, with hook points

These were deliberately left out so the mock-up presents with a roadmap. Notes on where each would attach:

- **Congestion heat map.** `city/roads.js` already merges carriageways into per-class geometries. Give the merged geometry a vertex-colour attribute and drive it from a per-segment density value; `layout.xRoads` / `layout.zRoads` are the keys to index density by. The generator would need to emit a density-per-segment channel alongside detections.
- **Per-category layer toggles.** Cheapest of the four. Each marker's `holder` is already grouped and knows its `event.typeId`; a filter that sets `holder.visible` would be a few lines in `events/markers.js`. The legend rows in `ui/hud.js` are the obvious control surface — they already carry the class colour and live count.
- **Timeline scrubber.** `sim.state.emitted` holds the full event history for the cycle. The work is making `markers` able to rebuild from a time-filtered slice rather than only append, plus suppressing drop animations during a scrub.
- **Origin–destination flow ribbons.** Copy the pattern in `fleet/routes.js → buildRouteRibbons`; it already builds an additive ground ribbon from a curve, which is the same primitive an O–D flow needs.
- **Route-delay overlay.** Buses already carry `odometer` and a per-vehicle `speed`; comparing actual against scheduled progress along `route.curve` would give a delay figure with no new machinery.

## The larger submission

This repo is **only the visualiser slice** of PS 26124. The full problem statement also asks for:

1. **Onboard edge-AI processing** — multi-camera inference on the bus for the detection classes in `events/catalog.js`, plus vehicle detection/classification/counting and plate extraction. Nothing here does any computer vision.
2. **Centralised aggregation** — real ingest from a fleet, storage, and the analytics the brief lists (O–D patterns, route delay estimation, infrastructure deficiency reporting).

The visualiser was built so that (2) can be dropped in without touching any view: replace `src/events/generator.js` with a WebSocket client that emits the same `DetectionEvent` shape and everything downstream keeps working. That substitution point is the thing to protect as the rest of the platform gets built.

## Known rough edges

- `dist/` is gitignored, so a fresh clone needs `npm install && npm run build` before `npm run preview` works.
- A Vite dev server may still be running on port 5173 from the last session; `npm run dev` will fail on `--strictPort` if so. Kill the stray node process or just use `npm run preview`.
- The evidence-card camera still is convincing at card size but does not hold up if scaled much larger — it is line art, not a photograph. Do not put it on a projector full-screen.
