# Backend architecture & standards — what to build for the long term

This is the reference for *how the backend should be shaped* so it survives years of
change, not just this week's feature. It is written against **this** codebase (FX vol
trading system) with concrete file references, and it uses the real bugs we hit as
lessons. Read it before adding a table, an endpoint, or an external integration.

The trigger for writing it: "Open positions" was rendering the **live IB portfolio**
as if the broker were the source of truth for *what a structure is*. That is exactly
the class of mistake this document exists to prevent.

---

## 0. The one principle that matters most

> **Own your truth. Reconcile everything external. Never let an outside system be the
> source of truth for a fact you are responsible for.**

A broker (IB), an exchange, a data vendor, a payment provider — these are **feeds**,
not systems of record. You *reconcile against* them; you do not *read your identity
from* them. The moment a domain fact ("this is a butterfly", "this order is filled",
"this user paid") is derived live from an external system instead of your own booked
record, you have built a system that lies the instant that feed is late, wrong, or nets
your data differently than you do.

Everything below is a corollary of this.

---

## 1. System of record vs. external mirror — the positions case study

### The mistake
IB nets positions **by contract**. A "butterfly" / "strangle" is a property of the
**trade/package**, not of a contract — the same `EUUV6 C1130` row can be a leg of many
structures. So a structure's *identity* **cannot** be recovered from the broker feed.
Rendering "Open positions" from the IB mirror (+ client-side `inferStructureName()`
guessing) means the desk's own booking is not the truth — the broker is. Wrong.

### The correct shape (already mostly present in this repo)

| Concern | Owner (system of record) | File |
|---|---|---|
| Product identity + economic terms | `trade_structure` (`structure_type`, `product_label`, tenor, expiry, qty) | `persistence/models.py` |
| Order + execution events | `trade_order`, `trade_fill` | idem |
| **Broker mirror (reconciliation feed)** | `open_position` — one row per **netted contract**, `trade_id` → structure | idem |
| Desk booking | `booked_position` | idem |

- **Written at Send**: `classify_legs()` (`core/trade_preview.py`) names the structure and
  it is persisted to `trade_structure`. That *is* "write the product + terms to the DB
  on Send". It already exists.
- **Read as one joined view**: `GET /positions/structured` (`api/routers/positions.py`)
  already joins `trade_structure` (name + legs + terms) to `open_position` (live qty,
  marks, greeks) by `trade_id`, and returns each structure fully hydrated with a per-leg
  `linked` flag + a `naked` break flag.

### What to do
1. **The Open positions panel reads ONE endpoint** (`/positions/structured`) and maps
   over `structures[].legs`. Delete the second data source (raw mirror leg rows) and the
   `inferStructureName()` fallback. → one fetch, zero client-side inference, terms straight
   from the DB.
2. **IB stays the reconciliation feed**: it supplies live qty / marks / greeks and powers
   the `naked` / break flags — it never supplies *identity*.
3. **Do not denormalize the structure name onto `open_position`.** It is a property of the
   package, not the contract; a copy on the contract row is a stale cache of the wrong
   grain. Reference (`trade_id`) and join.
4. Integrity: make `open_position.trade_id` a real FK to `trade_structure.id` so a
   position can never dangle.

### What the big systems do (Murex, Calypso, OpenLink/Findur, FIS Front Arena, Charles River, Bloomberg AIM)
- The **trade is the system of record**, booked with full economic terms **and a
  package/strategy id** at execution. Trades are immutable; **fills are events**.
- **Positions are a derived aggregation** (often materialized) over trades/fills — keyed by
  instrument **but carrying the strategy/package linkage**, so they can show "this
  butterfly", not only "net calls".
- The **broker/custodian feed is reconciled** by a separate STP process; mismatches are
  **breaks**, flagged and resolved. The feed is never the display source for the strategy view.
- Package identity is first-class **precisely because** the broker nets by instrument and
  you cannot recover it downstream.

---

## 2. The CTO checklist — what a backend needs to last

Each item: the principle, how it maps here, and the current status.
Status legend: ✅ have · 🟡 partial · ⛔ gap.

