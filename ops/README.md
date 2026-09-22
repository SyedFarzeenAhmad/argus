# `ops/` — infrastructure and the replay service

```bash
docker compose -f ops/compose/docker-compose.yml up -d
```

Brings up PostgreSQL + PostGIS + TimescaleDB, an EMQX broker, Redis, MinIO and a local pmtiles
server. Everything binds to `127.0.0.1`. Volumes land in `ops/volumes/`, which is gitignored.

## The replay service

```bash
python ops/replay/replay.py --log ops/replay/logs/bengaluru-mgroad.jsonl --speed 1.0
```

Reads a recorded `.jsonl` of real `Observation` / `SegmentPass` / `Incident` / `Telemetry`
messages and publishes them to MQTT at wall-clock speed. Produce a log with the edge pipeline's
`--sink file://` flag.

**It does three jobs, and it is maintained for all ten weeks — not abandoned after week 2:**

1. **Unblocks two people.** Backend and frontend develop against real message shapes from day
   one, without waiting for the CV pipeline or for anyone's laptop to have a GPU free.
2. **Reproducible testing.** The same log replays identically, so a fusion regression is
   detectable rather than anecdotal.
3. **The demo-day fallback.** If live inference fails on stage, replay produces an identical
   dashboard from a stored log. This is not a shortcut — it is the same substitution point the
   mock frontend was built around, kept honest. Practise switching to it mid-sentence.

## Seeding history for a demo

A platform whose value is *accumulation* — confidence rising with corroboration, severity
trends over weeks, wards ranked by durability — has nothing to show on a database created five
minutes ago. Generate ~30 days of plausible history from replayed logs before presenting:

```bash
python ops/replay/seed_history.py --days 30 --devices 15 --routes 6
```

**Say that it was generated.** Seeded demo history presented as real fleet data is the one
place in this project where a small dishonesty would be genuinely damaging, and it is entirely
unnecessary — "this is thirty days of simulated fleet history, so you can see what the
analytics look like once they've accumulated" costs nothing and sounds like competence.

## Layout

```
ops/
├── compose/     docker-compose.yml, init-db.sql
├── replay/      replay.py, seed_history.py, logs/
├── mosquitto/   TLS + per-device X.509 config for non-local deployment
├── grafana/     optional dashboards for fleet health during development
└── volumes/     gitignored runtime state
```
