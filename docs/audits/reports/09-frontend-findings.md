# Audit 9 — Frontend · Findings

**Run date:** 2026-08-01 · **Scope:** `frontend/src/`, `frontend/e2e/`, `vite.config.ts`,
`web.Dockerfile`, `infrastructure/nginx/frontend.conf` + `nginx.conf` (subpath).
**Method:** code review, backend payload cross-check (`src/api/routers`, `src/api/schemas`,
`src/bus`), plus read-only tooling: `npm run lint`, `npm run typecheck`, `npm test`,
production `npm run build`. No docker stack started, no live deployment touched, no source
file modified.

---

## Executive summary

The desk is built around an explicit and well-intentioned data-truthfulness contract:
`Fresh<T>` (`data / status / asOf / ageMs`), a `FreshBadge`, `neutral.ts` "honest empty
states", and a documented "never fabricate data" decision (2026-06-16). The contract is
correctly *designed*. It is not correctly *implemented*: the three mechanisms that would
make it work — a server-side data timestamp, a clock that ages the status, and a
connection-state indicator — are all absent or discarded.

**Direct answer to the audit's core question: no.** Stale and markets-closed data is *not*
visibly labeled. The `f301a560` "last full IV grid when markets closed" path is served by
the backend with its true `timestamp` (e.g. Friday 21:02), the frontend throws that
timestamp away, re-stamps the payload with `Date.now()` at fetch completion, and the Signals
panel renders a green **`● live`** badge over a frozen weekend grid. The same defect covers
every REST-backed panel in the desk. Separately, the top bar asserts the literal words
**"Market open"** whenever a tick message arrived in the last 10 s — and the market-data
engine republishes its last good tick at 5 msg/s forever, so that claim is true by
construction and false in fact every night and every weekend.

The quality floor is otherwise high and worth recording: `lint` and `typecheck` both pass
clean, `npm test` is 64/64 green, there is **zero** `any`, **zero** `@ts-expect-error`, and
`tsconfig` runs full strict plus `noUncheckedIndexedAccess` and `exactOptionalPropertyTypes`.
Plotly and dagre are fully code-split into the lazy `/dev` chunk, so the trader-facing
initial load is ~120 kB gzip. Auth is an httpOnly cookie with no token anywhere in JS
storage, and the write-action gate correctly degrades to `authenticated` rather than to the
build-time `VITE_WRITE_ENABLED` flag.

Wave 1 consequences found in this scope: the **QM-02 pattern (silent `None`/`0` on schema
drift) is systemic in the frontend adapter layer** — ~24 consumed endpoints are typed
`apiGet<unknown>`, so the `gen:api:check` drift gate cannot see them, and every adapter
coerces an absent field to `0` via a local `n()` helper. A backend field rename would render
a zero greek, not an error. Audit 6's analytics duplication has no visible UI consumer, so
no flapping was found.

### Totals

| Severity | Count |
|---|---|
| Blocker | 3 |
| High | 5 |
| Medium | 10 |
| Low | 8 |

---

## Findings table

| ID | Sev | Location | One-liner |
|---|---|---|---|
| FE-01 | Blocker | `frontend/src/voldesk/data/live/surface.ts:61` + `hooks/useFetch.ts:42` | Markets-closed frozen IV grid is badged `live`; backend `timestamp` discarded |
| FE-02 | Blocker | `frontend/src/hooks/useFetch.ts:42` + `voldesk/data/freshness.ts:31` | `Fresh.status` is computed once and never ages — no REST panel can ever go `stale` |
| FE-03 | Blocker | `frontend/src/voldesk/VoldeskApp.tsx:142` + `views/DashboardView.tsx:194` | UI asserts "Market open" from tick recency; engine republishes a frozen tick 5×/s forever |
| FE-04 | High | `frontend/src/hooks/streams.ts:18` | WS connection state computed then thrown away — no connecting/disconnected UI anywhere |
| FE-05 | High | `frontend/src/voldesk/data/core.ts:35` (4 call sites) | Mock `SPOT = 1.0842` is the silent fallback for live spot, incl. the order-sizing path |
| FE-06 | High | `frontend/src/api/endpoints.ts:117-158` | ~24 consumed endpoints typed `unknown` — outside the OpenAPI drift gate entirely |
| FE-07 | High | `frontend/src/main.tsx:37` | One error boundary for the whole desk; no per-view boundary |
| FE-08 | High | `frontend/vite.config.ts:38` + `infrastructure/nginx/frontend.conf:11` | Prod source maps built *and* served on the public deployment |
| FE-09 | Medium | `views/RiskView.tsx:148` vs `:430` | Two opposite VaR sign conventions, compensated by two hard-coded sign flips, untested |
| FE-10 | Medium | `frontend/src/voldesk/views/RiskView.tsx:177` | VaR table header says "ES 97.5%"; the value is ES 99% |
| FE-11 | Medium | `frontend/src/voldesk/views/DashboardView.tsx:252,350` | Dashboard Portfolio + Risk cards carry no freshness indicator at all |
| FE-12 | Medium | `frontend/src/voldesk/data/live/system.ts:60,88` | System view fabricates green `up` statuses; missing heartbeat age becomes `0s` |
| FE-13 | Medium | `frontend/src/tests/mocks/handlers.ts:35` + `e2e/dashboard.spec.ts` | MSW surface fixture uses the retired flat schema; zero WS handlers; e2e is a boot smoke |
| FE-14 | Medium | `frontend/src/voldesk/voldesk.css:307,613,1475` | Three `outline:none` rules, no `:focus-visible` anywhere — focus can be invisible |
| FE-15 | Medium | `frontend/src/voldesk/auth/LoginModal.tsx:25` | Modal has no `role="dialog"`, no focus trap, no Escape, no initial focus |
| FE-16 | Medium | `frontend/src/voldesk/voldesk.css:4` | `--text-faint` is 2.6–3.05:1 — below 3:1 on three of four surfaces, used for sole panel content |
| FE-17 | Medium | `infrastructure/nginx/nginx.conf:83` | `limit_conn ws_conn 10` vs 3 WS/tab; refused sockets produce no UI signal |
| FE-18 | Medium | `frontend/src/voldesk/data/provider.tsx:240` | `useFetch` returns a fresh object each render → the provider `useMemo` never hits |
| FE-19 | Low | `frontend/src/hooks/streams.ts:22` | `/ws/ticks` carries `symbol`; the frontend never filters on it |
| FE-20 | Low | `frontend/src/voldesk/VoldeskApp.tsx:285` | First tick always renders the red ▼ arrow (`prevMid === null` → `dir = -1`) |
| FE-21 | Low | `frontend/package.json:22` | `gen:api:check` hardcodes `/tmp` + POSIX `diff` — unrunnable on the dev machine |
| FE-22 | Low | 11 SVGs, 7 `<tr onClick>`, `VoldeskApp.tsx:149` | a11y: unlabelled data charts, mouse-only rows, unlabelled `<select>`, h1→h3 skip |
| FE-23 | Low | `frontend/src/voldesk/views/DashboardView.tsx:190` | Macro-event "in 3h" countdown frozen at mount (no poll on that fetch) |
| FE-24 | Low | `frontend/src/voldesk/data/live/trade.ts:150` | `adaptLimits` falls back to hardcoded prototype caps; the field is currently unrendered |
| FE-25 | Low | `frontend/src/` (141 sites) | 141 non-null assertions are the only TS escape hatch in use |
| FE-26 | Low | `frontend/src/voldesk/views/RiskView.tsx:168` | VaR histogram bins are √t-scaled, presenting a 1-day distribution as a yearly one |

---

