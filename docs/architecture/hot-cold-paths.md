# Hot path, cold path

The desk must feel instant where money moves (orders, fills, positions,
account) and may be slow where it thinks (vol surface, PCA, regime, rollups).
The six engines are already separate processes. What is missing is the rule
that keeps the slow work from standing in the fast work's way.

This note names the two paths, shows the three places where they collide
today (each one produced an incident in the first week of November 2026), and
lists the changes that separate them, in shipping order.

Companion notes: [data-flow.md](data-flow.md) (the live path end to end),
[backend.md](backend.md) (packages and import contracts),
[database.md](database.md) (schema).

## 1. The six engines and the path each one serves

| Engine | IB client | Cadence | Produces | Path |
|---|---|---|---|---|
| `market-data` | 1 | 0.1 s poll | EUR/USD bid/ask to Redis (`latest_spot`, channel `ticks`) | hot |
| `execution` | 5 | event-driven + 1 s position sync | orders to IB, fills, `open_position` rows, account snapshots, `orders:*` events | hot |
| `risk` | 3 | 2 s / 30 s | book greeks + P&L curve to Redis every 2 s; `open_position_history` rows every 30 s | hot read, cold write |
| `vol` | 2 | ~3 min | option chain (74 live lines), SVI/SSVI surface, fair vol, to Redis + Postgres | cold |
| `analytics` | none | 60 min | PCA snapshot, GMM regime refit, PC3 history, to Postgres | cold |
| `db-writer` | none | batched | streamed Redis payloads (`db_events`) into Postgres | cold |

**Hot** means an operator is waiting on the output within seconds. **Cold**
means the output is valuable but a minute late is fine.

```mermaid
flowchart LR
    classDef hot fill:#fbe3d6,stroke:#d9480f,color:#1d2430
    classDef cold fill:#dbe6f4,stroke:#3b6ea8,color:#1d2430
    classDef shared fill:#fff3cd,stroke:#b7791f,color:#1d2430
    classDef plain fill:#ffffff,stroke:#5b6676,color:#1d2430

    IB[IB Gateway<br/>paper, 4 client ids]:::plain

    MD[market-data<br/>ticks 0.1 s]:::hot
    EX[execution<br/>orders, fills, sync 1 s]:::hot
    RK[risk<br/>greeks 2 s, history 30 s]:::plain
    VOL[vol<br/>surface ~3 min]:::cold
    AN[analytics<br/>PCA, GMM hourly]:::cold
    DBW[db-writer<br/>Redis to Postgres]:::cold

    REDIS[(Redis<br/>last value + pub/sub)]:::plain
    PG[(Postgres<br/>one pool, 1.3 M history rows)]:::shared
    API[api<br/>REST + WS bridge]:::plain
    UI[voldesk UI]:::plain

    IB -->|ticks| MD
    IB -->|acks, fills, portfolio| EX
    IB -->|session only| RK
    IB -->|chain, 74 lines| VOL

    MD -->|SET + PUBLISH ticks| REDIS
    EX -->|PUBLISH orders:*| REDIS
    EX -->|open_position, account_history| PG
    RK -->|PUBLISH risk_update| REDIS
    RK -->|history every 30 s| PG
    VOL -->|PUBLISH vol_update| REDIS
    VOL -->|surface, fair vol| PG
    AN -->|pca, gmm, pc3| PG
    REDIS -.->|db_events| DBW
    DBW -->|batches| PG

    REDIS -->|pub/sub to WS| API
    PG -->|queries| API
    API -->|WS ticks, vol, risk| UI
    UI -.->|REST poll 15 / 30 / 60 s<br/>positions, orders, account, portfolio| API
```

Orange producers and blue producers write to the same Redis and the same
Postgres. The UI receives ticks, vol and risk by push and polls everything
else over REST. Positions, orders and account, the things an operator waits
on, sit on the dashed polling edge.

## 2. Where the two paths collide today

Separation by process is not separation by priority. The two paths still
share three resources with no rule about who yields. Each of the three
produced an incident between 2 and 5 November 2026, which is how we know these
are the three that matter.

