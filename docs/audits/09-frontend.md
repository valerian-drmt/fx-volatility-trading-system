# Audit 9 — Frontend

## Why

The voldesk UI is the only window into a system that mostly runs unattended. The
dangerous frontend failure isn't a crash — it's *plausible-looking wrong data*: a stale
surface rendered as live, a WS silently disconnected while panels keep their last
values, a sign flipped in a risk tile. The public read-only deployment also makes the
bundle itself part of the attack/leak surface.

## Scope

- `frontend/src/` — 7 voldesk views, `hooks/`, `store/` (zustand), `api/` typed client +
  generated `schema.d.ts`, WS client code.
- `frontend/e2e/` playwright specs; vite build config; `web.Dockerfile` + frontend nginx.

## What to check

### Data truthfulness (the core concern)
- [ ] Staleness surfaced: every live panel shows data age / connection state; the
      markets-closed "last full grid" path (Audit 5) is visibly labeled in the UI.
- [ ] WS lifecycle: reconnect with backoff, resubscribe after reconnect, explicit UI
      state for connecting/disconnected — panels must not freeze silently.
- [ ] Race conditions: out-of-order WS messages vs REST hydration (last-write-wins on
      an older tick?); store updates keyed by timestamp where it matters.
- [ ] Number formatting: units (%, vol pts, bp), sign conventions, rounding consistent
      with backend definitions — spot-check against API payloads.

### Contract & state
- [ ] `gen:api:check` drift gate covers all consumed endpoints; WS payloads (not in
      OpenAPI) have hand-written types that match publisher schemas (tie into the
      Audit 5 channel contract table).
- [ ] zustand stores: no unbounded growth (tick buffers capped), selectors don't force
      full-app re-renders on every tick (plotly re-draw cost).
- [ ] Error states: every fetch/WS failure has a rendered state (not console-only);
      error boundaries around each view.

### Auth & bundle hygiene
- [ ] No secrets/tokens in the bundle or in `import.meta.env` leakage; auth token
      storage choice (memory vs localStorage) deliberate; write-action UI hidden *and*
      calls still server-rejected when unauthenticated (defense in depth — server side
      is Audit 1's).
- [ ] Source maps: decide exposed-or-not for prod, deliberately.

### Quality & performance
- [ ] TS strict escapes: count of `any` / `as` casts / `@ts-expect-error` in data paths.
- [ ] Bundle size: plotly is the known heavyweight — code-splitting per view; initial
      load budget on the subpath deployment.
- [ ] Subpath correctness (`/fx-volatility-trading-system`): deep links, refresh, asset
      URLs all work behind nginx.
- [ ] Basic a11y sweep on the 7 views (labels, contrast, keyboard focus) — desk tool,
      not public product, so Low severity except blockers like invisible focus.

## Method

Code review + vitest/MSW gap check + a local playwright session against the compose
stack exercising disconnect/reconnect (stop api container mid-session) and stale-data
labeling.

## Deliverable & exit criteria

- Findings report; WS-payload type ↔ publisher contract check added to the Audit 5 table.
- **Exit:** no silent-stale scenario reproducible; reconnect works; drift gates cover
  all consumed contracts; zero Blocker/High open.