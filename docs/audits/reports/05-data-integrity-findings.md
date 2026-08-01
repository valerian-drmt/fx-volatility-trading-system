# Audit 5 — Data Integrity & Persistence — Findings

**Run date:** 2026-08-01 · **Scope:** `src/persistence/`, `src/engines/db_writer/`, `src/bus/`,
`src/shared/db_events.py`, engine direct-write paths, `VolConfig` machinery.
**Method:** static analysis only. No source/config was modified, no migration was run, no
Postgres/Redis connection was opened, no git state changed. Everything requiring a live
resource is deferred to the checklist in Appendix B.

Builds on Wave 1 (`WAVE1-SUMMARY.md`). QM-02, A6-02 and the `VOL_INLINE_ANALYTICS` residue
are *not* re-reported — this audit hunts their siblings and their persistence consequences.

---

## Executive summary

**4 Blocker · 13 High · 16 Medium · 5 Low.**

The theme of this audit is that **the system's idempotency, dedup and staleness guarantees
are declared but not real**. Four structural defects account for most of the severity:

1. **The dedup key is a wall-clock timestamp.** Four of the five entries in
   `IDEMPOTENT_TABLES` key `ON CONFLICT DO NOTHING` on `(…, timestamp)` where `timestamp` is
   `datetime.now(UTC)` computed *at the producer*. A value that never repeats cannot dedup
   anything except a byte-identical redelivery of one Redis frame. Every real duplication
   mode — producer retry, engine restart mid-cycle, two engines writing the same table —
   generates a fresh microsecond timestamp and inserts a duplicate row. The fifth entry
   (`open_position`) documents a deterministic hash-PK that **no longer exists in the schema**
   (it is a plain serial), and that table is no longer fed through the db-writer at all.

2. **A second QM-02, in the same file family.** `position_monitor` reads `payload.get("spot")`
   from the `latest_vol_surface` Redis payload. `publish_vol_update` writes
   `{symbol, timestamp, surface}` — there is no `spot` key. The read is therefore *always*
   `None`, and every MTM cycle silently falls back to the position's **entry** spot, or to a
   hardcoded `1.085`. That fabricated value is persisted every 60 s into a column called
   `spot`, feeds the gamma-PnL attribution (which is consequently identically zero for every
   position, forever), and feeds the delta-hedge trigger.

3. **Deleting a position destroys its entire history.** `position_sync` issues
   `db.delete(open_position_row)` when a position leaves the IB snapshot. `OpenPosition`
   declares `cascade="all, delete-orphan"` on `metric_history` *and* the FK carries
   `ondelete="CASCADE"`. The in-code justification says the audit trail survives "in fills and
   snapshots" — `open_position_history` **is** the snapshots table, and it is exactly what the
   cascade wipes.

4. **The vol-engine's pruner has been silently disabled by a foreign key.**
   `_prune_history` deletes `vol_surface_history` and `pca_signal_history` in **one session,
   one commit**. But `trade_preview.pca_signal_id` and `trade_structure.pca_signal_id`
   reference `pca_signal_history.id` with **no `ondelete` clause** — so a single signal older
   than 90 days that is still referenced by an open structure raises `IntegrityError`, which
   rolls back the *whole* session (including the `vol_surface_history` delete) and is
   swallowed by a bare `except Exception` in the caller. Both tables then stop pruning
   permanently, and `vol_surface_history` is the engine's own documented ~1.1 MB/day table.

Beyond those: the write pipeline has no dead-letter (one bad row discards up to 100 good
ones), no delivery guarantee (Redis pub/sub, fire-and-forget, nothing published while the
db-writer restarts is ever recovered), and no staleness contract on any REST read path —
`SurfaceResponse.timestamp` silently changes meaning between "live tick" and "last complete
grid, possibly days old" depending on which branch of `get_latest_surface` served it.

The **producer/consumer contract table (Appendix A) found 4 hard schema mismatches and
9 orphaned keys/channels** across 20 Redis keys and 10 channels.

### Cross-audit convergence

- **QM-02 + DI-01 + DI-14 are one defect class**: a payload shape changed, the producer was
  updated, and consumers reading it by `.get()` degraded to `None` instead of failing. There
  is no schema object for any Redis payload in this system — every consumer re-derives the
  shape by hand with `.get()` chains and a silent fallback.
- **A6-02's tenor duplication has a persistence consequence** (DI-08): the two hourly-snapshot
  paths ship with *different* default gap constants (`55` vs `60` minutes) for the same gate,
  reading the same table.
- **QM-01 (BS returns 0.0 on degenerate input) compounds DI-01**: with `interpolate_iv`
  returning `None` (QM-02), `price_position` records `n_surface_missing > 0`, the monitor
  takes the linearised branch, and `current_delta_unhedged` is written as a literal `0.0` —
  a fabricated zero persisted as a measurement.

---

## Findings table

| ID | Sev | Location | One-liner |
|---|---|---|---|
| DI-01 | **Blocker** | `src/api/orchestration/position_monitor.py:614` | `payload.get("spot")` on a payload with no `spot` key → live spot is always `None`; MTM re-prices at entry spot or a hardcoded `1.085`, persisted as `spot` |
| DI-02 | **Blocker** | `src/persistence/writer.py:80-86` | `IDEMPOTENT_TABLES` keys on `datetime.now()` timestamps that can never collide; `open_position`'s documented hash-PK no longer exists — idempotency is nominal |
| DI-03 | **Blocker** | `src/engines/execution/position_sync.py:209`, `src/persistence/models.py:106-108,122-124` | Closing a position `DELETE`s `open_position`, cascade-wiping its entire `open_position_history` greeks/PnL time series |
| DI-35 | **Blocker** | `src/engines/vol/engine.py:653-661`, `src/persistence/models.py:481,603` | An FK with no `ondelete` makes the vol pruner raise, rolling back both deletes in its shared session; the exception is swallowed → `vol_surface_history` and `pca_signal_history` stop pruning forever |
| DI-04 | High | `src/persistence/writer.py:261-290` | One bad row discards the whole batch (up to 100 rows / 8 tables); no per-row fallback, no dead-letter, no metric |
| DI-05 | High | `src/persistence/writer.py:234-259` | Retry on `OperationalError` re-executes a batch whose commit may have landed → duplicate rows on the 3 non-idempotent tables |
| DI-06 | High | `src/shared/db_events.py:18-26`, `src/engines/db_writer/service.py:137-178` | `db_events` is fire-and-forget pub/sub: everything published while db-writer is down/reconnecting is permanently lost; the subscriber-count return value is never checked |
| DI-07 | High | `src/engines/db_writer/service.py:193-195` | Queue overflow drops events with a `warning` and no metric/alert; silent data loss under backpressure |
| DI-08 | High | `src/engines/vol/engine.py:63,712-715`, `src/engines/analytics/engine.py:200-216` | Both engines write `pca_surface_snapshot_history` by default with independent `now()` timestamps and *different* gate defaults (55 vs 60 min) → duplicate hourly samples poison the PCA/GMM fit |
| DI-09 | High | `src/persistence/models.py:217-223` vs `:272-275` | `regime_snapshot_history` has no unique key while its same-cycle sibling `feature_history` does → divergent dedup for one logical snapshot |
| DI-10 | High | `src/engines/risk/engine.py:390-430`, `src/engines/execution/position_sync.py:167-215` | Two uncoordinated writers on `open_position`, no version column, `expire_on_commit=False`; a concurrent delete makes the risk-engine's whole history commit fail on FK violation |
| DI-11 | High | `src/persistence/models.py` (8 tables) | Money/PnL/prices as `Float` in every execution-side table vs `Numeric` in every risk-side table — undocumented, inconsistent, accumulation error on the PnL ledger |
| DI-12 | High | `src/api/routers/regime.py:371-381`, migration `049_sync_drift.py:31-33` | Manual event insert never sets `event_hash`; NULL is now allowed → dedup fully bypassed. Rescheduled events insert a duplicate instead of updating |
| DI-13 | High | `src/engines/vol/main.py:33-76`, `src/engines/vol/engine.py:507-518` | Config hot-reload never converges: applies the message payload (not a DB re-read), no version check, no re-read on reconnect, no periodic poll — and `apply_config` is a no-op |
| DI-14 | High | `src/api/schemas/vol.py:10-20`, `src/api/orchestration/vol_service.py:38-86` | No staleness field on any vol read path; `timestamp` silently means either "live tick" or "last full grid, possibly days old" |
| DI-15 | High | `src/engines/execution/position_sync.py:282-290`, `src/engines/risk/engine.py:453-467` | `contract_marks:EUR` / `option_marks:EUR` / `unrealized_pnl:EUR` hashes: no per-field timestamp, `hset` never prunes stale fields, TTL reset wholesale → dead marks served as live |
| DI-36 | High | `src/engines/execution/fills_handler.py:74-84,210-241` | Every IB fill callback spawns an unserialised `asyncio.create_task` with its own session; two fast partial fills interleave and the later commit overwrites `qty_filled`/`avg_fill_price` from a stale fill set |
| DI-16 | Medium | `src/engines/db_writer/service.py:198` | `_DATETIME_FIELDS` whitelist misses `entry_timestamp` and all `Date` columns → latent permanent poison batch |
| DI-17 | Medium | `src/shared/db_events.py:25` | `json.dumps(default=str)` silently stringifies `Decimal`/numpy scalars; no schema validation at either end of `db_events` |
| DI-18 | Medium | `src/bus/keys.py`, `src/bus/channels.py` | 9 orphaned keys/channels: `account_snapshot`+`CH_ACCOUNT` (no producer), `CH_SYSTEM_ALERTS` (no producer), `market_status` (neither), `latest_bid`/`latest_ask`/`latest_signals`/`latest_greeks:portfolio`/`latest_pnl_curve` (write-only) |
| DI-19 | Medium | `position_monitor.py:608`, `trade.py:262`, `dev.py:58-66,121-125` | Redis key literals drift outside `bus/keys.py`; `DB_EVENTS_CHANNEL` lives in `shared/` not `bus/channels.py`; no `ENGINE_EXECUTION` constant |
| DI-20 | Medium | `src/api/orchestration/vol_service.py:59-85,144-146` | After f301a560, `_fair_q` is sourced from the last *full-grid* row and can be staler than the freshest persisted `_fair_q` — partially regressing 0f4be379 |
| DI-21 | Medium | `src/engines/risk/engine.py:221-239`, `src/engines/vol/engine.py:639-662` | 6 tables have no pruner; the 2 pruners that exist are cycle-counted (not wall-clock), cross-service, and issue one unbounded `DELETE` |
| DI-22 | Medium | `src/api/routers/regime.py:201` | `datetime.utcnow()` (naive) compared against a `timestamptz` column; the sibling function 60 lines up does it correctly |
| DI-23 | Medium | `src/persistence/models.py:888-891` | `ck_signal_track_status` declared in the ORM but dropped by migration 026 and never recreated — the live column has no CHECK |
| DI-24 | Medium | `src/persistence/migrations/versions/` | Filename order ≠ apply order (4 duplicate numeric prefixes); `254fc54bb36f_add_col_x.py` is an autogenerate artifact in the chain; `028` refuses to downgrade; `022`/`026`/`049` downgrades are lossy or fabricate data |
| DI-25 | Medium | `src/api/orchestration/config_service.py:107-109,153-155` | `MAX(version)+1` then INSERT with no lock; the PK catches the race (no silent duplicate) but surfaces as an unhandled 500 |
| DI-26 | Medium | `src/engines/analytics/engine.py:268-271`, `src/bus/publisher.py:183` | `analytics:regime_model` payload carries no timestamp (its sibling `pc3_history` does); `set_heartbeat` docstring says 30 s, actual TTL is 300 s |
| DI-27 | Medium | `src/persistence/models.py:81,142` | `quantity` as `Numeric(15,4)` for contract counts that are always integral |
| DI-28 | Medium | `src/core/vol/tenors.py:231` | Interpolated pillars persist `strike: None`, so historical smiles are un-reconstructable for interp tenors |
| DI-29 | Medium | `src/engines/vol/chain_fetcher.py:139,151` | `datetime.now()` (naive local) drives days-to-expiry contract selection — off-by-one-day near a boundary on a non-UTC host |
| DI-37 | Medium | `src/api/orchestration/book_state_refresh.py:71-92` | `book_state_snapshot_history` rows are only flipped `is_current=False`, never deleted — append-only with no pruner |
| DI-38 | Medium | `src/api/orchestration/position_monitor.py:391-399`, `src/engines/execution/hedge_executor.py:59-162` | `hedge_order` rows are created in the api container and transitioned in the execution container with no `FOR UPDATE` guard against a retried submit racing the finaliser |
| DI-30 | Low | `src/persistence/writer.py:58-67` | `TABLE_MODELS` lists 3 tables (`account_history`, `open_position`, `open_position_history`) with no `db_events` producer — dead dispatcher entries |
| DI-31 | Low | `src/api/orchestration/config_service.py`, migration `004` | `config_vol_engine` append-only is convention-only; no trigger/rule/permission at the DB level |
| DI-32 | Low | `src/api/routers/ws.py:63-72`, `:56-60` | `/ws/positions`, `/ws/exit_alerts`, `/ws/orders/{id}` have no frontend consumer |
| DI-33 | Low | `src/api/orchestration/vol_service.py:55-56,255-259` | Docstring lists pillars `(1M,2M,3M,6M,9M,1Y)` but `DISPLAY_PILLARS` is `(1M…6M)`; `_TENOR_YEARS` is a 5th copy of the tenor ladder (A6-02 sibling) |
| DI-34 | Low | `src/api/orchestration/events/hashing.py:14-18` | 64-bit truncated SHA-256 event hash — negligible collision risk at calendar scale, recorded for completeness |

