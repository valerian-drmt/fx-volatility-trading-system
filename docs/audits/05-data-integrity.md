# Audit 5 — Data Integrity & Persistence

## Why

The stack has three data planes — Redis (live cache + pub/sub), Postgres (history,
28 ORM classes, 54 migrations), and in-memory engine state — and a dedicated db-writer
moving data between them. Bugs here don't crash anything: they produce charts of stale
data presented as live, analytics computed on gappy history, or a config hot-reload that
half the services saw. Silent wrongness is the theme.

## Scope

- `src/persistence/` — `models.py`, `db.py`, `writer.py`, all 54 alembic revisions.
- `src/engines/db_writer/` — `service.py` batching/retry/idempotency.
- `src/bus/` — channels, keys, publisher (payload contracts).
- `src/shared/db_events.py` — db-events publishing.
- Direct-write paths in vol/risk/execution engines (they bypass db-writer for own tables).
- `VolConfig` versioned-config machinery + Redis hot-reload.

## What to check

### Write pipeline
- [ ] db-writer idempotency: what is the dedup key; can a Redis redelivery or writer
      retry double-insert a row?
- [ ] Batch failure semantics: one bad row in a batch — is the whole batch lost, retried
      forever (poison message), or dead-lettered?
- [ ] Backpressure: Redis outpacing Postgres — bounded queues or unbounded memory growth?
- [ ] Ordering: does any consumer assume in-order delivery that pub/sub doesn't guarantee?

### Schema & migrations
- [ ] `alembic upgrade head` from empty DB == models.py metadata (autogenerate diff is
      empty). Round-trip downgrade sanity on recent revisions.
- [ ] Constraints match invariants: unique keys on natural keys (timestamps + symbol +
      tenor…), FKs where references exist, NOT NULL where code assumes presence.
- [ ] Timestamps: timezone-aware everywhere (UTC in DB), no naive-datetime mixing —
      grep-level sweep plus column type review.
- [ ] Numeric types: prices/vols stored with adequate precision (float vs Numeric
      decisions are deliberate, documented).

### Cache coherence
- [ ] Every Redis last-value key has a defined writer, TTL/staleness policy, and
      timestamp field — consumers can always tell *how old* a value is.
- [ ] "Markets closed" serving paths (`_fair_q`, last full IV grid — commits f301a560,
      0f4be379): stale data is *labeled* stale through to the API response.
- [ ] Key/channel naming: single source in `bus/keys.py` / `channels.py` — no literal
      strings drifting in engines or api.

### Config versioning
- [ ] `VolConfig` append-only actually enforced (no UPDATE path); hot-reload pub/sub
      reaches every consumer; a service that missed the message converges (re-read on
      interval or on reconnect?).

### Payload contracts
- [ ] Publisher and consumer schemas for each channel agree (field names, units, null
      handling) — build a channel contract table.

## Method

Static review + local-stack experiments (kill db-writer mid-batch, publish a malformed
payload, run migration round-trips on a scratch DB) gated behind `DB_RUN_INTEGRATION`/
`REDIS_RUN_INTEGRATION`.

## Deliverable & exit criteria

- Findings report + the Redis channel/key contract table committed to `docs/architecture/`.
- **Exit:** autogenerate diff empty; no double-insert reproducible; staleness visible on
  every read path; zero Blocker/High open.