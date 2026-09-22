# 05 — The frontend (`frontend/`)

**Owner:** Frontend.
**Stack:** Vite · three.js · MapLibre GL + deck.gl · anime.js · vanilla ES modules.

No UI framework, matching `mock_frontend/`. The application is two canvases and a modest
amount of DOM chrome; React would add a dependency and a build step in exchange for nothing
this project needs.

---

## Two views over one state

```
 ┌────────────────────────────────────────────────────────────────┐
 │  ARGUS          [ LIVE COMMAND ]   [ ANALYTICS ]   [ ASSETS ]  │
 ├────────────────────────────────────────────────────────────────┤
 │                                                                 │
 │   three.js                     MapLibre + deck.gl               │
 │   real Bengaluru geometry      real Bengaluru basemap           │
 │   buses driving live           congestion / density / wards     │
 │   markers dropping             time scrubber, filters           │
 │   evidence cards               ranked backlog                   │
 │                                                                 │
 └────────────────────────────────────────────────────────────────┘
                              ▲
                              │  ONE store, ONE socket
                   ┌──────────┴──────────┐
                   │  src/transport/     │
                   │  WebSocket + REST   │
                   │  ─ or ─             │
                   │  MockTransport      │  ← identical interface
                   └─────────────────────┘
```

**The transport is an interface, not a connection.** This is inherited directly from the mock,
where `generator.js` emitted synthetic `DetectionEvent`s and nothing downstream knew. Same idea
here: `LiveTransport` (WebSocket + REST) and `MockTransport` (seeded generator) satisfy one
interface, and the views cannot tell them apart.

That buys three things: the frontend develops before the backend exists, the internal-round
demo keeps working, and demo day has a fallback that is the same code rather than a video.

### Why two views rather than one

Each answers a different half of the brief, and neither does the other's job well.

| | **Live Command (3D)** | **Analytics (2D)** |
|---|---|---|
| Question | *What is happening right now?* | *What is true about this city?* |
| Time | Now | Hours, days, weeks |
| Strength | Spatial intuition, presence, a fleet you can watch working | Density, comparison, rigorous colour scales |
| PS clauses | B2 (GIS map), A15 (alerts) | B3, B4, B5, B6, B9 (heat maps, deficiency, O–D, delay) |

A 3D city is genuinely better at conveying *"6,400 buses are surveying your roads continuously
and one just found something"* — that is a story about motion and coverage, and a flat map
undersells it. A hexbin density map is genuinely better at *"these eleven wards account for 60%
of unrepaired high-severity defects"*. Trying to do both in one view does both badly.

---

## Live Command — the 3D view

Built on `mock_frontend/`'s renderer, camera, controls and visual language, with the
procedural city replaced by real geometry.

### What carries over unchanged

| From the mock | Why it survives |
|---|---|
| `core/theme.js` | Colour and spatial tokens feeding both WebGL and CSS. Single source of truth; stays. |
| `core/viewer.js` | Renderer, camera, orbit controls, bloom, fixed-timestep frame loop. Solved. |
| `events/markers.js` | Beacon geometry, drop animation, raycast picking. Directly reusable. |
| `ui/hud.js`, `feed.js`, `detail.js` | KPI header, live ticker, evidence card. Reskinned, not rebuilt. |
| The camera behaviour | Drifts on its own, yields instantly when grabbed, resumes after six seconds. It demos itself unattended without fighting a presenter. Keep exactly. |
| Fixed-timestep accumulator | Identical timing on a 144 Hz desktop and a struggling 30 fps laptop. |

### What gets replaced

| From the mock | Replaced with |
|---|---|
| `city/layout.js` — seeded PRNG street grid | **Real OSM road graph**, baked to JSON |
| `city/buildings.js` — procedural blocks | **Real OSM building footprints**, extruded |
| `fleet/routes.js` — synthetic closed curves | **Real BMTC route polylines** from GTFS |
| `events/generator.js` — seeded simulation | **`LiveTransport`** (kept as `MockTransport`) |

### The Bengaluru geometry pipeline