## FE-01 — Blocker · Markets-closed frozen IV grid is rendered as `live`

**Files:** `frontend/src/voldesk/data/live/surface.ts:61-91`,
`frontend/src/hooks/useFetch.ts:42`, `frontend/src/voldesk/views/SignalsView.tsx:367`,
`frontend/src/voldesk/data/provider.tsx:81-89`.

### Evidence

Commit `f301a560` deliberately makes the backend serve the last row that carries a real IV
grid rather than the newest model-only row, so the panel does not blank at close. Its own
message says the last grid can sit *"~19 model-only rows behind the latest"* — verified on
prod at 21:02. The response carries the true data time:

```python
# src/api/schemas/vol.py:10-19
class SurfaceResponse(BaseModel):
    symbol: str
    timestamp: datetime     # = VolSurface.timestamp — the real grid time
    surface: dict[str, Any]
```

`adaptSurface` reads only `resp.surface` and never touches `resp.timestamp`
(`live/surface.ts:61-91`). The provider then wraps it:

```ts
// provider.tsx:81
const liveSurface = useFetch<...>(async () => adaptSurface(await fetchVolSurface()), VOL_WARN_MS);
// useFetch.ts:42
if (!cancelled) setState(makeFresh<T>(d, Date.now(), warnMs));   // ← client fetch time
```

`makeFresh(data, Date.now(), 240_000)` → `statusFor` compares `Date.now() - Date.now()` → 0,
which is never `> warnMs`, so `status === "live"`. `SignalsView.tsx:367` renders
`<FreshBadge fresh={surface} />`, which prints a green dot and the word `live`, with no age
suffix (the age suffix is only emitted for `status === "stale"`, `FreshBadge.tsx:32`).

The same discard applies to `/vol/term-structure` (`TermStructureResponse.timestamp`, and
the `0f4be379` persisted-`_fair_q` path), `/portfolio/var` and `/portfolio/stats` (both emit
`computed_at`), and `/positions/open` (per-row `timestamp`, captured into `Position.updated`
by `live/trade.ts:98` and then rendered nowhere).

**Net effect:** from Friday 22:00 to Sunday 23:00, the Signals view shows a fully populated
IV surface with z-scores, an ATM/fair term curve, and a green `live` badge — all frozen at
Friday's close, with nothing on screen distinguishing it from a Tuesday-lunchtime surface.
This is exactly the "silent-stale scenario" the audit's exit criteria forbid.

### Recommended fix (not applied)

Thread the server timestamp end-to-end. Give each adapter a `{ data, asOf }` return (or a
sibling `extractAsOf(resp)`), and change `useFetch` to accept an optional server-time
extractor so `makeFresh` is called with the payload's own `timestamp` / `computed_at`
instead of `Date.now()`. `freshness.ts` already documents this intent — *"epoch ms of the
data (server `asOf` if provided, else receive time)"* — the "if provided" branch was simply
never wired. Where the backend serves a deliberately-frozen grid, prefer an explicit
`stale`-with-age render over the current silent `live`, and consider surfacing the
markets-closed condition as its own badge word (e.g. `frozen · Fri 21:02`) rather than
folding it into the generic age threshold.

---

## FE-02 — Blocker · `Fresh.status` is computed once and can never age into `stale`

**Files:** `frontend/src/hooks/useFetch.ts:31-60`,
`frontend/src/voldesk/data/freshness.ts:22-41`.

### Evidence

`statusFor(asOf, warnMs, now)` is a pure function of `now`, but nothing in `useFetch` ever
re-invokes it. The status is baked into React state exactly once, at the moment the fetch
resolves:

```ts
// useFetch.ts:39-55
void fetcherRef.current(controller.signal)
  .then((d) => { if (!cancelled) setState(makeFresh<T>(d, Date.now(), warnMs)); })
  .catch((err) => { ... setState((prev) => prev.data === null ? {missing} : {...prev, status: "stale"}); });
```

There is no interval, no `requestAnimationFrame`, and no clock dependency that recomputes
the status as time passes. Consequently:

- The **only** path from `live` to `stale` is a *rejected* fetch. A backend that keeps
  answering `200 OK` with month-old rows keeps the panel green forever.
- `ageMs` is frozen at ≈0 for the lifetime of the value, so even if a view wanted to print
  the age it would print `0s`.
- Panels that fetch once at mount with **no poll and no WS invalidation** are frozen at
  mount *and* permanently badged `live`. In `RiskView.tsx` alone that is `StressEngine`
  (`:293`, four stress grids), `LiveLadders` (`:568`, five greek ladders), `PinRiskTable`
  (`:327`), `MarginalVarPanel` (`:427`) and both `fetchStructuredPositions` calls
  (`:332`, `:438`). A trader who leaves the Risk tab open for an hour sees hour-old stress
  grids and marginal-VaR under a green `live` pill.
- The domains that *are* invalidated (`termStructure`, `surface`, `pca` on the vol beat;
  `trade`, `portfolio` on the risk beat — `provider.tsx:194-214`) re-stamp themselves `live`
  on every successful reload. If the **vol engine dies but the API stays up**, the reload
  still returns 200 with the last DB row, and the badge stays green indefinitely.

Note the asymmetry: WS-backed domains *do* re-evaluate, because `useStream`
(`hooks/streams.ts:19`) calls `makeFresh` inline on every render — but only when something
else forces a render, which is not guaranteed when the stream goes silent.

### Recommended fix (not applied)

Recompute the status against a ticking clock rather than freezing it. Store only
`{ data, asOf }` in state and derive `status`/`ageMs` at render time from a shared
low-frequency clock (a single `useSyncExternalStore`-backed 1 s tick shared across the
provider, so one timer serves the whole desk). Add a unit test that advances fake timers past
`warnMs` with no new fetch and asserts the badge flips to `stale` — `foundation.test.tsx`
currently only tests the pure `statusFor`/`makeFresh` helpers, never the hook, which is why
this passed 64/64 green.

---

## FE-03 — Blocker · "Market open" is asserted from message arrival, on a tick the engine republishes forever

**Files:** `frontend/src/voldesk/VoldeskApp.tsx:141-165`,
`frontend/src/voldesk/views/DashboardView.tsx:193-194`,
`frontend/src/hooks/streams.ts:11-31`.
**Backend root cause (cross-ref Audit 5/8):** `src/engines/market_data/main.py:54-72`,
`src/engines/market_data/engine.py:153-167`, `src/bus/publisher.py:36,95`.

### Evidence

The frontend derives a factual claim about the *exchange session* from the recency of a
*websocket message*:

```ts
// VoldeskApp.tsx:141-142
const dotColor  = status === "live" ? "var(--pos)" : status === "stale" ? "var(--warn)" : "var(--neg)";
const stateLabel = status === "live" ? "Market open" : status === "stale" ? "feed stale" : "no feed";
```

duplicated verbatim at `DashboardView.tsx:193-194` for the Trade card. `status` here is
`Fresh.status` from `useTicks()`, i.e. "a tick message was parsed less than 10 s ago"
(`streams.ts:12`, `WARN_MS.ticks = 10_000`).

The market-data engine guarantees that condition is met permanently. `latest` is a plain
dict holding the last good quote, and the callback deliberately refuses to overwrite it when
IB reports the market-closed sentinel:

```python
# src/engines/market_data/main.py:60-72
if not (bid_ok and ask_ok):
    return  # do not overwrite the last good tick with a sentinel
latest.update({"bid": bid, "ask": ask, "mid": (bid + ask) / 2})

def _fetch_latest_tick() -> dict[str, Any] | None:
    return latest or None          # never expires
```

