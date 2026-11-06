# Redis durability: what is lost, when, and why that is acceptable

Redis is the bus and the last-value cache of the stack, never a system of
record. Postgres holds every durable fact (trades, orders, positions, history,
config). This note is the acceptable-loss statement the audit asked for
(08#10, #343); it used to live only as a comment in
`infrastructure/redis/redis.conf`.

## Configuration

| Setting | Value | Why |
|---|---|---|
| `maxmemory` | 256 MB | bounded on a 3.8 GB host that also runs Postgres and six engines |
| `maxmemory-policy` | `volatile-lru` | evict only keys with a TTL; a key without a TTL is never silently dropped |
| `save` | 900/1, 300/10, 60/10000 | RDB snapshot to the `redis_data` volume |
| `appendonly` | `no` | no fsync-per-write; nothing here needs it |
| `FLUSHDB` / `FLUSHALL` / `CONFIG` | renamed away | no wipe from a compromised service |

Until 2026-11-06 the policy was `allkeys-lru`, which could evict *any* key
under memory pressure. The services only write TTL keys (see the inventory),
so `volatile-lru` behaves the same in normal operation and differs in one
case: a future non-TTL key under memory pressure makes writes fail loudly
(`OOM command not allowed`) instead of evicting state. That is the failure we
want to see.

## Inventory of what lives in Redis

| Key family | Writer | TTL | On loss |
|---|---|---|---|
| `latest_spot:*`, `latest_bid:*`, `latest_ask:*` | market-data | 30 s | next tick rewrites it |
| `bars:*`, `market_status:*` | market-data | 6 h / 5 min | rebuilt from IB history on the next fetch |
| `latest_vol_surface:*`, `latest_signals:*` | vol-engine | 10 min | next cycle rewrites it; panels show a stale badge meanwhile |
| `latest_greeks:portfolio`, `latest_pnl_curve`, `account_snapshot` | risk-engine / execution | 30 s to 60 s | next cycle rewrites it |
| `analytics:*` | analytics-engine | 2 h | next cycle rewrites it |
| `heartbeat:<engine>` | every engine | 5 min | `/health/extended` reports STALE after 30 s, DOWN when the key expires |
| `contract_marks:EUR`, `option_marks:EUR`, `unrealized_pnl:EUR` (hashes) | execution | 600 s | next position sync rewrites them |
| `cycle_progress:vol_engine` (hash) | vol-engine | 600 s | progress bar restarts |
| `trade:preview:*`, submit locks | api | 10 s to minutes | the trader re-previews; a lock loss is handled by the DB uniqueness (#319) |
| `db_events` pub-sub channel | engines → db-writer | none for pub/sub (fire and forget) | **the only real loss**: frames published while db-writer is down are gone; the engines that own tables (risk, execution) write them directly. Delivery once the writer has the frame is covered by #324 (below) |

Pub/sub itself is not stored: a subscriber that is down misses messages by
design. The WebSocket bridge and the db-writer are the two subscribers; both
recover by re-reading last values from Redis or from Postgres on start.

## What a Redis restart costs

- Up to one cache cycle per family (seconds to a few minutes) during which the
  panels show their last values with a stale badge (#369) and `/health/extended`
  reports the engines DOWN until they heartbeat again.
- Pub/sub frames published during the outage. The db-writer path loses them;
  the engine-owned tables do not.
- Nothing in the trading record. Every order, fill, position and config change
  is in Postgres before anything else reads it.

## What is **not** acceptable, and where it is tracked

- A state key written without a TTL that becomes load-bearing: the policy now
  makes that fail loudly; the key inventory above is the checklist for reviews.
- Relying on Redis to carry a trade through a restart: forbidden by design;
  the submit path's Redis lock is a convenience, the DB constraint is the
  guard (#319).
- Silent loss of db-writer frames inside the writer: closed by #324. Every
  streamed table has a natural key and the vol engine stamps one `cycle_ts`
  per cycle, so a retry is a no-op (`ON CONFLICT DO NOTHING`); a bulk insert
  that fails on a row error falls back to per-row writes and the rows that
  still fail land in `db_writer_dead_letter` with the error
  (`db_writer_dead_letter_total`); a full queue drops with a counter
  (`db_writer_queue_dropped_total`, alert `DbWriterDroppingFrames`). What
  remains open is the pub/sub hop itself (frames published while the writer
  is down), tracked above.

  Replaying a dead letter by hand: read the row, fix the cause (usually a
  producer sending a column the table does not have), then re-publish the
  payload on `db_events` or insert it directly, and stamp `replayed_at`.

## Operations

- Memory headroom: `docker exec fxvol-redis redis-cli -a "$REDIS_PASSWORD" INFO memory | grep used_memory_human` on the host (never echo the password itself; see `docs/ops/secrets.md`).
- Eviction count: `INFO stats | grep evicted_keys`. Non-zero means the cache is
  under pressure: raise `maxmemory` or shorten TTLs before anything else.
