# 04 — The central platform (`backend/`)

**Owner:** Backend.
**Stack:** FastAPI · PostgreSQL + PostGIS + TimescaleDB · MQTT (EMQX) · Redis · MinIO.

The backend's job is the one thing the edge fundamentally cannot do: **see what more than one
bus saw.** Everything distinctive about this platform lives here.

---

<a id="ingest"></a>
## Ingest

```
MQTT topic tree
  argus/v1/{fleet}/{device_id}/obs        QoS 1   observation
  argus/v1/{fleet}/{device_id}/pass       QoS 1   segment_pass
  argus/v1/{fleet}/{device_id}/incident   QoS 2   incident      ← exactly-once
  argus/v1/{fleet}/{device_id}/tlm        QoS 0   telemetry     ← retained, lossy is fine
```

Transport is **TLS 1.3 with per-device X.509 client certificates**. A device identity is a
certificate, not an API key in a config file — a bus is a physically accessible box parked in
a public depot, and a leaked shared secret would compromise the whole fleet. Certificates can
be revoked individually.

There are two ways messages arrive: MQTT (the design) and, for the phone MVP, a pull from the
processing client's `helpful/` folder ([below](#edge-helpful-consumer)). Both end in the same
ingest steps. The ingest worker, per message:

1. **Validate** against the JSON Schema in `contracts/`. Reject on major-version mismatch.
2. **Deduplicate** by `observation_id` / `segment_pass_id` — MQTT QoS 1 is *at least once*, so
   duplicates are normal operation, not an error.
3. **Verify the evidence hash.** Recompute SHA-256 on the stored crop and compare. This is the
   chain of custody; an incident clip that may end up in an enforcement process needs one.
4. **Reject unblurred evidence.** Any crop flagged as containing people with
   `faces_blurred: false` is refused and the device is flagged. Enforced at the boundary, not
   trusted from the edge.
5. **Stamp receipt time** and write to the raw hypertable. Raw tables are **immutable and
   append-only** — nothing ever updates an observation.
6. **Notify** the fusion worker via Redis.

Ingest does no interpretation whatsoever. It is deliberately boring, because it is the one
component that must never be the bottleneck or the source of a subtle data bug.

---

<a id="edge-helpful-consumer"></a>
## Consuming the edge phone's `helpful/` folder — MVP hand-off

> **Backend developer: this is yours to build.** Until the edge app publishes over MQTT, the
> processing-client phone does not push anything. It writes contract-format messages into a
> local `helpful/` folder and serves that folder over a small HTTP API. The backend **pulls**
> from it, stores what it pulled, and **deletes each file once it has been consumed**.

### What is on the phone

The processing client keeps two folders ([`edge-app/`](../edge-app/README.md)):

| Folder | Contents | Who reads it |
|---|---|---|
| `argus/processed/<session>/` | The full local record: every inference on every camera (`detections.jsonl`), annotated frames, crops. Faces already blurred. | Nobody off-phone. Debugging, labelling, audits. Never deleted by the backend. |
| `argus/helpful/` | Only what the servers need: one `Observation` JSON + its evidence JPEG per pothole found, and a `Telemetry` JSON every 30 s. Exactly the shapes in `contracts/schemas/`. | **The backend consumer**, which deletes what it consumed. |

File names start with a sortable UTC timestamp, so listing order is capture order:

```
20260925T081425123Z-obs-0b9e7c7e-6a2f-4c7b-9a0e-3f1d2c4b5a69.json   observation
20260925T081425123Z-obs-0b9e7c7e-6a2f-4c7b-9a0e-3f1d2c4b5a69.jpg    its evidence crop
20260925T081430000Z-tlm.json                                         telemetry
```

A file only appears once fully written (the phone writes to a temp file and renames), and an
observation's JPEG is always written **before** its JSON.

### The API