### 2.1 The Postgres connection pool

The api holds one asyncpg pool: 5 connections plus 10 overflow. Every REST
endpoint takes a connection from it, whether it reads six `open_position`
rows or scans 1.3 million `open_position_history` rows.

```mermaid
flowchart LR
    classDef hot fill:#fbe3d6,stroke:#d9480f,color:#1d2430
    classDef cold fill:#dbe6f4,stroke:#3b6ea8,color:#1d2430
    classDef shared fill:#fff3cd,stroke:#b7791f,color:#1d2430

    subgraph hotq [hot queries]
        A1["/portfolio/account<br/>1 row, 0.2 s"]:::hot
        A2["/positions/open<br/>6 rows, 0.2 s"]:::hot
        A3["/trade/spot-orders<br/>30 rows, 0.2 s"]:::hot
    end
    subgraph coldq [cold queries]
        C1["/portfolio/pnl-attribution<br/>history scan, 0.2 to 20 s"]:::cold
        C2["/portfolio/stats<br/>account_history rollup, 5 s"]:::cold
        C3["/portfolio/daily-pnl<br/>account_history rollup, 3 s"]:::cold
    end
    POOL["api asyncpg pool<br/>5 + 10 overflow<br/>first come, first served"]:::shared
    PG[(Postgres<br/>2 vCPU shared)]

    A1 & A2 & A3 --> POOL
    C1 & C2 & C3 --> POOL
    POOL --> PG
```

The pool is first come, first served. A cold query that holds a connection for
20 seconds holds it against an account read that needs it for 200 milliseconds.

**4 November, pool exhaustion.** The attribution endpoint ran a `DISTINCT ON`
over the whole history table with no position filter. One call took one to
eight minutes. The Portfolio tab polls it every 60 seconds, so copies piled
up.

```mermaid
sequenceDiagram
    participant UI as voldesk (several tabs)
    participant API as api
    participant POOL as asyncpg pool (15 slots)
    participant PG as Postgres

    loop every 60 s, per tab
        UI->>API: GET /portfolio/pnl-attribution
        API->>POOL: acquire
        POOL->>PG: DISTINCT ON over 1.28 M rows
        Note over PG: work_mem 4 MB, spills to disk<br/>1 to 8 min per call
    end
    Note over POOL: 13 to 21 copies in flight<br/>all 15 slots held by cold scans
    UI->>API: GET /portfolio/account (hot)
    API->>POOL: acquire
    POOL-->>API: QueuePool limit reached (timeout)
    API-->>UI: 500
    Note over UI: Cash & margin $0, indicators "no data",<br/>while IB and Redis were fine
```

Fixed in PR #285: per-position `LATERAL` probe, index on
`account_history.timestamp`, and a 20 s `statement_timeout` on the api pool so
a slow query fails alone instead of holding a slot for minutes.

**5 November, the same endpoint, a second shape.** The forward-price lookup
for the attribution walked the history index backwards looking for a 6E
future that was not in the book. With no match, `LIMIT 1` never stopped the
walk; the query hit the new 20 s timeout and returned 500. That is the
timeout doing its job. The damage came from the next collision point.

### 2.2 The UI refresh loop

The frontend fetches each domain as one bundle. Until PR #289 the portfolio
bundle was a single `Promise.all` over six endpoints: one rejection rejected
the bundle, and the Trade tab fell back to the trade slice's account, which
is the trade-book bootstrap mock.