`_poll_once` runs every `POLL_INTERVAL_S = 0.1` and publishes whenever the
`TICK_PUBLISH_THROTTLE_MS = 200` window has elapsed — so with the market shut, the `ticks`
channel carries **5 messages per second of Friday's closing price, indefinitely**. The
frontend parses each one, stamps `asOf = Date.now()` (`useWebSocket.ts:87`), and reports
`live`.

Result, every night and every weekend: a green dot, the words **"Market open"**, and a
stale mid rendered to 5 decimals as if it were a live quote — in the top bar of every view
and on the Dashboard Trade card. This is not a degraded state the user can detect; it is
indistinguishable from a genuinely open market.

Two aggravating details:

1. The tick payload *does* carry a `timestamp` (`publisher.py`, `_iso_utc`), but the
   frontend's hand-written `TickMsg` (`streams.ts:22-27`) declares only
   `{symbol?, mid?, bid?, ask?}` — the timestamp is not in the type and is not read. (Even
   if it were, it is the *publish* time, not the quote time, so the fix has to reach the
   engine.)
2. `_subscribe_ib_ticks` calls `ib.reqMarketDataType(3)` — **delayed** IB data, documented in
   that function as *"~20 min old"*. Nothing in the UI discloses that the displayed spot is
   delayed. A grep for `delayed` across `frontend/src` returns only unrelated log-window
   time ranges.

### Recommended fix (not applied)

Three independent changes, all needed:

- Stop asserting session state from message recency. Either derive market-open from an
  actual FX session calendar client-side, or have the backend publish an explicit
  `market_open` / `session` flag and render that. If neither is available, degrade the label
  to something the data supports — "feed live" rather than "Market open".
- Have the engine expose quote age: either stop republishing an unchanged tick, or add a
  `quote_ts` (the IB tick time, not the publish time) to the payload, add it to `TickMsg`,
  and let `makeFresh` key off it. This is the change that actually makes the badge honest.
- Disclose the delayed-data mode in the ticker (a `delayed` chip next to the mid), which also
  helps the DEP-04 redistribution question from Wave 1.

---

## FE-04 — High · WS connection state is computed, then discarded

**Files:** `frontend/src/hooks/streams.ts:18`, `frontend/src/hooks/useWebSocket.ts:22-97`.

### Evidence

`useWebSocket` does the hard part correctly — exponential backoff capped at 15 s, `attempt`
reset on open, cleanup on unmount, error→close→reconnect:

```ts
socket.onclose = () => {
  if (stopped) return;
  setState((s) => ({ ...s, status: "closed" }));
  const backoff = Math.min(1000 * 2 ** attempt, MAX_BACKOFF_MS);
  attempt += 1;
  timer = setTimeout(connect, backoff);
};
```

and then the single consumer throws the result away:

```ts
// streams.ts:18
const { last, asOf } = useWebSocket<T>(path, undefined, throttleMs);
return makeFresh<T>(last, asOf, warnMs);
```

Grepping `WsStatus` across `frontend/src` confirms the only rendering consumer is
`pages/dev/PipelineViz.tsx:438`, which uses the *separate* `useWsLog` hook. **No component in
the seven trader-facing voldesk views renders a connection state.** There is no
"connecting", no "disconnected", no reconnect countdown. When all three sockets drop, the
desk looks exactly as it did a second earlier.

Two secondary defects in the same hook:

- **Throttle resurrects a dead status.** With `throttleMs > 0` (all three streams use it:
  1 s / 30 s / 15 s), `emit` buffers `{last, status: "open", asOf}` into `pending`
  (`:62-64`). If the socket closes while `pending` is non-null, `onclose` sets
  `status: "closed"`, and then the still-running flush interval does `setState(pending)` —
  restoring `status: "open"` on a socket that is closed, for up to the backoff duration.
  Harmless today only because nothing reads `status`; it becomes a live bug the moment
  FE-04 is fixed.
- **No liveness watchdog.** There is no ping/pong and no "no message for N × expected
  interval → treat as dead" check. A half-open TCP connection (proxy or NAT drop, common on
  a 3600 s `proxy_read_timeout`) never fires `onclose`, so the reconnect logic never runs and
  the panels freeze with no signal.

### Recommended fix (not applied)

Surface `WsStatus` through `Fresh<T>` (add a `connection` field, or return it alongside) and
render it — a single connection pill in the top bar covering all three streams, plus
per-panel degradation. Fix the throttle by buffering only `{last, asOf}` and merging into
the current status at flush time. Add an application-level heartbeat: if `asOf` exceeds a
per-stream deadline, force-close the socket so the existing backoff path takes over.

---

## FE-05 — High · A mock spot constant is the silent fallback for live spot, including in the order-sizing path

**Files:** `frontend/src/voldesk/data/core.ts:35`,
`views/DashboardView.tsx:149`, `views/PortfolioView.tsx:1062`,
`views/TradeView.tsx:903-904`, `components/OrderBuilder.tsx:352,700,725`.

### Evidence

```ts
// core.ts:35 — "Reference spot for the OrderBuilder preview maths (NOT live market data)."
export const SPOT = 1.0842;
```

That constant is used as the `??` fallback for live spot in four places, none of which is
the OrderBuilder preview maths it was scoped to:

```ts
DashboardView.tsx:149   const spot = ticks.data?.mid ?? DATA.SPOT;
PortfolioView.tsx:1062  const spot = useTicks().data?.mid ?? DATA.SPOT;
TradeView.tsx:903-904   const spotBid = ticks.data?.bid ?? DATA.SPOT - 0.0001;
                        const spotAsk = ticks.data?.ask ?? DATA.SPOT + 0.0001;
OrderBuilder.tsx:352    const spotMid = ((ticks.data?.bid ?? DATA.SPOT) + (ticks.data?.ask ?? DATA.SPOT)) / 2;
```

With no tick, `TradeView`'s Indicators panel renders `1.08410 / 1.08430` as a live bid/ask
spread. `DashboardView` divides net-liq by it to produce the gross/net leverage multiples
(`:163-165`). `OrderBuilder.tsx:700` prints `spot {DATA.SPOT.toFixed(4)}` as a literal
caption under "Legs on the smile". None of these is labelled as synthetic.

The order path is the serious one. `OrderBuilder` derives the default vanilla strike from
`spotMid`, and its own comment states the hazard precisely:

```ts
// OrderBuilder.tsx:384-387
// A hand-typed vanilla strike is shipped RAW to IB (orderLegs.ts), so it must
// sit on the LIVE market — not the mock spot (DATA.SPOT = 1.0842). Otherwise the
// contract is deep ITM/OTM vs the real ~1.14 market and IB won't qualify it.
const liveAtm = gridStrike(spotMid);
```

