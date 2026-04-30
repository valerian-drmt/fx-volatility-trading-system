# container — `risk`

**Image** : maison (`docker/risk/Dockerfile`)
**Container** : `fxvol-risk`, IP `172.20.0.12`, IB clientId `3`
**État** : ✅ existe
**Steps** : 3 (greeks preview), 5 (MTM live + 5 exit rules + delta hedge)

---

## Rôle

Cycle 60s. Lit positions ouvertes + surface vol live + spot, calcule :
1. Greeks par leg (Δ, Γ, Vega, Θ) via BS.
2. Aggregation par position multi-leg + portefeuille.
3. P&L decomposition (spot, vol, theta, residual).
4. Delta hedge sizing (cible Δ-neutre, deadband paramétrable).

Cible v1.0 : ajout des **5 exit rules** (Step 5) + déclenchement actions.

## Inputs

- Postgres `positions` (ouvertes), `position_snapshots` (latest)
- Redis `latest_vol_surface`, `ticks:eurusd`
- Postgres `risk_config` (limites, deadbands, exit rules thresholds — versioned)

## Outputs

| Cible | Topic / Table | Cadence |
|---|---|---|
| Postgres | `position_pnl_snapshots` (à créer step 5) | 60s |
| Redis | `risk:greeks` (PUBLISH) | 60s |
| Redis | `action:hedge` (commande hedge order) | trigger-driven |
| Redis | `action:close` (commande close position) | trigger-driven |
| Postgres | `exit_decisions` (à créer step 5) | trigger-driven |

## Mapping steps

- **Step 3** — `api /preview` consume risk via call synchrone (sizing + greeks d'une structure
  hypothétique). Pas de boucle.
- **Step 5** — boucle 60s :
  - MTM live + P&L decomposition
  - 5 exit rules (cf. STEP5 §5) : take-profit, stop-loss, time-decay, vol-mean-reversion,
    régime-flip
  - Delta hedge sizing → publish `action:hedge` si |Δ_total| > deadband
  - Close trigger → publish `action:close`

## Configuration

`risk_config` (versioned, hot-reloadable subset) :
- delta_deadband, max_vega, max_gamma_per_position
- exit_rules.take_profit_pct, .stop_loss_pct, .max_dte_close, etc.

## Failure modes

- IB déconnect → greeks calc continue (pure BS), mais hedge orders bloqués (alerte).
- Surface vol stale > 5 min → P&L decomp marque `vol_pnl = null`.

## À faire pour v1.0

- [ ] 5 exit rules + table `exit_decisions`.
- [ ] Publisher `action:hedge` / `action:close`.
- [ ] Migration `position_pnl_snapshots`.
- [ ] Mode "read-only" toggle (risk calcule mais ne publie pas d'actions) pour rampup.