Served by the processing client on port **8080** of its local IP (shown on its screen, e.g.
`http://192.168.43.1:8080`). Every `/api` call needs the token shown on the phone:
`Authorization: Bearer <token>` (or `?token=` for quick manual checks).

| Method | Path | Returns |
|---|---|---|
| `GET` | `/api/v1/status` | device id, bus, route, session state, linked cameras, GNSS, pending count |
| `GET` | `/api/v1/helpful` | `{count, files: [{name, type, bytes, modified_at, url}]}`, oldest first. `type` ∈ `observation`, `evidence`, `telemetry` |
| `GET` | `/api/v1/helpful/{name}` | the file — `application/json` or `image/jpeg` |
| `DELETE` | `/api/v1/helpful/{name}` | `204`. Deleting an observation JSON **also deletes its JPEG**. |

```bash
T=<token from the phone>; E=http://192.168.43.1:8080/api/v1
curl -s -H "Authorization: Bearer $T" $E/helpful | jq
curl -s -H "Authorization: Bearer $T" $E/helpful/<name>.json | jq
curl -s -X DELETE -H "Authorization: Bearer $T" $E/helpful/<name>.json -o /dev/null -w '%{http_code}\n'
```

### The consumer to write — `argus_api/ingest/edge_pull.py`

Configured with a list of edge units (`ARGUS_EDGE_UNITS=http://192.168.43.1:8080|<token>,...`).
For each unit, every ~5 s:

1. `GET /helpful`. Take the **`.json`** entries in order (JPEGs are fetched through their
   observation, never on their own).
2. `GET` the JSON and **validate** it against `contracts/schemas/` — the same validation as
   MQTT ingest step 1. `observation_id` present → observation; `health` present → telemetry.
3. **Observation:** `GET` the evidence JPEG named in `evidence.uri`
   (`edge://<device_id>/helpful/<name>.jpg` → `/api/v1/helpful/<name>.jpg`). Recompute its
   SHA-256 and compare with `evidence.sha256` (ingest step 3). Upload it to MinIO and
   **rewrite `evidence.uri`** to the `s3://argus-evidence/...` key before storing.
4. Hand the message to the **same ingest path as MQTT** — dedupe by id, reject unblurred
   evidence, stamp receipt, write the raw hypertable, notify fusion (ingest steps 2, 4–6).
5. **Only after the database commit succeeds**, `DELETE` the JSON. That is the consumption
   acknowledgement; the phone then frees the space.

**Rules that keep it correct:**

- **Never delete before the commit.** A crash between commit and delete just means the file is
  read again next poll, and dedupe-by-id makes the second read harmless. A crash between
  delete and commit loses data. So: commit, then delete — at-least-once, like MQTT QoS 1.
- **Don't delete what failed validation or hash verification.** Log it, skip it for the rest of
  the run, and surface it on the fleet-health panel; the file stays on the phone for someone to
  inspect. Deleting it would destroy the evidence of the bug.
