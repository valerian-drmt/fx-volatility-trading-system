# container — `snapshot-collector`

**Image** : maison (à créer)
**Container** : `fxvol-snapshot-collector`
**État** : ❌ à créer (Step 2 v0.2 prerequisite)
**Steps** : 2 (alimente l'historique nécessaire au PCA fit)

---

## Rôle

Cron horaire (top de chaque heure UTC). Lit le payload `latest_vol_surface` Redis, le
projette sur la grille canonique 30-dim (5 tenors × 6 strikes ATM±) et persiste un
snapshot dans Postgres.

C'est l'**accumulateur** qui rend possible le refit hebdo de `pca-fitter` : 12 mois × 24/j ≈
8500 surfaces. Sans ça, pas de data pour Step 2.

## Inputs

- Redis `latest_vol_surface` (publish par `vol-engine`).

## Outputs

| Cible | Schema | Cadence |
|---|---|---|
| Postgres | `vol_snapshots_30d` | 1× / heure |

Schéma `vol_snapshots_30d` :
- `id`, `timestamp` (heure ronde UTC), `underlying`
- `tenor_grid_json` (5 tenors fixes : 1W, 2W, 1M, 2M, 3M)
- `strike_grid_json` (6 deltas : 10P, 25P, ATM, 25C, 10C — ou 6 moneyness fixes)
- `iv_matrix_json` (5×6 = 30 valeurs)
- `spot_at_snapshot`, `source` (`live` / `replay` / `backfill`)

## Mapping steps

- **Step 2** — alimente `pca-fitter` (12 mois rolling).
- **Backtest** — replay : peut être désactivé en backtest (le harness lit directement
  `vol_snapshots_30d` historiques).

## Edge cases

- Surface manquante à l'heure ronde → snapshot avec `iv_matrix_json = null` + `source = missing` ;
  pca-fitter skip ces lignes.
- Spot bouge entre snapshots → grille strikes recalibrée à chaque snapshot (delta-based).

## À faire pour v1.0

- [ ] Créer service Python + Dockerfile (très léger : 1 cron + 1 INSERT).
- [ ] Migration `vol_snapshots_30d`.
- [ ] Backfill script si besoin de remonter historique avant déploiement.
