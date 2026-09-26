# `backend/` — the central platform

**FastAPI · PostgreSQL + PostGIS + TimescaleDB · MQTT (EMQX) · Redis · MinIO**

The backend does the one thing the edge fundamentally cannot: **see what more than one bus saw.**

Full design: [`docs/04-backend.md`](../docs/04-backend.md).
The maths it implements: [`docs/07-analytics-methods.md`](../docs/07-analytics-methods.md).

## Layout

```
argus_api/
├── ingest/     validation.py (contracts/schemas as-is) · pipeline.py · worker.py (MQTT)
├── fusion/     evidence.py (pure maths) · clustering.py · engine.py (lifecycle, ledger)
│               work_orders.py · actions.py (per-class action, SLA, IDI weight) · worker.py
├── analytics/  congestion · freeflow · pedestrian · infrastructure (road condition,
│               ward scorecard, coverage) · transit (route delay, corridor flow, fleet)
├── api/routes/ REST handlers + /ws/live
├── realtime/   Redis pub/sub (or in-process) → WebSocket fan-out
├── reports/    incident report PDF (dependency-free writer)
├── db/         SQLAlchemy models, Alembic migration 0001 (PostGIS, hypertables, caggs)
├── core/       config, auth (JWT roles), audit log, evidence store, geo helpers
├── seeds/      load_osm · load_wards · load_gtfs · simulate
├── jobs/       retention · verify-evidence · freeflow · stale
└── cli.py      create-user · init-db
```

## Run it

```bash
docker compose -f ../ops/compose/docker-compose.yml up -d
uv sync --extra dev && uv run alembic upgrade head
uv run python -m argus_api.cli create-user admin --role admin

uv run python -m argus_api.seeds.load_osm   --bbox bengaluru-core     # needs internet once
uv run python -m argus_api.seeds.load_wards --source bbmp --file <bbmp-wards.geojson>
uv run python -m argus_api.seeds.load_gtfs  --source bmtc --path <bmtc-gtfs.zip>

uv run fastapi dev argus_api/main.py          # :8000, OpenAPI at /docs
uv run python -m argus_api.ingest.worker      # MQTT → DB
uv run python -m argus_api.ingest.edge_pull   # phone MVP: pull new processed/ records from edge phones → DB
uv run python -m argus_api.fusion.worker      # observations → assets
```

Set `ARGUS_REDIS_URL=redis://127.0.0.1:6379/0` when the workers and the API run as separate
processes, so fusion events reach the WebSocket. Every setting is an `ARGUS_*` environment
variable — see `argus_api/core/config.py`.

### No infrastructure at all (demo laptop, CI)

```bash
export ARGUS_DATABASE_URL=sqlite:///argus-demo.db ARGUS_EMBEDDED_FUSION=true \
       ARGUS_EVIDENCE_VERIFICATION=off ARGUS_ANONYMOUS_ROLE=viewer
uv run python -m argus_api.seeds.simulate --days 14 --create-tables   # SIMULATED history
uv run fastapi dev argus_api/main.py
```

`seeds.simulate` builds a synthetic road grid over central Bengaluru and pushes contract-valid
messages through the **real** ingest and fusion code, with planted scenarios: worsening
potholes, a repair the fleet verifies, a repair that fails and re-opens, a dirty-lens bus whose
hallucinations stay candidates, a vanished crossing that raises `missing_zebra`, a waterlogging
bottleneck, a school zone. Everything it creates is labelled `SIM`/`sim:` — say so when you show
it. `--write-jsonl` also writes a log in `{topic, kind, payload}` form for the replay service.

No CV yet? `python ../ops/replay/replay.py --log <file>.jsonl` publishes real messages, or POST
them to `/api/v1/ingest/{kind}` as an admin (same pipeline as MQTT).

## API

| | |
|---|---|
| `GET /api/v1/assets?bbox=&class=&state=&ward=&severity=&since=` | Contract-shaped assets. Default hides `candidate`/`rejected`/`resolved`. |
| `GET /api/v1/assets/{id}` | Asset + sighting history + negative evidence + lifecycle + work orders |
| `POST /api/v1/assets/{id}/reject` | engineer · hard negative, device reliability ↓ |
| `GET /api/v1/work-orders?ward=&state=` · `/export.csv` | ranked backlog · CSV (audited) |
| `POST /api/v1/work-orders/{ref}/status` | `issued` / `in_progress` / `completed` (= awaiting fleet verification) |
| `GET /api/v1/incidents` · `/{id}` · `/{id}/report.pdf` · `/{id}/evidence` | list is plate-redacted; the rest need a named engineer and are audited |
| `GET /api/v1/analytics/congestion?bbox=&at=&window=&direction=` | CI, delay s/km, free-flow, attribution |
| `GET /api/v1/analytics/bottlenecks` | ranked by passenger-hours lost |
| `GET /api/v1/analytics/pedestrian-density?bbox=&since=&until=&preset=&resolution=` | per segment or H3 9/10/11, with standard errors |
| `GET /api/v1/analytics/ward-scorecard?period=` | deficiency / responsiveness / closure / durability |
| `GET /api/v1/analytics/road-condition?bbox=` · `/coverage?bbox=&since=` | 0–100 per segment · what we have **not** surveyed |
| `GET /api/v1/analytics/route-delay?route=` · `/corridor-flow` | GNSS vs GTFS + delay attribution · directional PCU |
| `GET /api/v1/fleet/live` · `/fleet/summary` | positions + health flags · HUD numbers |
| `GET /api/v1/geo/wards` · `/geo/segments?bbox=` | GeoJSON for the map layers |
| `WS /ws/live?token=&types=` | `asset.created · asset.updated · asset.resolved · incident · telemetry` |
| `POST /api/v1/auth/token` · `/api/v1/admin/*` | JWT login · devices, audit log, rejections, hard negatives, config |