```
  Overpass API                                    build time, run once
       │                                          ───────────────────
       ├── ways: highway=* within bbox     ──┐
       ├── ways: building=* within bbox    ──┤
       └── nodes: highway=crossing,        ──┤
                  amenity=school           ──┤
                                             ▼
                              frontend/scripts/bake-bangalore.mjs
                                             │
                    ┌────────────────────────┼────────────────────────┐
                    ▼                        ▼                        ▼
            roads.json              buildings.json            landmarks.json
        centrelines, class,      footprint + height,      crossings, schools,
        oneway, name, width       merged into 500 m         bus stops
                                     tiles
                                             │
                                             ▼
                              frontend/data/ (gitignored, regenerable)
```

**Baked at build time, not fetched at runtime.** Venue wifi cannot be relied on — the mock's
README is emphatic about this and it was right. A static JSON bundle also loads in one frame
instead of waiting on Overpass, which is rate-limited and occasionally slow.

**Extent.** Roughly 8 × 8 km over the core: `12.93–13.01 N, 77.56–77.65 E`. That covers MG
Road, Shivajinagar, Indiranagar, Domlur, Koramangala and a stretch of Outer Ring Road — dense
enough to look like Bengaluru, and containing real BMTC corridors with real congestion
character.

```bash
node frontend/scripts/bake-bangalore.mjs \
  --bbox 12.93,77.56,13.01,77.65 \
  --out  frontend/data/
```

**Route data.** BMTC publishes a GTFS feed; `shapes.txt` gives route polylines and `stops.txt`
gives stop positions. We use 6–8 real routes that intersect the extent, taking route numbers
and geometry from the feed rather than from memory — using a real route ID and getting its path
wrong is worse than not using one.

### Performance, which is a real constraint here

The procedural mock ran 203 draw calls and ~19k triangles. Real OSM geometry for 8 × 8 km of
Bengaluru is **tens of thousands of building footprints**, and a naive extrusion would not hold
60 fps on the integrated graphics you will present from.

| Technique | Effect |
|---|---|
| Drop footprints < 40 m² | Removes a long tail of sheds and jettys that are invisible at camera distance |
| Merge buildings per 500 m tile into one `BufferGeometry` | Draw calls scale with tiles, not buildings |
| Load tiles within ~2.5 km of camera; unload beyond | Bounded memory regardless of extent |
| Instanced road dashes, zebra bars, windows | Already proven in the mock |
| Vertex-colour road meshes for the congestion overlay | Heat map with no extra draw calls |
| `B` to toggle bloom | The mock's escape hatch. Keep it. Bloom is the single biggest cost. |

**Budget: under 500 draw calls, under 400k triangles, 60 fps at 1920×1080 on integrated
graphics.** Measured on the actual presentation machine, not assumed — the mock's own
`NEXT-STEPS.md` flags that its frame rate was never confirmed on real hardware, and that is the
one open item inherited from the internal round. Close it in week 1.

---

## Analytics — the 2D view

MapLibre GL for the basemap, deck.gl for every data layer.

| Layer | deck.gl class | Data |
|---|---|---|
| Congestion | `PathLayer` on segment geometry, coloured by index | `/analytics/congestion` |
| Crowd density | `H3HexagonLayer`, res 10 city-wide / 11 zoomed | `/analytics/pedestrian-density` |
| Assets | `IconLayer`, clustered at low zoom | `/assets` |
| Ward scorecard | `GeoJsonLayer` choropleth | `/analytics/ward-scorecard` |
| **Coverage** | `PathLayer`, desaturated, "not recently surveyed" | `/analytics/coverage` |
| Live fleet | `TripsLayer` | `/ws/live` |
| Corridor flow *(ph. 3)* | `ArcLayer` | `/analytics/od` |

**Basemap tiles are self-hosted.** A `pmtiles` extract of the Bengaluru bbox is a few hundred
MB, served locally by the ops stack. No external tile requests, no venue-wifi dependency, no
rate limit at the worst possible moment.

