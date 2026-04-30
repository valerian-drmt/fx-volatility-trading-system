# container — `vol-engine`

**Image** : maison (`docker/vol-engine/Dockerfile`)
**Container** : `fxvol-vol-engine`, IP `172.20.0.11`, IB clientId `2`
**État** : ✅ existe — refactor v2 in progress
**Steps** : 1 (régime), 2 (z-scores PCA — à wirer), parts of 3 (σ_fair_q par tenor)

---

## Rôle

Cycle batch toutes les 180s. À chaque tick :
1. Lit spot live (Redis) + chain FOP (Redis ou IB direct).
2. BS-inverse les IV par strike → SVI per-tenor → SSVI surface fit.
3. Calcule RV via Yang-Zhang sur OHLC daily + HAR-RV + GARCH(1,1).
4. Convertit P-measure → Q-measure via VRP table → σ_fair_q par tenor.
5. Génère signaux CHEAP / FAIR / EXPENSIVE par tenor (seuil hot-reloadable).
6. PUBLISH `vol:surface` + INSERT `vol_surfaces` / `signals` / `svi_params` / `ssvi_params`.

Détails complets dans `docs/VOL_SYSTEM.md` + `docs/VOL_MODELS.md`.

## Outputs

| Cible | Topic / Table | Cadence |
|---|---|---|
| Redis | `latest_vol_surface` (SET) + `vol:surface` (PUBLISH) | 180s |
| Redis | `signal:vol` (PUBLISH) | 180s par signal |
| Postgres | `vol_surfaces`, `signals`, `svi_params`, `ssvi_params` | 180s |

## Mapping steps

- **Step 1** — *cible v0.1* : ajout d'une heuristique 3-states (`TRENDING_UP / RANGE / TRENDING_DOWN`) calculée à partir de HAR-RV residuals + spot momentum. Publie `regime:state` Redis + INSERT `regime_states`. Cf. STEP1 §4.
- **Step 2** — *cible v0.2* : consume `pca_models` (loadings posés par `pca-fitter`), project surface 30-dim live → 3 z-scores (PC1/PC2/PC3) + flag actionable. Publie `signal:pca`. Cf. STEP2 §5-7.
- **Step 3** — déjà : σ_fair_q par tenor consumé par `api /preview` pour pricing leg.

## Configuration

`vol_config` table (versioned). 2 fields hot-reloadable :
- `signal.threshold_vol_pts` (seuil |écart| pour CHEAP/EXPENSIVE)
- `signal.model_p` (`har` ou `garch`)

Le reste read-only depuis le frontend (cf. `VolConfigEditor.tsx`).

## Failure modes

- chain trop sparse → SVI fail → fallback flat smile + fail flag dans payload.
- OHLC < 60 obs → HAR/GARCH skip → σ_fair_p = NULL, signal = FAIR forcé.
- spot stale > 30s → cycle skip (publie `degraded:vol-engine:1`).

## À faire pour v1.0

- [ ] Wirer Step 1 régime publisher (heuristique simple, pas de ML).
- [ ] Wirer Step 2 PCA projection (lecture loadings depuis Postgres, projection en mémoire).
- [ ] Persist `_regime` field dans payload `latest_vol_surface`.