### 2.1 Clear boundaries, enforced by tooling — ✅
Layered, dependency-directed, and **checked in CI**, not by good intentions.
- `core/` is pure (no I/O); `bus/` + `persistence/` are adapters; `engines/` don't import
  `api`; `api/` doesn't import `engines`. Enforced by `.importlinter` (5 contracts) in CI.
- **Do**: when you add a module, decide its layer first. If a lint contract blocks you,
  the design is wrong — fix the design, don't relax the contract.

### 2.2 Single source of truth per fact — 🟡
Every fact has exactly one owner (§0/§1). No fact is computed live from two places that
can disagree.
- **Do**: before adding a field, ask "who owns this?" If the answer is "the frontend
  reassembles it from two endpoints", stop — move the join server-side.

### 2.3 Read model vs write model — 🟡
Clients read **hydrated, purpose-built views**; they never reconstruct domain facts.
- `/positions/structured` is the right shape; the panel not using it as the sole source is
  the gap (§1).
- **Do**: one screen → ideally one endpoint that returns exactly what it renders. Push
  joins/aggregation into the API, never the client.

### 2.4 Money-touching state is event-sourced & immutable — 🟡
Fills, orders, cash movements are **append-only events**; positions/P&L are **derived**.
This gives you an audit trail and reproducible P&L.
- Have: `trade_fill` is append-only; `VolConfig` is versioned append-only.
- Gap: `open_position` is mutated in place (mirror), and P&L is read off it rather than
  derived from fills. Fine for a mirror; **not** fine if you ever want audit-grade P&L —
  then positions become a fold over `trade_fill`.
- **Do**: never UPDATE a financial event row. Correct with a new compensating event.

### 2.5 Idempotency on every external side-effect — 🟡 (just fixed a violation)
Any operation that hits the broker must be safe to retry and must not stack.
- We literally shipped this: closing had **no idempotency**, so re-clicks stacked duplicate
  market orders (24 resting at IB, positions over-sold). Fixed with a server-side stacking
  guard (`close_one_open_position`) + a UI in-flight lock + a self-healing recency window.
- **Do**: every submit/close carries a client intent key; the server dedupes. "The user
  double-clicked" must never become "we sent two orders".

### 2.6 Reconciliation is a first-class feature — 🟡
Book vs broker is compared on a schedule; drift is surfaced, not hidden.
- Have: `order_reconciler` (stuck order → filled when IB holds it), `reconcile_trade_positions`
  (auto-close stale bookings), `account_is_reporting()` (distinguish flat vs dead feed).
- Gap: no explicit **break view** ("book says X, IB says Y, diff = Z"). That's the §1 ③ end-state.
- **Do**: treat "we disagree with the broker" as a monitored state with an owner, not an
  exception to swallow.

### 2.7 Typed contracts at the edges — ✅
The API surface is a **contract**, drift-checked.
- OpenAPI ↔ `schema.d.ts` drift check in CI (`gen:api:check`); Pydantic v2 request/response.
- **Do**: never hand-edit the generated client; change the schema, regenerate. A breaking
  API change is a versioned decision, not a silent one.

### 2.8 Migrations: reversible, tested, no data loss — ✅ (with sharp edges)
- Alembic, autogenerate + review. Known landmines captured in team memory: revision ids
  capped at 32 chars; rename/drop cascades to writer/payloads/analytics/tests; early
  downgrades need `DROP INDEX IF EXISTS`. See `docs/db_schema_drift_workflow.md`.
- **Do**: every migration has a working `downgrade`. Column-altering migrations patch the
  payload builders and the db_integration tests in the same PR.

### 2.9 Config as versioned data, not code — ✅
- `VolConfig` is versioned, append-only, hot-reloaded via Redis pub/sub. Runtime knobs are
  env-vars with sane defaults (`MARKETABLE_LIMIT_BUFFER`, `RECONCILE_AUTOCLOSE`, …).
- **Do**: a behavioural constant a trader might tune is an env-var/config row with a default,
  never a magic literal buried in a function.

