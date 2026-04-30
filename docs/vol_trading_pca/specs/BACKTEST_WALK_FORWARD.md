# Backtest — Walk-forward methodology

> Spec du backtest pour valider OOS le pipeline de signaux PCA développé en étape 2.
>
> **Objectif fonctionnel** : produire des métriques OOS (Sharpe, drawdown, hit rate, capacity) qui démontrent que les signaux ont un edge statistique au-delà du bruit, **avant** d'autoriser tout trading live.
>
> **Prérequis** :
> - Étape 2 livrée (`pca_signals` produits + persistés)
> - Étape 3 livrée (pricing structure + greeks + sizing) — sinon backtest = signaux dans le vide
> - Historique IB reconstruit ≥ 18 mois (12 fit + 6 OOS minimum)
>
> **Pas dans cette étape** :
> - Live trading (jamais avant Sharpe OOS validé + cost model intégré)
> - Optimisation hyperparamètres (= overfitting déguisé en validation)
> - Stress test (complémentaire, doc séparé)
>
> **Audience** : agent code (Claude Code) qui implémente. Spec auto-suffisante.

---

## 1. Système formel

| Élément | Spec |
|---|---|
| Agents | Backtest harness (orchestrateur), PCA fitter (réutilisé étape 2), structure pricer (réutilisé étape 3), cost model (nouveau), exit rules engine (réutilisé étape 5) |
| États | `backtest_run ∈ {pending, fitting, simulating, computing_metrics, completed, failed}` |
| Inputs | `surface_snapshots_hourly` historique, `events` historique, `vrp_table_default` (ou `vrp_estimated` si dispo) |
| Outputs | Sharpe IS/OOS par PC, P&L curve, drawdown distribution, capacity curve, attribution (vega/gamma/theta), trade log complet |
| Contrainte temporelle | walk-forward strict : aucune utilisation d'info > date courante du fold |
| Contrainte coût | cost model OBLIGATOIRE (spread + commission + slippage). Sharpe gross sans cost = données fausses |

---

## 2. Fondamentaux conceptuels (à comprendre avant le code)

### 2.1 Qu'est-ce qu'un walk-forward backtest

Le standard quant moderne. Mime ce qui se passerait en live :

```
Pour chaque date t dans [start_date + 12 mois, end_date]:
    1. Refit PCA sur fenêtre [t - 12 mois, t]
    2. Pour chaque cycle de [t, t + 1 mois]:
        a. Project snapshot sur loadings du fit-t
        b. Generate signal selon règles step 2
        c. Si actionable, generate trade selon règles step 3
        d. Track P&L mark-to-market
        e. Apply exit rules selon step 5
    3. Avancer t de 1 mois (sliding window)
```

Différences vs single-split :
- Single-split fitte une fois → mesure performance OOS sur **une** période
- Walk-forward refitte régulièrement → mesure performance OOS sous **évolution réaliste**

Différence vs stress test : stress test prend un modèle déjà fitté et le rejoue sur périodes choisies pour leur extrémité. Walk-forward ne sélectionne pas les périodes, il prend tout l'historique séquentiellement.

### 2.2 Les pièges classiques à éviter explicitement

| Piège | Mécanisme | Comment l'éviter ici |
|---|---|---|
| Look-ahead bias | Utiliser une info au fold N qui n'existait qu'au fold N+1 | Strict timestamping : seules données avec `timestamp ≤ t` accessibles |
| Survivorship bias | Backtest sur instruments qui existent aujourd'hui (bias vers survivants) | EUR/USD est unique sous-jacent → pas applicable directement, mais attention si extension multi-currency |
| Data snooping | Tester N variantes de paramètres et garder la meilleure | UNE config par run, documenter chaque tweak comme run distinct |
| Overfit via fenêtre fit | Choisir longueur de fit qui maximise OOS Sharpe | Fenêtre fixée a priori (12 mois), justification théorique |
| Ignored transaction costs | Sharpe gross >> Sharpe net, illusion d'edge | Cost model OBLIGATOIRE (cf. §6) |
| Bias de sélection des trades | Ne backtest que les "bons" signaux | Backtest TOUS les signaux qui passent les gates étape 2 |
| In-sample contamination | Z-scores calculés sur fenêtre incluant le présent | Z-scores rolling computed sur t-N à t-1, jamais inclure t |

### 2.3 Schéma temporel walk-forward

```
fit window 12M       OOS test 1M
─────────────────────┼──────────
                     ↑
                  fold k

         fit window 12M       OOS test 1M
         ─────────────────────┼──────────
                              ↑
                           fold k+1

(sliding by 1 month each fold)
```

Pour 5 ans d'historique : ~48 folds. Chaque fold = 1 PCA fit + ~720 cycles tests (1 mois × 24h × 30j).

---

## 3. Décision logic — qu'est-ce qui valide le backtest

