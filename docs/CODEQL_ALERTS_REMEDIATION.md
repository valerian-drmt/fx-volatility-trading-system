# CodeQL Alerts — Audit & Remediation (2026-07)

Audit of the 13 open CodeQL code-scanning alerts on `main` (as of 2026-07-16),
the exploitability verdict for each, and how every alert was cleared — 11 by
code changes in this PR, 2 by documented dismissal.

**Headline: none of the 13 alerts was exploitable.** Most were false positives
caused by CodeQL not recognising validation layers (typed path params, dynamic
allowlists, HMAC token charsets). Four pointed at code worth hardening anyway
(raw-SQL string building, exception text returned to clients, unsanitised log
interpolation); those got real fixes.

## Summary

| # | Rule | Severity | Location | Verdict | Resolution |
|---|------|----------|----------|---------|------------|
| 7 | `py/partial-ssrf` | Critical | `api/routers/orders.py:34` | False positive | Dismissed (documented below) |
| 21, 22 | `py/sql-injection` | High | `api/routers/dev.py:532/540` | Safe (allowlists), style smell | Rewritten with SQLAlchemy Core |
| 29 | `py/stack-trace-exposure` | Medium | `api/routers/trades.py:267` | Real, low sev | Generic client error + server-side log |
| 20 | `py/stack-trace-exposure` | Medium | `engines/execution/main.py:419` | Real, internal-only | Generic client error + server-side log |
| 23–27 | `py/log-injection` | Medium | `engines/execution/main.py` (5 sites) | 1 real (str), 4 FP (int) | `_scrub()` CR/LF sanitiser at each site |
| 28 | `py/log-injection` | Medium | `api/routers/ws.py:30` | False positive (int param) | CR/LF scrub on channel name |
| 19 | `py/cookie-injection` | Medium | `api/routers/auth.py:28` | False positive (twice over) | Token minted from server-side username |
| 1 | `py/bind-socket-all-network-interfaces` | Medium | `infrastructure/docker/ib-stub/server.py:33` | By design (CI stub) | Dismissed as "used in tests" |

## Alert-by-alert analysis

### #7 — Partial SSRF, `orders.py` (Critical) → dismissed as false positive

`_forward()` interpolates `order_id` / `con_id` into the proxied URL
(`http://execution-engine:8001/internal/orders/{order_id}`). Both are FastAPI
`int` path parameters: any non-integer request is rejected with a 422 before
the handler runs, so a caller can never alter the target host, path structure,
or protocol. CodeQL taints FastAPI path params regardless of their type
annotation and does not treat the `int` coercion as a sanitiser.

Fixing "properly" would mean moving the id out of the URL path into a query
parameter on the execution-engine internal API — churn on a private API purely
for the scanner's benefit. Dismissed as *false positive* with this document as
rationale. If the internal API is ever reworked, prefer query-param ids.

### #21 / #22 — SQL injection, `dev.py` `/tables/{name}` (High) → rewritten

The endpoint built `SELECT ... FROM {name} WHERE {clauses} ORDER BY {order_by}`
as f-strings. It was **not injectable as written**: `name` is allowlisted
against `Base.metadata.tables`, `order_by` and every filter column against the
table's real column names, and all values were already bound parameters. CodeQL
cannot recognise a dynamic allowlist as a sanitiser. (Defence in depth: prod
nginx also returns 404 on `/api/v1/dev/*`.)

The raw-SQL style was still the real smell, so the query is now built entirely
with SQLAlchemy Core expressions: `select(t)`, `cast(t.c[col], Text).ilike(...)`,
`t.c[order_by].desc()`, `select(func.count()).select_from(t)`. Identifiers come
from ORM metadata objects, values are bound parameters, no raw SQL remains.
Matching semantics (`col:foo` substring / `col:%pat_` wildcards / `col:=exact`)
are unchanged.

### #29 — Exception exposure, `trades.py` `cancel_trade` (Medium) → fixed

The `httpx.HTTPError` branch returned `str(e)` to the API client, leaking
internal hostnames/ports (`execution-engine:8001`). The sibling `close_trade`
had already been fixed for the same CWE-209 pattern. `cancel_trade` now logs
the full exception server-side (`logger.exception`) and returns a generic
`"execution-engine unreachable"` to the client.

### #20 — Exception exposure, `execution/main.py` post-close sync (Medium) → fixed

The best-effort post-close position sync returned `{"error": str(e)[:300]}`.
The engine is internal-only (`fxvol-internal` network behind the authed API
proxy), so exposure was limited to authenticated operators — still, the
response now carries a generic message and the detail lives only in the engine
log (the existing `logger.exception` already captured it).

### #23–27 — Log injection, `execution/main.py` (Medium ×5) → sanitised

Five log sites interpolated request-derived values. Four used
`body.structure_id`, an `int` (`Field(gt=0)`) that cannot carry a newline —
false positives. One (`post_close_sync_failed`) logged `body.local_symbol`, a
genuine string through which a (write-authenticated) caller could inject `\r\n`
and forge log lines in Loki. A `_scrub()` helper now escapes CR/LF in every
request-derived value at these sites (CodeQL recognises the replace-chain as a
CWE-117 sanitiser).

### #28 — Log injection, `ws.py` (Medium) → sanitised

`ws_handler_crashed` logged the channel name, which for `/ws/orders/{structure_id}`
embeds a client-supplied path param — but that param is typed `int`, so this
was a false positive in practice. The channel string is CR/LF-scrubbed before
logging anyway; one line, alert gone.

### #19 — Cookie injection, `auth.py` login (Medium) → fixed at the source

Flagged because `body.username` (user input) flowed into `issue_token()` and
then into `Set-Cookie`. Two independent reasons it was not exploitable: the
token is `base64(json)` + hex HMAC — a charset that physically cannot contain
`;` or CR/LF — and the code path is only reached when `body.username` equals
`settings.auth_username`. The fix makes that structural: the token is now
minted from `settings.auth_username` (identical semantics, equality was just
checked), so no client-supplied bytes ever reach the header.

### #1 — Bind to 0.0.0.0, `ib-stub/server.py` (Medium) → dismissed as "used in tests"

The ib-stub is a CI-only dumb TCP sink that lets the e2e-compose job reach
"healthy" without real IB credentials. Inside a Docker network a service must
bind `0.0.0.0` to be reachable from other compose services; it discards all
input and holds no data. Changing the bind would break the e2e-compose job.
Dismissed with reason *used in tests*.

## Verification

- `ruff check src tests`, `python -m compileall -q src`, `lint-imports` — clean.
- `python -m pytest` (unit suite) — green.
- CodeQL closes alerts #19–29 automatically on the first analysis of `main`
  containing these changes; #7 and #1 were dismissed via the code-scanning API
  with the rationale above (reversible — dismissed alerts can be reopened).