### 2.10 Secrets never on disk, never in output — ✅
- AWS SSM Parameter Store (KMS), loaded into RAM only; a hard rule set forbids echoing them
  (see `CLAUDE.md`). Write endpoints are auth-gated.
- **Do**: no secret in logs, error messages, `docker inspect`, or test fixtures. Ever.

### 2.11 Observability — 🟡
Structured logs (structlog), health endpoints, engine heartbeats, `docs/observability/`.
- Gap: no consistent **correlation id** threaded request → order → fill → position, and no
  metrics on fill latency / reconcile breaks.
- **Do**: one id follows a trade end-to-end so you can answer "what happened to order N"
  from logs alone.

### 2.12 Testing pyramid, gated by cost — ✅
- `unit` (pure, mocked) default; `db_integration` / `redis_integration` / `integration`
  gated by env vars + markers. CI runs the pyramid; coverage floor on the frontend.
- **Do**: domain logic (`core/`) is unit-tested with zero I/O. External behaviour is
  integration-tested behind a gate. Don't mock what you're actually trying to verify.

### 2.13 Fail loud, degrade gracefully, never silently truncate — 🟡
- Timeouts on every outbound call (httpx `timeout=`), retries with backoff in the writer,
  fallbacks (portfolio when positions empty).
- Rule: if you cap, sample, or drop (top-N, no-retry), **log what you dropped** — silent
  truncation reads as "covered everything" when it didn't.

---

## 3. Anti-patterns we actually hit (lessons, keep them)

1. **External system as source of truth** — rendering structures from the IB mirror (§1).
2. **Legacy names that lie** — `entry_price_per_contract_usd` is actually a *premium in
   price points* (`CONTRACT_MULTIPLIER = 1`); dividing by a multiplier produced a `0.0`
   limit-price crash. A name that no longer matches reality is a latent bug. Rename or
   comment at the definition.
3. **Market orders on instruments with a price cap** — IB caps option market orders → BUY
   legs go `Inactive`, closes dribble forever. The fix is *pricing off the live quote*
   (marketable limit), and the design lesson: **understand the venue's execution model**
   before you assume "market order = instant fill".
4. **No idempotency on a retryable side-effect** — the stacked-closes incident (§2.5).
5. **A guard with no self-healing** — the first stacking guard counted *cancelled-but-not-
   flipped* rows forever and permanently locked closing. Guards that read state must have a
   staleness/recency bound or they become the next outage.
6. **Two polled sources, different intervals, for one view** — the Open positions name
   flickered because legs (15 s) and structure context (60 s) refreshed on different clocks.
   One view → one cadence → ideally one source.

---

## 4. Definition of done for a backend change

A change is done when:
- [ ] It has **one** owner for each fact it introduces (no cross-source reconstruction).
- [ ] Every external side-effect is **idempotent** and **timeout-bounded**.
- [ ] Financial state changes are **append-only events**; derived views are recomputable.
- [ ] Layer contracts (`.importlinter`) pass; the public API contract is regenerated, not hand-edited.
- [ ] Migration has a working `downgrade`; payloads + integration tests updated in the same PR.
- [ ] Unit tests cover the pure logic; integration tests cover the external behaviour behind a gate.
- [ ] No secret can reach any output; new write paths are auth-gated.
- [ ] Anything dropped/capped/sampled is logged; failures are loud.

---

## 5. Immediate, concrete next steps for THIS repo

1. **Point Open positions at `/positions/structured` as its sole source**; delete the raw
   mirror leg rows + `inferStructureName()` fallback (§1 ①). Low risk, high value.
2. **FK `open_position.trade_id → trade_structure.id`** (§1 ④).
3. **Add a reconciliation/break view** endpoint (book vs IB, per structure) (§2.6).
4. **Thread a correlation id** request → order → fill → position (§2.11).
5. **Later**: fold positions/P&L from `trade_fill` events for audit-grade numbers (§2.4).

Related: `docs/ORDER_PIPELINE.md` (execution path), `docs/db_schema_drift_workflow.md`
(migrations), `.importlinter` (layer contracts), `CLAUDE.md` (secrets + workflow rules).