The guard the comment describes was never added: `spotMid` *is* `DATA.SPOT` when the tick
feed is absent, so `liveAtm` becomes 1.085 against a ~1.14 market — 550 pips off, silently.
This state is reachable on every page load before the first tick lands, and for the entire
duration of any API/WS outage. It contradicts the `neutral.ts` contract ("never fabricated
numbers") directly.

### Recommended fix (not applied)

Make the fallback structural, not numeric: propagate `spot: number | null` and render `—`
plus a `no spot` badge wherever it is null. Gate the OrderBuilder's strike defaulting and
the leverage multiples on a non-null spot, and disable the submit control while spot is
unavailable. Restrict `DATA.SPOT` to the smile-preview geometry it was written for, and add
a lint rule or a test asserting `DATA.SPOT` appears in no `??` expression.

---

## FE-06 — High · Most consumed endpoints sit outside the OpenAPI drift gate

**File:** `frontend/src/api/endpoints.ts` (lines 117-158, 173-181, 313-314, plus the
hand-written interfaces at 66-109, 198-298).

### Evidence

The drift gate is real and works: CI dumps the live FastAPI schema
(`frontend/scripts/dump_openapi.py`), regenerates `schema.d.ts`, and `diff -q`s it
(`.github/workflows/ci.yml:128-132`). But a drift check on `schema.d.ts` only protects
call-sites whose types are *derived from* `schema.d.ts`. Counting `endpoints.ts`:

- **Typed via `Get<...>` (protected):** 9 — `auth/me`, `health`, `health/extended`,
  `price`, `vol/surface`, `vol/term-structure`, `vol/smile/{tenor}`, `signals/pca/state`,
  `signals/pca/history`, `admin/config`.
- **Typed `apiGet<unknown>` (unprotected):** 24 — `regime/events`, `positions/open`,
  `portfolio/{account,cash,daily-pnl,stats,var,risk-per-tenor,equity-curve,vega-per-tenor,
  stress-grid,greeks-ladder,pnl-attribution,pin-risk,marginal-var}`, `trade/{limits,book}`,
  `admin/config/history`, `admin/settings/{domain}`, `dev/engines`, `dev/cycle-progress`,
  and the write endpoints.
- **Hand-written interfaces not in `schema.d.ts` (unprotected):** `Bar`, `TradeMarkerRaw`,
  `GreekPnlHistoryRaw`, `ValuationHistoryRaw`, `TradePreview`, `SubmittedTrade`/`SubmittedLeg`,
  `StructuredPositions`/`StructuredStructure`/`StructuredLeg`/`UnlinkedPosition`.

For every unprotected endpoint the adapter is the only contract, and every adapter coerces
a missing field to a real number:

```ts
// live/portfolio.ts:28
const n = (v: unknown): number => (typeof v === "number" ? v : 0);
// :231-236 — a renamed vega_usd silently becomes 0 vega, per tenor bucket
vega: n(r.vega_usd) / 1000,
```

This is QM-02's failure mode reproduced on the read path. I spot-checked the riskiest
adapters against the current backend and found **no active drift** — `/portfolio/stats`,
`/portfolio/var`, `/portfolio/risk-per-tenor` and `/portfolio/vega-per-tenor` all match
field-for-field today. The finding is that nothing would catch it if they stopped matching.

`live/portfolio.ts` deserves credit for distinguishing "absent" from "zero" where it
matters — `nn()` keeps VaR and attribution nulls null, with an explicit comment that a 0
VaR would read as "no risk". The problem is that `n()` (coerce to 0) is used for every
greek, and greeks are the numbers the desk is for.

### Recommended fix (not applied)

Two options, ideally both: (a) give the un-modelled endpoints Pydantic response models
server-side so they enter `schema.d.ts` and the existing gate covers them for free — this
is the cheap fix for most of the 24; (b) add runtime validation at the adapter boundary
(zod/valibot, or a hand-rolled `requireNumber(field)`) that throws on a missing field
instead of yielding 0, so a drift surfaces as a rendered error state rather than a plausible
zero. Add a CI assertion that every path passed to `apiGet` appears as a key in `paths`.

---

## FE-07 — High · Single error boundary for the whole desk

**File:** `frontend/src/main.tsx:36-42`.

### Evidence

```tsx
<ErrorBoundary label="desk">
  <DataProvider>
    <VoldeskApp />
  </DataProvider>
</ErrorBoundary>
```

That is the only boundary on the desk root. `VoldeskApp.tsx:315-321` renders the seven views
as bare conditionals with no wrapping. A throw anywhere — a malformed payload reaching a
`.toFixed()`, an unexpected `null` in a chart's geometry pass — replaces the entire
application, top bar and rail included, with the fallback box. The user loses the ticker,
the navigation, and every other view's data to a fault in one panel.

The boundary itself is well built (401-aware, retry button, component stack to console), and
`pages/DevLayout.tsx:98` already comments on doing this per-tab for the dev console — the
pattern exists, it just was not applied to the desk.

Also note that the boundary catches **render** errors only. Adapter/fetch rejections are
handled by `useFetch`'s catch (which is good — it keeps last-known data and flags stale),
but that path logs nothing and renders no distinguishable error state: a persistent 500 on
`/portfolio/var` is visually identical to slightly-old data.

### Recommended fix (not applied)

Wrap each of the seven route branches in `<ErrorBoundary label="<view>">` in
`VoldeskApp.tsx`, keeping the root boundary as the last resort. Separately, distinguish
`status: "stale"`-because-of-error from `stale`-because-of-age in `Fresh<T>` (a third
`error` state, or an `error: unknown` field) so a failing endpoint renders as an error
rather than as age.

---

## FE-08 — High · Production source maps are built and publicly served

**Files:** `frontend/vite.config.ts:36-40`, `infrastructure/nginx/frontend.conf:11-15`.

### Evidence

```ts
build: { outDir: "dist", sourcemap: true, target: "es2022" },
```

The build I ran emits them, and they are large:

```
dist/assets/index-DLbwaD99.js       329.32 kB │ gzip: 100.44 kB │ map: 1,032.48 kB
dist/assets/DevLayout-Bvy90yWW.js 1,312.17 kB │ gzip: 438.59 kB │ map: 3,255.58 kB
```

`ls dist/assets` confirms `index-*.js.map` and `DevLayout-*.js.map` on disk. `web.Dockerfile`
does `COPY --from=build /app/dist /usr/share/nginx/html` with no exclusion, and
`frontend.conf`'s `location /assets/ { try_files $uri =404; }` serves any file that exists —
so `https://valeriandarmente.dev/fx-volatility-trading-system/assets/index-<hash>.js.map`
returns the complete original TypeScript of the desk *and* of the lazily-loaded dev console
(DB explorer, Redis inspector, log viewer, pipeline topology, migration list).

The audit asks for this to be a *deliberate* decision. There is no comment, no CI step, and
no nginx rule addressing it, so it is currently accidental. No secrets are in the maps —
`import.meta.env` usage is limited to `BASE_URL`, `VITE_API_BASE_URL`, `VITE_WS_BASE_URL`
and `VITE_WRITE_ENABLED`, none of which carry credentials, and no `VITE_*` value is set
anywhere in CI or infra. The exposure is source disclosure, not credential disclosure.

### Recommended fix (not applied)

Pick one and document it: either `sourcemap: false` for prod (`sourcemap: 'hidden'` if you
want them for error reporting without the `//# sourceMappingURL` comment), or keep them and
add `location ~ \.map$ { return 404; }` to `frontend.conf` while shipping the maps to an
error tracker out-of-band. Given the dev console's maps expose the internal diagnostic
surface, the 404 rule is the minimum.

---

## FE-09 — Medium · Two opposite VaR sign conventions, reconciled by two hard-coded flips

**Files:** `frontend/src/voldesk/views/RiskView.tsx:148-153` vs `:430-431`,
`frontend/src/voldesk/data/live/portfolio.ts:205,598-605`.
**Backend:** `src/api/routers/portfolio_panel.py` `_var_stats` vs
`src/core/risk/marginal_var.py` `_loss_var`.

### Evidence

The two endpoints feeding adjacent panels in the same view use opposite conventions, and
both say so in their own docstrings:

```python
# _var_stats — "Values are losses (negative)."
# _loss_var  — "Historical VaR as a positive USD loss at the given confidence."
```

Neither adapter normalises (`adaptVar` just divides by 1000; `adaptMarginalVar` just reads
the numbers). Instead each *formatter* hard-codes the opposite flip:

```ts
// RiskView.tsx:148-153 — VaR card, expects negative-as-loss
const kc = (vk) => { const s = vk < 0 ? "−" : "+"; ... };
// RiskView.tsx:430-431 — Marginal-VaR panel, expects positive-as-loss
const money = (v) => (v >= 0 ? "-$" : "+$") + m;
const compCls = (v) => "r mono " + (v >= 0 ? "neg" : "pos");
```

Both currently render correctly. But the convention is pinned nowhere — not in a type, not
in an adapter, not in a test — and `EmpiricalHist` already contains an explicit admission
that it is untrusted:

```ts
// RiskView.tsx:71-75 — "pin them to the negative (left) side as −|value|,
// whatever sign the backend sends"
const l95 = -Math.abs(var95), l99 = -Math.abs(var99), les = -Math.abs(es99);
```

If `/portfolio/var` is ever normalised to the more conventional positive-loss form (matching
`marginal-var`), the VaR table starts printing `+$12k` for a loss while its cell keeps the
hardcoded `className="neg"` red — a green-reading sign in a red cell, with the histogram
unchanged because of the `-Math.abs()` hack. Nothing would fail.

### Recommended fix (not applied)

Normalise at the adapter, not the formatter: have `adaptVar` and `adaptMarginalVar` both
emit losses in one documented convention (positive-magnitude loss is the clearer choice) and
delete the sign flips from `kc`/`money`/`compCls`. Add a unit test per adapter that feeds a
known backend payload and asserts the rendered sign. Longer term, unify the two backends.

---

## FE-10 — Medium · VaR table header says "ES 97.5%"; the value is ES 99%

**File:** `frontend/src/voldesk/views/RiskView.tsx:177` (and the ratio at `:146,192`).

### Evidence

```tsx
<th className="r">ES 97.5%</th>
```

The value in that column is `c.es`, which traces to `VarData.es99` ←
`adaptVar → v.es_99_usd` ← `_var_stats`, whose docstring reads *"ES 99 (mean of the losses
at or below the 99% quantile)"*. The `/var` route docstring likewise says "VaR 95 / 99 +
ES 99". Every layer except the column header agrees on 99%.