```python
def backtest_validates_strategy(metrics: BacktestMetrics) -> ValidationVerdict:
    """Strict gates avant d'autoriser passage au paper trading puis live."""
    
    # Gate 1 : Sharpe OOS positif et significatif
    if metrics.sharpe_oos < 0.5:
        return ValidationVerdict(passed=False, reason="sharpe_oos_too_low")
    
    # Gate 2 : Sharpe gap IS-OOS contenu (overfit detector)
    sharpe_gap = metrics.sharpe_is - metrics.sharpe_oos
    if sharpe_gap > metrics.sharpe_oos:  # gap > OOS = plus d'overfit que de signal
        return ValidationVerdict(passed=False, reason="overfitting_detected")
    
    # Gate 3 : Cost model intégré
    if metrics.sharpe_gross_minus_net < 0.3:
        return ValidationVerdict(passed=True, warning="cost_model_dominant")
    
    # Gate 4 : Drawdown soutenable
    if metrics.max_drawdown_pct > 30:
        return ValidationVerdict(passed=False, reason="drawdown_unacceptable")
    
    # Gate 5 : Hit rate cohérent avec espérance
    if metrics.hit_rate < 0.45:
        return ValidationVerdict(passed=False, reason="hit_rate_too_low")
    
    # Gate 6 : Capacity sufficient pour notional cible
    if metrics.capacity_at_target_notional < 0.7 * metrics.sharpe_oos:
        return ValidationVerdict(passed=True, warning="capacity_constrained")
    
    # Gate 7 : Robustesse cross-régime
    for regime in ["calm", "stressed"]:
        if metrics.sharpe_by_regime[regime] < 0:
            return ValidationVerdict(passed=False, reason=f"unprofitable_in_{regime}")
    
    return ValidationVerdict(passed=True)
```

Constantes (placeholders à calibrer après premier run) :
```python
SHARPE_MIN_OOS = 0.5            # ratio Sharpe annualisé minimum
DRAWDOWN_MAX_PCT = 30           # max drawdown acceptable
HIT_RATE_MIN = 0.45             # straddles longs ont hit rate < 50% normal
CAPACITY_DECAY_TOLERANCE = 0.3  # max 30% Sharpe loss à notional cible
```

---

## 4. Architecture du backtest harness

### 4.1 Pipeline orchestrateur

```python
# src/backtest/harness.py
class BacktestHarness:
    def run(self, config: BacktestConfig) -> BacktestRun:
        run = BacktestRun(config=config, status="pending")
        
        try:
            # Phase 1 : préparer folds
            folds = self._generate_folds(config)
            
            # Phase 2 : pour chaque fold
            for fold_idx, fold in enumerate(folds):
                self._run_fold(run, fold_idx, fold)
            
            # Phase 3 : agréger métriques
            self._compute_aggregate_metrics(run)
            
            # Phase 4 : générer rapport
            self._generate_report(run)
            
            run.status = "completed"
        except Exception as e:
            run.status = "failed"
            run.error_message = str(e)
        finally:
            self.db.commit()
        
        return run
    
    def _run_fold(self, run, fold_idx, fold):
        # 4a. Fit PCA sur fit_window
        pca_model = self._fit_pca_isolated(fold.fit_start, fold.fit_end)
        
        # 4b. Pour chaque cycle de test_window, simuler
        cycles = self._enumerate_cycles(fold.test_start, fold.test_end)
        
        position_book = PositionBook()  # état du book pour ce fold
        
        for cycle_ts in cycles:
            # 4b-i. Reconstruct le _regime à cette date (sans lookahead)
            regime = self._reconstruct_regime(cycle_ts)
            
            # 4b-ii. Gate decision étape 1
            gate = gate_decision(regime, history=...)
            if not gate.authorized:
                self._log_skipped_cycle(run, cycle_ts, gate.reason)
                continue
            
            # 4b-iii. Project snapshot sur PCA loadings
            snapshot = self._load_snapshot(cycle_ts)
            signals = project_and_compute_signals(snapshot, pca_model)
            
            # 4b-iv. Pour chaque signal actionable → generate trade
            for pc_id, signal in signals.items():
                if signal.actionable:
                    structure = build_structure(signal.recommended_structure)
                    trade = generate_trade(structure, signal, position_book)
                    
                    # 4b-v. Apply cost model
                    trade.entry_cost = compute_entry_cost(structure, snapshot)
                    
                    # Persist trade
                    self._record_trade(run, cycle_ts, trade)
                    position_book.add(trade)
            
            # 4b-vi. Mark to market positions ouvertes
            for position in position_book.open_positions:
                mtm = compute_mtm(position, snapshot)
                self._record_mtm(run, cycle_ts, position, mtm)
                
                # 4b-vii. Apply exit rules
                exit_decision = check_exit_rules(position, signals, snapshot, cycle_ts)
                if exit_decision.exit:
                    exit_cost = compute_exit_cost(position, snapshot)
                    self._record_exit(run, cycle_ts, position, exit_decision, exit_cost)
                    position_book.close(position)
            
            # 4b-viii. Apply delta hedge si nécessaire
            for position in position_book.open_positions:
                if needs_delta_hedge(position, snapshot):
                    hedge_trade = compute_delta_hedge(position, snapshot)
                    hedge_trade.cost = compute_hedge_cost(hedge_trade)
                    self._record_hedge(run, cycle_ts, hedge_trade)
        
        # 4c. Close remaining open positions à fin de fold (no carry-over)
        self._close_all_at_fold_end(run, fold_idx, position_book)
```

### 4.2 Isolation stricte fit / test

**Critique** : `_fit_pca_isolated` doit garantir aucune fuite future :

```python
def _fit_pca_isolated(self, fit_start: datetime, fit_end: datetime) -> PcaModel:
    """Fit PCA en utilisant UNIQUEMENT les snapshots avec timestamp <= fit_end."""
    
    snapshots_df = pd.read_sql(
        f"""
        SELECT * FROM surface_snapshots_hourly
        WHERE symbol = 'EURUSD'
          AND timestamp >= '{fit_start}'
          AND timestamp <= '{fit_end}'  -- strict inequality on upper bound
          AND n_strikes_present = 30
        ORDER BY timestamp
        """,
        self.db.bind
    )
    
    # Compute means/stds UNIQUEMENT sur fit_window
    # NE JAMAIS utiliser les means/stds globaux du dataset complet
    iv_columns = [c for c in snapshots_df.columns if c.startswith("iv_")]
    X = snapshots_df[iv_columns].values
    means = X.mean(axis=0)
    stds = X.std(axis=0)
    X_std = (X - means) / stds
    
    pca = PCA(n_components=6)
    pca.fit(X_std)
    
    # Crée un PcaModel **temporaire** pour ce fold (pas persisté en pca_models)
    return TempPcaModel(
        loadings=pca.components_,
        means=means,
        stds=stds,
        variance_explained=pca.explained_variance_ratio_,
        fit_window_start=fit_start,
        fit_window_end=fit_end,
    )
```

