# Surface tenor pillars — change spec (1M·2M·3M·4M·5M·6M → 1M·2M·3M·6M·9M·1Y)

> Status: **PLAN** (not yet implemented). Drives the Signal-tab front+back change.
> Owner decision (2026‑06‑25): show the standard FX‑vol pillars **1M, 2M, 3M, 6M, 9M, 1Y**
> instead of the dense monthly strip 1M…6M.

## 1. Why & the core principle

CME EUR FX options are listed at **discrete** dates: weekly + serial monthlies near‑term
(~5 months out), then **quarterlies** (Mar/Jun/Sep/Dec). There is **no contract at every
round tenor** — e.g. today the 6‑month point falls in the gap between the Dec serial
(~162d) and the Mar quarterly (~254d). So we cannot "fetch a 6M option"; we fetch what's
listed and **interpolate** the standard pillars for display.

Two distinct tenor sets — keep them separate everywhere:

| Concept | Set | Used by | Nature |
|---|---|---|---|
| **DISPLAY_PILLARS** | `1M, 2M, 3M, 6M, 9M, 1Y` | Signal IV surface, term curve, PCA grid | continuous — interpolated from listed anchors, each cell flagged `listed` vs `interp` |
| **TRADEABLE_TENORS** | the listed expiries the engine actually qualified | OrderBuilder, `/trade/preview`, submit | discrete — only what you can actually buy |

**Hard rule:** an interpolated pillar (e.g. 6M) is a value to *read*, never to *trade*.
The OrderBuilder must offer only TRADEABLE_TENORS.

## 2. Interpolation spec (the "estimate underneath")

- Anchors = qualified listed expiries (DTE, per‑delta IV) from the vol‑engine scan.
- For each DISPLAY_PILLAR at target DTE `T`:
  - if an anchor sits within ±10d of `T` → mark `listed`, use it directly;
  - else interpolate each delta pillar's **variance** linearly in **√time** between the two
    bracketing anchors → mark `interp`;
  - if `T` is beyond the furthest liquid anchor → **drop the pillar** (no extrapolation).
- Carry a per‑cell `source: "listed" | "interp"` flag through the payload to the frontend.
- Long‑end honesty: 1Y shows only if the Jun quarterly (~11M) is liquid; else the surface
  stops at 9M. Never fabricate / extrapolate past the last anchor.

## 3. Backend changes

| File | Change |
|---|---|
| `src/engines/vol/chain_fetcher.py` | `DEFAULT_TARGET_DTES (30,60,90,120,150,180)` → add the long anchors so interpolation is bracketed: e.g. `(30,60,90,120,150,180,270,365)`. Extend `tenor_label()` past `6M` (add `9M`, `1Y`). NOTE these are *anchor discovery* targets, not the display set. |
| **NEW** term‑interp step (vol‑engine or `src/core/vol/`) | Build the DISPLAY_PILLARS surface from the qualified anchors per §2; attach `source` flag per cell. Emit this as the published surface. |
| `src/core/vol/pca_engine.py` (`DEFAULT_TENORS`, `N_FEATURES`) | `DEFAULT_TENORS = ("1M","2M","3M","6M","9M","1Y")`. Still 6 tenors × 5 pillars = 30 features → **dimensionality unchanged**, but the model must be **refit** on new‑pillar surfaces. |
| `src/core/vol/surface_pca.py` (`DEFAULT_TENORS`) | same new tuple. |
| `src/core/risk/vega_pca.py` (`TENORS`) | same new tuple. |
| `src/api/orchestration/vol_service.py:226` | tenor→year map: add `"9M": 9/12` (already has `1Y`); drop `4M/5M` if unused. |
| `src/api/routers/portfolio_panel.py:55‑58` | per‑tenor DTE buckets (`4M`,`6M` rows) → realign to new pillars. |
| `src/api/routers/cockpit.py:62`, `src/core/vol/fair_term.py:67` | hardcoded `surface.get("6M")` ATM reads — 6M is now interpolated; confirm it still keys correctly (key stays `"6M"`). |
| `src/api/routers/trade.py:663‑664` | mock/synthetic fallback surface `base_atm` map (`4M,5M,6M`) → new pillars. |
| `src/core/trade_preview.py:23` `TENOR_TO_DTE` | This governs **TRADEABLE** tenors, NOT display. Set to the tradeable set (listed expiries / their labels). `build_from_legs` validates against it (line 458). Must match what the OrderBuilder offers. |
| `src/api/routers/positions.py:64‑93` | OTC tenor bucketer already spans 1W…2Y — verify 9M/1Y buckets unaffected. |

