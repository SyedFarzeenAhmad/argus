# `frontend/` — the dashboard

**Vite · three.js · MapLibre GL + deck.gl · anime.js · vanilla ES modules**

Two views over one store: a 3D **Live Command** view and a 2D **Analytics** view, both over
real Bengaluru geometry.

Full design: [`docs/05-frontend.md`](../docs/05-frontend.md).

## Layout

```
src/
├── transport/   LiveTransport (WS + REST) · MockTransport · ONE interface
├── shared/      store, event bus, theme tokens, colour scales, formatters
├── command3d/   viewer, OSM city geometry, fleet, markers, camera director
├── analytics2d/ MapLibre setup, deck.gl layers, time scrubber, filters
└── ui/          hud, feed, evidence card, assets table, ward scorecard
scripts/         bake-bangalore.mjs · fetch-gtfs.mjs · extract-pmtiles.sh
data/            baked JSON — gitignored, regenerable, never committed
```

## Run it

```bash
npm install

# one-time: bake real Bengaluru geometry to static JSON (no runtime network calls)
node scripts/bake-bangalore.mjs --bbox 12.93,77.56,13.01,77.65 --out data/
node scripts/fetch-gtfs.mjs --agency bmtc --out data/

npm run dev             # :5173 against localhost:8000
npm run dev -- --mock   # MockTransport — no backend required
npm run build && npm run preview   # :4173 — PRESENT FROM THIS
```

## The transport is an interface, not a connection

Inherited from `mock_frontend/`, where a seeded generator drove everything and no view could
tell. `LiveTransport` and `MockTransport` satisfy the same interface. That buys three things:
the frontend develops before the backend exists, the internal-round demo keeps working, and
demo day has a fallback that is the *same code* rather than a video.

## Non-negotiables

1. **Nothing is fetched from the internet at runtime.** OSM geometry, GTFS routes and basemap
   tiles are all baked or self-hosted. Venue wifi cannot be relied on — the mock's README was
   emphatic about this and it was right.
2. **The coverage layer is on by default.** A road no bus has driven must not render like a road
   with free-flowing traffic. Desaturate it.
3. **Every heat map has a legend with units** — `km/h below free-flow`, `persons / 100 m²`. A
   heat map without units is decoration.
4. **Congestion and severity never share a colour ramp.** Sequential for the first, categorical
   for the second; a viewer who learned "red = congested" must not read "red = severe" next to it.
5. **anime.js drives DOM chrome only**, never the WebGL loop — that has its own fixed-timestep
   accumulator and must not have a second animation system reaching into it.
6. **Motion has a job or it doesn't ship.** Ask: what would the user miss if this changed
   instantly? Decorative animation costs frame budget the 3D view needs.
7. **Respect `prefers-reduced-motion`.**

## Performance budget

Under **500 draw calls**, under **400k triangles**, **60 fps at 1920×1080 on integrated
graphics**, bloom on.

Real OSM geometry is far heavier than the mock's procedural city, so: drop footprints under
40 m², merge buildings into per-500 m-tile geometries, load tiles within ~2.5 km of the camera,
keep everything else instanced.

**Measure this on the actual presentation machine.** The mock's `NEXT-STEPS.md` flags that its
frame rate was never confirmed on real hardware — that is the one open item inherited from the
internal round. Close it in week 1.

## What carries over from `mock_frontend/`

`theme.js`, `viewer.js`, `markers.js`, the HUD/feed/evidence-card chrome, the fixed-timestep
accumulator, and the camera behaviour (drifts on its own, yields instantly when grabbed, resumes
after six seconds — it demos itself unattended without fighting a presenter). Keep that exactly.

Replaced: the procedural city, the synthetic routes, and the seeded generator.