---

## 5. Reconstruction historique du régime (lookback only)

Pour le gate decision étape 1, le backtest a besoin de reconstruire le label régime à chaque cycle historique. Le piège : utiliser `regime_snapshots` directement → utilise potentiellement des info futures (vol_of_vol calculé sur fenêtre incluant le futur).

Solution : recalculer à la volée avec strict cutoff temporel.

```python
def _reconstruct_regime(self, cycle_ts: datetime) -> RegimeState:
    """Reconstruct regime label using ONLY data available at cycle_ts."""
    
    # Lecture features avec strict cutoff
    features_window = self.db.execute(
        select(FeatureHistory)
        .where(FeatureHistory.symbol == "EURUSD")
        .where(FeatureHistory.timestamp <= cycle_ts)  # strict
        .where(FeatureHistory.timestamp >= cycle_ts - timedelta(days=90))
        .order_by(FeatureHistory.timestamp)
    ).scalars().all()
    
    if len(features_window) < 30:
        return RegimeState(label=None, reason="insufficient_history")
    
    current = features_window[-1]
    history = features_window[:-1]  # tout SAUF le current
    
    # Recalcule vol_of_vol sur fenêtre passée stricte
    iv_3m_history = [f.iv_atm_3m_pct for f in history[-30:]]  # 30 derniers jours
    vol_of_vol = float(np.std(iv_3m_history))
    
    # Recalcule z-scores sur fenêtre passée stricte
    z_scores = compute_zscores_strict(
        current=current,
        history=history,
        window_days=90
    )
    
    # Régime label avec features reconstruites
    regime_label = detect_regime(
        vol_level=current.iv_atm_3m_pct,
        vol_of_vol=vol_of_vol,
        term_slope=current.term_slope_pct
    )
    
    # Event dampener : nécessite events table avec strict cutoff sur scheduled_at > cycle_ts
    next_event = self.db.execute(
        select(Event)
        .where(Event.scheduled_at > cycle_ts)
        .where(Event.impact == "high")
        .order_by(Event.scheduled_at)
        .limit(1)
    ).scalar_one_or_none()
    
    days_to_event = (next_event.scheduled_at - cycle_ts).total_seconds() / 86400 if next_event else None
    event_dampener = days_to_event is not None and days_to_event < 5
    
    return RegimeState(
        label=regime_label,
        event_dampener=event_dampener,
        days_to_next_event=days_to_event,
    )
```

---

## 6. Cost model — section critique

Sans cost model intégré, le backtest est **trompeur**. Spec cost model :

### 6.1 Composantes du coût

| Composante | Source | Magnitude typique EUR/USD FOP |
|---|---|---|
| Bid-ask spread | différence quote IB | 0.5 - 1.5 vol pts ATM, jusqu'à 3+ vol pts wings |
| Commission IB | structure tarifaire IB | $0.85 par contract option, $2.04 par micro-future |
| Slippage marché | impact prix selon size | quasi 0 sur petites tailles (< 50 contracts), ~0.2 vol pts à 100+ |
| Hedging cost récurrent | rebalance delta hedge | spread sur 6E future + commission, accumulés sur durée trade |
| Carry cost | cost of capital sur margin | LIBOR + 1-2% sur margin requise |

### 6.2 Implémentation par trade

```python
def compute_entry_cost(structure: Structure, snapshot: dict) -> CostBreakdown:
    """Compute total cost paid to enter a structure."""
    
    cost = CostBreakdown()
    
    for leg in structure.legs:
        if leg.contract_type in ("call", "put"):
            # Option leg
            mid_iv = get_iv(snapshot, leg.tenor, leg.delta_pillar)
            spread_iv = get_quoted_spread(snapshot, leg.tenor, leg.delta_pillar)
            
            # Half-spread crossed on entry (assuming aggressive)
            iv_paid = mid_iv + (spread_iv / 2) if leg.side == "BUY" else mid_iv - (spread_iv / 2)
            
            # Convert IV cost to cash cost via vega
            vega = compute_vega(leg, snapshot)
            cost.spread_cost += abs(leg.qty) * vega * (spread_iv / 2) * 100  # in cash
            
            # Commission
            cost.commission += abs(leg.qty) * 0.85
        
        elif leg.contract_type == "future":
            # Hedge leg
            spread_pips = get_future_spread(snapshot)
            cost.spread_cost += abs(leg.qty) * spread_pips * leg.tick_value / 2
            cost.commission += abs(leg.qty) * 2.04
    
    return cost


def compute_hedge_cost(hedge_trade: HedgeTrade) -> float:
    """Cost of a single delta rebalance."""
    spread_pips = hedge_trade.future_spread_pips
    qty = abs(hedge_trade.qty)
    return qty * (spread_pips / 2) * hedge_trade.tick_value + qty * 2.04


def estimate_hedging_cost_over_lifetime(position: Position) -> float:
    """Expected total hedging cost based on expected number of rebalances."""
    # Heuristique : N rebalances = realized vol × √days / hedge_band_width
    expected_realized_vol = position.entry_iv  # use IV as proxy for ex-ante
    days_to_expiry = position.dte_at_entry
    hedge_band_pips = 50  # typical
    
    expected_n_rebalances = (
        expected_realized_vol * np.sqrt(days_to_expiry / 252) 
        / hedge_band_pips
    ) * 100  # rough
    
    avg_hedge_cost = position.entry_spread_pips * 5  # rough heuristic
    
    return expected_n_rebalances * avg_hedge_cost
```