Roles: `viewer` (read) · `engineer` (work orders, reject, incident evidence) · `admin`.
`ARGUS_ANONYMOUS_ROLE` lets a kiosk read without logging in; audited endpoints still require a
named operator.

## Your first task for the phone MVP: consume `processed/`

The edge app does not push yet. Each processing-client phone writes contract-format
`Observation` / `Telemetry` JSON (+ evidence JPEGs) into `processed/<category>/` and serves it
over a **read-only** API at `http://<phone-ip>:8080/api/v1/processed` with a bearer token.
Write `argus_api/ingest/edge_pull.py` to pull **new** files with the `after=` cursor, validate,
verify the evidence hash, store them through the normal ingest path, and advance a
per-device, per-category cursor in the same commit. **Nothing is deleted on the phone.** Full
spec: [`docs/04` → Consuming the processing client](../docs/04-backend.md#edge-processed-consumer).

## The two populations — do not conflate them

| | **Observations / SegmentPasses** | **Assets** |
|---|---|---|
| Nature | Raw sensor readings | Fused conclusions |
| Mutability | **Immutable, append-only** | Mutable, lifecycle-managed |
| Volume | ~600/bus/day | Orders of magnitude fewer |
| Who reads them | The fusion worker. **Nobody else.** | Every API, every UI, every work order |

A dashboard that renders observations shows forty pins for one pothole and loses the user in
eight seconds. This boundary is also why a single false positive is harmless: it produces a
`candidate` that never reaches `confirmed`, never becomes a work order, and never appears on
the default view.

## Fusion, as built

Deliberate refinements of docs/04, each covered by a test:

- **Per-observation evidence cap** (`max_observation_probability = 0.90`). Pure log-odds
  summing makes one 0.95 (logit 2.94) beat three 0.70s (3 × 0.85 = 2.54); capping any single
  sighting at 9:1 odds is what makes the documented claim — *three independent 0.70s outrank
  one isolated 0.95* — true.
- **Uncertainty-aware association.** A new sighting joins an existing asset within
  `max(8 m, 2·√(σ_asset² + σ_obs²))` (≤ 15 m). A fixed 8 m gate splits ~30% of repeat
  sightings of a young asset into duplicates. DBSCAN (`eps = 8 m`, `min_samples = 1`) still
  seeds new candidates from unclaimed sightings.
- **Resolution needs weight, not just a count.** Three qualifying misses *and* a discounted,
  quality-weighted sum ≥ 1.5, so one bus missing a pothole repeatedly (occlusion, dirty lens)
  cannot close it. Only confirmed / reported / in-progress assets resolve; a candidate the fleet
  stops seeing just loses confidence.
- **No manual "resolved".** An engineer can mark work *completed*; only the fleet closes it
  (`closed_by = 'auto:fleet-verified'`).

## `fusion/` gets the most test attention

It is the only component where a bug produces **plausible-looking wrong answers** rather than a
crash. `tests/test_fusion_properties.py` uses Hypothesis to check, for any observation sequence:

- confidence stays in [0, 1]
- an observation from an already-seen device raises confidence **strictly less** than one from a
  new device (the correlation discount)
- positional σ never increases when evidence is added, and never drops below the multipath floor
- negative evidence only lowers confidence; misses before the last sighting don't count

and `tests/test_fusion_engine.py` drives the lifecycle through real ingest: confirmation by
distinct devices, auto-resolution, re-opening, `stale`, operator rejection, and the ledger
raising and clearing `missing_zebra`.

```bash
uv run pytest            # SQLite, no docker needed
```

## Non-negotiables

1. **Ingest interprets nothing.** Validate, verify, store, notify. Boring on purpose.
2. **Face blurring is deferred** ([`docs/08`](../docs/08-privacy-and-compliance.md#face-blurring-deferred)). When it is built it most likely lands here, before anything is labelled or shown.
3. **Rate numerators and denominators stay separate columns.** Pre-divided rates cannot be
   re-aggregated correctly across segments or wards.
4. **`stale` is a real state.** Silence about a road nobody drove is not "no defects".
5. **Every incident-evidence access is audit-logged** against a named operator.

## Known gaps

- **Healthy infrastructure has no presence signal in the contract.** The edge can report a
  *faded* crossing but not an intact one, so under the rule in docs/02 ("inspected
  `faded_zebra`, found nothing" = absence) a well-painted OSM crossing looks the same as a
  missing one. The ledger thresholds (10 qualifying absences across 3 devices) limit the damage,
  but the fix belongs in `contracts/`: a presence report for zebra / divider / sign in
  `segment_pass.inspection`.
- **Passenger-hours lost uses an assumed bus load** (`ARGUS_ASSUMED_BUS_OCCUPANCY`), because no
  message carries the cabin-camera occupancy yet. Responses say `occupancy_source: "assumed"`.
- **Junction turn ratios** need per-track trajectories, which no contract message carries.
  Corridor flow (directional PCU by hour, AM/PM tidal ratio) is implemented.
- The PostgreSQL-specific migration section (PostGIS columns, hypertables, continuous
  aggregates, retention policies) has been rendered with `alembic upgrade head --sql` but not
  yet run against the compose stack; the test suite runs on SQLite.