---

## Blockers

### DI-01 — `latest_vol_surface` has no `spot` field; the position monitor reads one anyway

**Severity: Blocker** — corrupt data written to Postgres in normal operation, and a
money-moving decision (delta hedge) taken on it.

**Evidence.** The producer, `src/bus/publisher.py:132-136`:

```python
surface_payload = _dumps({
    "symbol": symbol,
    "timestamp": ts_str,
    "surface": surface_data,
})
```

Three keys. There is no `spot`. Confirmed against the only writer of that key
(`bus/publisher.py:143-147`) and against the vol-engine's surface builder — nothing in
`src/engines/vol/engine.py` assigns `surface["spot"]` or a top-level `spot`
(`_compute_surface` attaches only `_`-prefixed meta keys: `_rv_full_pct`, `_garch`, `_har`,
`_fair_q`, `_svi`, `_regime`, `_pca_signals`, `_symbol`).

The consumer, `src/api/orchestration/position_monitor.py:608-620`:

```python
raw = await client.get(f"latest_vol_surface:{self.symbol}")
...
payload = json.loads(raw)
surface = payload.get("surface") or payload
spot = payload.get("spot")                       # ← always None
atm_3m = (surface.get("3M") or {}).get("atm", {})
iv_3m = atm_3m.get("iv") if isinstance(atm_3m, dict) else None
```

Note that the *IV* read on the next line uses the correct nested path and works. Only `spot`
is wrong — exactly the QM-02 signature: a payload reshape that reached one reader and not the
adjacent one.

**Blast radius**, `position_monitor.py:197` onward:

```python
spot_eff = spot_now if spot_now is not None else (pos.entry_spot or 1.085)
```

`spot_now` is unconditionally `None`, so `spot_eff` is *always* `pos.entry_spot`, or the
literal `1.085` when the position has no recorded entry spot. From there:

- `_reprice_position(db, struct.id, surface, spot_eff, now)` → `price_position(spot=…)`
  re-prices every leg at a **frozen spot**. The whole point of the live re-pricing path.
- `attribute_pnl(spot_entry=pos.entry_spot or spot_eff, spot_now=spot_eff)` — both arguments
  are now the same number, so **`gamma_usd` is identically 0 for every position, forever**.
  It is persisted to `booked_position_metric_history.gamma_pnl_usd` at 60 s cadence.
- `compute_mtm(spot_now=spot_eff, …)` → `position_monitor.py:262,322` persist `spot=spot_eff`
  into `booked_position_metric_history.spot` and publish it on the `positions` channel. A
  column named `spot`, at 60 s resolution, containing the entry spot (or `1.085`) labelled as
  the current market.
- `current_delta_unhedged` from the live mark feeds `check_delta_hedge_needed(...)` at
  `position_monitor.py:383-390`, and its value is persisted onto
  `hedge_order.delta_imbalance_at_trigger`. The hedge decision is computed at a spot that has
  not moved since entry.

**The contrast that proves it is a bug, not a design choice.** `src/api/routers/trade.py:255-277`
reads the *same key* and does it correctly — it takes `payload.get("timestamp")`, computes
`age = (now - ts).total_seconds()`, and returns it. Two readers of one key in one repo: one
measures staleness and reads only fields that exist; the other reads a phantom field and
measures nothing.

**Recommended fix (described, not applied).**
1. Add `spot` to the `publish_vol_update` payload (the vol-engine already has `F` in hand at
   `engine.py:721` and passes `float(F)` to the `db_events` frame at `:746` — the same value
   simply never reached the Redis frame). This is the one-line correctness fix.
2. Make the read fail loudly rather than silently: if `spot` is absent, skip the cycle and
   emit a `system_alerts` message, rather than substituting entry spot. A monitor that cannot
   see the market must not write a row claiming it can.
3. Delete the `1.085` literal. A hardcoded FX rate as a fallback in a persistence path is
   indefensible — there is no value that is better than `NULL` here.
4. Structural: define the Redis payloads as Pydantic models in `bus/` and have both
   `publish_*` and every consumer go through them. Every finding in this class (QM-02, DI-01)
   exists because the payload contract is only ever expressed as ad-hoc `.get()` chains.

---

### DI-02 — the db-writer's idempotency key can never collide

**Severity: Blocker** — the audit question "can a Redis redelivery or writer retry
double-insert a row?" answers **yes, for every table**, despite the code claiming otherwise.

**Evidence.** `src/persistence/writer.py:80-86`:

```python
IDEMPOTENT_TABLES: dict[str, list[str]] = {
    "vol_surface_history": ["timestamp", "underlying"],
    "open_position": ["id"],
    "feature_history": ["symbol", "timestamp"],
    "pca_surface_snapshot_history": ["symbol", "timestamp"],
    "pca_signal_history": ["symbol", "timestamp", "pca_model_id", "pc_id"],
}
```

with the stated contract at `writer.py:69-79`: *"the engines can re-emit the same
(timestamp, underlying[, tenor]) on a retry: we want the first write to win and the second to
be a no-op."*

The producer, `src/engines/vol/engine.py:740-745`:

```python
ts_iso = datetime.now(UTC).isoformat().replace("+00:00", "Z")
await publish_db_event(
    self.redis,
    table="vol_surface_history",
    payload={"timestamp": ts_iso, "underlying": self.symbol, ...},
)
```

`ts_iso` is microsecond-resolution wall clock captured at publish time. Four of the five
entries include it in the conflict target. Therefore `ON CONFLICT DO NOTHING` fires **only**
when the exact same JSON frame is delivered twice — the one duplication mode Redis pub/sub
does not actually produce. The modes that *do* occur all mint a new timestamp:

| Duplication mode | New `timestamp`? | Deduped? |
|---|---|---|
| Redis redelivers one frame | no | yes (the only case that works) |
| Writer retries a batch after an in-doubt commit (DI-05) | no | yes for these 4, **no** for the 3 non-idempotent tables |
| Producer re-runs the cycle after a publish failure | **yes** | **no** |
| Engine restarts mid-cycle and re-emits | **yes** | **no** |
| Two engines write the same table (DI-08) | **yes** | **no** |

**The fifth entry is worse.** `writer.py:73-77` documents:

> `position` uses the primary key `id` (a deterministic hash of the IB composite key
> symbol/side/type/strike/maturity): re-observing the same open position on every risk cycle
> must be idempotent so that only the first sighting creates the row