### 6.3 Cost validation

Cost model doit être validé par sample : prendre 5-10 trades historiques, comparer cost prédit vs cost réel observé. Tolérance : ±30%. Au-delà, recalibrer.

---

## 7. Tables Postgres nécessaires

### 7.1 `backtest_runs` — métadonnées de chaque exécution backtest

```sql
CREATE TABLE backtest_runs (
    id              BIGSERIAL PRIMARY KEY,
    
    name            TEXT NOT NULL,                  -- ex: "wf_v1_2026_05_03_full_history"
    config          JSONB NOT NULL,                  -- config complète du run
    
    -- temporal
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    
    -- status
    status              TEXT NOT NULL,               -- 'pending' | 'running' | 'completed' | 'failed'
    error_message       TEXT,
    
    -- temporal scope
    historical_start    TIMESTAMPTZ NOT NULL,
    historical_end      TIMESTAMPTZ NOT NULL,
    n_folds             INTEGER NOT NULL,
    
    -- model config
    fit_window_months   INTEGER NOT NULL DEFAULT 12,
    test_window_months  INTEGER NOT NULL DEFAULT 1,
    refit_frequency     TEXT NOT NULL DEFAULT 'monthly',
    
    -- cost model config
    cost_model_version  TEXT NOT NULL,
    
    -- aggregate metrics (populated after completion)
    sharpe_is               DOUBLE PRECISION,
    sharpe_oos              DOUBLE PRECISION,
    sharpe_oos_net          DOUBLE PRECISION,        -- after cost
    sharpe_gap              DOUBLE PRECISION,        -- IS - OOS
    max_drawdown_pct        DOUBLE PRECISION,
    hit_rate                DOUBLE PRECISION,
    n_trades                INTEGER,
    avg_trade_pnl_usd       DOUBLE PRECISION,
    total_pnl_gross_usd     DOUBLE PRECISION,
    total_pnl_net_usd       DOUBLE PRECISION,
    total_cost_usd          DOUBLE PRECISION,
    
    validation_verdict      TEXT,                    -- 'passed' | 'failed' | 'passed_with_warning'
    validation_reasons      JSONB,
    
    notes                   TEXT,
    
    CONSTRAINT chk_status CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    CONSTRAINT chk_verdict CHECK (validation_verdict IS NULL OR validation_verdict IN ('passed', 'failed', 'passed_with_warning'))
);

CREATE INDEX ix_backtest_runs_status_ts ON backtest_runs (status, created_at DESC);
CREATE INDEX ix_backtest_runs_completed ON backtest_runs (completed_at DESC) WHERE status = 'completed';
```

**Cardinalité** : ~10-50 runs / mois pendant phase dev. Quasi rien.

---

### 7.2 `backtest_folds` — détail par fold walk-forward

```sql
CREATE TABLE backtest_folds (
    id              BIGSERIAL PRIMARY KEY,
    run_id          BIGINT NOT NULL REFERENCES backtest_runs(id),
    
    fold_idx        INTEGER NOT NULL,                -- 0, 1, 2, ...
    
    -- temporal scope
    fit_start       TIMESTAMPTZ NOT NULL,
    fit_end         TIMESTAMPTZ NOT NULL,
    test_start      TIMESTAMPTZ NOT NULL,
    test_end        TIMESTAMPTZ NOT NULL,
    
    -- PCA fit info (TempPcaModel, pas persisté ailleurs)
    pca_loadings        JSONB NOT NULL,              -- (6, 30)
    pca_means           JSONB NOT NULL,              -- (30,)
    pca_stds            JSONB NOT NULL,              -- (30,)
    pca_variance_explained JSONB NOT NULL,           -- (6,)
    n_obs_in_fit        INTEGER NOT NULL,
    
    -- IS metrics (computed pendant fit window)
    is_sharpe           DOUBLE PRECISION,
    is_total_pnl_usd    DOUBLE PRECISION,
    
    -- OOS metrics (computed pendant test window)
    oos_sharpe          DOUBLE PRECISION,
    oos_total_pnl_gross DOUBLE PRECISION,
    oos_total_cost      DOUBLE PRECISION,
    oos_total_pnl_net   DOUBLE PRECISION,
    oos_n_trades        INTEGER,
    oos_hit_rate        DOUBLE PRECISION,
    oos_max_drawdown    DOUBLE PRECISION,
    
    -- regime breakdown OOS
    oos_pnl_by_regime   JSONB,                       -- {calm: X, stressed: Y, pre_event: Z}
    oos_pnl_by_pc       JSONB,                       -- {pc1: X, pc2: Y, pc3: Z}
    
    UNIQUE (run_id, fold_idx)
);

CREATE INDEX ix_backtest_folds_run ON backtest_folds (run_id, fold_idx);
```

**Cardinalité** : ~48 folds × 50 runs = 2400. Trivial.

---

### 7.3 `backtest_trades` — log de chaque trade simulé

