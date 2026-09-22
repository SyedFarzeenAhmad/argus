-- Extensions ARGUS depends on. Alembic owns every table; this file owns only the
-- extensions, because they must exist before the first migration runs.

CREATE EXTENSION IF NOT EXISTS postgis;          -- spatial types, GIST indexes, ST_* joins
CREATE EXTENSION IF NOT EXISTS timescaledb;      -- hypertables + continuous aggregates
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pg_trgm;          -- fuzzy search over road and ward names

-- h3-pg powers the pedestrian-density hexbins. It is not in the base image; if it
-- is unavailable, the analytics layer falls back to computing H3 indexes in Python
-- and storing them as text. Slower, identical output.
-- CREATE EXTENSION IF NOT EXISTS h3;