97.5% is not an arbitrary typo — it is the Basel FRTB expected-shortfall level, so a reader
will take the header at face value and compare against 97.5%-calibrated intuition. The
"ES/VaR" ratio column (`:146`, `es99 / var99`, amber above 1.25) is affected too: a
97.5%-ES over a 99%-VaR would be a genuinely different diagnostic from the 99%/99% ratio
actually computed.

### Recommended fix (not applied)

Change the header to `ES 99%`, or (if 97.5% is the intended desk standard) compute ES at
97.5% server-side and rename the field through `es_99_usd` → `es_975_usd` → `VarData.es`.
Do not leave the label and the number disagreeing.

---

## FE-11 — Medium · Dashboard Portfolio and Risk cards carry no freshness indicator

**File:** `frontend/src/voldesk/views/DashboardView.tsx:252-427`.

### Evidence

`DashboardView` defines a perfectly good `LiveBadge` (`:27-38`) and then uses it exactly
once, on the Macro-events sub-panel (`:225`). Counting the four summary cards:

| Card | Contents | Freshness indicator |
|---|---|---|
| Trade | ticker chart, macro events | market dot (see FE-03) + `LiveBadge` on macro only |
| Portfolio | net liq, init/maint margin, gross/net leverage, holdings donut, per-window performance | **none** |
| Risk | net Δ/Γ/vega/θ, VaR 95 across four horizons | **none** |
| Signal | three PCA mode cards | none (only a `df-degraded` CSS class when term structure is missing) |

The Dashboard is the landing route (`VoldeskApp.tsx:241`), so the first screen a trader sees
presents net liquidation, margin utilisation, portfolio greeks and VaR with no indication of
whether any of it is current. Compounded by FE-02, even adding the badge would show `live`
— but its absence means there is not even a hook to fix.

### Recommended fix (not applied)

Add `right={<LiveBadge status={portfolio.status} />}` to the Portfolio card,
`risk.status`/`trade.status` to the Risk card, and `pca.status` to the Signal card. Once
FE-01/FE-02 land, render the age alongside.

---

## FE-12 — Medium · System view fabricates green statuses; a missing heartbeat age becomes `0s`

**File:** `frontend/src/voldesk/data/live/system.ts:56-105`,
`frontend/src/voldesk/views/SystemView.tsx:30-74`.

### Evidence

`stackLayers` hardcodes three components as healthy regardless of any measurement:

```ts
{ layer: "EDGE", items: [{ name: "nginx", status: "up", meta: "reverse proxy · TLS" }] },
// APP: { name: "web (Vite)", status: "up", ... }
{ layer: "OBS", items: [{ name: "AWS SSM/KMS", status: "up", meta: "secrets · encrypted" }] },
```

`nginx` and `web` are arguably self-evident (you are being served), but `AWS SSM/KMS: up`
is a green dot on a component the frontend has no signal about whatsoever. On a page titled
"Container stack" next to genuinely-probed postgres/redis/engine dots, it reads as a
measurement.

Second issue, in `engineRows`:

```ts
const hb = typeof e.hb_age_s === "number" ? e.hb_age_s : 0;
```

A missing or renamed `hb_age_s` yields `0`, and the view renders
`width: (e.hb / e.stale) * 100 + "%"` (`SystemView.tsx:64`) — a 0 %-full green bar reading
`0s / 30s`, i.e. the *healthiest possible* display. The health-probe fallback path is worse:
`{hb: 0, stale: 1}` (`:74`), which prints `0s / 1s`. This is the QM-02 pattern again — the
absence of a signal renders as the best possible signal.

`mapStatus` is correctly conservative in the other direction (unknown → `"down"`,
`:20-24`), which makes the `hb` handling look like an oversight rather than a policy.

The "Database schema" panel is honestly labelled `(mock)` (`SystemView.tsx:75`) — good, and
the model the other two panels should follow.

### Recommended fix (not applied)

Drop the three fabricated `up` literals or mark them `unknown`/`n/a` with a neutral dot.
Make `hb` nullable and render `—` with a neutral bar when the age is absent, rather than 0.

---

## FE-13 — Medium · Test fixtures encode a retired schema; WS lifecycle is untested; e2e is a boot smoke

**Files:** `frontend/src/tests/mocks/handlers.ts:35-47`, `frontend/e2e/dashboard.spec.ts`,
`frontend/e2e/fixtures.ts:6-30`.

### Evidence

The MSW `/vol/surface` handler returns the **retired flat schema**:

```ts
HttpResponse.json({ symbol, snapshot_ts, tenors: ["1W","1M"], strikes: [...], vols: [[...]] })
```

while `adaptSurface` reads `resp.surface[tenor][delta].iv` — the nested shape the backend
actually serves. Fed that fixture, the adapter returns an all-`NaN` grid. This is precisely
the producer/consumer drift QM-02 flagged in `core/pricing`, reproduced in the test harness,
where it also guarantees the drift *cannot* be caught by the suite.

`e2e/fixtures.ts:6-30` looks similarly aged (`instrument_type`, string `quantity`,
`status: "OPEN"`), though those routes are no longer consumed by the desk.

Coverage gaps in the same file: no handler exists for `/portfolio/marginal-var`,
`/portfolio/greeks-ladder`, `/positions/structured`, `/trade/submitted`,
`/portfolio/trade-markers`, or `/bars` — all consumed by rendered panels. Under MSW these
404, the adapters swallow it, and the panels render empty without failing anything.

**No WebSocket handlers exist at all.** The `npm test` run emits, per test:

