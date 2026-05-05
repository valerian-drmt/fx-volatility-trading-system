-- Runs once on first Postgres startup (empty data dir). Subsequent boots skip
-- this file — Alembic owns the schema from R1 PR #5 onwards, so we keep the
-- init script minimal: just the extensions the app expects to be present.
--
-- The role and database themselves are provisioned by the postgres:16-alpine
-- image from the POSTGRES_USER / POSTGRES_DB / POSTGRES_PASSWORD env vars
-- declared in docker-compose.yml.

-- Enable uuid_generate_v4() for any future primary key that prefers UUIDs
-- over bigint identity. Currently unused by the ORM but cheap to ship.
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- pg_stat_statements is the canonical query-profiling extension — useful when
-- debugging slow analytics queries from the FastAPI router.
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