That schema does not exist. `src/persistence/models.py:53` is `id: Mapped[int] =
mapped_column(primary_key=True)` — a plain autoincrement serial. And
`src/engines/execution/position_sync.py:183-195` constructs `OpenPosition(...)` with no `id`
at all, letting the sequence assign it. The comment describes a retired design; the
`ON CONFLICT (id)` it justifies is dead code on a table that is **not even routed through the
db-writer any more** (see DI-30 — `open_position` has zero `publish_db_event` producers).

**Recommended fix.** Give every idempotent table a *deterministic* natural key derived from
the data, not from the clock:
- `vol_surface_history` / `feature_history` / `regime_snapshot_history`: quantise the
  producer's timestamp to the cycle boundary (`floor` to the engine interval) before
  publishing, so a re-emitted cycle collides. Alternatively add an explicit
  `cycle_id`/`producer_seq` column and make that the conflict target.
- `pca_surface_snapshot_history`: quantise to the hour — the table's own semantics
  ("30-dim **hourly** snapshot") already imply the key should be the hour, not the instant.
  This also fixes DI-08 for free.
- Delete the `open_position` entry and its stale comment, or restore a deterministic id.
- Add a unit test that asserts, for each entry in `IDEMPOTENT_TABLES`, that publishing the
  same logical event twice from two separate producer invocations yields one row.

---

### DI-03 — closing a position cascade-deletes its entire metric history

**Severity: Blocker** — irreversible loss of the primary audit trail during normal operation.

**Evidence.** `src/engines/execution/position_sync.py:203-215`:

```python
# Closed positions = DELETE the row (audit trail lives in fills + snapshots).
...
if trustworthy:
    for key, db_row in db_by_key.items():
        if key not in ib_by_key:
            await db.delete(db_row)
            closed += 1
```

`src/persistence/models.py:106-108`:

```python
metric_history: Mapped[list[OpenPositionHistory]] = relationship(
    back_populates="position", cascade="all, delete-orphan"
)
```

`src/persistence/models.py:122-124`:

```python
position_id: Mapped[int] = mapped_column(
    ForeignKey("open_position.id", ondelete="CASCADE"), nullable=False
)
```

Both the ORM cascade and the database-level `ON DELETE CASCADE` fire. The comment's claimed
survivor — "snapshots" — is `open_position_history`, renamed from `PositionSnapshot` in
migration 026 and documented at `models.py:113-116` as *"Greeks + pnl + iv time series per
IB-leg position. Sole writer = risk-engine. One row per OPEN position per 2 s cycle."* That
is the table being destroyed.

**Magnitude.** At the risk-engine's 2 s cycle (`PRUNE_EVERY_CYCLES = 43_200 × 2s ≈ 24h`,
`engine.py:73`), a position held for one week accumulates ~300,000 rows of greeks, mark, IV
and PnL. All of it is deleted the moment IB stops reporting the position — which includes not
only a genuine close but any expiry roll-off. `trade_fill` survives, but fills are entry/exit
points; they cannot reconstruct the mark-to-market path, the greeks trajectory, or the IV the
position was carried at. Post-trade analysis of a closed position is impossible, and so is any
retrospective investigation of a loss.

**Recommended fix.** The `open_position` table is a *mirror of the live IB snapshot*, not the
book of record — deleting from a mirror is correct. The error is that history hangs off the
mirror's primary key.
1. Drop the FK cascade: make `open_position_history.position_id` nullable with
   `ondelete="SET NULL"`, and remove `cascade="all, delete-orphan"` from the relationship.
2. Denormalise the identity onto each history row so it stands alone — the columns are
   already there (`structure` = IB `localSymbol`, `contract_id`, `trade_id`, `package_id`),
   so history remains joinable to `trade_structure` after the mirror row is gone.
3. Preferably, replace the delete with a soft close (`closed_at` timestamp — migration 027
   already added such a column once) so the mirror keeps its identity row and the FK stays
   valid.
4. Add a migration-safe backfill guard and a test asserting that deleting an `open_position`
   leaves its history rows intact.

**Note for Valérian:** any history already lost is unrecoverable. Worth checking how much
survives before the fix lands — command in Appendix B.

---

### DI-35 — a foreign key silently disables the vol-engine's pruner, permanently

**Severity: Blocker** — unbounded growth of the largest table, masked by a pruner that appears
to work and by a log line nobody reads.

**Evidence.** `src/engines/vol/engine.py:653-661` — two deletes, **one session, one commit**:

```python
cutoff = datetime.now(UTC) - timedelta(days=HISTORY_RETENTION_DAYS)
async with get_sessionmaker()() as session:
    await session.execute(delete(VolSurface).where(VolSurface.timestamp < cutoff))
    await session.execute(delete(PcaSignal).where(PcaSignal.timestamp < cutoff))
    await session.commit()
```

`src/persistence/models.py:481` and `:603` — two tables reference the rows being deleted:

```python
pca_signal_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("pca_signal_history.id"))
```

on `TradePreviewRow` and on `TradeStructure`. **Neither declares `ondelete`**, so Postgres
applies the default `NO ACTION`, checked **immediately** — `grep DEFERRABLE src/persistence/`
returns zero hits, so there is no deferred-constraint escape either. The column being
nullable does not help: a *non-null* reference is what blocks the delete.

`src/engines/vol/engine.py:554-559` — the caller swallows it:

```python
if self._cycles_since_prune >= PRUNE_EVERY_CYCLES:
    self._cycles_since_prune = 0
    try:
        await self._prune_history()
    except Exception:
        logger.exception("prune_history_failed")
```

**The failure sequence.** A `trade_structure` armed off a PCA signal stays open past 90 days —
entirely ordinary for an options structure, and the system's own tenor ladder goes out to 6M.
The next daily prune tries to delete that signal, Postgres raises `IntegrityError`, the
session rolls back — **including the `VolSurface` delete that had already succeeded in the
same transaction** — and the caller logs and moves on. From that moment:

- `pca_signal_history` never prunes again while any such structure is open;
- `vol_surface_history` never prunes again *at all*, even though nothing references it —
  it is collateral damage from sharing a transaction;
- the engine's own docstring puts `vol_surface_history` at **≈1.1 MB/day**, so a year of
  silent failure is ~400 MB on a table the API queries with `ORDER BY timestamp DESC` on
  every markets-closed surface request (`vol_service.py:70-79`);
- the only symptom is one `prune_history_failed` line per day in a log stream that also
  carries every other engine's output.

This is the same shape as DI-04 (one bad row, whole batch lost) applied to maintenance: no
isolation between independent units of work sharing a transaction.

**Recommended fix.**
1. Split the two deletes into **separate sessions with separate commits**, so one cannot roll
   back the other. This alone converts a Blocker into a bounded Medium.
2. Decide the referential semantics deliberately: `ondelete="SET NULL"` on both
   `pca_signal_id` FKs (a preview/structure keeps working when its originating signal ages
   out — the signal is provenance, not a dependency), or exclude referenced signals from the
   prune with a `NOT EXISTS` sub-query.
3. Do not swallow the prune exception silently — emit a `system_alerts` message and a
   Prometheus counter, so "retention is broken" is visible before the disk is.
4. Fold into DI-21's single wall-clock maintenance task with chunked deletes.

---

## High

### DI-04 — one bad row discards the whole batch, with no dead-letter

`src/persistence/writer.py:261-290` groups a batch by table and executes every table's INSERT
in a **single session and transaction**, then commits. `_write_batch_with_retries`
(`:234-259`) retries only `OperationalError`/`InterfaceError` and re-raises everything else
immediately — correctly noting that a constraint violation will not succeed on retry. `run()`
(`:143-155`) then catches it:

```python
except Exception:
    logger.exception("writer loop error, batch dropped")
    await asyncio.sleep(1)
```

So a single `IntegrityError`, a single unconsumed column name, or a single bad type discards
**up to 100 rows spanning up to 8 tables**. There is no per-row retry, no split-and-bisect, no
dead-letter table or file, and no metric — the only trace is one log line whose message does
not identify which row or which table was at fault. Because engines re-emit on their own
cadence, a systematically bad payload becomes a permanent silent outage for *every* table that
shares a batch with it, and the only symptom is that history quietly stops growing.

**Fix:** on a non-transient failure, bisect the batch (or fall back to per-row inserts) so the
poison row is isolated; write the poison row plus the exception to a `db_writer_dead_letter`
table (or a bounded on-disk file); increment a Prometheus counter labelled by table so the
existing alerting sees it. Log the failing table and the offending key, never the whole
payload.

### DI-05 — retry after an in-doubt commit double-inserts