```mermaid
flowchart TB
    classDef hot fill:#fbe3d6,stroke:#d9480f,color:#1d2430
    classDef cold fill:#dbe6f4,stroke:#3b6ea8,color:#1d2430
    classDef bad fill:#f8d7da,stroke:#b02a37,color:#1d2430
    classDef ok fill:#d1e7dd,stroke:#146c43,color:#1d2430

    subgraph before [before PR 289: one Promise.all]
        B1["account 200"]:::hot
        B2["positions 200"]:::hot
        B3["vega-per-tenor 200"]:::cold
        B4["stats 200"]:::cold
        B5["daily-pnl 200"]:::cold
        B6["pnl-attribution 500 after 20 s"]:::bad
        BALL{{"Promise.all rejects"}}:::bad
        BOUT["portfolio.data = null<br/>Trade tab shows $100,000 / $0 mock"]:::bad
        B1 & B2 & B3 & B4 & B5 & B6 --> BALL --> BOUT
    end

    subgraph after [after PR 289: primary + degradable]
        P1["account 200"]:::hot
        P2["positions 200"]:::hot
        S3["vega-per-tenor 200"]:::cold
        S4["stats 200"]:::cold
        S5["daily-pnl 200"]:::cold
        S6["pnl-attribution 500 → null"]:::cold
        PRIM{{"primary Promise.all"}}:::ok
        SEC{{"each .catch(() => null)"}}:::ok
        POUT["account panel live,<br/>waterfall shows its neutral shape"]:::ok
        P1 & P2 --> PRIM --> POUT
        S3 & S4 & S5 & S6 --> SEC --> POUT
    end
```

The lesson generalises: a hot panel must never depend on a cold request
succeeding, and a panel must never substitute a mock for a value it cannot
fetch. Keep the last good value and show the freshness badge.

### 2.3 Host CPU

Two cores, thirteen containers (twenty with observability), and the Linux
scheduler gives every container the same weight. The only hard cap is
`cpus: 0.5` on the analytics engine.

```mermaid
gantt
    title One minute on the host during a PCA cycle (illustrative)
    dateFormat ss
    axisFormat %S s
    section cold
    analytics PCA + GMM fit, 2 threads      :crit, a1, 00, 25s
    vol chain scan, 74 lines                :a2, 30, 20s
    section hot
    execution order submit + ack            :active, h1, 05, 2s
    execution position sync (every 1 s)     :h2, 00, 60s
    market-data ticks (every 0.1 s)         :h3, 00, 60s
    api GET /portfolio/account              :h4, 06, 1s
```

While the fit holds both cores, the order submit at second 5 and the account
read at second 6 wait their turn in the run queue. Nothing is broken, but the
operator feels a stutter exactly when they are watching. The same applies to
the IB Gateway's Java process, which is busiest during the vol engine's
three-minute chain scan.

Measured on 5 November with averaged `docker stats`: at rest the whole stack
uses about a quarter of one core. The problem is not the average, it is the
burst, and the burst lands on whoever is running at that moment.

### 2.4 One more shared thing: the write side of Postgres

Not an incident yet, but the same shape. The risk engine writes one
`open_position_history` row per open position every 30 seconds, the vol engine
writes a surface every 3 minutes, db-writer streams payloads in batches, and
the execution engine writes positions and account snapshots. All of it lands
in one database on the same two cores that serve the hot reads.

```mermaid
flowchart LR
    classDef hot fill:#fbe3d6,stroke:#d9480f,color:#1d2430
    classDef cold fill:#dbe6f4,stroke:#3b6ea8,color:#1d2430
    classDef shared fill:#fff3cd,stroke:#b7791f,color:#1d2430

    EX["execution<br/>open_position, account_history<br/>every 1 s when changed"]:::hot
    RK["risk<br/>open_position_history<br/>N rows / 30 s"]:::cold
    VOL["vol<br/>surface, fair vol<br/>every 3 min"]:::cold
    DBW["db-writer<br/>streamed payloads<br/>batched"]:::cold
    AN["analytics<br/>pca, gmm, pc3<br/>hourly"]:::cold
    PG[(Postgres<br/>autovacuum, WAL, checkpoints<br/>on the same 2 vCPU)]:::shared
    READS["hot reads<br/>account, positions, spot orders"]:::hot

    EX --> PG
    RK --> PG
    VOL --> PG
    DBW --> PG
    AN --> PG
    PG --> READS
```

The 90-day retention delete in the risk engine and the autovacuum that follows
it are the kind of background work that can make Postgres busy for minutes
with no query of ours visible in `pg_stat_activity`.

## 3. The target: same engines, four rules