```sql
CREATE TABLE backtest_trades (
    id              BIGSERIAL PRIMARY KEY,
    run_id          BIGINT NOT NULL REFERENCES backtest_runs(id),
    fold_id         BIGINT NOT NULL REFERENCES backtest_folds(id),
    
    -- timestamps
    entry_ts        TIMESTAMPTZ NOT NULL,
    exit_ts         TIMESTAMPTZ,                    -- null si force-closed à fin de fold
    
    -- signal source
    triggering_pc       INTEGER NOT NULL,            -- 1, 2, ou 3
    triggering_signal_z DOUBLE PRECISION NOT NULL,
    triggering_signal_label TEXT NOT NULL,           -- 'CHEAP' | 'EXPENSIVE'
    
    -- structure
    structure_type      TEXT NOT NULL,               -- 'straddle_atm' | 'butterfly_25d' | 'calendar' | ...
    reference_tenor     TEXT NOT NULL,               -- '1M' | ... | '6M'
    legs                JSONB NOT NULL,              -- liste de legs {contract_type, strike, dte, qty, side, entry_iv, entry_price}
    
    -- entry
    entry_premium_usd   DOUBLE PRECISION NOT NULL,   -- + si paid (BUY net), - si received (SELL net)
    entry_spread_cost   DOUBLE PRECISION NOT NULL,
    entry_commission    DOUBLE PRECISION NOT NULL,
    entry_total_cost    DOUBLE PRECISION NOT NULL,   -- spread + commission
    entry_iv_avg        DOUBLE PRECISION,            -- IV moyenne pondérée à l'entrée
    
    -- exit
    exit_reason         TEXT,                        -- 'signal_reverse' | 'time_based' | 'stop_loss_vega' | 'expiry' | 'fold_end'
    exit_premium_usd    DOUBLE PRECISION,
    exit_spread_cost    DOUBLE PRECISION,
    exit_commission     DOUBLE PRECISION,
    exit_total_cost     DOUBLE PRECISION,
    exit_iv_avg         DOUBLE PRECISION,
    
    -- hedging
    n_hedges            INTEGER DEFAULT 0,
    total_hedge_cost    DOUBLE PRECISION DEFAULT 0,
    
    -- P&L decomposition
    gross_pnl_usd       DOUBLE PRECISION,            -- before any cost
    vega_pnl_usd        DOUBLE PRECISION,            -- attribution
    gamma_pnl_usd       DOUBLE PRECISION,
    theta_pnl_usd       DOUBLE PRECISION,
    other_pnl_usd       DOUBLE PRECISION,
    total_cost_usd      DOUBLE PRECISION,
    net_pnl_usd         DOUBLE PRECISION,            -- gross - total_cost
    
    -- metadata
    regime_at_entry     TEXT,
    sizing_qty          INTEGER,
    sizing_factors      JSONB,                       -- {z_score_mult, book_penalty, event_dampener}
    
    CONSTRAINT chk_exit_reason CHECK (
        exit_reason IS NULL OR 
        exit_reason IN ('signal_reverse', 'time_based', 'stop_loss_vega', 'expiry', 'fold_end')
    )
);

CREATE INDEX ix_backtest_trades_run ON backtest_trades (run_id);
CREATE INDEX ix_backtest_trades_fold ON backtest_trades (fold_id);
CREATE INDEX ix_backtest_trades_entry_ts ON backtest_trades (entry_ts);
CREATE INDEX ix_backtest_trades_pc ON backtest_trades (triggering_pc);
```

**Cardinalité** : très variable. Estimation : 5-20 trades / mois × 48 folds × 50 runs = 12k-50k. Toujours gérable.

---

### 7.4 `backtest_mtm_history` — séries P&L mark-to-market

Permet de plotter equity curve, calculer drawdown, détecter overfit.

```sql
CREATE TABLE backtest_mtm_history (
    id              BIGSERIAL PRIMARY KEY,
    run_id          BIGINT NOT NULL REFERENCES backtest_runs(id),
    fold_id         BIGINT NOT NULL REFERENCES backtest_folds(id),
    
    timestamp       TIMESTAMPTZ NOT NULL,
    
    -- P&L cumulés au timestamp
    cumulative_gross_pnl_usd    DOUBLE PRECISION NOT NULL,
    cumulative_cost_usd         DOUBLE PRECISION NOT NULL,
    cumulative_net_pnl_usd      DOUBLE PRECISION NOT NULL,
    
    -- état book à ce moment
    n_open_positions    INTEGER NOT NULL,
    book_vega           DOUBLE PRECISION,
    book_gamma          DOUBLE PRECISION,
    book_theta          DOUBLE PRECISION,
    book_delta          DOUBLE PRECISION,
    
    -- equity & drawdown
    equity_usd          DOUBLE PRECISION NOT NULL,   -- starting_capital + cumulative_net_pnl
    drawdown_pct        DOUBLE PRECISION NOT NULL    -- (peak - current) / peak
);

CREATE INDEX ix_backtest_mtm_run_ts ON backtest_mtm_history (run_id, timestamp);
```

**Cardinalité** : 1 row / cycle de test × 720 cycles/mois × 48 folds × 50 runs = ~1.7M. Indexer correctement (run_id+timestamp), partition possible si > 10M.

---

### 7.5 `backtest_capacity_curves` — analyse capacity

Estimation Sharpe vs notional simulé. Utile pour comprendre quand l'edge se dégrade avec la taille.

```sql
CREATE TABLE backtest_capacity_curves (
    id              BIGSERIAL PRIMARY KEY,
    run_id          BIGINT NOT NULL REFERENCES backtest_runs(id),
    
    notional_per_trade_usd      DOUBLE PRECISION NOT NULL,  -- 10k, 50k, 100k, 500k, 1M, 5M
    
    -- métriques à ce notional
    sharpe_oos_at_notional      DOUBLE PRECISION,
    avg_trade_pnl_at_notional   DOUBLE PRECISION,
    avg_slippage_at_notional    DOUBLE PRECISION,
    
    n_simulations               INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX ix_backtest_capacity_run ON backtest_capacity_curves (run_id, notional_per_trade_usd);
```