`writer.py:234-252` retries the identical batch on `OperationalError`. That exception class
covers a connection reset — including one that occurs *during* `session.commit()`, when the
server may already have committed. The retry re-executes every INSERT in the batch. For the
four timestamp-keyed idempotent tables the `ON CONFLICT` saves it (this is the one case where
DI-02's key actually works, since the payload is unchanged); for the three tables with **no**
unique key it silently duplicates. `writer.py:78-79` acknowledges the gap — *"Other tables
(position_metric_history, account_history) have no natural dedup key, duplicates there are
real data and must not be silenced"* — without noticing that the retry path manufactures
duplicates that are *not* real data.

**Fix:** attach a producer-generated idempotency key (UUID per published frame) to every
`db_events` payload, persist it in a unique column, and make the conflict target that key for
every table. That is the only construction that survives an in-doubt commit, and it also
fixes DI-02's redelivery case without depending on the clock.

### DI-06 — `db_events` has no delivery guarantee and nothing detects loss

`src/shared/db_events.py:18-26` is a bare `redis.publish`. Redis pub/sub does not persist,
does not ack, and does not replay. Any frame published while the db-writer is restarting,
reconnecting after a Redis drop (`service.py:160-172` backs off and re-subscribes, losing
everything in between), or simply not running, is gone. There is no outbox, no stream
(`XADD`/consumer group), no WAL.

The function's own docstring flags the detection mechanism and then nobody implements it:

> Returns the number of subscribers that received the message — useful in dev to detect "no
> db_writer running" silently. **The caller should log a warning when the return is 0** for
> more than a handful of cycles.

Every one of the seven call sites in `src/engines/vol/engine.py:740-777` and
`src/engines/analytics/engine.py:215` discards the return value. So the "no subscribers"
signal exists, is documented, and is thrown away at every call site.

**Fix (in ascending order of cost):** (a) check the return and publish a `system_alerts`
message plus a Prometheus counter when it is 0 — cheap, catches the whole "db-writer is down"
class; (b) move `db_events` from pub/sub to a Redis Stream with a consumer group, which gives
at-least-once delivery, replay after a restart, and a visible backlog — and which pairs
naturally with the idempotency key from DI-05; (c) a transactional outbox in Postgres if the
guarantee needs to be exactly-once.

### DI-07 — writer queue drops silently on overflow

`src/engines/db_writer/service.py:193-195`:

```python
try:
    self.writer.queue.put_nowait((table, payload))
except asyncio.QueueFull:
    logger.warning("writer_queue_full_event_dropped", extra={"table": table})
```

The queue is bounded at `QUEUE_MAX_SIZE = 10_000` (`writer.py:52`) — which is the right
choice, unbounded would be worse. But the overflow path drops the event with a `warning` and
no counter, so sustained backpressure (Postgres slow, a long prune holding locks per DI-21)
silently punches gaps in history that nothing will ever detect or backfill. The heartbeat
stays green throughout, because `_subscriber_ok` only tracks the subscription, not the queue.

**Fix:** a Prometheus gauge on queue depth and a counter on drops, wired to the existing
stale-heartbeat alerting; consider suppressing the heartbeat (as `service.py:124-130` already
does for a dead subscriber) once the drop counter moves, so "db-writer is losing data" is
visible in the same place as "db-writer is down".

### DI-08 — duplicate hourly PCA snapshots poison the factor model

Both paths are enabled by default. `src/engines/vol/engine.py:63`:

```python
_INLINE_ANALYTICS: bool = os.environ.get("VOL_INLINE_ANALYTICS", "1") != "0"
```

and `docker-compose.yml:212` → `VOL_INLINE_ANALYTICS: ${VOL_INLINE_ANALYTICS:-1}`. The
`analytics-engine` service (`docker-compose.yml:324-337`) sits on the same `engines` profile
as `vol-engine`, so `docker compose --profile engines up -d` starts both with the inline path
on.

Both write the same table via `db_events`, with independently computed timestamps:

| | vol-engine inline | analytics-engine |
|---|---|---|
| builder | `engine.py:_maybe_collect_hourly_snapshot` | `analytics/engine.py:_collect_snapshot` |
| timestamp | `datetime.now(UTC).isoformat()` | `datetime.now(UTC).isoformat()` |
| `source` | `"live_engine"` | `"analytics_engine"` |
| gate default | `PCA_SNAPSHOT_INTERVAL_MIN` = **`"55"`** | `PCA_SNAPSHOT_INTERVAL_MIN` = **`"60"`** |
| gate | `SELECT MAX(timestamp) … ` then compare | identical query |

Two independent `now()` values, so `uq_surface_snap_hourly_symbol_ts` **cannot** dedup them
(DI-02). Worse, the gate is a read-modify-write across an asynchronous write path: both
processes read `MAX(timestamp)` from Postgres, but their writes traverse Redis pub/sub and the
db-writer's batching (`BATCH_TIMEOUT_S = 5.0`, up to 100 rows). Any two gate evaluations
inside that window both observe the same stale `last` and both publish. The mismatched
defaults (55 vs 60) mean the two paths are not even trying to agree — the same environment
variable name means two different things in two processes.

**Persistence consequence.** `pca_surface_snapshot_history` is the training set. Duplicated,
near-identical samples understate the variance of the PCA fit and of the PC3 rolling arrays,
which inflates every downstream z-score and makes the `CHEAP`/`FAIR`/`EXPENSIVE` thresholds
fire early. And `_recompute_pc3_history` (`analytics/engine.py:284-289`) takes
`LIMIT PC3_SNAPSHOT_LIMIT` most-recent rows — with duplicates, that window covers roughly half
the intended calendar span.

**Fix:** complete the cutover (default `VOL_INLINE_ANALYTICS` to `0`, or make the two
mutually exclusive at compose level), *and* quantise the snapshot timestamp to the hour so the
unique constraint becomes a real guard regardless of who writes. Unify the interval constant
in one place.

### DI-09 — sibling rows of one snapshot have divergent dedup semantics

`src/engines/vol/engine.py:757-765` emits `regime_snapshot_history` and `feature_history` from
the same cycle with the same `ts_iso`. `feature_history` has
`UniqueConstraint("symbol", "timestamp")` (`models.py:274`) and is in `IDEMPOTENT_TABLES`;
`regime_snapshot_history` has only a `CheckConstraint` on `label` (`models.py:218-223`) and is
in neither. So on any redelivery the regime row duplicates while its feature row does not, and
the two tables — which the analytics engine joins on `(symbol, timestamp)` to build the GMM
training matrix (`analytics/engine.py:236-247`) — drift out of 1:1 correspondence. A regime
snapshot with no matching feature row (or two regime rows for one feature row) is a silent
correctness hazard for anything reasoning about the regime time series.

**Fix:** add `UniqueConstraint("symbol", "timestamp", name="uq_regime_snapshot_symbol_ts")`
and the corresponding `IDEMPOTENT_TABLES` entry — after DI-02's key fix, so the constraint is
meaningful rather than decorative.

### DI-10 — two uncoordinated writers on `open_position`

- **risk-engine** (`src/engines/risk/engine.py:390-399`) mutates the greeks/mark columns in
  place on rows it loaded, every 2 s, then inserts a matching `OpenPositionHistory` row and
  commits (`:428-430`).
- **execution-engine** (`src/engines/execution/position_sync.py:167-215`) mutates the
  identity/quantity columns (`quantity`, `side`, `expiry`, `tenor`, `nominal_eur`,
  `contract_price_entry`, `product_label`, `contract_id`, `trade_id`, `package_id`), inserts
  new rows, and **deletes** rows.

There is no version/`updated_at`-compare column, no `SELECT … FOR UPDATE`, and
`get_sessionmaker()` sets `expire_on_commit=False` (`src/persistence/db.py:48`) so each
process works from its own stale identity map. The column sets are mostly disjoint, so routine
clobbering is limited — but two failure modes are real:

1. `open_position.timestamp` has `onupdate=func.now()` (`models.py:99-104`). Both writers bump
   it, so it means "last touched by either engine", not "last repriced". Any consumer using it
   as a freshness signal for the greeks is wrong.
2. If `position_sync` deletes a row between the risk-engine's `SELECT` and its
   `db.add(OpenPositionHistory(position_id=live_pos.id, …))`, the insert violates the FK and
   the **entire commit fails** — losing that cycle's history for *every* position, not just
   the deleted one, since `engine.py:430` commits them all together.

**Fix:** declare a single owner per column group and route the other engine's updates through
it; or add an optimistic-lock version column; or at minimum wrap the risk-engine's history
insert so an FK violation on one position skips that position instead of aborting the batch.
DI-03's soft-close change also removes failure mode 2.

### DI-11 — money as `Float` in every execution-side table

Eight tables store USD amounts, prices and greeks as `Float` while the risk-side tables store
the same concepts as `Numeric`: `TradeStructure` (`total_premium_paid_usd`,
`total_slippage_usd`, `total_commission_usd`, `total_entry_cost_usd`), `StructureOrder`
(`limit_price`, `avg_fill_price`, `contract_strike`, `total_commission_usd`,
`total_slippage_usd`), `StructureFill` (`fill_price`, `commission_usd`, `spot_at_fill`),
`BookedPosition` (`gross_pnl_usd`, `net_pnl_usd`, `entry_premium_usd`, `entry_spot`),
`BookedPositionMetricHistory` (all PnL and greek columns — the highest-volume PnL table in the
schema), `HedgeOrder`, `IbConnectionState` (`available_funds_usd`, `buying_power_usd`,
`margin_used_usd`), `BookStateSnapshot`. Meanwhile `OpenPosition`, `OpenPositionHistory`,
`AccountHistory`, `LegPosition` and `ReconciliationBreak` use `Numeric(15,2)` / `Numeric(15,8)`
for exactly the same quantities.

The split is undocumented — no docstring or comment in `models.py` explains it — and it tracks
build order (Step 3/4/5 execution tables on `Float`, later risk/reconciliation tables on
`Numeric`) rather than any deliberate criterion. The concrete hazard is the reconciliation
seam: `StructureFill.commission_usd` (Float) is summed in Python and folded into
`BookedPosition.net_pnl_usd` (Float) via `round(x, 2)` — a cosmetic round into a column that
cannot hold exact cents — while `LegPosition.avg_price` (Numeric(15,8)), derived from the same
fills, and `ReconciliationBreak.diff` (Numeric(15,4)) are exact. Drift between the ledger and
its own reconciliation report is guaranteed at some magnitude.

No column was found where a `Numeric` scale is too coarse to represent its value (the
truncation class of bug is absent — all FX-rate columns are scale 6-8). This is purely the
accumulation class.

**Fix:** migrate the money columns to `Numeric(15,2)` and the price columns to `Numeric(15,8)`
to match the risk-side convention, and record the convention in a `models.py` module docstring
so the next table follows it. Column-type migrations here are mechanical but touch the
db-writer payloads and the analytics endpoints — see the alembic note in Appendix B.

### DI-12 — `event_hash` dedup is bypassable and reschedules duplicate

Migration `049_sync_drift.py:31-33` explicitly removed the guard that migration
`012_events_hash_unique.py:73` had installed:

```python
# 1. ORM says nullable — relax the live NOT NULL.
op.execute("ALTER TABLE event_calendar ALTER COLUMN event_hash DROP NOT NULL;")
```

In Postgres, NULLs are distinct in a unique index, so `UniqueConstraint("event_hash")`
(`models.py:302`) no longer constrains anything for NULL-hash rows. And there is a live path
that writes NULL — `src/api/routers/regime.py:371-381` constructs `Event(...)` without
`event_hash`. Repeated calls insert unlimited duplicates of the same event, and a manual event
is never deduped against the pipeline's version of the same event.

Separately, `src/api/orchestration/events/hashing.py:14-18` builds the identity from
`event_type|region|scheduled_at(minute)`. The pipeline uses
`on_conflict_do_nothing(index_elements=["event_hash"])`. So when a source revises an event's
time by a minute or more — routine for central-bank and data releases — the hash changes,
nothing conflicts, and a **second row is inserted** while the stale one is never updated or
removed. Both feed `event_dampener` and the regime pre-event logic, so the event's influence
window is double-counted.

**Fix:** restore `NOT NULL` after backfilling legacy rows; compute the hash inside the `Event`
constructor (or a `before_insert` hook) so no path can skip it; add a stable
`source_event_key` (source + the source's own identifier) that survives a reschedule and
switch to `ON CONFLICT (source_event_key) DO UPDATE SET scheduled_at = EXCLUDED.scheduled_at`
so a revision updates rather than duplicates.

### DI-13 — config hot-reload has no convergence path

The only subscriber to `CH_CONFIG_CHANGED` is `src/engines/vol/main.py:33-76`. It:
- subscribes once at startup and never re-reads the DB on reconnect;
- applies `payload["config"]` from the message rather than re-reading the current row;
- never inspects the `version` field the publisher includes, so it cannot detect being behind;
- has no periodic poll.

Redis pub/sub has no replay, so a message published while the container is restarting — which
happens routinely, e.g. around the nightly IB Gateway bounce — is lost permanently. And the
restart does not recover it: `main.py:146-149` documents that boot config comes from **env
vars**, not the DB, so *"first-boot-after-PUT sees stale defaults until the next admin PUT"*.
The only convergence is a human doing another PUT.

Compounding this, `src/engines/vol/engine.py:507-518` — the apply hook — is a no-op:

```python
del config  # currently unused — kept for forward compatibility
logger.info("vol_engine_config_reloaded")
```

So the operator gets a green "config reloaded" log line for a change that was never applied,
and a `config_vol_engine` version history recording intent that never took effect. Every panel
of the admin Settings page (regime, signal, sizing, exit_rules, surface, calibration,
delta_hedge, structures) silently no-ops for the vol-engine. The genuinely hot-reloaded
config lives in *different* tables (`config_exit_rules`, `config_scalar`) that are polled
per-cycle by the API — a second, parallel config system with no relationship to the first.

**Fix:** the subscriber should treat the message purely as an *invalidation signal* and
re-read the latest `config_vol_engine` row from Postgres; boot should read the DB (env vars as
fallback only); and each cycle should compare its applied version against the DB's max version
on a slow interval so a missed message converges within one interval. Then either wire
`apply_config` to something real or remove the machinery and the Settings panels it backs —
shipping a config UI that does nothing is worse than not shipping it.

### DI-14 — no staleness contract on any vol read path

`src/api/schemas/vol.py` defines `SurfaceResponse`, `TermStructureResponse` and
`SmileResponse` with exactly one temporal field: `timestamp`. There is no `source`, no
`stale`, no `age_s`. A grep for `stale|as_of|age_s|source` across `src/api/schemas/` returns a
single hit — `analytics.py:34: heartbeat_age_s`.

That matters because `get_latest_surface` (`vol_service.py:38-86`) serves from two sources
with wildly different freshness under one response shape:

- Redis hit → `timestamp` is the last vol-engine cycle, ≤ 600 s old (TTL-bounded).
- Redis miss → the DB fallback runs `jsonb_exists_all` for the most recent row carrying **all
  six display pillars**. Over a weekend, or after any extended market closure, that row can be
  days old. The response is byte-shaped identically to the live one.

The consumer has no way to distinguish them other than parsing `timestamp` and knowing the
policy. `trade.py:255-277` does compute an age from the same key and threads it through — so
the pattern exists in the codebase; it just is not on the vol endpoints. This directly fails
the audit's own exit criterion ("staleness visible on every read path").

**Fix:** add `source: Literal["live","last_known"]` and `age_s: float` to every vol response
(and to `/positions`, which is fed by DI-15's untimestamped hashes), populate them in
`get_latest_surface`/`get_term_structure`, and surface them in the OpenAPI schema so the
frontend can render the distinction rather than infer it.

### DI-15 — the mark-price hashes have no timestamp and never shed stale fields

`src/engines/execution/position_sync.py:282-290`:

```python
if contract_marks:
    await redis.hset("contract_marks:EUR", mapping=contract_marks)
    await redis.expire("contract_marks:EUR", 600)
```

(same for `option_marks:EUR` and `unrealized_pnl:EUR`). Three problems:

1. **No timestamp anywhere.** The values are bare stringified floats keyed by position id.
   Consumers — `src/engines/risk/engine.py:453-467` (which uses them as the *mark* for greeks
   and PnL) and `src/api/routers/positions.py:107-118` — cannot tell whether a mark is from
   this second or from nine minutes ago.
2. **`hset` only adds and overwrites; it never removes.** When a position stops appearing in
   `ib.portfolio()`, its field remains in the hash indefinitely, still readable, still served
   as the current mark.
3. **The TTL is reset wholesale on every publish**, so individual stale fields never expire —
   only a full 600 s publisher outage clears anything, and then it clears everything at once.

None of these keys is in `bus/keys.py`, so they have no declared TTL policy, no documented
writer, and no schema. The same is true of `cycle_progress:vol_engine`
(`vol/engine.py:612-613`) and `heartbeat:execution` (`position_sync.py:565`, written as a
literal because there is no `ENGINE_EXECUTION` constant).

Note also that the key names embed `EUR` while `risk/engine.py:303` documents them as
`option_marks:<symbol>` — the "symbol" is hardcoded at both producer and consumer, a latent
break the moment a second currency pair is added.

**Fix:** replace the bare-float hash values with `{"value": …, "ts": …}` (or add a parallel
`…:ts` hash), `DEL` the key before each full republish so closed positions drop out, register
all five keys in `bus/keys.py` with explicit TTL constants, and add `ENGINE_EXECUTION`.

### DI-36 — concurrent fill callbacks lose updates on the order aggregates

`src/engines/execution/fills_handler.py:74-84` wires the IB callbacks so that **every event
spawns an independent, unserialised task**:

```python
trade.statusEvent += lambda t: asyncio.create_task(_on_order_status(...))
trade.fillEvent  += lambda t, fill: asyncio.create_task(_on_execution(...))
```

Each task opens its own `AsyncSession` (`_persist_fill`, `:174`). A fast partial-fill sequence
— routine on a multi-leg structure — therefore lets two `_persist_fill` coroutines interleave:
both read `all_fills` (`:210-212`), both compute `update_order_aggregates` (`:213-218`) from a
fill set that is missing the other's row, and whichever commits second (`:241`) **overwrites**
`trade_order.qty_filled` and `avg_fill_price` with the stale computation. There is no
`SELECT … FOR UPDATE`, no per-order lock, and no unique guard on the aggregate.

The authors were aware of *a* race here — `:131-132` comments *"Filled is handled in
`_on_execution` after fill aggregates; we don't transition here to avoid races"* — but the
guard addresses the state transition, not the aggregate recomputation.

The persistence consequence is a fills journal (`trade_fill`) that is correct row-by-row while
its own derived aggregate on `trade_order` is wrong, which then propagates into
`booked_position.entry_premium_usd` and every PnL derived from it, and shows up later as a
`reconciliation_break` with no explicable cause.

**Fix:** serialise per order — an `asyncio.Lock` keyed on `order_id`, or recompute the
aggregate with `SELECT … FOR UPDATE` on the `trade_order` row inside the same transaction as
the fill insert. Recomputing from the fills table under a row lock is the simplest correct
version. (Overlaps Audit 4 — recorded here for the persistence impact.)

---

## Medium

**DI-16 — `_coerce_datetime_fields` whitelist is incomplete.**
`src/engines/db_writer/service.py:198` lists `("timestamp", "opened_at", "closed_at", "ts")`.
`open_position.entry_timestamp` is `DateTime(timezone=True), nullable=False` and is not on the
list; neither is any `Date` column (`open_position.expiry`, `open_position_history.expiry`).
Both tables are still in `TABLE_MODELS`, so the moment anything routes them through
`db_events`, asyncpg raises *"expected datetime.datetime instance"*, the batch is non-retryable
(DI-04), and because the producer re-emits every cycle it becomes a **permanent** poison that
also kills every co-batched table. Today it is latent only because those tables have no
`db_events` producer (DI-30). Fix: derive the coercion from the ORM column types rather than a
hand-maintained name list.

**DI-17 — `default=str` is a silent type-corruption channel.**
`src/shared/db_events.py:25` serialises with `json.dumps(..., default=str)`. Any value
`json` cannot handle natively — `Decimal`, `numpy.float64`, `numpy.bool_`, `date` — becomes a
**string**, which then either fails at insert (poisoning the batch per DI-04) or, worse,
silently coerces. The current producers happen to be clean (`float()`/`round()` everywhere in
`regime_engine`, `feature_enrichment_stamp` and the analytics snapshot builder), so this is a
latent hazard rather than an active bug — but nothing enforces it, at either end. Fix: replace
`default=str` with an explicit encoder that handles `Decimal`→`float`, numpy scalars→`.item()`,
`date`/`datetime`→ISO, and raises on anything else, so a new producer fails loudly in dev.

**DI-18 — nine orphaned keys and channels.**
Written-and-never-read, or declared-and-never-written (see Appendix A for the full table):
`account_snapshot` + `CH_ACCOUNT` — `publish_account` (`publisher.py:110-120`) has **zero call
sites**, yet the WS bridge subscribes to `account` at `redis_bridge.py:22`;
`CH_SYSTEM_ALERTS` — subscribed at `redis_bridge.py:25`, no producer anywhere;
`market_status:{symbol}` + `TTL_MARKET_STATUS` — zero references outside `keys.py`;
`latest_bid`/`latest_ask`, `latest_signals`, `latest_greeks:portfolio`, `latest_pnl_curve` —
written by `publisher.py` on every cycle, read by nothing except the `/dev` raw-key debug
dump. Each represents either a dead code path to delete or a missing consumer.

**DI-19 — key literals drift outside `bus/keys.py`.**
`f"latest_vol_surface:{self.symbol}"` at `position_monitor.py:608` and
`f"latest_vol_surface:{symbol}"` at `trade.py:262` (both bypassing `keys.LATEST_VOL_SURFACE`);
the twelve hardcoded key strings in `dev.py:58-66,121-125`; `heartbeat:execution` at
`position_sync.py:565`; the three mark hashes and `cycle_progress:vol_engine` (DI-15). Also,
`DB_EVENTS_CHANNEL` — the single most important channel in the system — is defined in
`src/shared/db_events.py:15`, not in `bus/channels.py`, so the module that is supposed to be
the single source of channel names does not know it exists. Fix: move it, replace the
literals, and add a test that greps `src/` for `latest_|heartbeat:|analytics:` outside
`bus/keys.py`.

**DI-20 — `_fair_q` can be served from an older vintage than the freshest persisted one.**
Commit 0f4be379 made `/vol/term-structure` read fair-vol from the persisted `_fair_q` so the
panel survives market close. Commit f301a560 then changed `get_latest_surface`'s DB fallback
to select the last row containing **all six display pillars** (`vol_service.py:70-75`), to
avoid serving a lone-6M sparse row. But `get_term_structure` sources both the IV grid *and*
`_fair_q` from that one row (`:144-146`). Model-only rows — which carry a fresh `_fair_q` but
no top-level pillars — are therefore skipped, and the term structure serves whatever `_fair_q`
was attached to the last complete grid. The two rows are internally consistent (good), but the
response's single `timestamp` cannot express that the fair-vol curve may be older than the
freshest fair-vol the engine actually computed. Fix: fetch `_fair_q` from the latest row that
has one, independently of the grid row, and return both vintages (ties into DI-14).

**DI-21 — retention is partial, cycle-counted, cross-service, and unbounded per statement.**
Two pruners exist: `risk/engine.py:221-239` (deletes `open_position_history` and
`account_history` older than 90 d) and `vol/engine.py:639-662` (deletes `vol_surface_history`
and `pca_signal_history`). Issues: (a) **no pruner at all** for
`regime_snapshot_history`, `feature_history`, `pca_surface_snapshot_history`,
`booked_position_metric_history`, `trade_event` or `event_calendar` — all append-only and
growing; (b) the schedule is a **cycle counter**
(`PRUNE_EVERY_CYCLES = 43_200`, `risk/engine.py:73`) that assumes a never-skipping 2 s loop, so
retention drifts every time the engine idles through a market closure; (c) the risk-engine
prunes `account_history`, which the **execution**-engine owns — cross-service ownership with
no coordination; (d) each prune is a single unbounded `DELETE`, and at 2 s × 90 d ×
n_positions the first one past the boundary is a multi-million-row statement holding locks on
a table the same engine writes every 2 s; (e) the vol-engine's pruner is, per **DI-35**,
probably not running at all. Fix: one scheduled maintenance task (wall-clock), covering every
append-only table, deleting in bounded chunks with a `LIMIT`/`ctid` loop, each table in its
own transaction.

**DI-22 — naive `datetime.utcnow()` compared against a `timestamptz` column.**
`src/api/routers/regime.py:201`: `since = datetime.utcnow() - timedelta(days=past_days)`, then
`.where(Event.scheduled_at > since)` where `scheduled_at` is `DateTime(timezone=True)`
(`models.py:310`). The sibling `_fetch_next_events` in the same file (`:138-156`) correctly
uses `datetime.now(UTC)` for the identical comparison. On a non-UTC host the window shifts by
whole hours, silently including or excluding events near the boundary. (The rest of the
codebase is clean on this: all time-to-expiry math is `date`-to-`date`, and the defensive
`ts if ts.tzinfo else ts.replace(tzinfo=UTC)` pattern is applied consistently in
`publisher.py`, `bars_fetcher.py` and `portfolio_panel.py`.)

**DI-23 — a CHECK constraint declared in the ORM does not exist in the database.**
`models.py:888-891` declares `ck_signal_track_status` on
`booked_position_metric_history.signal_status`. That constraint was created by migration
`016_step5_position_monitoring.py:73` on the *old* `position_signal_tracking` table;
migration `026_theme2_portfolio.py:52-74` folded that table into
`position_mtm_history`, backfilled the column as nullable, and **dropped the source table
along with its constraint** — never recreating an equivalent. So the ORM documents a guard the
live schema does not have. Fix: add the constraint in a migration (after checking for existing
violations), or delete the ORM declaration. Verify with the `\d+` command in Appendix B.

**DI-24 — migration-chain hygiene.**
The 56 revision files form a single linear chain with exactly one head
(`050_submit_claims`) — no branch points, no orphans, no revision id over the 32-char
`alembic_version` cap. So `alembic upgrade head` is safe. But: (a) **filename order is not
apply order** — four duplicate numeric prefixes (`023`/`024`/`025`/`026`) thread through the
chain out of `ls` order (the `theme*` files actually run *after* `031_iv_vanna_volga`), which
will mislead anyone reading history by directory listing; (b)
`254fc54bb36f_add_col_x.py` is an `--autogenerate` artifact spliced between `002` and `003`
that drops the 16 indexes `002` just created, which `003` then recreates — inert, documented,
but it is exactly the kind of file that gets "cleaned up" and breaks `003`'s `down_revision`;
(c) `028_positions_panel_exact.py:123-128` **raises** on downgrade, so any runbook claiming
downgrade-ability across the whole history is wrong; (d) `022_purge_market_closed_rows.py`
cascades `DELETE` across five tables with `downgrade() = pass`, and `026`/`049` downgrades
*fabricate* replacement data (`md5('ec-' || id)` synthetic event hashes at `049:46-49`) rather
than restoring it — acceptable and commented, but they must not be described as reversible.

**DI-25 — config version race surfaces as a 500.**
`config_service.py:107-109` (and `:153-155`) computes `next_version = MAX(version) + 1` and
INSERTs, with no `FOR UPDATE`, advisory lock, or serializable isolation. Because `version` is
the table's primary key (`models.py:342`), a genuine race produces an `IntegrityError` rather
than a silent duplicate — good. But nothing catches it (`admin.py:59-78` handles only
`ValidationError`), so a concurrent PUT returns an unhandled 500. Availability, not integrity.
Fix: catch and retry once.

**DI-26 — inconsistent staleness metadata on the analytics keys.**
`analytics:pc3_history:{symbol}` carries `fit_timestamp` and `latest_snapshot_ts` inside the
payload (`analytics/engine.py:311-316`) — exactly right. Its sibling
`analytics:regime_model:{symbol}` (`:268-271`) is `json.dumps(serialize_gmm(gmm, fit))` with
**no timestamp at all**, so the vol-engine cannot tell whether it is inferring against a model
fitted an hour ago or two hours ago (TTL is 2 h). Separately, `publisher.py:183` documents
*"SET heartbeat:{engine_name} with a 30s TTL"* while the constant is
`TTL_HEARTBEAT = 300` (`keys.py:72`) — the docstring predates a deliberate bump and now
misleads anyone reasoning about liveness detection.

**DI-27 — `quantity` as `Numeric(15,4)`.** `models.py:81,142`. Contract counts are integral in
every path that writes them (`position_sync.py:140` takes `abs(Decimal(str(...)))` of an IB
integer position). A 4-decimal numeric invites a fractional value that no downstream consumer
handles. Fix: `Integer`/`BigInteger`, or a CHECK asserting integrality.

**DI-28 — interpolated pillars persist `strike: None`.**
`core/vol/tenors.py:231` emits `{d: {"iv": iv, "strike": None, "source": source}}` for interp
pillars, and that dict is what gets persisted into `vol_surface_history.surface_data`. The
smile reader (`vol_service.py:327-337`) skips any node with a null strike, so **historical
smiles cannot be reconstructed for interpolated tenors** — the strike is gone for good, not
just unavailable live. Fix: persist the implied strike from the forward and the interpolated
IV even when there is no listed contract, flagged `source="interp"` (the flag already exists,
so no consumer needs to change to stay honest).

**DI-29 — naive local time drives contract selection.**
`src/engines/vol/chain_fetcher.py:139` `now = datetime.now()` (naive, local) minus
`exp_date` (naive, from `strptime`) at `:151` → `dte = (exp_date - now).days`. Both naive so
no `TypeError`, but on a non-UTC host the DTE that decides which expiry to fetch is off by a
day near the boundary. Fix: `datetime.now(UTC)`.

**DI-37 — `book_state_snapshot_history` grows forever by design.**
`src/api/orchestration/book_state_refresh.py:71-92` flips the previous row's `is_current` to
`False` and inserts a new one on every refresh. Nothing ever deletes. Unlike the other
unpruned tables in DI-21, this one is not merely missing a pruner — its write pattern is an
explicit supersede-never-delete, so it will accumulate one row per refresh indefinitely. Fix:
include it in the DI-21 maintenance task, or keep only the current row plus a daily sample.

**DI-38 — `hedge_order` is handed between containers with no lock.**
The api container creates the row (`position_monitor.py:391-399`, `state="pending"`, then
`_pending_hedge_ids.append(hedge.id)` for out-of-band submission); the execution container
transitions it `pending → submitted → filled/failed` (`hedge_executor.py:59-162`) by mutating
fields on a row it loaded. Neither side takes `SELECT … FOR UPDATE`. A retried submit racing
the finaliser can move the row backwards through its state machine, and
`hedge_order.total_cost_usd` is summed into `booked_position.net_pnl_usd`
(`position_close_finaliser.py:64-81`), so a double-counted or dropped hedge lands directly in
realised PnL. This is a state machine rather than a naive dual-writer — hence Medium, not
High — but it has no guard. Fix: transition with a conditional
`UPDATE … WHERE id = :id AND state = :expected_state` and assert `rowcount == 1`.

---

## Low

**DI-30 — dead dispatcher entries.** `TABLE_MODELS` (`writer.py:58-67`) lists eight tables, but
`account_history`, `open_position` and `open_position_history` have **zero** `publish_db_event`
producers — they are written directly by `position_sync` and the risk-engine. Three of the
five `IDEMPOTENT_TABLES` comments therefore describe a routing that no longer exists. Prune
them (this is also what makes DI-16 latent rather than active).

**DI-31 — `config_vol_engine` append-only is convention-only.** No trigger, rule or permission
prevents `UPDATE`/`DELETE`; migration `004_add_vol_config_table.py:21-53` creates a plain
table. No code path currently violates it (`revert` correctly INSERTs a new version —
`config_service.py:135-169`), but a `psql` session or a future bug can silently rewrite an
audit trail whose entire purpose is to be immutable. Fix: `REVOKE UPDATE, DELETE` from the
application role, or a `BEFORE UPDATE OR DELETE` trigger that raises.

**DI-32 — three WebSocket endpoints have no consumer.** `/ws/positions`, `/ws/exit_alerts` and
`/ws/orders/{structure_id}` are wired end-to-end (publisher → bridge → endpoint) but the
frontend connects only to `/ws/ticks`, `/ws/vol` and `/ws/risk`
(`frontend/src/hooks/streams.ts:30-41`). Notably `exit_alerts` is published best-effort with
failures swallowed (`position_monitor.py:433-435`) — acceptable *because* the alert is already
persisted to `exit_alert` first, which is the right ordering; worth recording that the
guarantee rests entirely on the DB row.

**DI-33 — tenor-ladder docs and constants drift.** `vol_service.py:55-56` says the display
pillars are `(1M,2M,3M,6M,9M,1Y)`; `DISPLAY_PILLARS` is `("1M","2M","3M","4M","5M","6M")`
(`tenors.py:25`). `vol_service.py:256-259` adds `_TENOR_YEARS` — a fifth copy of the ladder
(A6-02 counted four), including `1W` and `1Y` entries that are not display pillars.

**DI-34 — 64-bit truncated event hash.** `hashing.py:18` takes `sha256(...).hexdigest()[:16]`.
Birthday-bound 50% collision at ~2³² distinct keys; an economic calendar produces thousands
per year. Not a practical risk — recorded so it is not re-flagged.

---

## Appendix A — Redis producer/consumer contract table

Contract verdicts: **OK** = producer and consumer agree on every field read;
**MISMATCH** = a consumer reads a field the producer does not write (or a different shape);
**ORPHAN-W** = written, never read; **ORPHAN-R** = read/subscribed, never written;
**ORPHAN** = neither.

### A.1 — Last-value keys

| Key | Declared in `keys.py`? | Producer | Consumer(s) | TTL | Payload | Timestamp in payload? | Verdict |
|---|---|---|---|---|---|---|---|
| `latest_spot:{symbol}` | yes | `publisher.publish_tick` | `vol/engine.py:786`, `risk/engine.py:497`, `analytics/engine.py:327` | 30 s | bare float string `str(mid)` | **no** (TTL-bounded only) | OK |
| `latest_bid:{symbol}` | yes | `publisher.publish_tick` | — | 30 s | bare float string | no | **ORPHAN-W** |
| `latest_ask:{symbol}` | yes | `publisher.publish_tick` | — | 30 s | bare float string | no | **ORPHAN-W** |
| `latest_vol_surface:{symbol}` | yes | `publisher.publish_vol_update` | `vol_service.py:49`, `risk/engine.py:514`, `analytics/engine.py:168`, `position_monitor.py:608` (literal), `trade.py:262` (literal) | 600 s | `{symbol, timestamp, surface}` | yes | **MISMATCH** — `position_monitor` reads `payload["spot"]`, never written (DI-01) |
| `latest_signals:{symbol}` | yes | `publisher.publish_vol_update` | — (only `/dev` raw dump) | 600 s | `{symbol, timestamp, signals}` | yes | **ORPHAN-W** |
| `latest_greeks:portfolio` | yes | `publisher.publish_risk_update` | — (only `/dev` raw dump) | 30 s | `{timestamp, greeks}` | yes | **ORPHAN-W** |
| `latest_pnl_curve` | yes | `publisher.publish_risk_update` | — (only `/dev` raw dump) | 30 s | `{timestamp, curve}` | yes | **ORPHAN-W** |
| `account_snapshot` | yes | `publish_account` — **zero call sites** | — | 60 s | `{…, timestamp}` | yes | **ORPHAN** |
| `market_status:{symbol}` | yes | — | — | 300 s | n/a | n/a | **ORPHAN** |
| `bars:{symbol}:{timeframe}` | yes | `market_data/main.py:86` | `analytics.py:59`, `portfolio_panel.py:2056` | 6 h | JSON list `{t,o,h,l,c}` | per-bar `t` only | OK |
| `heartbeat:{engine}` | yes | `publisher.set_heartbeat` (all engines) | `health.py:91`, `analytics_service.py:64`, compose healthchecks | 300 s | ISO-8601 UTC string | the value *is* the ts | OK (docstring says 30 s — DI-26) |
| `heartbeat:execution` | **no** (literal) | `position_sync.py:565` | `dev.py:125` (literal) | 300 s | ISO-8601 UTC string | yes | OK — but undeclared (DI-19) |
| `analytics:regime_model:{symbol}` | yes | `analytics/engine.py:268` | `vol/engine.py:1256` (only when `VOL_INLINE_ANALYTICS=0` — off by default) | 2 h | serialised GMM | **no** | OK-but-untimestamped (DI-26); unread in the default config |
| `analytics:pc3_history:{symbol}` | yes | `analytics/engine.py:307` | `vol/engine.py:1285` (same gating) | 2 h | `{fit_timestamp, latest_snapshot_ts, skew[], convex[]}` | yes | OK; unread in the default config |
| `contract_marks:EUR` (hash) | **no** | `position_sync.py:283` | `risk/engine.py:456`, `positions.py:115` | 600 s (reset wholesale) | `{position_id: float-string}` | **no** | **Undeclared + no staleness + never pruned** (DI-15) |
| `option_marks:EUR` (hash) | **no** | `position_sync.py:286` | `risk/engine.py:454` | 600 s | `{position_id: float-string}` | **no** | same (DI-15) |
| `unrealized_pnl:EUR` (hash) | **no** | `position_sync.py:289` | `risk/engine.py:460` | 600 s | `{position_id: float-string}` | **no** | same (DI-15) |
| `cycle_progress:vol_engine` (hash) | **no** | `vol/engine.py:612` | `dev.py:984` | 600 s | progress map | no | Undeclared (DI-19) |

### A.2 — Pub/sub channels

| Channel | Declared in `channels.py`? | Producer | Consumer(s) | Payload contract | Verdict |
|---|---|---|---|---|---|
| `ticks` | yes | `publisher.publish_tick` (throttled 200 ms) | `redis_bridge` → `/ws/ticks` → `streams.ts:30` | `{symbol, bid, ask, mid, timestamp}` | **OK** — the frontend's `TickMsg` matches field-for-field |
| `account` | yes | **none** (`publish_account` uncalled) | `redis_bridge.py:22` | `{…, timestamp}` | **ORPHAN-R** |
| `vol_update` | yes | `publisher.publish_vol_update` | `redis_bridge` → `/ws/vol` → `streams.ts:37` | `{symbol, timestamp, surface}` (**raw**, not `to_display_surface`-mapped like the REST path) | OK **by accident** — the frontend types it `unknown` and uses it only as a REST-invalidation ping (`streams.ts:33-36`). A future consumer that reads the WS payload as a surface will get a different shape than `/vol/surface` returns |
| `risk_update` | yes | `publisher.publish_risk_update` | `redis_bridge` → `/ws/risk` → `streams.ts:40` | `{timestamp, greeks}` | OK (same `unknown`-typed ping caveat) |
| `system_alerts` | yes | **none** | `redis_bridge.py:25` | n/a | **ORPHAN-R** |
| `config:changed` | yes | `config_service.py:121,168` | `vol/main.py:45` only | `{version, config}` | **MISMATCH (semantic)** — consumer never reads `version`, and applies the payload instead of re-reading the DB (DI-13) |
| `orders:*` | yes | `fills_handler.py:529` | `redis_bridge` (pattern) → `/ws/orders/{id}` | `{structure_id, timestamp, event_type, order_id, state, …}` loose-schema | OK; no frontend consumer (DI-32) |
| `positions` | yes | `position_monitor.py:428` (runs in the **api** container) | `redis_bridge` → `/ws/positions` | `{position_id, structure_id, spot, iv_avg_pct, current_pnl_*, current_*_greeks, …}` | **MISMATCH (value)** — `spot` is the entry spot or `1.085`, not the market (DI-01). No frontend consumer |
| `exit_alerts` | yes | `position_monitor.py:433` | `redis_bridge` → `/ws/exit_alerts` | `{…, timestamp}` | OK — persisted to `exit_alert` before publish, so loss is tolerable. No frontend consumer |
| `db_events` | **no** — lives in `shared/db_events.py:15` | `vol/engine.py:740-777` (5 tables), `analytics/engine.py:215` (1 table) | `db_writer/service.py:149` | `{"table": str, "payload": dict}` | **MISMATCH (structural)** — no schema at either end; `default=str` can stringify numerics (DI-17); no delivery guarantee (DI-06); dedup key cannot collide (DI-02) |

### A.3 — `db_events` table routing

| `table` value | Producer | In `TABLE_MODELS`? | In `IDEMPOTENT_TABLES`? | Unique constraint in `models.py` | Effective dedup |
|---|---|---|---|---|---|
| `vol_surface_history` | `vol/engine.py:742` | yes | yes `[timestamp, underlying]` | `uq_vol_surfaces_ts_underlying` | **none in practice** (DI-02) |
| `regime_snapshot_history` | `vol/engine.py:759` | yes | **no** | none | none (DI-09) |
| `feature_history` | `vol/engine.py:763` | yes | yes `[symbol, timestamp]` | `uq_feature_history_symbol_ts` | **none in practice** |
| `pca_signal_history` | `vol/engine.py:768` | yes | yes `[symbol, timestamp, pca_model_id, pc_id]` | `uq_pca_signals_symbol_ts_model_pc` | **none in practice** |
| `pca_surface_snapshot_history` | `vol/engine.py:773` **and** `analytics/engine.py:215` | yes | yes `[symbol, timestamp]` | `uq_surface_snap_hourly_symbol_ts` | **none in practice; two writers** (DI-08) |
| `account_history` | **none** (direct ORM write by `position_sync.py:325`) | yes | no | none | n/a — dead route (DI-30) |
| `open_position` | **none** (direct ORM write by `position_sync.py:183`) | yes | yes `["id"]` — describes a retired hash-PK | PK only | n/a — dead route (DI-02, DI-30) |
| `open_position_history` | **none** (direct ORM write by `risk/engine.py:403`) | yes | no | none | n/a — dead route (DI-30) |

---

## Appendix B — needs live verification by Valérian

Everything below requires a running Postgres/Redis and is therefore **out of scope for this
static audit**. Run against a scratch database, never prod. All commands assume
`DATABASE_URL` is already loaded in the shell — do not echo it.

### B.1 — Autogenerate diff (the audit's primary exit criterion)

```bash
# scratch DB, empty
docker compose exec api alembic -c src/persistence/alembic.ini upgrade head
docker compose exec api alembic -c src/persistence/alembic.ini \
  revision --autogenerate -m "tmp_drift_check"
# inspect the generated file; it MUST be empty apart from DI-23's missing CHECK.
# then DELETE the generated file — do not commit it.
```

Expected non-empty items given this audit: `ck_signal_track_status` on
`booked_position_metric_history` (DI-23).

### B.2 — Confirm DI-23 directly

```bash
docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "SELECT conname, contype FROM pg_constraint
     WHERE conrelid = 'booked_position_metric_history'::regclass;"
# expect ck_signal_track_status to be ABSENT
```

### B.3 — Measure the DI-03 blast radius before fixing

```bash
# how much position history currently exists vs how many positions are live
docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "SELECT (SELECT count(*) FROM open_position)          AS live_positions,
          (SELECT count(*) FROM open_position_history)  AS history_rows,
          (SELECT count(DISTINCT position_id) FROM open_position_history) AS positions_with_history;"
# positions_with_history should be >= live_positions.
# If it EQUALS live_positions, every closed position's history is already gone.
```

### B.4 — Confirm DI-08 (duplicate hourly snapshots)

```bash
docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "SELECT date_trunc('hour', timestamp) AS hr, source, count(*)
     FROM pca_surface_snapshot_history
    WHERE timestamp > now() - interval '7 days'
    GROUP BY 1,2 ORDER BY 1 DESC LIMIT 50;"
# Two rows per hour (one 'live_engine', one 'analytics_engine') confirms DI-08.
# Any hour with count > 1 for a SINGLE source confirms the gate race as well.
```

### B.5 — Confirm DI-12 (NULL event hashes)

```bash
docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "SELECT source, count(*) FILTER (WHERE event_hash IS NULL) AS null_hash, count(*) AS total
     FROM event_calendar GROUP BY source;"
# also look for reschedule duplicates:
docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "SELECT event_type, region, count(*), min(scheduled_at), max(scheduled_at)
     FROM event_calendar
    WHERE scheduled_at > now() - interval '90 days'
    GROUP BY 1,2 HAVING count(*) > 1 ORDER BY 3 DESC LIMIT 20;"
```

### B.6 — Confirm DI-01 end-to-end (does NOT need the DB)

```bash
# Read the live key and check for a top-level "spot". Expect: absent.
docker compose exec redis redis-cli --no-auth-warning GET latest_vol_surface:EURUSD \
  | python -c "import sys,json; d=json.load(sys.stdin); print('top-level keys:', list(d)); print('spot present:', 'spot' in d)"
# Then check what got persisted:
docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "SELECT position_id, count(DISTINCT spot) AS distinct_spots, count(*) AS rows
     FROM booked_position_metric_history
    WHERE timestamp > now() - interval '3 days'
    GROUP BY 1 ORDER BY 1;"
# distinct_spots = 1 per position (or spot = 1.085) is the smoking gun.
```

### B.7 — Kill db-writer mid-batch (DI-04 / DI-06 / DI-07)

Scratch stack only.

```bash
# 1. baseline row count
docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "SELECT count(*) FROM vol_surface_history;"
# 2. hard-kill the writer mid-flight and hold it down for two vol cycles (~6 min)
docker compose kill -s SIGKILL db-writer
sleep 400
docker compose up -d db-writer
# 3. recount. Any gap in timestamps across the outage window is unrecoverable
#    loss and confirms DI-06 (there is no replay).
docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "SELECT timestamp FROM vol_surface_history ORDER BY timestamp DESC LIMIT 20;"
```

### B.8 — Poison-message drill (DI-04 / DI-16 / DI-17)

Scratch stack only. Publish a malformed frame and confirm it takes down co-batched rows.

```bash
docker compose exec redis redis-cli --no-auth-warning PUBLISH db_events \
  '{"table":"vol_surface_history","payload":{"timestamp":"2026-01-01T00:00:00Z","underlying":"EURUSD","spot":"not-a-number","forward":1.0,"surface_data":{}}}'
# Then immediately publish a VALID frame for a different table and check whether it landed.
# Expect: writer logs "writer loop error, batch dropped" and the valid row is missing.
```

### B.9 — Double-insert reproduction (DI-02 / DI-05)

```bash
# Publish the same logical event twice with DIFFERENT timestamps (simulating a
# producer retry across a cycle). Expect TWO rows — proving ON CONFLICT never fires.
docker compose exec redis redis-cli --no-auth-warning PUBLISH db_events \
  '{"table":"feature_history","payload":{"timestamp":"2026-01-01T00:00:00.000001Z","symbol":"EURUSD","iv_atm_3m_pct":7.0}}'
docker compose exec redis redis-cli --no-auth-warning PUBLISH db_events \
  '{"table":"feature_history","payload":{"timestamp":"2026-01-01T00:00:00.000002Z","symbol":"EURUSD","iv_atm_3m_pct":7.0}}'
sleep 10
docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "SELECT count(*) FROM feature_history WHERE timestamp::date = '2026-01-01';"
# 2 = DI-02 confirmed.
```

### B.10 — Migration round-trip (DI-24)

```bash
# scratch DB only. Note that 028 RAISES on downgrade — stop above it.
docker compose exec api alembic -c src/persistence/alembic.ini downgrade 029_positions_rename_cols
docker compose exec api alembic -c src/persistence/alembic.ini upgrade head
# Then repeat B.1 and confirm the autogenerate diff is still empty.
```

### B.11 — Unbounded-table growth (DI-21)

```bash
docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "SELECT relname, n_live_tup, pg_size_pretty(pg_total_relation_size(relid)) AS size
     FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 15;"
# Watch regime_snapshot_history, feature_history, pca_surface_snapshot_history,
# booked_position_metric_history, trade_event, event_calendar — none has a pruner.
```

### B.13 — Confirm DI-35 (the pruner is blocked by an FK)

This is the highest-value check in the list — it is a one-query yes/no.

```bash
# Does any structure/preview still reference a PCA signal older than the 90-day cutoff?
# A non-zero count means the vol-engine pruner has been raising IntegrityError (and both
# vol_surface_history AND pca_signal_history have stopped pruning) since that row aged out.
docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "SELECT count(*) AS blocking_refs, min(s.timestamp) AS oldest_referenced
     FROM pca_signal_history s
    WHERE s.timestamp < now() - interval '90 days'
      AND (EXISTS (SELECT 1 FROM trade_structure t WHERE t.pca_signal_id = s.id)
        OR EXISTS (SELECT 1 FROM trade_preview   p WHERE p.pca_signal_id = s.id));"

# Corroborate from the data: is anything older than the retention window still present?
docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "SELECT 'vol_surface_history' AS t, min(timestamp), count(*) FROM vol_surface_history
   UNION ALL
   SELECT 'pca_signal_history', min(timestamp), count(*) FROM pca_signal_history;"
# min(timestamp) older than 90 days = the pruner is not working, regardless of cause.
```

Also grep the vol-engine logs for the swallowed exception (safe — this log line carries no
secret):

```bash
docker compose logs vol-engine --since 720h 2>/dev/null | grep -c "prune_history_failed"
```

### B.12 — Concurrent config PUT (DI-25)

Two simultaneous `PUT /api/v1/admin/config` calls; expect one 200 and one 500 with an
`IntegrityError` in the api logs (not two 200s with duplicate versions).

---

## Exit criteria status

| Criterion | Status |
|---|---|
| `alembic upgrade head` == `models.py` (autogenerate diff empty) | **Not verified** — needs B.1. Statically: single head, linear chain, one known drift (DI-23) |
| No double-insert reproducible | **FAILS** — DI-02 and DI-05 are reproducible by construction (B.9) |
| Staleness visible on every read path | **FAILS** — DI-14, DI-15 |
| Zero Blocker/High open | **FAILS** — 4 Blocker, 13 High |

## Suggested fix ordering

The four Blockers are independent and all small. In descending value-per-line:

1. **DI-35** — split the pruner into two sessions (~3 lines) and set `ondelete="SET NULL"` on
   the two `pca_signal_id` FKs. Run B.13 first to size the damage already done.
2. **DI-01** — add `spot` to the `publish_vol_update` payload (~1 line), then remove the
   `1.085` fallback and make the monitor skip rather than fabricate.
3. **DI-03** — break the cascade before any more closed positions lose their history. Run
   B.3 first; the loss is not recoverable.
4. **DI-02** — the largest of the four, because it needs a deterministic key design. The
   cheapest correct version is a producer-generated idempotency UUID per `db_events` frame
   (which also fixes DI-05) rather than re-keying five tables individually.

DI-04 (dead-letter) and DI-06 (delivery guarantee) are the two that change the *class* of the
pipeline rather than an instance, and both point at the same move: `db_events` should be a
Redis Stream with a consumer group, not pub/sub. That single change closes DI-06 outright,
makes DI-07's drops visible as a backlog instead of a lost log line, and gives DI-02/DI-05 a
natural message id to dedup on.
