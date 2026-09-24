# `backend/` — the central platform

**FastAPI · PostgreSQL + PostGIS + TimescaleDB · MQTT (EMQX) · Redis · MinIO**

The backend does the one thing the edge fundamentally cannot: **see what more than one bus saw.**

Full design: [`docs/04-backend.md`](../docs/04-backend.md).
The maths it implements: [`docs/07-analytics-methods.md`](../docs/07-analytics-methods.md).

## Layout

```
argus_api/
├── ingest/     MQTT subscriber → validate → verify hash → raw hypertables
├── fusion/     clustering · log-odds · lifecycle · asset ledger · actions.py
├── analytics/  congestion · pedestrian density · ward index · coverage · delay
├── api/routes/ REST handlers
├── realtime/   Redis pub/sub → WebSocket fan-out
├── db/         SQLAlchemy models, Alembic migrations, PostGIS helpers
└── core/       config, auth, audit log, object storage
```

## Run it

```bash
docker compose -f ../ops/compose/docker-compose.yml up -d
uv sync && uv run alembic upgrade head

uv run python -m argus_api.seeds.load_osm   --bbox bengaluru-core
uv run python -m argus_api.seeds.load_wards --source bbmp
uv run python -m argus_api.seeds.load_gtfs  --source bmtc

uv run fastapi dev argus_api/main.py          # :8000
uv run python -m argus_api.ingest.worker      # MQTT → DB
uv run python -m argus_api.ingest.edge_pull   # phone MVP: pull helpful/ from edge phones → DB, then DELETE
uv run python -m argus_api.fusion.worker      # observations → assets
```

No CV yet? `python ../ops/replay/replay.py --log <file>.jsonl` publishes real messages.

## Your first task for the phone MVP: consume `helpful/`

The edge app does not push yet. Each processing-client phone writes contract-format
`Observation` / `Telemetry` JSON (+ evidence JPEGs) into its `helpful/` folder and serves it
over `http://<phone-ip>:8080/api/v1/helpful` with a bearer token. Write
`argus_api/ingest/edge_pull.py` to pull those files, validate, verify the evidence hash, store
them through the normal ingest path, and **DELETE each file only after the DB commit**. Full
spec: [`docs/04` → Consuming the edge phone's helpful folder](../docs/04-backend.md#edge-helpful-consumer).

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

## `fusion/` gets the most test attention

It is the only component where a bug produces **plausible-looking wrong answers** rather than a
crash — the worst failure mode in the system, and one a demo will never surface.

Use property-based tests (Hypothesis). The invariants must hold for *any* observation sequence:

- confidence stays in [0, 1]
- an observation from an already-seen device raises confidence **strictly less** than one from a
  new device (the correlation discount)
- positional σ never increases when evidence is added
- σ never drops below the multipath floor
- a `resolved` asset re-opens if a positive observation arrives
- negative evidence from a pass with low `assessable_fraction` is ignored

## Non-negotiables

1. **Ingest interprets nothing.** Validate, verify, store, notify. Boring on purpose.
2. **Reject unblurred evidence at the boundary.** Don't trust the edge's flag — enforce it.
3. **Rate numerators and denominators stay separate columns.** Pre-divided rates cannot be
   re-aggregated correctly across segments or wards.
4. **`stale` is a real state.** Silence about a road nobody drove is not "no defects".
5. **Every incident-evidence access is audit-logged** against a named operator.