```
[MSW] Error: intercepted a WebSocket connection without a matching event handler:
  • ws://localhost:3000/ws/ticks   (and /ws/vol, /ws/risk)
```

So reconnect, backoff, resubscribe, the throttle-buffer status race (FE-04) and the
out-of-order/stale-message paths have zero test coverage.

The e2e suite is one spec asserting the banner renders. Its own comment explains why (the
previous specs targeted a retired layout), and a boot smoke is a legitimate stopgap — but it
means nothing in CI exercises disconnect/reconnect or stale labelling.

### Recommended fix (not applied)

Regenerate the `/vol/surface` fixture from a real backend response (a small script pinned to
`dump_openapi.py`'s app instance would keep it honest). Add handlers for the six missing
endpoints. Add MSW WebSocket handlers and a hook-level test for `useWebSocket`: open →
message → server close → assert backoff schedule → reopen → assert `attempt` reset. Add a
`useFetch` fake-timer test per FE-02.

---

## FE-14 — Medium · Focus can be invisible (a11y)

**Files:** `frontend/src/voldesk/voldesk.css:307,613,1475`; `frontend/src/theme.css`.

Three rules remove the focus ring, and neither stylesheet defines `:focus-visible` or any
global focus style:

```css
.field select:focus, .field input:focus { outline:none; border-color:var(--accent) }
.cfg-val-input:focus                    { outline:none; border-color:var(--accent,#6fd) }
.auth-modal input:focus                 { outline:none; border-color:var(--accent) }
```

The replacement is a 1 px border *hue* change with no thickness change. On `.auth-modal
input` the unfocused border is `--border #262a33`, which is **1.2:1 against the surface** —
effectively invisible — so the focus state is a change from an invisible border to a visible
one, which fails WCAG 2.4.13 focus-appearance sizing. Buttons keep the UA default ring
(`voldesk.css:19` resets `border`/`background`/`color` but not `outline`), so the rail and
chips are fine; the affected controls are the Settings config inputs, the OrderBuilder
`.field` selects/inputs, and the login form.

The audit file rates a11y Low "except blockers like invisible focus" — this is the
exception, hence Medium.

**Fix:** add a global `:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px }`
and delete the three `outline:none` rules (or pair them with a `box-shadow` ring). Raise
`--border` to at least 3:1 against `--surface` while you are there (WCAG 1.4.11 for input
boundaries).

---

## FE-15 — Medium · Login modal has no dialog semantics (a11y)

**File:** `frontend/src/voldesk/auth/LoginModal.tsx:25-32`, rendered at
`VoldeskApp.tsx:325`.

No `role="dialog"`, no `aria-modal="true"`, no `aria-labelledby` pointing at the `<h2>Sign
in</h2>` (`:32`). No Escape handler (grep: no `onKeyDown` in the file). No initial focus —
focus stays on the now-obscured topbar `auth-btn`. No focus trap: Tab walks straight out into
the rail behind the overlay. No focus restore on close. Rendered inline rather than through a
portal, so the background stays in the accessibility tree. The overlay
`<div onClick={onClose}>` at `:25` is a mouse-only dismissal.

Correctly done: the error region carries `role="alert"` (`:54`), and both fields use
wrapping `<label>` (`:34-52`).

This is the one modal in the app, and it gates every write action.

**Fix:** `role="dialog" aria-modal="true" aria-labelledby="login-title"` on the form,
`autoFocus` on the username field, an Escape listener, a minimal focus trap (or the native
`<dialog>` element with `showModal()`, which gives trap + Escape + inert background for
free), and focus restore to the invoking button on close.

---

## FE-16 — Medium · `--text-faint` fails contrast and carries sole-content text (a11y)

**File:** `frontend/src/voldesk/voldesk.css:4` (`--text-faint: #5b6170`), duplicated in
`theme.css`.

Computed WCAG contrast against the four surfaces in use:

| against | ratio |
|---|---|
| `--bg #0f1115` | 3.05:1 |
| `--bg-2 #14171d` | 2.90:1 |
| `--surface #181b22` | 2.78:1 |
| `--bg-3 #1e222b` | 2.57:1 |

It never reaches the 4.5:1 AA threshold and drops **below 3:1** on three of four surfaces.
It is applied to 8.5–11.5 px text, so the large-text 3:1 exemption does not apply. It is
used for ~12 CSS classes plus SVG `fill` in six chart components. The worst cases are the
two empty-state messages that are the *entire content* of their panel at ~2.8:1:
`RiskView.tsx:67` ("distribution accumulating…") and `SignalsView.tsx:212`
("z-history accumulating…").

By contrast `--text-dim`/`--muted` (`#8a90a0`) passes AA everywhere at ≥5.0:1, and all four
semantic colours (`--pos`/`--neg`/`--warn`/`--accent`) pass as text. Only `--text-faint` and
`--border` fail.

**Fix:** lighten `--text-faint` to ≈`#7a808f` (≈4.6:1 on `--surface`), and promote the two
empty-state messages to `--text-dim`.

---

## FE-17 — Medium · WS connection cap vs per-tab socket count

**File:** `infrastructure/nginx/nginx.conf:82-84`.

```nginx
location /fx-volatility-trading-system/ws/ {
    limit_req  zone=ws_rl burst=10 nodelay;
    limit_conn ws_conn 10;
```

The desk opens **three** WebSockets per tab (`/ws/ticks`, `/ws/vol`, `/ws/risk`, all mounted
unconditionally in `DataProvider`). Four tabs from one client key exhausts the cap; the
fourth tab's sockets are refused, `useWebSocket` enters its 1→15 s backoff loop, and — per
FE-04 — the UI shows nothing at all. Compounded by FE-02, that tab's panels stay green
`live` while receiving no data.

**Fix:** either raise `limit_conn` to comfortably exceed 3 × expected tabs, or (better)
multiplex the three streams onto one socket with a channel field, which also reduces the
`limit_req` pressure. Independently, FE-04's connection indicator would make the failure
visible.

---

## FE-18 — Medium · The provider `useMemo` never hits

**File:** `frontend/src/voldesk/data/provider.tsx:240-253`, `hooks/useFetch.ts:69`.

`useFetch` returns `{ ...state, reload }` — a fresh object literal on every render. The
provider's `useMemo` deps are exactly those objects:

```ts
const value = useMemo<DeskData>(() => ({...}),
  [liveTerm, surface, livePca, liveSystem, liveConfig, liveTrade, reloadTrade, livePortfolio, liveRisk]);
```

Every dep changes identity on every `DataProvider` render, so the memo recomputes every time
and `DeskDataContext`'s value identity always changes — re-rendering **every** `useDeskData()`
consumer, i.e. all seven views' panels. The comment above it ("keeps `useDeskData()`
consumers from re-rendering on every tick") describes an outcome the code does not achieve.

Practical severity is bounded, and this is worth crediting: the high-frequency tick stream
was correctly split into its own `TicksContext` (`provider.tsx:266-269`), and the vol/risk
beats are throttled to 30 s / 15 s (`streams.ts:38,41`), so `DataProvider` re-renders about
every 15 s rather than 5×/s. The `IVSurfaceZ`, `ModeCard` and `FairVolGate` `memo()`
wrappers are individually defeated by the same identity churn, but their `useMemo` inner
caches still hold. Plotly is not in the desk bundle at all, so there is no plotly redraw
cost on the trader-facing path.

**Fix:** memoise `useFetch`'s return (`useMemo(() => ({...state, reload}), [state, reload])`)
so a slice keeps its identity when nothing changed. That single change makes the provider
memo and all three `memo()` boundaries effective.

---

## Low findings (evidence in brief)

- **FE-19** `hooks/streams.ts:22-27` — `TickMsg` declares `symbol?` and nothing filters on
  it. `/ws/ticks` is a flat channel (`bus/channels.py:14`, "subscribers interested in a
  specific symbol filter on the payload"). Single-symbol today
  (`MarketDataEngine(symbol=settings.MARKET_SYMBOL)`), so latent — but a second pair would
  silently drive the EURUSD ticker. *Fix: filter on `symbol === "EURUSD"` in `useTicks`.*
- **FE-20** `VoldeskApp.tsx:285` — `dir: liveMid !== null && prevMid !== null && liveMid >= prevMid ? 1 : -1`.
  On the first tick `prevMid` is `null`, so `dir = -1` and the price renders red with a ▼
  regardless of direction. *Fix: a third `dir = 0` neutral state.*
- **FE-21** `package.json:22` — `gen:api:check` hardcodes `/tmp/schema.d.ts` and POSIX
  `diff`; it also requires a live `localhost:8000`. It cannot run on the Windows dev machine,
  so the drift gate is CI-only. CI's own version (`ci.yml:128-132`) is correct. *Fix: make
  the npm script call `dump_openapi.py` and use a cross-platform diff.*
- **FE-22** a11y, aggregated: 11 data-bearing `<svg>` roots with no `role="img"`/`<title>`
  (`DashboardView:75`, `SignalsView:152,229`, `PortfolioView:245,346,378,611,1031`,
  `RiskView:87`, `TickerChart:177`); 7 `<tr onClick>` (all but one backed by a real caret
  `<button>`, so keyboard-reachable — `PortfolioView:557,821`, `RiskView:183,390,491`,
  `PositionsTable:375`, `PositionBreakdown:118`); 4 caret buttons with no `aria-label`
  (`PositionsTable:380` shows the correct pattern); `TickerChart:234` macro-event dots are
  hover-only with no keyboard/SR path; `VoldeskApp.tsx:149` pair `<select>` has no label;
  `SettingsView.tsx:109,178` inputs are labelled only by a sibling `<span>`; h1→h3 skip on
  every page (`VoldeskApp:310` then `common.tsx:34`); `<section className="panel">` has no
  accessible name; no skip-to-content link. Landmarks (`header`/`nav`/`main`) are correct.
- **FE-23** `DashboardView.tsx:190` — `chartEvents` uses `useFetch(..., 300_000)` with no
  poll, and `adaptEvents` bakes the relative countdown (`in: "3h"`) at fetch time
  (`live/trade.ts:226`). Left open, the Dashboard's event countdowns drift arbitrarily far
  from reality. *Fix: recompute `in` at render from the stored ISO `date`.*
- **FE-24** `live/trade.ts:150-170` — `adaptLimits` falls back to `core.ts`'s hardcoded
  prototype caps (`gamma 20400`, `vega 48000`, `vanna 260`, `var99 420`) when the backend
  configures no value. Currently harmless: `TradeData.limits` is populated but rendered by no
  view (verified by grep). Flagged so it is not wired up as-is. *Fix: make absent caps
  `null` and render "no cap configured".*
- **FE-25** TS hygiene, recorded as a positive with one caveat: **zero** `any`, **zero**
  `@ts-expect-error`/`@ts-ignore`, 8 `eslint-disable` (all `react-hooks/exhaustive-deps`, all
  in dev pages or OrderBuilder). The only escape hatch in volume is **141 non-null
  assertions**, most of them array indexing forced by `noUncheckedIndexedAccess` — an
  acceptable trade, but they are unchecked runtime assumptions in chart geometry code. The
  `as` casts cluster in the adapters (`live/portfolio.ts` 17, `live/trade.ts` 5) and are the
  same `unknown`-shape casts as FE-06.
- **FE-26** `RiskView.tsx:166-168` — the empirical histogram bins are √t-scaled alongside the
  quantiles (`{lo: b.lo * selM, hi: b.hi * selM}`), so the "Yearly" view presents a 1-day
  empirical distribution stretched ×15.9 as if it were a yearly one. Defensible as a
  convention, but unlabelled. *Fix: annotate the chart with "1d empirical, √t-scaled".*

---

## Bundle size

Measured from a clean `npm run build` (vite 5.4.21, 116 modules, 4.26 s).

| Chunk | Raw | Gzip | Source map | Loaded by |
|---|---|---|---|---|
| `index.html` | 0.47 kB | 0.30 kB | — | all |
| `assets/index-*.css` | 108.59 kB | **18.99 kB** | — | all |
| `assets/index-*.js` | 329.32 kB | **100.44 kB** | 1,032.48 kB | all (desk) |
| `assets/DevLayout-*.js` | 1,312.17 kB | 438.59 kB | 3,255.58 kB | `/dev` only (lazy) |
| **Desk initial load** | **438.4 kB** | **≈119.7 kB** | — | — |

**Assessment: good, and the heavyweight is correctly isolated.** The audit flags plotly as
the known risk; it is imported only by `components/charts/PlotlyChart.tsx`, whose only
consumer is `pages/dev/Hardware.tsx` — inside the `lazy()` `DevLayout` (`main.tsx:10-12`).
`plotly.js-basic-dist-min`, `react-plotly.js` and `@dagrejs/dagre` therefore land entirely in
the 1.31 MB dev chunk and never reach a trader's browser. Every trader-facing chart is
hand-rolled SVG (`SignalsView`, `RiskView`, `PortfolioView`, `TickerChart`), which is why the
desk chunk stays at ~100 kB gzip.

Two observations, neither a finding:

- ~120 kB gzip initial load over the subpath is comfortably inside any reasonable budget;
  no per-view code splitting is needed at this size. The 500 kB rollup warning refers solely
  to the dev chunk.
- The CSS is a single 108 kB `voldesk.css` + `theme.css` bundle loaded on every route
  including `/dev`. Splitting it is not worth doing at 19 kB gzip.

**Subpath deployment (`/fx-volatility-trading-system`): verified correct by inspection.**
`vite.config.ts` sets `base`, so asset URLs emit as `/fx-volatility-trading-system/assets/…`.
The outer nginx `location /fx-volatility-trading-system/ { proxy_pass http://frontend_upstream/; }`
strips the prefix, the inner `frontend.conf` serves `/assets/…` and falls back to
`index.html` for everything else — so deep links and refresh work at any depth.
`main.tsx:21-23` strips `BASE_URL` before path routing (`/dev` detection), `client.ts:6-7`
and `useWebSocket.ts:13-20` both derive their prefix from `BASE_URL`, and
`VoldeskApp.tsx:206` builds the dev link base-aware. `location = /fx-volatility-trading-system`
301s to the trailing-slash form. No hardcoded absolute path was found anywhere in `src/`.

---

## Verified clean (worth recording)

- `npm run lint` (`--max-warnings 0`): **pass**. `npm run typecheck`: **pass**.
  `npm test`: **64/64 across 7 files**. Production build: **succeeds**.
- Full TS strict + `noUncheckedIndexedAccess` + `exactOptionalPropertyTypes` +
  `noImplicitReturns` + `noUnusedLocals`/`Parameters`. Zero `any`, zero `@ts-expect-error`.
- **No token in JS storage.** Auth is the httpOnly `fxvol_auth` cookie; `authStore.ts` holds
  only a boolean. `credentials: "include"` is set centrally in `client.ts:49,59`. No
  `localStorage`/`sessionStorage` usage anywhere in `src/`. Confirms Wave 1's finding.
- **Write-gate defense-in-depth is correct.** All 8 `canWrite` sites compute
  `useAuthStore(s => s.authenticated) || WRITE_ENABLED`. `VITE_WRITE_ENABLED` is set nowhere
  in CI, compose, or the Dockerfile, so it is `false` in every production build and the gate
  reduces to real authentication — the build flag cannot accidentally unlock prod. (Server-side
  enforcement is Audit 1's; it verified all 115 routes.)
- **No secret in the bundle or in `import.meta.env`.** Only `BASE_URL`,
  `VITE_API_BASE_URL`, `VITE_WS_BASE_URL`, `VITE_WRITE_ENABLED` are read; none is set to a
  credential anywhere in the repo.
- **WS reconnect logic is sound where it exists**: exponential backoff 1 s → 15 s cap,
  `attempt` reset on open, `stopped` guard, timer + socket cleanup on unmount
  (`useWebSocket.ts:75-107`). The gap is surfacing it (FE-04), not the mechanism.
- **No REST/WS race producing last-write-wins on stale data.** `useFetch` guards with both
  an `AbortController` and a `cancelled` flag (`:37-59`), and every `reload()` bumps `tick`,
  which runs the cleanup and sets `cancelled = true` before the new fetch — so a superseded
  response can never call `setState`. The WS beats are used purely as invalidation triggers
  and their payloads are never rendered (`provider.tsx:189-214`), which sidesteps
  out-of-order rendering entirely. This is a deliberate and well-documented design choice.
- **No unbounded store growth.** There is one zustand store (`authStore`, three booleans, no
  `persist`). `useWebSocket` keeps only `last`. The one buffered stream, `useWsLog`, is FIFO-
  capped at `max = 50` (`useWsLog.ts:74-77`) and its rate ring buffer is time-windowed to 5 s.
  No tick history is accumulated anywhere.
- The `nn()` / null-preserving discipline in `live/portfolio.ts` (VaR, attribution rows,
  hit-rate `hitRateNull`) is genuinely careful work — an absent value renders `—`, not `$0`.
- `EmpiricalHist` and `VarCard` correctly refuse to draw a distribution during the VaR
  warm-up (`RiskView.tsx:64-69,141-142`) rather than plotting a 2-sample histogram with every
  mark pinned to zero.
- `SystemView`'s ER-diagram panel is honestly labelled `(mock)`.
- `api/client.ts:62-76` `apiFetchJson` exists specifically to stop callers `.json()`-ing an
  error body — a past-bug fix with the rationale documented in place.

---

## Needs live verification by Valérian

Playwright e2e was **not** run (it needs the compose stack, which is out of scope for this
audit). These are the checks that require a running stack; each maps to a finding above.

1. **Markets-closed labelling (FE-01).** On a Saturday, open `#signals`. Confirm the IV
   surface renders a full grid with a green `live` badge, and read the DB
   (`SELECT timestamp FROM vol_surface_history ORDER BY timestamp DESC LIMIT 1` vs the row
   the API served) to confirm the grid is Friday's. Expected result: badge says `live`,
   grid is ≥ 12 h old.
2. **Staleness never ages (FE-02).** Open `#risk`, note the time, leave the tab untouched for
   10 minutes without navigating. Confirm the Stress test, Greeks ladder, Expiries & roll-off
   and Marginal-VaR panels still show `● live` with unchanged numbers (their `warnMs` is
   120 s / 30 s, so they should long since have flipped to `stale`).
3. **"Market open" out of hours (FE-03).** On a Sunday, confirm the top bar shows a green dot
   and "Market open" with a five-decimal mid. Cross-check the mid against Friday's close.
   Then `docker compose logs market-data | tail` to confirm ticks are still publishing at
   ~5/s with an unchanged price.
4. **Silent disconnect (FE-04).** With the desk open, `docker compose stop api`. Expected
   per this audit: no visible change anywhere for ≥ 10 s (ticks) / 90 s (risk) / 240 s (vol);
   the top bar keeps "Market open" until the tick badge ages out on the next re-render;
   `useFetch`-backed panels stay green until their next reload attempt fails. Then
   `docker compose start api` and confirm all three sockets reconnect and every panel
   resumes without a page reload — this is the reconnect/resubscribe leg the audit's exit
   criteria require, and it is the one thing I could not verify statically.
5. **Half-open socket (FE-04, watchdog).** Harder: `docker compose pause api` (rather than
   stop) so the TCP connection is not cleanly closed. Confirm whether `onclose` ever fires,
   or whether the desk freezes silently and permanently. This is the case with no reconnect
   path at all.
6. **Mock spot fallback (FE-05).** With the stack up but `market-data` stopped, hard-reload
   the desk and open `#trade`. Confirm the Indicators panel shows `1.08410 / 1.08430` and
   the Order builder pre-fills a strike near 1.085 against the real ~1.14 market. Do **not**
   submit.
7. **WS connection cap (FE-17).** Open five browser tabs on the desk simultaneously. Confirm
   whether the later tabs' sockets are refused (`nginx` error log: `limiting connections by
   zone "ws_conn"`) and whether anything in those tabs indicates it.
8. **Source maps (FE-08).** From outside the network, `curl -sI` the deployed
   `…/assets/index-<hash>.js.map` and confirm it returns 200 with the full source. (I did not
   hit the live deployment.)
9. **Focus visibility (FE-14).** Tab through the Settings config inputs and the login modal
   and confirm whether the focused control is identifiable without a mouse.
10. **Subpath deep link (verified statically, worth one live check).** Load
    `https://valeriandarmente.dev/fx-volatility-trading-system/dev/logs` directly (not via
    in-app navigation) and hard-refresh on it, to confirm the SPA fallback + `BASE_URL` path
    stripping behave as the config implies.

---

## Suggested issue grouping

- **Blockers, one issue each:** FE-01, FE-02, FE-03. FE-01 and FE-02 share a root cause
  (the freshness contract has no clock and no server timestamp) and should probably be fixed
  in one PR, but tracked separately since FE-01 is the audit's named exit criterion.
- **High, one issue each:** FE-04, FE-05, FE-06, FE-07, FE-08.
- **Medium, grouped:** *data truthfulness* (FE-09, FE-10, FE-11, FE-12) · *test & contract
  coverage* (FE-13, with FE-21) · *accessibility* (FE-14, FE-15, FE-16, with FE-22) ·
  *infrastructure & perf* (FE-17, FE-18).
- **Low, one grouped issue:** FE-19, FE-20, FE-23, FE-24, FE-25, FE-26.

### Contribution to the Audit 5 channel-contract table

WS payload types are hand-written and only one of the three is a real type:

| Channel | Publisher | Frontend type | Match |
|---|---|---|---|
| `ticks` | `bus/publisher.py::publish_tick` → `{symbol, bid, ask, mid, timestamp}` | `hooks/streams.ts:22` `TickMsg {symbol?, mid?, bid?, ask?}` | **partial** — `timestamp` missing from the type and unread (see FE-03) |
| `vol_update` | `bus/publisher.py::publish_vol_update` | `useVolStream(): Fresh<unknown>` | n/a — payload never read, used only as a REST-invalidation trigger |
| `risk_update` | `bus/publisher.py::publish_risk_update` | `useRiskStream(): Fresh<unknown>` | n/a — same, and deliberately so (payload is not USD-scaled; see `provider.tsx:202-205`) |

`account`, `system_alerts`, `positions`, `exit_alerts` and `orders:*` are forwarded by the
bridge (`api/ws/redis_bridge.py:20-31`) and exposed as endpoints (`routers/ws.py:56-72`) but
**no frontend code subscribes to them** — `/ws/positions`, `/ws/orders/{id}` and
`/ws/exit_alerts` have no client. Worth noting in the Audit 5 table as unused surface.
