# container — `backtest-runner`

**Image** : maison (à créer)
**Container** : `fxvol-backtest-runner`
**État** : ❌ à créer (tag v0.3)
**Steps** : backtest walk-forward

---

## Rôle

Job batch invoqué par `api` (`POST /backtest/runs`). Exécute un walk-forward
out-of-sample sur l'historique `vol_snapshots_30d` + `ohlc_daily`.

Pour chaque fold :
1. Refit `pca-fitter` en CLI mode sur la fenêtre train.
2. Replay vol-engine offline (re-compute surfaces + signaux).
3. Replay risk → exit rules + delta hedge.
4. Apply cost model (spreads, commissions, slippage).
5. Aggrège P&L par fold + métriques (Sharpe, max DD, hit rate, turnover).

## Inputs

- Postgres : `vol_snapshots_30d`, `ohlc_daily`, `vol_config`, `risk_config`, `exec_config`
  (snapshot des configs au début du fold pour reproductibilité).

## Outputs

| Cible | Schema | Cadence |
|---|---|---|
| Postgres | `backtest_runs` (1 row par run, paramétrage + verdict) | run start/end |
| Postgres | `backtest_folds` (n rows par fold) | par fold |
| Postgres | `backtest_trades` (replay des trades simulés) | par trade |

## Mapping steps

- **Backtest** uniquement. **Strict cutoff** `timestamp <= current_fold_time`
  (cf. README §Garde-fous #6 + ADR-301-302).
- Pas d'optimisation de paramètres (cf. ADR-307) : N variantes = N runs.
- Verdict {`passed`, `failed`} sur la stratégie globale → débloque/bloque le passage à v0.4.

## Cost model

- Spread bid/ask depuis l'historique chain (si pas dispo, fallback table par tenor).
- Commissions IB FOP (~$1.60/contract round-turn).
- Slippage : quantile 75% du spread, pas optimiste.

## Failure modes

- Données train < 252 obs → fold skip + warning ; si > 30% folds skipped, run = `failed`.
- Run interrompu → `state = stopped`, reproductible via `--resume-from-fold N`.

## À faire pour v1.0

- [ ] Service Python + Dockerfile.
- [ ] Migrations `backtest_runs` / `backtest_folds` / `backtest_trades`.
- [ ] CLI : `python -m src.services.backtest_runner --config configs/run1.yaml`.
- [ ] Endpoint `api` pour trigger + poll.
- [ ] Tab frontend Backtest (lance + visualise courbes equity / DD par fold).