**Cardinalité** : ~6-10 niveaux notional × 50 runs = 300-500. Rien.

---

### 7.6 `backtest_skipped_cycles` — diagnostic des cycles non-tradés

Critique pour comprendre où le système se gate-out. Aide à calibrer thresholds.

```sql
CREATE TABLE backtest_skipped_cycles (
    id              BIGSERIAL PRIMARY KEY,
    run_id          BIGINT NOT NULL REFERENCES backtest_runs(id),
    fold_id         BIGINT NOT NULL REFERENCES backtest_folds(id),
    
    timestamp       TIMESTAMPTZ NOT NULL,
    skip_reason     TEXT NOT NULL,                   -- 'gate_step1' | 'no_actionable_signal' | 'incoherent_signals' | ...
    skip_detail     JSONB                            -- détail spécifique au reason
);

CREATE INDEX ix_backtest_skipped_run ON backtest_skipped_cycles (run_id);
CREATE INDEX ix_backtest_skipped_reason ON backtest_skipped_cycles (skip_reason, run_id);
```

**Cardinalité** : peut être grosse (la majorité des cycles est skip en pratique). Estimation : 720 cycles/mois × 48 folds × 50 runs × 80% skip = ~1.4M. Compressible.

---

## 8. Schéma relationnel

```
┌──────────────────────┐
│   backtest_runs      │
│   (1 / run)          │
└──────────┬───────────┘
           │ 1:N
           ▼
┌──────────────────────┐      ┌──────────────────────────┐
│   backtest_folds     │─────►│  backtest_skipped_cycles │
│   (~48 / run)        │      │  (gates diagnostic)      │
└──────────┬───────────┘      └──────────────────────────┘
           │ 1:N
           ▼
┌──────────────────────┐      ┌──────────────────────────┐
│   backtest_trades    │      │   backtest_mtm_history   │
│  (~5-20 / fold)      │      │  (~720 / fold)           │
└──────────────────────┘      └──────────────────────────┘
                                           │
           ┌───────────────────────────────┘
           ▼
┌──────────────────────┐
│ backtest_capacity_   │
│   curves             │
│ (computed once /run) │
└──────────────────────┘
```

---

## 9. Métriques calculées et leur interprétation

### 9.1 Sharpe IS vs OOS (overfit detector)

```python
def compute_sharpe(pnl_series: pd.Series, risk_free_rate: float = 0.04) -> float:
    """Annualized Sharpe ratio."""
    daily_returns = pnl_series.pct_change().dropna()
    excess = daily_returns - risk_free_rate / 252
    if excess.std() == 0:
        return np.nan
    return np.sqrt(252) * excess.mean() / excess.std()

# Decision rules
sharpe_is = compute_sharpe(in_sample_equity_curve)
sharpe_oos = compute_sharpe(out_of_sample_equity_curve)

if sharpe_is - sharpe_oos > sharpe_oos:
    flag_overfit_severe()
elif sharpe_is - sharpe_oos > 0.5:
    flag_overfit_moderate()
```

Interprétation :
- Sharpe IS > Sharpe OOS toujours : c'est normal (in-sample = fit)
- Gap > Sharpe OOS : overfit sévère, modèle apprend du bruit
- Sharpe OOS < 0.5 : pas d'edge exploitable
- Sharpe OOS net (post-cost) < 0.3 : cost domine

### 9.2 Maximum drawdown

```python
def compute_max_drawdown(equity_curve: pd.Series) -> tuple[float, datetime, datetime]:
    """Returns max DD pct, date_peak, date_trough."""
    running_max = equity_curve.cummax()
    drawdown = (equity_curve - running_max) / running_max
    max_dd_pct = abs(drawdown.min()) * 100
    
    date_trough = drawdown.idxmin()
    date_peak = equity_curve.loc[:date_trough].idxmax()
    
    return max_dd_pct, date_peak, date_trough
```

Interprétation :
- Drawdown < 15% : exceptionnel (probablement size trop petit)
- Drawdown 15-30% : acceptable
- Drawdown > 30% : strategy non viable même avec Sharpe positif (psychologie + capital constraint)

### 9.3 Hit rate par PC

```python
def compute_hit_rate_per_pc(trades: list[BacktestTrade]) -> dict:
    """% of trades with positive net P&L per triggering PC."""
    by_pc = defaultdict(list)
    for t in trades:
        by_pc[t.triggering_pc].append(t.net_pnl_usd > 0)
    
    return {pc: np.mean(wins) for pc, wins in by_pc.items()}
```

Interprétation :
- Vol long (straddle, BF long) : hit rate 35-45% normal (P&L skewed, gros gains rares)
- Vol short (strangle short, BF short) : hit rate 60-70% normal (theta steady, gros pertes rares)
- PC2 calendar trades : 50-55% normal
- Hit rate uniforme entre PCs et incohérent avec structure type → quelque chose ne va pas

### 9.4 Capacity curve

```python
def simulate_capacity(trades: list[BacktestTrade], notional_levels: list[float]) -> dict:
    """Re-simulate P&L at different notional sizes, accounting for slippage."""
    results = {}
    for notional in notional_levels:
        scaled_pnl = []
        for t in trades:
            scale = notional / t.entry_premium_usd
            slippage = compute_slippage(t.legs, scale)  # increases nonlinearly
            scaled_net = t.gross_pnl_usd * scale - t.total_cost_usd * scale - slippage
            scaled_pnl.append(scaled_net)
        results[notional] = compute_sharpe(pd.Series(scaled_pnl))
    return results
```

Interprétation :
- Sharpe constant jusqu'à $1M/trade → strategy a capacity
- Sharpe collapse rapide → strategy crowded ou taille trop grande pour FOP liquidity

