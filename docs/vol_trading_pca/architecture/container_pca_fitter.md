# container — `pca-fitter`

**Image** : maison (à créer)
**Container** : `fxvol-pca-fitter`
**État** : ❌ à créer (Step 2 v0.2)
**Steps** : 2 (PCA factor model)

---

## Rôle

Job batch hebdomadaire (cron : Sunday 22:00 UTC). Refit le modèle PCA sur 12 mois rolling
de surfaces vol 30-dim collectées par `snapshot-collector`.

Output = matrice de loadings (3 PCs : level / slope / curvature à la Litterman-Scheinkman) +
moyennes/variances historiques pour z-score → table `pca_models`.

## Inputs

- Postgres `vol_snapshots_30d` (lit ~12 mois × ~24/jour ≈ 8500 obs).

## Outputs

| Cible | Cible | Cadence |
|---|---|---|
| Postgres | `pca_models` (versioned, append-only) | hebdo |
| Redis | `pca:refit` (notification) | hebdo |

Schéma `pca_models` :
- `id`, `fitted_at`, `n_obs`, `tenor_grid_json` (30-dim grid)
- `loadings_json` (3 × 30 matrix)
- `means_json`, `stds_json` (pour z-score live)
- `eigenvalue_ratios_json` (variance expliquée)
- `is_active` (1 seul `true` à la fois)

## Mapping steps

- **Step 2** uniquement. vol-engine consume le modèle `is_active=true` pour projeter chaque
  surface live → 3 z-scores. Cf. STEP2 §4-6 + ADR sur sign correction (rotation determinism).
- **Backtest** — refit *par fold* (no-lookahead). Le container est invocable en CLI :
  `python -m src.services.pca_fitter --as-of 2024-06-30 --output-table pca_models_backtest`.

## Sign correction

Loadings PCA ambigus en signe. Conv : PC1 loadings tous positifs (sinon flip).
PC2/PC3 : ancrer le signe via cosine similarity avec la dernière version active.
Cf. STEP2 §6 et ADR-201 (à créer).

## Failure modes

- < 252 obs → refuse refit, garde modèle précédent, alerte critique.
- Eigenvalue ratio PC1 < 70% → flag `instable`, modèle inserted mais `is_active=false`.

## À faire pour v1.0

- [ ] Créer service Python + Dockerfile.
- [ ] Migration `pca_models` table.
- [ ] CLI mode pour backtest replay.
- [ ] Tests : déterminisme refit (même input → mêmes loadings au signe près).
