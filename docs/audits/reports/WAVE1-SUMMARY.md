# Wave 1 — Consolidated Findings (2026-08-01)

Audits run: **1 Security**, **2 Quant models**, **6 Architecture**, **10 Dependencies**.
All four were findings-only: no source file was modified, no git state changed.

## Totals

| Audit | Blocker | High | Medium | Low |
|---|---|---|---|---|
| 1 — Security | 0 | 3 | 8 | 9 |
| 2 — Quant models | 0 | 2 | 8 | 5 |
| 6 — Architecture | 0 | 2 | 8 | 4 |
| 10 — Dependencies | 0 | 4 | 7 | 4 |
| **Total** | **0** | **11** | **31** | **22** |

No Blockers. Eleven High findings gate the release per `../README.md`.

## Immediate action required (outside the fix queue)

**Rotate `REDIS_PASSWORD`.** Finding F-02: `src/api/main.py:107` has been logging
`redis_url=` verbatim at every API startup, and `REDIS_URL` embeds the password — so the
credential is in cleartext in container stdout and in Loki for the whole retention window.
Rotation is Valérian's action (SSM console), independent of the code fix.

## The eleven High findings

### Security
- **F-01** `docker-compose.yml:92` + `nginx.conf:78` — `FORWARDED_ALLOW_IPS: "*"` plus XFF
  append lets a client control `request.client.host`, so slowapi's rate-limit key is
  attacker-controlled and the 5/min login throttle is bypassable — on the credential that
  authorises trade submission.
- **F-02** `src/api/main.py:107` — Redis password logged in cleartext at startup (see above).
- **F-03** `src/api/middleware/timing.py:26` — concrete request path used as a Prometheus
  label; an unauthenticated visitor can mint unbounded histogram children and OOM the
  512 MB api container into a restart loop.

### Quant
- **QM-01** `src/core/pricing/bs.py:13-57` — all BS functions return 0.0 when `T<=0` or
  `sigma<=0` instead of intrinsic/limit greeks. Verified: a deep-ITM call is marked at
  **$0 with delta 0 on its expiry day** (should be ~$25,000, delta ~+1).
- **QM-02** `src/core/pricing/bs.py:116-144` — `interpolate_iv` still expects the retired
  flat surface schema while `position_monitor` feeds the nested shape, so it always returns
  `None`: the live re-pricing path is **silently dead** and the monitor runs forever on
  linearised entry-greeks.

### Architecture
- **A6-01** — the allow-listed `cockpit → engines.execution.structures` import is not
  data-classes-only: it executes a Black-76 pricer and `net_greeks()` at request time.
- **A6-02** — **three** divergent Black-Scholes implementations (`core/pricing/bs.py`,
  `core/trade_preview.py`, `engines/execution/structures.py`) with different right encodings
  (`"C"`/`"call"`/`"CALL"`), different degenerate-input behavior, and different vega units.

### Dependencies
- **DEP-01** — no Python lockfile; Docker builds fresh-resolve and today already land
  **pandas 3.0.5 + numpy 2.4.6** into quant containers written for pandas 2.x.
- **DEP-02** — `fastapi<0.112` freezes starlette 0.37.2 (9 advisories, unsupported line)
  on the public edge.
- **DEP-03** — `ib-insync==0.9.86` archived upstream; no patch path if the IB protocol
  shifts again. `ib_async` is the maintained fork.
- **DEP-04** — the public read-only API/WS redistributes IB-derived real-time data, which
  subscriber agreements prohibit for non-pro subscribers. **Legal decision, not a code fix.**

## Cross-audit convergence

Three findings from two independent audits share one root cause: **QM-01, QM-02 and A6-02
are all consequences of duplicated pricing code**. Three BS copies drifted apart; one of
them lost its intrinsic-value branch, and a schema change reached the monitor but not the
interpolator. Consolidating pricing into a single `core/pricing` implementation fixes the
class, not just the instances — and A6-01 (promote `structures.py` pricing into `core/`)
is the same move.

Second convergence: **DEP-01 (no lockfile) and DEP-03 (archived ib-insync)** are both
supply-chain reproducibility gaps, and the ib-gateway version-drift incident already
demonstrated the failure mode in production.

## Verified clean (worth recording)

- Auth matrix: all **115 routes** enumerated programmatically; every mutating endpoint
  carries `Depends(require_write)`, with three intentional exceptions (login, logout,
  stateless pricing calculators).
- No f-string SQL anywhere; no SSRF in the events pipeline; no `pull_request_target`;
  no committed secrets; no token in frontend storage; prod compose publishes host ports
  for nginx only.
- All six greeks match central finite-difference bumps of the pricer (worst 4.5e-5);
  put-call parity to 5.6e-17; delta conventions consistent end-to-end; no look-ahead in
  PCA/GMM/feature-enrichment; PCA/GMM deterministic.
- All 5 import-linter contracts hold; ruff clean; no Qt/pre-rename dead code; Pydantic
  uniformly v2; heavy math correctly offloaded via `to_thread`.
- LICENSE present (MIT); no GPL/AGPL anywhere; prod npm graph has **0** vulnerabilities.

## Open questions for Valérian (not resolvable by audit)

1. **DEP-04** — IB market-data redistribution on the public endpoint. Cheapest defensible
   stance proposed: auth-gate raw quotes, keep only derived analytics public.
2. **FRED terms of use (2024)** prohibit storing FRED content in a database; the events
   pipeline persists FRED release dates and omits the mandatory disclaimer (Medium).
3. **ib-insync → ib_async** migration before v1.0, or stay pinned with a written runbook.

## Next

Wave 2 (3 Risk, 4 Execution, 5 Data integrity, 9 Frontend) is on hold pending go-ahead.
Wave 3 (7 Coverage, 8 Infra/ops, 11 Docs) runs after Wave 1–2 fixes land.