**The coverage layer ships as a default-on layer, not an option.** A congestion map that shows
nothing on a road no bus drives, styled identically to a road with genuinely free-flowing
traffic, is misinformation. Desaturating unsurveyed roads is a small rendering decision that
makes the whole product honest.

### Colour, with actual care

Congestion and density are **sequential** scales (viridis/magma family — perceptually uniform,
colourblind-safe, and they survive a projector's poor gamma). Severity is a **categorical**
scale from the existing token file. The two never share a ramp, because a viewer who has
learned "red = congested" must not also read "red = high severity" on the adjacent panel.

Both scales get a legend with **units**, not just colours: `km/h below free-flow`,
`persons / 100 m²`. A heat map without units is decoration.

---

## Assets view — the least glamorous, most convincing screen

A sortable table. Ward, class, severity, confidence, evidence count, distinct devices, age,
trend, state, recommended action, SLA status. Click a row to fly the 3D camera to it or centre
the 2D map on it.

This is the screen that answers *"what would a BBMP ward engineer actually open on Monday
morning?"* — and it is the screen that makes the difference between a visualisation and a tool.
It also renders the fusion fields (`distinct_devices`, `severity_trend`, `negative_passes`)
where a judge can see them, which is where the multi-pass argument stops being a claim in a
slide and becomes something visible in the product.

---

## Motion — where anime.js earns its place

Used for DOM chrome only. Never for the WebGL render loop, which has its own fixed-timestep
accumulator and must not have a second animation system reaching into it.

| Element | Motion | Why |
|---|---|---|
| KPI counters | Tween to new value, ~600 ms | A number that jumps is a number that got missed |
| Feed rows | Slide + fade in, staggered | Direction of travel conveys "new at the top" |
| Evidence card | Scale + fade from the marker's screen position | Ties the card to the thing it describes |
| View switch | Cross-fade with a short camera settle | Prevents the jarring cut between 3D and 2D |
| Critical incident | Border pulse, 3 cycles then stop | Attention without a permanently blinking UI |
| Ward scorecard bars | Grow on data change | Makes a re-rank legible rather than instantaneous |

**Motion has a job or it doesn't ship.** Every entry above answers "what would the user miss if
this changed instantly?" Decorative animation costs frame budget that the 3D view needs, and on
a projector it reads as noise.

Respect `prefers-reduced-motion`: swap tweens for instant state changes, keep the camera drift
off.

---

## Folder layout

```
frontend/
├── src/
│   ├── transport/   LiveTransport (WS+REST) · MockTransport · one interface
│   ├── shared/      store, event bus, theme tokens, formatters, colour scales
│   ├── command3d/   viewer, city (OSM geometry), fleet, markers, camera director
│   ├── analytics2d/ MapLibre setup, deck.gl layers, time scrubber, filters
│   └── ui/          hud, feed, evidence card, assets table, ward scorecard
├── scripts/         bake-bangalore.mjs · fetch-gtfs.mjs · extract-pmtiles.sh
├── data/            baked JSON — gitignored, regenerable, never committed
└── public/
```

`shared/` holds the store and the tokens. Neither view owns state; both subscribe. That is what
makes "click a row in the table, both maps move" a two-line feature rather than a refactor.

## Running it

```bash
cd frontend && npm install

node scripts/bake-bangalore.mjs --bbox 12.93,77.56,13.01,77.65 --out data/
node scripts/fetch-gtfs.mjs --agency bmtc --out data/

npm run dev          # :5173, LiveTransport against localhost:8000
npm run dev -- --mock # MockTransport, no backend needed
npm run build && npm run preview   # :4173 — use this to present
```

The frozen internal-round demo remains independently runnable in `mock_frontend/` and needs
nothing else at all.

---

## What "done" means for this folder

- Both views render real Bengaluru geometry, driven by one store.
- A `WS` asset event appears in both views and the table within one frame of arrival.
- The coverage layer is on by default and visibly distinguishes "clear" from "unsurveyed".
- 60 fps confirmed **on the presentation machine**, bloom on.
- `--mock` produces a complete, presentable demo with the backend switched off.
- Every heat map has a legend with units.