### 9.5 P&L attribution

Décomposer le P&L gross en sources :

```python
def attribute_pnl(trade: BacktestTrade) -> dict:
    """Decompose gross P&L into vega, gamma, theta contributions."""
    # Calcul via finite differences sur la trajectoire IV/spot/time
    return {
        "vega_pnl": vega_pnl,        # IV change × vega
        "gamma_pnl": gamma_pnl,      # spot move² × gamma / 2
        "theta_pnl": theta_pnl,      # days × theta (négatif normalement)
        "other_pnl": gross_pnl - sum([vega, gamma, theta]),  # jumps, second-order
    }
```

Interprétation :
- Vega P&L domine : strategy capture vraiment le VRP
- Gamma P&L domine : strategy fait du gamma scalping (différent objectif)
- Theta P&L négatif important : structure trop longue, pas de capture vol

---

## 10. Estimation effort par sous-tâche

| Sous-tâche | Effort | Bloquant ? |
|---|---|---|
| Migration Postgres : 6 tables + indices | 0.5 j | Oui |
| Backtest harness skeleton (orchestrateur) | 1 j | Oui |
| `_fit_pca_isolated` avec strict isolation | 1 j | Oui — point différenciateur |
| `_reconstruct_regime` lookback only | 1 j | Oui — point différenciateur (anti lookahead) |
| Cost model implémentation + validation | 2-3 j | Oui — sans cost = données fausses |
| Position book in-memory state machine | 1 j | Oui |
| MTM history tracking par cycle | 0.5 j | Oui |
| Sharpe + drawdown + hit rate computations | 1 j | Oui |
| P&L attribution decomposition | 1.5 j | Oui — point différenciateur |
| Capacity curve simulation | 1 j | Non (peut être en V2) |
| Validation gates (gate 1-7) | 0.5 j | Oui |
| Skipped cycles diagnostic logging | 0.5 j | Oui — debug essentiel |
| Notebook reporting (Sharpe curves, DD plot, attribution heatmap) | 2 j | Oui pour interview signaling |
| Tests : isolation fit/test, cost calibration, walk-forward sliding | 2 j | Oui |
| **Total MVP fonctionnel** | **~16 jours dev** | |

---

## 11. Stratégie de premier run (avant tout résultat exploitable)

Démarrer simple, ajouter complexité progressivement :

### Run 1 — Sanity check
- Période : 6 mois de data (3 fit + 3 test, single-split, pas walk-forward)
- Cost model : simplifié (spread fixe = 0.8 vol pts ATM, commission $1/contract)
- Aucune sizing optimization, qty fixe = 1 contract par leg
- Objectif : confirmer pipeline tourne end-to-end, no crash, P&L nombres sensibles

### Run 2 — Walk-forward minimal
- Période : 12 mois (6 fit, 6 test sur 6 folds mensuels)
- Cost model complet
- Sizing trivial (qty = 1)
- Objectif : vérifier walk-forward isolation correcte

### Run 3 — Plein
- Période : tout l'historique disponible (idéalement 3-5 ans)
- Walk-forward standard (12 fit, 1 test, sliding monthly)
- Sizing selon règles step 3
- Objectif : métriques production-grade pour décision validation

### Run 4+ — Sensitivity analysis
- Vary 1 paramètre à la fois (fit window, refit frequency, threshold actionable)
- **Documenter chaque variation comme run distinct** dans `backtest_runs.notes`
- Rejeter toute tentation de "garder le meilleur" sans walk-forward chaque variante

---

## 12. Tests à écrire (acceptance criteria)

```python
# test_backtest_pipeline.py

def test_fit_isolation_no_lookahead(db):
    """Fit at t=2025-06-01 must NOT use any data with timestamp > 2025-06-01"""
    # Insert future snapshot
    db.add(SurfaceSnapshotHourly(timestamp=datetime(2025, 7, 1), iv_3m_atm=10.0))
    
    fit = harness._fit_pca_isolated(
        fit_start=datetime(2025, 5, 1),
        fit_end=datetime(2025, 6, 1)
    )
    
    # Vérifier que le fit n'a pas vu la row de juillet
    assert fit.fit_window_end == datetime(2025, 6, 1)
    # Means/stds doivent être calculés sans la row de juillet
    
def test_reconstruct_regime_uses_only_past_data(db):
    """Regime reconstruction at t doit utiliser SEULEMENT data avec timestamp < t"""
    # Setup features avec timestamps spread sur 90 jours avant t
    # Insert one feature with timestamp > t
    
    regime = harness._reconstruct_regime(cycle_ts=datetime(2025, 6, 15))
    # Vérifier que vol_of_vol calculé n'inclut pas la future feature
    
def test_walk_forward_sliding(db):
    """Folds must slide by 1 month, with proper overlap of fit windows"""
    folds = harness._generate_folds(BacktestConfig(
        historical_start=datetime(2024, 1, 1),
        historical_end=datetime(2025, 12, 31),
        fit_window_months=12,
        test_window_months=1
    ))
    
    assert len(folds) == 12  # 12 folds = 12 mois OOS
    for i in range(len(folds) - 1):
        assert folds[i+1].fit_start == folds[i].fit_start + timedelta(days=30)
        assert folds[i+1].test_start == folds[i].test_start + timedelta(days=30)

def test_cost_model_significant():
    """Cost ne doit pas être négligeable vs gross P&L typique"""
    structure = build_test_straddle()
    cost = compute_entry_cost(structure, mock_snapshot)
    
    expected_min_cost = structure.notional * 0.001  # 10 bps minimum
    assert cost.total > expected_min_cost

def test_sharpe_overfit_detector():
    """Si Sharpe IS=2.0 et OOS=0.3, gap > OOS → flag overfit"""
    metrics = BacktestMetrics(sharpe_is=2.0, sharpe_oos=0.3, ...)
    verdict = backtest_validates_strategy(metrics)
    assert verdict.passed is False
    assert verdict.reason == "overfitting_detected"

def test_position_book_no_carry_over_between_folds():
    """Toutes les positions doivent être closed à fin de fold"""
    harness._run_fold(run, fold_idx=0, fold=fold0)
    
    open_at_fold_end = db.query(BacktestTrade).filter(
        BacktestTrade.fold_id == fold0.id,
        BacktestTrade.exit_ts.is_(None)
    ).count()
    
    assert open_at_fold_end == 0  # toutes closed avec exit_reason='fold_end'

def test_pnl_attribution_sums_to_gross():
    """vega + gamma + theta + other = gross P&L (réconciliation)"""
    trade = create_test_trade()
    attr = attribute_pnl(trade)
    
    total = attr["vega_pnl"] + attr["gamma_pnl"] + attr["theta_pnl"] + attr["other_pnl"]
    assert abs(total - trade.gross_pnl_usd) < 0.01  # à 1 cent près
```