### PCA / DB implication (the big ripple)
- `vol_surfaces` rows are JSON keyed by tenor label. Historical rows hold the OLD labels
  (`4M/5M`). After the switch, the PCA fit must run on **new‑label** surfaces only — mixing
  old+new history corrupts the feature axis. Options: (a) refit fresh once enough new rows
  accumulate (≥ `MIN_OBS`), gating the PCA model until then; (b) one‑off backfill/migration
  of historical surfaces to the new pillars via interpolation. **Decision needed.**
- Seeds: `scripts/db/seed_pca_*` — check for hardcoded tenor assumptions.

## 4. Frontend changes

| File | Change |
|---|---|
| `frontend/src/voldesk/data/live/surface.ts` | `SURFACE_TENOR_KEYS` → `["1M","2M","3M","6M","9M","1Y"]`. `adaptSurface` carries the per‑cell `source` flag; keep "always show the canonical pillars, missing → '—'". |
| `frontend/src/voldesk/data/core.ts:65` | mock `tenors` → new set. |
| `frontend/src/voldesk/data.ts` (mock surface/termStructure/`smileFor`/`ivSurface`) | re‑key to new pillars (mock parity for tests/offline). |
| `frontend/src/voldesk/views/SignalsView.tsx` | IV surface already renders `data.tenors` (adaptive) → mostly free once keys change. **Add interp styling**: cells with `source==="interp"` rendered dimmed / hatched + tooltip "interpolated, not tradeable". Loadings `Heatmap rows={DATA.tenors}` follows. |
| `frontend/src/voldesk/data/live/termStructure.ts` | term‑structure tenors → new set. |
| `frontend/src/voldesk/components/OrderBuilder.tsx` | `TENORS` must come from **TRADEABLE_TENORS**, not the display pillars. Source: a new `/trade/tenors` (or reuse `/trade/limits`/book) listing tradeable expiries. So the builder never offers an interp‑only pillar (e.g. 6M today). |
| `frontend/src/api/schema.d.ts` | regenerate after backend surface payload gains `source` + tenor set changes. |

## 5. Tests to update
- `frontend/src/voldesk/data/__tests__/foundation.test.tsx` — surface tenor assertions (now `1M…1Y`), interp‑flag rendering.
- `frontend/src/voldesk/components/__tests__/orderBuilderLegs.test.ts` — tenors used in specs (3M/4M…) → tradeable set.
- Backend: `tests/unit/core/test_pca_engine.py` / surface_pca tests — new `DEFAULT_TENORS`. `tests/unit/core/test_trade_preview.py` — `TENOR_TO_DTE`/tradeable tenors. Any `vega_pca` test.
- `tests/old` + alembic `db_integration` job if it asserts surface shape (cf. CI rollup).

## 6. Phasing (suggested)
1. **Backend anchors + interp** — extend targets/labels, add the term‑interp step, emit `source` flags. (vol‑engine)
2. **PCA refit policy** — new `DEFAULT_TENORS`, decide refit‑fresh vs backfill; gate model until enough new‑pillar obs.
3. **Frontend display** — new pillar keys, interp styling, term curve. (Signal tab)
4. **Tradeable split** — `/trade/tenors` + OrderBuilder reads it; `TENOR_TO_DTE` = tradeable set.
5. **Tests + schema regen + mocks.**

## 7. Open questions (decide before coding)
- **Far extremity:** 9M or 1Y as the last pillar? (depends on Jun‑quarterly liquidity — run the trading‑class/expiry diagnostic first).
- **PCA history:** refit‑fresh (simpler, model dark for a while) vs backfill old surfaces (continuous, more work)?
- **Interp anchor for 6M when no Mar anchor yet:** interpolate 5M↔next‑listed, or leave 6M `—` until a bracketing anchor exists?
- Keep `4M/5M` anywhere (e.g. risk per‑tenor buckets) or drop them entirely from the system?