None of this adds a service or rewrites an algorithm. Each change installs a
priority rule at one of the collision points, plus one measurement so we know
the rules hold.

```mermaid
flowchart LR
    classDef hot fill:#fbe3d6,stroke:#d9480f,color:#1d2430
    classDef cold fill:#dbe6f4,stroke:#3b6ea8,color:#1d2430
    classDef plain fill:#ffffff,stroke:#5b6676,color:#1d2430
    classDef new stroke-width:3px

    subgraph hotlane [hot lane: pushed end to end, cpu_shares 1024]
        HEX[execution]:::hot
        HRED[Redis pub/sub<br/>positions, account, orders:*]:::plain
        HAPI[api WS bridge<br/>already forwards these]:::plain
        HUI[UI<br/>patches rows in place]:::hot
        HEX ==>|NEW publish after each sync / fill| HRED
        HRED ==>|exists| HAPI
        HAPI ==>|NEW subscribe /ws/positions, /ws/orders, /ws/account| HUI
    end

    subgraph coldlane [cold lane: computed once, read cheaply, cpu_shares 128 to 256]
        CAN[analytics<br/>PCA, GMM, rollups, attribution]:::cold
        CTAB[(result tables<br/>one small row per cycle)]:::plain
        CAPI[api<br/>SELECT ... LIMIT 1]:::plain
        CUI[UI<br/>keeps last value + badge]:::cold
        CAN ==>|NEW write| CTAB
        CTAB ==>|ms read| CAPI
        CAPI ==>|poll 60 s| CUI
    end
```

Why the push is cheap to finish: the api already serves `/ws/positions` and
`/ws/orders/{structure_id}`, the Redis bridge already forwards the
`positions`, `account` and `orders:*` channels, and the bus has
`publish_position_update` and `publish_account`. Nobody calls them: the
execution engine writes positions to Postgres and the UI reads them back on a
15 s timer. Two edges close the loop.

## 4. The changes, in shipping order

| # | Change | Where | Effort | Collision point |
|---|---|---|---|---|
| 1 | `cpu_shares` per container: execution, api, market-data 1024; risk 512; vol, db-writer 256; analytics, obs 128 | `docker-compose.yml` | 1 h, deploy-time | 2.3 |
| 2 | Push the hot path: execution publishes position and account deltas; UI subscribes to `/ws/positions`, `/ws/orders/{id}`, new `/ws/account`; REST poll becomes a 60 s reconciliation | execution, api/ws, frontend provider | 2 PRs | 2.2, 2.1 |
| 3 | Materialise cold results: analytics computes attribution and daily rollups once per cycle into result tables; api endpoints become single-row reads (closes #286) | analytics, migration, api | 2 PRs | 2.1, 2.4 |
| 4 | Never blank a panel: last good value + freshness badge, no mock fallback, on the trade and risk bundles too | frontend provider | 1 PR | 2.2 |
| 5 | Measure: `order_submit_latency_seconds` and `fill_to_screen_seconds` histograms, one Grafana panel with a 2 s target | execution, frontend beacon, grafana | 1 PR | all |
| 6 | Only then, hardware: split hosts (trading core / analytics + obs) rather than grow one | infra | later | decided by 5 |

Shares only bite under contention: an idle box still gives analytics both
cores. Step 1 is the manual brake, applied by the kernel.

## 5. Where a runtime capacity dial fits

A Settings dial for the analytics engine (cadence, training window, thread
count, hot-reloaded over `config:changed` like `VolConfig`) is a good operator
tool once steps 1 to 3 are in: it then changes how long a chart takes to
refresh and nothing else. Without the structural rules, the dial would be the
only thing between a PCA fit and a slow order ticket, which is too much to ask
of a slider.

## Related

- [data-flow.md](data-flow.md): the live path end to end.
- [../observability/](../observability/): the obs profile (re-enabled with memory caps on 5 November 2026).
- Issues: #284 / #285 (pool exhaustion), #288 / #289 (attribution timeout, portfolio bundle), #286 (daily rollups), #290 / #291 / #292 (spot orders), #293 / #294 (obs caps).