---

## 13. Ce qui n'est PAS dans ce backtest (et où ça ira)

| Concept | Étape future |
|---|---|
| Stress test sur périodes choisies (2008, 2015 SNB, COVID, 2022) | Doc `STRESS_TEST.md` séparé |
| Optimisation hyperparamètres (grid search, bayesian) | Pas dans ce projet — overfitting déguisé |
| Cross-validation par régime (fit séparé calm vs stressed) | V2 du PCA, pas backtest |
| Backtest multi-instrument (ajouter GBP/USD, USD/JPY) | Hors scope étape 2 (mono-symbol) |
| Live paper trading parallèle au live signal generation | Phase post-validation |
| Monte Carlo des paths futurs | Non, walk-forward suffit |

---

## 14. Definition of done — backtest validé

Le backtest est livré et exploité quand :

- [ ] 6 tables Postgres créées
- [ ] Backtest harness exécute Run 1 sans crash
- [ ] Cost model validé sur 5+ trades historiques (cost prédit ≈ cost observé ±30%)
- [ ] Run 3 complet exécuté sur ≥ 18 mois historique
- [ ] Métriques agrégées populated dans `backtest_runs.sharpe_oos`, `max_drawdown_pct`, etc.
- [ ] Validation gates exécutés, verdict stored
- [ ] Notebook reporting généré : equity curve gross+net, drawdown plot, P&L attribution heatmap par PC, capacity curve
- [ ] Skipped cycles analysis : > 70% des cycles skipped est normal et expliqué
- [ ] Sharpe IS-OOS gap documenté et interprété
- [ ] Tests automatisés passent : isolation fit/test, no lookahead, walk-forward sliding, cost calibration, P&L attribution réconciliation

**Decision gate post-backtest** :
- Si `validation_verdict = "passed"` : autorisation paper trading (toujours pas live)
- Si `passed_with_warning` : analyse warning, decision case-by-case
- Si `failed` : retour aux étapes 2/3 pour comprendre pourquoi (loadings instables ? cost trop haut ? threshold mal calibré ?)

---

## 15. Décisions de design notables (pour `DECISIONS.md`)

1. **Walk-forward refit mensuel** plutôt que continu (à chaque cycle). Trade-off computation vs fidélité. Mensuel = compromise standard quant moderne.

2. **Aucune carry-over de positions entre folds**. Force-close à fin de fold, exit_reason='fold_end'. Évite contamination temporelle entre folds. Coût : surestimation de turnover, mais c'est conservateur.

3. **Cost model OBLIGATOIRE en V1**. Pas de "Sharpe gross" sans Sharpe net. Empêche l'illusion d'edge.

4. **MTM tracking par cycle, pas seulement à exit**. Permet equity curve continue, drawdown calculé proprement.

5. **`backtest_skipped_cycles` row par skip**. Volumineux mais critique pour debug. Sans ça, impossible de calibrer les thresholds.

6. **Validation gates strictes** (Sharpe min, DD max, hit rate min, capacity). Mieux vaut un backtest qui dit "FAIL" qu'un backtest qui dit "PASS" sur du bruit.

7. **Pas de warm-up pour le book** : à chaque fold, position book commence vide. Conservateur.

8. **Pas d'optimization paramètre dans le backtest harness**. Si tu veux tester N variantes, lance N runs distincts. Empêche le data snooping.

---

## 16. Ouvertures (limitations connues à documenter dans le rapport)

1. **Hindsight bias résiduel sur vrp_table** : si tu utilises `vrp_table_default` même historique, tu fais comme si les valeurs avaient été connues en 2024. C'est faux. À long terme : utiliser `vrp_estimated` rolling.

2. **Pas de modélisation slippage non-linéaire** au-delà d'une heuristique simple. Pour notional > $5M, slippage devient mal modélisé.

3. **Hedging cost approximé** par heuristique nombre de rebalances. Vrai cost dépend de path réalisé. À itérer.

4. **Mono-instrument EUR/USD** : pas de cross-currency vol arbitrage capturé.

5. **Pas de modélisation des interruptions trading** (crash IB, weekend gaps, holidays). Petite source d'erreur.

6. **PCA refit mensuel calé sur calendrier**, pas event-driven. Un changement de régime majeur (ex: SNB 2015) ne déclenche pas refit immédiat.

Ces limitations doivent être listées dans le rapport final pour signaling pro — montrer qu'on connaît les angles morts du backtest.