- **Telemetry** is consumed the same way (validate → store → delete). It has no JPEG.
- The consumer has to be on the same network as the phone (in development, a laptop joined to
  the processing phone's hotspot).

**Later:** when the edge app gains its MQTT uplink, `helpful/` becomes the store-and-forward
spool behind it and this pull path is kept as the fallback for phones that come back to the
depot with a backlog.

---

<a id="multi-pass-evidence-fusion"></a>
## Multi-pass evidence fusion

**This is the core of the platform.** Fifteen buses drive the same corridor forty times a day.
Naively that is forty pins on a map for one pothole, and a dashboard nobody trusts.

Fusion is not merely deduplication. Combining N observations of the same defect **improves
every field**, because N independent measurements of the same thing are strictly more
informative than one.

### Step 1 — Clustering

DBSCAN over observations, in metres (local UTM projection, not degrees), **partitioned by
`(segment_id, class_id)`**:

```python
eps = 8.0          # metres. ~1.5× the single-pass positional σ of 5.2 m
min_samples = 1    # every observation seeds at least a candidate
```

**Why partition by segment.** Two potholes 6 m apart on opposite carriageways of a divided
road are geometrically adjacent and functionally unrelated — different direction, different
maintenance crew, different work order. Partitioning by the map-matched segment separates them
for free. Clustering on raw distance alone would merge them, and the resulting asset would be
on neither carriageway.

**Why `min_samples = 1`.** A lone observation should still become a `candidate` asset — it
just must not become `confirmed`. Requiring density to form a cluster at all would discard
first sightings, which is exactly the data you need to detect a *new* pothole.

### Step 2 — Confidence, accumulated in log-odds

Probabilities don't average. Evidence adds — in log-odds space:

```
  L  =  log( p_prior / (1 − p_prior) )              prior = class base rate

  for each observation i:
      L  +=  w_i · log( p_i / (1 − p_i) )

  p_fused = σ(L) = 1 / (1 + e^(−L))
```

where the weight `w_i` is the honest part:

```
  w_i  =  r_device  ×  q_conditions  ×  a_range  ×  γ^(n_d − 1)
          │            │                │           │
          │            │                │           └─ CORRELATION DISCOUNT: the n-th
          │            │                │              observation from the SAME device,
          │            │                │              γ ≈ 0.5
          │            │                └───────────── range attenuation; 25 m is worth
          │            │                               less than 10 m
          │            └────────────────────────────── illumination × (1 − occlusion)
          │                                            × (1 − motion_blur)
          └─────────────────────────────────────────── per-device reliability, learned from
                                                       that device's operator-rejection rate
```

**The correlation discount is what makes the whole thing correct.** Log-odds accumulation
assumes *independent* evidence. Forty sightings from one bus are not forty independent pieces
of evidence — they're one camera, one calibration, one recurring viewing geometry, at one time
of day. A miscalibrated camera that sees a shadow as a pothole will see it forty times with
complete consistency. The geometric discount means that bus's fortieth sighting adds
essentially nothing, while a *different* bus's first sighting adds a great deal.

The practical consequence, which is the line worth saying out loud:

> **Three independent 0.70 detections from three different buses outrank one isolated 0.95.**

And the corollary that makes false positives harmless: a single bus's mistake produces a
`candidate` that never reaches `confirmed`, never becomes a work order, and never appears on
the default dashboard.

### Step 3 — Position, by inverse-variance weighting

Each observation carries its own `accuracy_m`. Combine them weighted by precision:

```
       Σ ( x_i / σ_i² )                          1
  x̂ = ─────────────────        σ̂²  =  ───────────────────────
        Σ ( 1 / σ_i² )                     Σ ( 1 / σ_i² )
```

So a clear daytime observation at 9 m range dominates a rainy one at 24 m, automatically,
without a hand-tuned rule.

| Passes | Positional σ |
|---:|---|
| 1 | ±5.2 m |
| 4 | ±2.6 m |
| 12 | ±2.2 m (floored) |

`σ̂` is **floored at ~2 m** rather than allowed to shrink as 1/√N forever. GNSS multipath in an
urban canyon is spatially correlated — every bus passing that flyover inherits a similar bias,
so repeated passes share the error rather than averaging it out. Claiming centimetre accuracy
from 40 passes would be arithmetic that ignores physics. ±2 m is inside a lane width, which is
the precision a patching crew actually needs.

### Step 4 — Severity, robustly

- **Value:** the **median** of per-observation severity, not the mean. One observation ranged
  badly through rain produces an outlier, and a median ignores it where a mean would not.
- **Trend:** **Theil–Sen slope** of severity against time — a median-of-slopes estimator that
  tolerates up to ~29% outliers. Yields `worsening` / `stable` / `improving`.

The trend is a genuinely new capability rather than a refinement. A pothole widening week on
week is a different maintenance priority from a stable one, and **only a fleet that repeatedly
passes the same road can produce that derivative.** A one-off inspection, a citizen complaint,
or a fixed CCTV camera cannot. This is what "proactive road maintenance" in the brief actually
looks like when you build it.

### Step 5 — Canonical evidence

Across all observations, pick the crop maximising `sharpness × illumination × subject_size`,
and keep up to six more spread across time.

The fleet effectively photographs every defect dozens of times under varying light. The
platform keeps the best frame — and the gallery shows an engineer the *deterioration*, not a
single moment.

### Step 6 — Negative evidence

Every `segment_pass` whose `inspection.inspected_for` contains a class, whose `found` does not,
and which qualified on conditions and `assessable_fraction`, contributes **negative** log-odds
to every asset of that class on that segment.

This one mechanism does two jobs:

```
  asset EXISTS + repeated qualifying non-observation   ⇒  RESOLVED
                                                          (the repair verified itself)

  asset EXPECTED (OSM / history) + never observed      ⇒  MISSING_*
                                                          (the map-difference conclusion)
```

Same machinery, opposite priors. That symmetry is why `SegmentPass` exists as a first-class
message.

---

<a id="asset-lifecycle"></a>
## The asset lifecycle

```
                    ┌───────────┐
    first obs ─────▶│ candidate │
                    └─────┬─────┘
       ≥K distinct devices │        operator says "not real"
       AND p_fused ≥ 0.85  │        ┌──────────────┐
                           ├───────▶│   rejected   │──▶ hard negative,
                           ▼        └──────────────┘    feeds retraining
                    ┌───────────┐
                    │ confirmed │───▶ work order drafted
                    └─────┬─────┘
                          │ issued to ward
                          ▼
                    ┌───────────┐         ┌──────────────┐
                    │ reported  │────────▶│ in_progress  │
                    └─────┬─────┘         └──────┬───────┘
                          │                      │
                          └──────────┬───────────┘
                                     ▼
                   N qualifying passes find nothing
                                     ▼
                              ┌───────────┐
                              │ resolved  │  ← NOBODY TOLD THE SYSTEM
                              └───────────┘
                                     │
                   no qualifying pass in 30 days
                                     ▼
                              ┌───────────┐
                              │   stale   │  ← platform declines to assert
                              └───────────┘
```

Defaults, all configurable per class:

| Parameter | Default | Reasoning |
|---|---|---|
| `K` distinct devices to confirm | 2 | One device is an opinion. Two is corroboration. |
| `p_fused` to confirm | 0.85 | Tuned against the operator rejection rate after week 6. |
| Qualifying passes to resolve | 3 | Fewer risks closing on three occluded passes. |
| Stale threshold | 30 days | Beyond this, silence means "not surveyed", not "fine". |

**`stale` is the state that keeps the platform honest.** A system that quietly reports "no
defects" for roads no bus has driven in a month is actively misleading a ward engineer. Saying
*"we have not looked recently"* is a more useful output than a confident nothing, and it pairs
with the coverage layer in the UI.

**`rejected` is the feedback loop.** An operator marking a false positive writes a hard
negative with its evidence crop straight into the retraining set, and decrements that device's
reliability weight. The model improves from use, and a bus with a dirty lens gradually stops
being believed.

---

## Database schema

```sql
-- ─── Reference geography (loaded once from OSM + BBMP) ──────────────────────
CREATE TABLE road_segment (
  segment_id   text PRIMARY KEY,               -- 'osm:way/23847561:3'
  osm_way_id   bigint NOT NULL,
  geom         geometry(LineString, 4326) NOT NULL,
  name         text,
  highway      text,                            -- trunk | primary | secondary ...
  oneway       boolean DEFAULT false,
  maxspeed_kmh smallint,
  length_m     real NOT NULL,
  ward_id      text REFERENCES ward(ward_id),
  freeflow_kmh real                             -- learned; see docs/07
);
CREATE INDEX ON road_segment USING GIST (geom);

CREATE TABLE ward (
  ward_id    text PRIMARY KEY,                  -- 'bbmp:150'
  name       text NOT NULL,
  geom       geometry(MultiPolygon, 4326) NOT NULL,
  population integer
);
CREATE INDEX ON ward USING GIST (geom);

-- The Road Asset Ledger's expectation side
CREATE TABLE expected_asset (
  expected_id  text PRIMARY KEY,                -- 'osm:node/1029384756'
  class_id     text NOT NULL,
  segment_id   text REFERENCES road_segment,
  geom         geometry(Point, 4326) NOT NULL,
  source       text NOT NULL,                   -- osm | historical_observation | municipal
  valid_from   timestamptz NOT NULL DEFAULT now()
);

-- ─── Raw telemetry: immutable, append-only, never read by a UI ───────────────
CREATE TABLE observation (
  observation_id  uuid NOT NULL,
  captured_at     timestamptz NOT NULL,
  received_at     timestamptz NOT NULL DEFAULT now(),
  device_id       text NOT NULL,
  segment_pass_id uuid,
  class_id        text NOT NULL,
  confidence      real NOT NULL,
  geom            geometry(Point, 4326) NOT NULL,
  accuracy_m      real NOT NULL,
  segment_id      text,
  severity_score  real,
  conditions      jsonb NOT NULL,
  geometry_m      jsonb,
  evidence_uri    text,
  evidence_sha256 char(64),
  model_version   text NOT NULL,
  asset_id        uuid,                         -- set by fusion
  PRIMARY KEY (observation_id, captured_at)
);
SELECT create_hypertable('observation', 'captured_at', chunk_time_interval => INTERVAL '1 day');
CREATE INDEX ON observation USING GIST (geom);
CREATE INDEX ON observation (segment_id, class_id, captured_at DESC);

CREATE TABLE segment_pass (
  segment_pass_id uuid NOT NULL,
  entered_at      timestamptz NOT NULL,
  exited_at       timestamptz NOT NULL,
  device_id       text NOT NULL,
  route_id        text,
  trip_id         text,
  segment_id      text NOT NULL,
  ward_id         text,
  direction       text NOT NULL,
  mean_speed_kmh  real NOT NULL,
  stopped_s       real,
  dwell_excl_s    real,
  vehicle_counts  jsonb,
  occupancy_ratio real,
  pcu_estimate    real,
  pedestrian      jsonb,
  inspected_for   text[] NOT NULL,
  found           text[] NOT NULL,
  assessable_frac real NOT NULL,
  conditions      jsonb NOT NULL,
  PRIMARY KEY (segment_pass_id, entered_at)
);
SELECT create_hypertable('segment_pass', 'entered_at', chunk_time_interval => INTERVAL '1 day');
CREATE INDEX ON segment_pass (segment_id, direction, entered_at DESC);

CREATE TABLE telemetry (
  at         timestamptz NOT NULL,
  device_id  text NOT NULL,
  geom       geometry(Point, 4326),
  speed_kmh  real,
  heading    real,
  delay_s    integer,
  health     jsonb NOT NULL
);
SELECT create_hypertable('telemetry', 'at', chunk_time_interval => INTERVAL '6 hours');

-- ─── Fused truth: what every UI and API actually reads ───────────────────────
CREATE TABLE asset (
  asset_id          uuid PRIMARY KEY,
  class_id          text NOT NULL,
  state             text NOT NULL DEFAULT 'candidate',
  geom              geometry(Point, 4326) NOT NULL,
  accuracy_m        real NOT NULL,
  segment_id        text REFERENCES road_segment,
  ward_id           text REFERENCES ward,
  confidence        real NOT NULL,
  log_odds          double precision NOT NULL,   -- kept so new evidence is incremental
  evidence_count    integer NOT NULL DEFAULT 1,
  distinct_devices  integer NOT NULL DEFAULT 1,
  negative_passes   integer NOT NULL DEFAULT 0,
  severity_score    real,
  severity_band     text,
  severity_trend    text,
  physical          jsonb,
  first_seen_at     timestamptz NOT NULL,
  last_evidence_at  timestamptz NOT NULL,
  confirmed_at      timestamptz,
  resolved_at       timestamptz,
  canonical_evidence jsonb,
  ledger            jsonb,                       -- missing_* reasoning
  UNIQUE (segment_id, class_id, geom)
);
CREATE INDEX ON asset USING GIST (geom);
CREATE INDEX ON asset (state, class_id, ward_id);

CREATE TABLE work_order (
  reference      text PRIMARY KEY,               -- 'ARG-BBMP-150-004417'
  asset_id       uuid NOT NULL REFERENCES asset,
  ward_id        text NOT NULL REFERENCES ward,
  action         text NOT NULL,
  priority_rank  integer NOT NULL,
  issued_at      timestamptz NOT NULL DEFAULT now(),
  sla_due_at     timestamptz NOT NULL,
  closed_at      timestamptz,
  closed_by      text                            -- 'auto:fleet-verified' | operator id
);
```

`closed_by = 'auto:fleet-verified'` is a small column that carries a large idea, and it is the
one to point at on a slide.

### Continuous aggregates

TimescaleDB pre-computes these incrementally, which is the single feature that keeps heat-map
queries fast at fleet scale. The alternative — scanning raw passes per request — does not
survive contact with a live demo.

```sql
CREATE MATERIALIZED VIEW congestion_15min
WITH (timescaledb.continuous) AS
SELECT
  time_bucket('15 minutes', entered_at)                      AS bucket,
  segment_id,
  direction,
  avg(mean_speed_kmh)                                        AS mean_speed_kmh,
  count(*)                                                   AS passes,
  avg(occupancy_ratio)                                       AS occupancy,
  sum(pcu_estimate)                                          AS pcu
FROM segment_pass
GROUP BY bucket, segment_id, direction;

CREATE MATERIALIZED VIEW pedestrian_density_hourly
WITH (timescaledb.continuous) AS
SELECT
  time_bucket('1 hour', entered_at)                          AS bucket,
  segment_id,
  sum((pedestrian->>'unique_tracks')::int)                   AS pedestrians,
  sum((pedestrian->>'observed_area_m2')::real)               AS observed_area_m2,
  sum((pedestrian->>'crossing_events')::int)                 AS crossings
FROM segment_pass
WHERE pedestrian IS NOT NULL
GROUP BY bucket, segment_id;
```

Note that `pedestrian_density_hourly` stores the **numerator and denominator separately** and
never a pre-divided rate. Rates cannot be re-aggregated — averaging per-segment densities
across a ward gives the wrong answer unless each is weighted by its observed area. Keeping
both columns means any later rollup is still correct.

---

<a id="work-orders"></a>
## Work orders

A `confirmed` asset drafts a work order carrying:

- the class's **recommended action** from the taxonomy (`backend/argus_api/fusion/actions.py`)
- the **ward** and its engineer-of-record
- an **SLA** by severity band (critical 24 h, high 72 h, medium 7 d, low 30 d)
- a **priority rank** within the ward, from severity × confidence × exposure (how many buses,
  and therefore roughly how many road users, pass it daily)
- the **canonical evidence photograph** and the gallery
- the **coordinates**, to ±2 m

Exportable as CSV for a works department and as a one-page PDF per order. Unglamorous, and the
difference between a dashboard and a tool someone uses on a Tuesday morning.

**Exposure-weighted priority is worth defending in the video.** Two identical potholes are not
equally urgent if one is on a corridor 40 buses use per hour and the other is on a lane used by
two. The fleet measures that exposure directly, as a by-product of being the sensor.

<a id="incident-reports"></a>
## Incident reports

One packet per incident:

| Field | Source |
|---|---|
| Type, timestamp, GPS, road segment | `incident` message |
| Plate + overall confidence + **per-character confidence** | ANPR vote |
| Kinematic triggers (TTC, lateral g, ego impact g) | `triggers` block |
| Evidence clip (from depot wifi) + plate crop | MinIO, encrypted |
| SHA-256 chain of custody | verified at ingest |
| Access log | every view recorded against an operator identity |

The triggers block is what makes the report reviewable rather than merely assertive. An
operator sees *why* the system concluded rash driving, and can disagree.

---

## API surface

```
GET  /api/v1/assets?bbox=&class=&state=&ward=&severity=&since=
GET  /api/v1/assets/{id}                       full evidence gallery + observation history
POST /api/v1/assets/{id}/reject                operator feedback → hard negative
GET  /api/v1/incidents?since=&type=
GET  /api/v1/incidents/{id}/report.pdf

GET  /api/v1/analytics/congestion?bbox=&at=&direction=&window=
GET  /api/v1/analytics/pedestrian-density?bbox=&window=&resolution=
GET  /api/v1/analytics/ward-scorecard?period=
GET  /api/v1/analytics/road-condition?bbox=
GET  /api/v1/analytics/coverage?bbox=&since=   ← what we have NOT surveyed
GET  /api/v1/analytics/route-delay?route=      ← phase 3

GET  /api/v1/fleet/live                        current positions + health
GET  /api/v1/work-orders?ward=&state=
GET  /api/v1/work-orders/export.csv

WS   /ws/live                                  asset.created | asset.updated |
                                               asset.resolved | incident | telemetry
```

Auth is JWT with three roles: `viewer` (read), `engineer` (work orders, reject), `admin`
(devices, thresholds). Every incident-evidence access is audit-logged.

**The `coverage` endpoint is not filler.** It is the endpoint that stops the platform lying by
omission, and a judge who asks "what about roads buses don't go down?" gets an answer that is
already built rather than a concession.

---

## Folder layout

```
backend/
├── argus_api/
│   ├── ingest/      MQTT subscriber, validation, hash verification, spool→DB
│   ├── fusion/      clustering, log-odds, lifecycle, ledger, actions.py
│   ├── analytics/   congestion, pedestrian density, ward index, coverage, delay
│   ├── api/routes/  REST handlers
│   ├── realtime/    Redis pub/sub → WebSocket fan-out
│   ├── db/          SQLAlchemy models, Alembic migrations, PostGIS helpers
│   └── core/        config, auth, audit log, object storage
├── seeds/           OSM Bengaluru extract loader, BBMP wards, BMTC GTFS
└── tests/           fusion property tests, API contract tests, analytics fixtures
```

`fusion/` gets the most test attention by a wide margin. It is the component where a subtle bug
produces plausible-looking wrong answers rather than a crash — the worst failure mode in the
system, and the one a demo will never reveal.

**Property-based tests are the right tool here**, because fusion has invariants that must hold
for *any* input: confidence never exceeds 1 or drops below 0; adding an observation from an
already-seen device raises confidence strictly less than one from a new device; positional σ
never increases when evidence is added; a resolved asset re-opens if a positive observation
arrives. Hypothesis can generate thousands of observation sequences and check all of those,
which hand-written examples never will.

## Running it

```bash
docker compose -f ops/compose/docker-compose.yml up -d
cd backend && uv sync

uv run alembic upgrade head
uv run python -m argus_api.seeds.load_osm      --bbox bengaluru-core
uv run python -m argus_api.seeds.load_wards    --source bbmp
uv run python -m argus_api.seeds.load_gtfs     --source bmtc

uv run fastapi dev argus_api/main.py            # :8000
uv run python -m argus_api.ingest.worker        # MQTT → DB
uv run python -m argus_api.fusion.worker        # observations → assets
```
