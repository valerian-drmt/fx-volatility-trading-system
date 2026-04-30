# Étape 5 — Active Positions (Panel 4 monitoring + exit rules + delta hedge)

> Spec de la cinquième et dernière étape du workflow trading vol.
>
> **Objectif fonctionnel** : pour chaque position ouverte (créée par étape 4), maintenir un état mark-to-market continu, vérifier le signal d'origine, appliquer les règles de sortie systématique, et exécuter le delta hedge récurrent automatiquement.
>
> **Prérequis** :
> - Étape 4 livrée (positions créées avec `state='open'`)
> - Étape 2 toujours active (signaux PCA continuent d'être produits pour comparison)
> - Étape 3 toujours active (pour pouvoir générer un closing trade preview si besoin)
>
> **Pas dans cette étape** :
> - Création de nouvelles positions (étape 3+4)
> - Backtest des règles d'exit (cf. backtest doc, étape 5 doit être une fonction du backtest)
>
> **Audience** : agent code (Claude Code) qui implémente. Spec auto-suffisante.

---

## 1. Système formel

| Élément | Spec |
|---|---|
| Agents | Position monitor service (cycle 30-60s), exit rules engine, delta hedge engine, P&L attributor |
| États position | `open → closing → closed | expired` |
| États exit signal | `hold → trim → exit_recommended` |
| Inputs requis | Surface IV courante, signal PCA courant (du modèle qui a triggered), spot courant, position book |
| Outputs | MTM continue, exit alerts, hedge orders, P&L attribution finale |
| Contrainte temporelle | Cycle monitoring < 60s (pas 180s comme vol-engine — réactivité critique) |
| Contrainte de cohérence | Exit decision = automatique (pas humaine) si règles déclenchent |

---

## 2. Décision logic — du monitoring à la sortie

```python
def monitor_position_cycle(position: Position, surface: dict, current_signals: dict) -> MonitorResult:
    
    # 1. Mark-to-market
    mtm = compute_mtm(position, surface)
    
    # 2. Update aggregated greeks (changent dans le temps)
    current_greeks = compute_current_greeks(position, surface)
    
    # 3. Check exit rules (toutes en parallèle)
    exit_decisions = []
    for rule in EXIT_RULES:
        decision = rule.evaluate(position, mtm, current_signals, surface)
        if decision.triggered:
            exit_decisions.append(decision)
    
    # 4. Check delta hedge
    hedge_decision = check_delta_hedge_needed(position, current_greeks, surface)
    
    # 5. Persist MTM history row
    persist_mtm_snapshot(position, mtm, current_greeks)
    
    # 6. Si exit triggered, action
    if exit_decisions:
        # Priorité : la plus prudente (signal_reverse > stop_loss > time_based > expiry)
        triggered = max(exit_decisions, key=lambda d: d.priority)
        
        if triggered.action == "EXIT":
            initiate_position_close(position, reason=triggered.rule_name)
        elif triggered.action == "TRIM":
            initiate_position_trim(position, reduction_pct=0.5, reason=triggered.rule_name)
        elif triggered.action == "ALERT_ONLY":
            log_alert(position, triggered)
    
    # 7. Si hedge needed, execute
    if hedge_decision.needs_hedge:
        execute_delta_hedge(position, hedge_decision.hedge_qty, hedge_decision.side)
    
    return MonitorResult(
        position_id=position.id,
        mtm=mtm,
        current_greeks=current_greeks,
        exit_decisions=exit_decisions,
        hedge_executed=hedge_decision.needs_hedge,
    )
```

---

## 3. Exit rules — les 4 règles systématiques

Du user guide §Panel 4 "Règles de sortie systématique" :

### 3.1 Rule 1 — Signal reverse

```python
class SignalReverseRule(ExitRule):
    name = "signal_reverse"
    priority = 4  # priorité max
    
    def evaluate(self, position, mtm, current_signals, surface) -> ExitDecision:
        # Récupère le signal d'origine
        original_signal = position.entry_signal  # snapshot au moment de l'entrée
        
        # Compare au signal courant pour le même PC
        current = current_signals.get(f"pc{original_signal.pc_id}")
        if current is None:
            return ExitDecision(triggered=False)
        
        # Trigger 1: z-score a flippé de signe
        signal_flipped = (original_signal.z_score > 0) != (current.z_score > 0)
        
        # Trigger 2: |z| descendu sous 0.5
        signal_too_weak = abs(current.z_score) < 0.5
        
        if signal_flipped or signal_too_weak:
            return ExitDecision(
                triggered=True,
                rule_name=self.name,
                action="EXIT",
                priority=self.priority,
                detail={
                    "original_z": original_signal.z_score,
                    "current_z": current.z_score,
                    "reason_subtype": "flipped" if signal_flipped else "weakened"
                }
            )
        
        # Trigger 3: signal s'est affaibli >50% depuis entrée → TRIM
        weakened_50pct = abs(current.z_score) < 0.5 * abs(original_signal.z_score)
        if weakened_50pct:
            return ExitDecision(
                triggered=True,
                rule_name=self.name,
                action="TRIM",
                priority=self.priority - 1,
                detail={
                    "weakening_ratio": abs(current.z_score) / abs(original_signal.z_score)
                }
            )
        
        return ExitDecision(triggered=False)
```

### 3.2 Rule 2 — Time-based

```python
class TimeBasedRule(ExitRule):
    name = "time_based"
    priority = 2
    
    def evaluate(self, position, mtm, current_signals, surface) -> ExitDecision:
        # Si T_remaining < 0.3 × T_entry, theta dominant → exit
        days_at_entry = position.dte_at_entry
        days_remaining = (position.expiry_date - datetime.utcnow().date()).days
        
        ratio = days_remaining / days_at_entry
        
        if ratio < 0.3:
            return ExitDecision(
                triggered=True,
                rule_name=self.name,
                action="EXIT",
                priority=self.priority,
                detail={
                    "days_remaining": days_remaining,
                    "days_at_entry": days_at_entry,
                    "ratio": round(ratio, 2)
                }
            )
        return ExitDecision(triggered=False)
```

### 3.3 Rule 3 — Stop loss en vega

```python
class StopLossVegaRule(ExitRule):
    name = "stop_loss_vega"
    priority = 3
    
    def evaluate(self, position, mtm, current_signals, surface) -> ExitDecision:
        # P&L < -3 × vega_at_entry signifie IV a bougé 3 vol pts contre nous
        loss_threshold = -3 * abs(position.entry_vega_usd_per_volpt)
        
        if mtm.pnl_usd < loss_threshold:
            return ExitDecision(
                triggered=True,
                rule_name=self.name,
                action="EXIT",
                priority=self.priority,
                detail={
                    "current_pnl_usd": mtm.pnl_usd,
                    "loss_threshold_usd": loss_threshold,
                    "implied_iv_move_volpts": mtm.pnl_usd / position.entry_vega_usd_per_volpt
                }
            )
        return ExitDecision(triggered=False)
```

### 3.4 Rule 4 — Time to expiry critical

```python
class TimeToExpiryCriticalRule(ExitRule):
    name = "time_to_expiry_critical"
    priority = 5  # priorité absolue, override tout
    
    def evaluate(self, position, mtm, current_signals, surface) -> ExitDecision:
        # T_remaining < 7 jours → forcément sortir (theta extrême + gamma incontrôlable)
        days_remaining = (position.expiry_date - datetime.utcnow().date()).days
        
        if days_remaining < 7:
            return ExitDecision(
                triggered=True,
                rule_name=self.name,
                action="EXIT",
                priority=self.priority,
                detail={"days_remaining": days_remaining}
            )
        return ExitDecision(triggered=False)
```

### 3.5 Rule 5 — Régime change pre_event (override depuis étape 1)

```python
class PreEventRegimeRule(ExitRule):
    name = "pre_event_regime"
    priority = 6  # max absolu
    
    def evaluate(self, position, mtm, current_signals, surface) -> ExitDecision:
        # Si régime devient pre_event, le user guide §Panel 1 dit "sortir positions existantes si possible"
        regime = surface.get("_regime", {}).get("label")
        
        if regime == "pre_event":
            return ExitDecision(
                triggered=True,
                rule_name=self.name,
                action="EXIT",
                priority=self.priority,
                detail={"regime": regime, "reason": "pre_event_detected"}
            )
        return ExitDecision(triggered=False)
```

---

## 4. Delta hedge — règle de re-balancing

```python
def check_delta_hedge_needed(position, current_greeks, surface) -> HedgeDecision:
    """Trigger hedge si |delta| > 0.05 (=5% du notional contract)"""
    
    abs_delta = abs(current_greeks.delta_unhedged)
    threshold = HEDGE_THRESHOLD  # 0.05 par défaut, configurable
    
    if abs_delta < threshold:
        return HedgeDecision(needs_hedge=False)
    
    # Compute hedge qty
    # Rappel : 1 future EUR/USD = 125000 EUR notional
    # Delta exprimé en unités d'underlying
    hedge_qty_fractional = -current_greeks.delta_unhedged
    
    # Round to nearest integer (futures sont integer)
    hedge_qty = round(hedge_qty_fractional)
    if hedge_qty == 0:
        return HedgeDecision(needs_hedge=False)  # arrondi à 0
    
    return HedgeDecision(
        needs_hedge=True,
        hedge_qty=abs(hedge_qty),
        side="BUY" if hedge_qty > 0 else "SELL",
        post_hedge_residual_delta=current_greeks.delta_unhedged + hedge_qty
    )
```

---

## 5. Le panel (UI) — Panel 4 Active Positions Monitor

| Section | Contenu | Source data | Statut |
|---|---|---|---|
| A — Open structures table | Liste positions avec : ID, struct type, DTE, entry signal, current signal, P&L $, vega $, action recommandée | `positions` + `mtm_snapshots latest` + `current_signals` | À implémenter |
| B — Aggregate greeks | Vega total par tenor (bar chart), gamma total, theta total, net delta | aggregation `position_greeks_history` | À implémenter |
| C — Delta hedge status | Current imbalance, rebalance trigger, last hedge ago | `hedge_orders latest` | À implémenter |
| D — Exit alerts | Liste des positions avec exit recommendation active, raison, urgence | `exit_alerts active` | À implémenter |

---

## 6. Schema des payloads

### 6.1 Payload position monitoring (publié sur WS `/ws/positions`)

```jsonc
{
  "type": "position_update",
  "timestamp": "2026-04-30T15:30:00Z",
  "position_id": 6789,
  "structure_id": 12345,
  
  "mtm": {
    "current_pnl_usd":         1240,
    "current_pnl_pct":         32.4,                    // vs entry premium
    "vega_pnl_usd":            980,                     // attribution
    "gamma_pnl_usd":           320,
    "theta_pnl_usd":           -180,
    "other_pnl_usd":           120
  },
  
  "current_greeks": {
    "vega_usd_per_volpt":      720,                     // peut avoir baissé vs entry (theta decay du vega)
    "gamma_usd_per_pip2":      1.9,
    "theta_usd_per_day":       -75,
    "delta_unhedged":          0.03,
    "delta_post_hedge":        0.00
  },
  
  "signal_status": {
    "entry_pc":                1,
    "entry_z":                 1.8,
    "entry_label":             "CHEAP",
    "current_z":               1.4,
    "current_label":           "CHEAP",
    "weakening_ratio":         0.78,                    // current/entry
    "status":                  "HOLD"                    // HOLD | TRIM | EXIT
  },
  
  "exit_alerts": [
    // Vide si pas d'alerte, sinon liste des règles déclenchées
  ],
  
  "delta_hedge": {
    "last_hedge_at":           "2026-04-30T13:16:00Z",
    "minutes_since_last":      134,
    "current_imbalance":       0.03,
    "rebalance_needed":        false
  }
}
```

### 6.2 Payload exit alert (publié sur WS `/ws/exit_alerts`)

```jsonc
{
  "type": "exit_alert",
  "timestamp": "2026-04-30T15:30:00Z",
  "position_id": 6789,
  "structure_id": 12345,
  
  "rule_triggered":      "signal_reverse",
  "action":              "EXIT",                        // EXIT | TRIM | ALERT_ONLY
  "priority":            4,
  
  "rule_detail": {
    "original_z":        1.8,
    "current_z":         -0.3,
    "reason_subtype":    "flipped"
  },
  
  "auto_executed":       true,                          // si système auto-execute
  "execution_status":    "in_progress"                  // in_progress | done | failed
}
```

---

## 7. Tables Postgres nécessaires

### 7.1 `position_mtm_history` — séries P&L mark-to-market continu

Une row par position par cycle monitoring. Permet equity curve, drawdown, attribution dynamique.

```sql
CREATE TABLE position_mtm_history (
    id              BIGSERIAL PRIMARY KEY,
    position_id     BIGINT NOT NULL REFERENCES positions(id) ON DELETE RESTRICT,
    
    timestamp       TIMESTAMPTZ NOT NULL,
    
    -- prices courants
    spot                DOUBLE PRECISION NOT NULL,
    iv_avg_legs_pct     DOUBLE PRECISION,               -- IV moyenne pondérée des legs
    
    -- P&L
    current_pnl_gross_usd       DOUBLE PRECISION NOT NULL,  -- mark - entry, sans déduire cost
    current_pnl_net_usd         DOUBLE PRECISION NOT NULL,  -- gross - entry_cost - hedge_cost_cumul
    
    -- attribution (depuis entry)
    vega_pnl_usd        DOUBLE PRECISION,               -- (iv_now - iv_entry) × vega_avg
    gamma_pnl_usd       DOUBLE PRECISION,
    theta_pnl_usd       DOUBLE PRECISION,
    other_pnl_usd       DOUBLE PRECISION,               -- residual jumps + 2nd order
    
    -- greeks courants
    current_vega_usd_per_volpt      DOUBLE PRECISION,
    current_gamma_usd_per_pip2      DOUBLE PRECISION,
    current_theta_usd_per_day       DOUBLE PRECISION,
    current_delta_unhedged          DOUBLE PRECISION,
    
    UNIQUE (position_id, timestamp)
);

CREATE INDEX ix_mtm_position_ts ON position_mtm_history (position_id, timestamp DESC);
CREATE INDEX ix_mtm_timestamp ON position_mtm_history (timestamp DESC);
```

**Cardinalité** : 1 row / cycle (60s) / position. Pour 1-3 positions ouvertes en moyenne sur 1 an : ~1.5M rows. Indexer correctement, partitionner par mois si > 10M.

---

### 7.2 `hedge_orders` — orders de delta hedge récurrents

Distinct des `orders` (qui sont les orders d'entrée/sortie de structure). Hedges = orders future en cours de vie de position.

```sql
CREATE TABLE hedge_orders (
    id              BIGSERIAL PRIMARY KEY,
    position_id     BIGINT NOT NULL REFERENCES positions(id),
    
    -- timing
    triggered_at        TIMESTAMPTZ NOT NULL,           -- quand le check delta a triggered
    submitted_at        TIMESTAMPTZ,
    filled_at           TIMESTAMPTZ,
    
    -- decision context
    delta_imbalance_at_trigger      DOUBLE PRECISION NOT NULL,
    rebalance_threshold_used        DOUBLE PRECISION NOT NULL,
    
    -- hedge spec
    hedge_qty           INTEGER NOT NULL,               -- positif = nombre de futures
    side                TEXT NOT NULL,                  -- 'BUY' | 'SELL'
    
    -- IB execution
    ib_order_id         TEXT,
    fill_price          DOUBLE PRECISION,
    commission_usd      DOUBLE PRECISION,
    spread_paid_usd     DOUBLE PRECISION,               -- bid-ask half × qty
    total_cost_usd      DOUBLE PRECISION,
    
    state               TEXT NOT NULL,                  -- 'pending' | 'submitted' | 'filled' | 'failed'
    
    CONSTRAINT chk_side CHECK (side IN ('BUY', 'SELL')),
    CONSTRAINT chk_state CHECK (state IN ('pending', 'submitted', 'filled', 'failed'))
);

CREATE INDEX ix_hedge_position ON hedge_orders (position_id, triggered_at DESC);
CREATE INDEX ix_hedge_pending ON hedge_orders (state) WHERE state IN ('pending', 'submitted');
```

**Cardinalité** : variable. Estimation : 5-20 hedges / position / lifetime × 500-1500 positions = 5k-30k / an. Trivial.

---

### 7.3 `exit_alerts` — log des alertes d'exit déclenchées

Log de chaque trigger de règle, qu'elle ait été agie sur ou pas.

```sql
CREATE TABLE exit_alerts (
    id              BIGSERIAL PRIMARY KEY,
    position_id     BIGINT NOT NULL REFERENCES positions(id),
    
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    rule_triggered      TEXT NOT NULL,                  -- 'signal_reverse' | 'time_based' | 'stop_loss_vega' | 'time_to_expiry_critical' | 'pre_event_regime'
    action_recommended  TEXT NOT NULL,                  -- 'EXIT' | 'TRIM' | 'ALERT_ONLY'
    priority            INTEGER NOT NULL,
    
    rule_detail         JSONB NOT NULL,                 -- détail spécifique à la règle
    
    -- action prise
    auto_executed       BOOLEAN NOT NULL DEFAULT false,
    execution_status    TEXT,                           -- 'in_progress' | 'done' | 'failed' | 'overridden'
    closing_structure_id BIGINT REFERENCES structures(id),  -- structure de fermeture créée si EXIT
    
    notes               TEXT,
    
    CONSTRAINT chk_action CHECK (action_recommended IN ('EXIT', 'TRIM', 'ALERT_ONLY')),
    CONSTRAINT chk_exec_status CHECK (
        execution_status IS NULL OR 
        execution_status IN ('in_progress', 'done', 'failed', 'overridden')
    )
);

CREATE INDEX ix_exit_alerts_position ON exit_alerts (position_id, timestamp DESC);
CREATE INDEX ix_exit_alerts_active ON exit_alerts (timestamp DESC) 
    WHERE auto_executed = false OR execution_status = 'in_progress';
```

**Cardinalité** : 1-10 alerts / position lifetime × 1500 positions = 1.5k-15k / an. Trivial.

---

### 7.4 `closing_orders` — orders de fermeture de position

Quand une exit rule trigger EXIT, on crée un trade closing (opposite side de l'entry). Distinct des `orders` (entrée) et `hedge_orders` (delta hedging).

Actually, **conceptuellement c'est le même que `orders`**, juste avec un flag indiquant que c'est une closing. Choix de design : ajouter colonne à `orders` plutôt que table séparée.

```sql
-- Modification de la table orders existante (étape 4)
ALTER TABLE orders ADD COLUMN order_role TEXT NOT NULL DEFAULT 'entry';
ALTER TABLE orders ADD CONSTRAINT chk_order_role 
    CHECK (order_role IN ('entry', 'closing', 'unwind'));

-- Quand on close une position, on crée :
-- 1. Une nouvelle structure avec close_reason populated
-- 2. Des orders avec order_role='closing' pour chaque leg (opposite side)
-- 3. Au fully_filled, on update positions.state = 'closed'
```

Pas de nouvelle table. Le tracking se fait par jointure `closing_structures.preview_id IS NULL` + `orders.order_role='closing'`.

---

### 7.5 `position_signal_tracking` — état du signal d'origine au cours du temps

Pour la zone "Current signal" du Panel 4 sans recomputer à chaque affichage.

```sql
CREATE TABLE position_signal_tracking (
    id              BIGSERIAL PRIMARY KEY,
    position_id     BIGINT NOT NULL REFERENCES positions(id),
    
    timestamp       TIMESTAMPTZ NOT NULL,
    
    -- snapshot signal courant (pour le PC d'origine)
    triggering_pc       INTEGER NOT NULL,
    current_z_score     DOUBLE PRECISION NOT NULL,
    current_label       TEXT NOT NULL,
    
    -- comparison avec entry
    entry_z_score       DOUBLE PRECISION NOT NULL,
    entry_label         TEXT NOT NULL,
    
    weakening_ratio     DOUBLE PRECISION,               -- abs(current_z) / abs(entry_z)
    sign_flipped        BOOLEAN NOT NULL,
    
    -- status calculé
    status              TEXT NOT NULL,                  -- 'HOLD' | 'TRIM' | 'EXIT'
    
    UNIQUE (position_id, timestamp),
    CONSTRAINT chk_status CHECK (status IN ('HOLD', 'TRIM', 'EXIT'))
);

CREATE INDEX ix_position_signal_position_ts ON position_signal_tracking (position_id, timestamp DESC);
```

**Cardinalité** : 1 row / position / cycle = ~1.5M / an pour positions actives sur le long terme. Partition possible.

---

### 7.6 `exit_rules_config` — config hot-reloadable des règles

Permet d'ajuster thresholds sans redeploy.

```sql
CREATE TABLE exit_rules_config (
    id              SERIAL PRIMARY KEY,
    
    rule_name           TEXT NOT NULL UNIQUE,
    is_active           BOOLEAN NOT NULL DEFAULT true,
    priority            INTEGER NOT NULL,
    
    -- params (varient par règle, JSONB pour flexibilité)
    params              JSONB NOT NULL,
    
    description         TEXT,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by          TEXT,
    
    CONSTRAINT chk_priority CHECK (priority BETWEEN 1 AND 10)
);

INSERT INTO exit_rules_config (rule_name, priority, params, description) VALUES
    ('signal_reverse', 4, 
     '{"flip_triggers_exit": true, "weak_threshold": 0.5, "weakening_50pct_triggers_trim": true}',
     'Exit if signal flipped or weakened to <0.5; trim if weakened >50%'),
    
    ('time_based', 2,
     '{"time_remaining_ratio_threshold": 0.3}',
     'Exit if days_remaining / days_at_entry < 0.3'),
    
    ('stop_loss_vega', 3,
     '{"loss_in_vega_units": 3.0}',
     'Exit if P&L < -3 × entry_vega'),
    
    ('time_to_expiry_critical', 5,
     '{"min_days_remaining": 7}',
     'Hard exit if days_remaining < 7'),
    
    ('pre_event_regime', 6,
     '{"trigger_regimes": ["pre_event"]}',
     'Exit any open position if regime becomes pre_event');
```

---

### 7.7 `delta_hedge_config` — paramètres delta hedge hot-reloadables

```sql
CREATE TABLE delta_hedge_config (
    id              SERIAL PRIMARY KEY,
    
    config_name         TEXT NOT NULL UNIQUE,
    config_value        DOUBLE PRECISION NOT NULL,
    unit                TEXT NOT NULL,
    description         TEXT,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO delta_hedge_config VALUES
    (DEFAULT, 'rebalance_threshold_delta', 0.05, 'fraction', 'Trigger hedge if |delta| > threshold'),
    (DEFAULT, 'min_hedge_qty', 1, 'count', 'Skip hedges below this qty (round to 0)'),
    (DEFAULT, 'max_hedge_frequency_seconds', 300, 'seconds', 'No hedge more often than every 5 min'),
    (DEFAULT, 'hedge_during_close', false, 'boolean', 'Continue hedging during position close phase ?');
```

---

## 8. Schéma relationnel

```
Étape 4 livre :
┌─────────────────────────┐
│   positions             │
│   (state='open')        │
└──────────┬──────────────┘
           │
           │ Étape 5 monitoring loop (60s)
           │
           ├──► position_mtm_history       (1 row / cycle)
           │
           ├──► position_signal_tracking   (1 row / cycle)
           │
           ├──► hedge_orders               (si delta drift)
           │
           └──► exit_alerts                (si rule trigger)
                          │
                          │ if action=EXIT
                          ▼
                ┌─────────────────────┐
                │  Create closing     │
                │  structure (étape 4 │
                │  flow re-used)      │
                │  orders.order_role  │
                │     = 'closing'     │
                └──────────┬──────────┘
                           │
                           │ closing structure fully_filled
                           ▼
                ┌──────────────────────┐
                │  positions.state =   │
                │  'closed'            │
                │  P&L final calculé   │
                └──────────────────────┘

Configs sidecar :
  exit_rules_config       (5 règles)
  delta_hedge_config      (4 params)
```

---

## 9. Pipeline backend par opération

### 9.1 Service position-monitor architecture

Nouveau service `position-monitor` distinct, cycle 60s :

```python
# src/services/position_monitor/main.py
class PositionMonitor:
    def __init__(self):
        self.db = create_db_session()
        self.redis = create_redis_client()
        self.execution_client = ExecutionEngineClient()  # client vers execution-engine
    
    async def start(self):
        await self._load_exit_rules()
        await self._load_hedge_config()
        
        # Listen to cycle events (vol-engine publishes after each cycle)
        await self._subscribe_to_vol_updates()
        
        # Démarrer le main monitoring loop
        asyncio.create_task(self._monitoring_loop())
    
    async def _monitoring_loop(self):
        """Cycle every 60s : update all open positions."""
        while True:
            try:
                open_positions = self.db.query(Position).filter_by(state='open').all()
                
                # Lecture surface courante (cached)
                surface = await self._get_current_surface()
                current_signals = await self._get_current_signals()
                
                for position in open_positions:
                    try:
                        await self._monitor_single_position(position, surface, current_signals)
                    except Exception as e:
                        log.error(f"Failed to monitor position {position.id}: {e}")
                        # Continue avec les autres positions
            except Exception as e:
                log.critical(f"Monitoring loop iteration failed: {e}")
            
            await asyncio.sleep(60)
```

### 9.2 Single position monitoring

```python
async def _monitor_single_position(self, position, surface, current_signals):
    # 1. Mark to market
    mtm = compute_mtm(position, surface)
    
    # 2. Compute current greeks
    current_greeks = compute_current_greeks(position, surface)
    
    # 3. P&L attribution depuis entry
    attribution = attribute_pnl_from_entry(position, mtm, surface)
    
    # 4. Persist mtm snapshot
    self.db.add(PositionMtmHistory(
        position_id=position.id,
        timestamp=datetime.utcnow(),
        spot=surface["spot"],
        iv_avg_legs_pct=compute_iv_avg(position, surface),
        current_pnl_gross_usd=mtm.pnl_gross,
        current_pnl_net_usd=mtm.pnl_net,
        vega_pnl_usd=attribution.vega,
        gamma_pnl_usd=attribution.gamma,
        theta_pnl_usd=attribution.theta,
        other_pnl_usd=attribution.other,
        current_vega_usd_per_volpt=current_greeks.vega,
        current_gamma_usd_per_pip2=current_greeks.gamma,
        current_theta_usd_per_day=current_greeks.theta,
        current_delta_unhedged=current_greeks.delta,
    ))
    
    # 5. Track signal status
    signal_status = self._track_signal_status(position, current_signals)
    self.db.add(PositionSignalTracking(
        position_id=position.id,
        timestamp=datetime.utcnow(),
        triggering_pc=position.entry_pc,
        current_z_score=signal_status.current_z,
        current_label=signal_status.current_label,
        entry_z_score=position.entry_z_score,
        entry_label=position.entry_label,
        weakening_ratio=signal_status.weakening_ratio,
        sign_flipped=signal_status.sign_flipped,
        status=signal_status.status,
    ))
    
    # 6. Évaluer exit rules
    exit_decisions = []
    for rule in self.active_exit_rules:
        decision = rule.evaluate(position, mtm, current_signals, surface)
        if decision.triggered:
            exit_decisions.append(decision)
            
            # Persist alert (1 row par trigger, dédup via timestamp idempotent ou cooldown)
            existing_recent = self.db.query(ExitAlert).filter_by(
                position_id=position.id,
                rule_triggered=decision.rule_name,
            ).filter(
                ExitAlert.timestamp > datetime.utcnow() - timedelta(minutes=5)
            ).first()
            
            if existing_recent is None:
                self.db.add(ExitAlert(
                    position_id=position.id,
                    rule_triggered=decision.rule_name,
                    action_recommended=decision.action,
                    priority=decision.priority,
                    rule_detail=decision.detail,
                ))
    
    # 7. Si exit triggered, prioriser et agir
    if exit_decisions:
        triggered = max(exit_decisions, key=lambda d: d.priority)
        
        if triggered.action == "EXIT":
            await self._initiate_position_close(position, reason=triggered.rule_name)
        elif triggered.action == "TRIM":
            await self._initiate_position_trim(position, reduction_pct=0.5, 
                                               reason=triggered.rule_name)
    
    # 8. Check delta hedge
    hedge_decision = check_delta_hedge_needed(position, current_greeks, surface)
    
    if hedge_decision.needs_hedge:
        # Vérifier cooldown : pas de hedge plus fréquent que 5 min par défaut
        last_hedge = self.db.query(HedgeOrder).filter_by(position_id=position.id).order_by(
            HedgeOrder.triggered_at.desc()
        ).first()
        
        cooldown_seconds = self.hedge_config["max_hedge_frequency_seconds"]
        
        if last_hedge is None or (datetime.utcnow() - last_hedge.triggered_at).seconds > cooldown_seconds:
            await self._execute_delta_hedge(position, hedge_decision)
    
    self.db.commit()
    
    # 9. Publish WS update
    await self._publish_position_update(position, mtm, current_greeks, signal_status, 
                                        exit_decisions, hedge_decision)
```

### 9.3 Position close orchestration

```python
async def _initiate_position_close(self, position, reason: str):
    """Crée une closing structure et la submit via execution-engine."""
    
    # Marquer position comme closing
    position.state = "closing"
    position.state_updated_at = datetime.utcnow()
    
    # Build closing structure : opposite side de chaque leg de l'entry structure
    entry_structure = position.structure
    entry_orders = entry_structure.orders
    
    closing_legs = []
    for entry_order in entry_orders:
        closing_legs.append({
            "contract_type": entry_order.contract_type,
            "expiry": entry_order.contract_expiry.isoformat(),
            "strike": entry_order.contract_strike,
            "qty": entry_order.qty_filled,  # close exactement ce qu'on a filled
            "side": "SELL" if entry_order.side == "BUY" else "BUY",
            "preview_iv_pct": get_current_iv(entry_order),  # IV courant pour limit price
        })
    
    # Submit via execution-engine (réutilise étape 4 pipeline)
    submit_result = await self.execution_client.submit_closing_trade(
        position_id=position.id,
        legs=closing_legs,
        close_reason=reason,
    )
    
    if submit_result.success:
        # Record closing structure ID for tracking
        position.closing_structure_id = submit_result.structure_id
        
        # Update exit_alert avec exec status
        latest_alert = self.db.query(ExitAlert).filter_by(
            position_id=position.id
        ).order_by(ExitAlert.timestamp.desc()).first()
        
        if latest_alert:
            latest_alert.auto_executed = True
            latest_alert.execution_status = "in_progress"
            latest_alert.closing_structure_id = submit_result.structure_id
    else:
        # Rollback : position reste 'open', alert marqué failed
        position.state = "open"
        log.error(f"Failed to close position {position.id}: {submit_result.reason}")

async def _on_closing_structure_filled(self, closing_structure_id: int):
    """Callback quand la closing structure est fully_filled."""
    closing_structure = self.db.query(Structure).get(closing_structure_id)
    position = self.db.query(Position).filter_by(
        closing_structure_id=closing_structure_id
    ).first()
    
    if position is None:
        return
    
    # Update position : closed
    position.state = "closed"
    position.closed_at = closing_structure.fully_filled_at
    position.exit_premium_usd = closing_structure.total_premium_paid_usd
    position.exit_total_cost_usd = closing_structure.total_entry_cost_usd
    
    # Compute final P&L
    position.gross_pnl_usd = (
        position.exit_premium_usd - position.entry_premium_usd
    )  # signed correctly per leg sides
    
    # Cumul des hedge costs
    total_hedge_cost = sum(
        h.total_cost_usd for h in self.db.query(HedgeOrder).filter_by(position_id=position.id).all()
    )
    
    position.net_pnl_usd = (
        position.gross_pnl_usd 
        - position.entry_total_cost_usd 
        - position.exit_total_cost_usd
        - total_hedge_cost
    )
    
    # Refresh book state
    await self._refresh_book_state()
    
    self.db.commit()
```

### 9.4 Delta hedge execution

```python
async def _execute_delta_hedge(self, position, hedge_decision: HedgeDecision):
    """Submit a future order to neutralize delta."""
    
    hedge_order_record = HedgeOrder(
        position_id=position.id,
        triggered_at=datetime.utcnow(),
        delta_imbalance_at_trigger=hedge_decision.delta_unhedged,
        rebalance_threshold_used=self.hedge_config["rebalance_threshold_delta"],
        hedge_qty=hedge_decision.hedge_qty,
        side=hedge_decision.side,
        state="pending",
    )
    self.db.add(hedge_order_record)
    self.db.flush()
    
    # Submit single future order via execution-engine
    submit_result = await self.execution_client.submit_hedge_order(
        hedge_order_id=hedge_order_record.id,
        qty=hedge_decision.hedge_qty,
        side=hedge_decision.side,
        contract_expiry=position.expiry_date,
    )
    
    if submit_result.success:
        hedge_order_record.state = "submitted"
        hedge_order_record.submitted_at = datetime.utcnow()
        hedge_order_record.ib_order_id = submit_result.ib_order_id
    else:
        hedge_order_record.state = "failed"
        log.error(f"Hedge order failed for position {position.id}: {submit_result.reason}")
    
    self.db.commit()
```

---

## 10. Estimation effort par sous-tâche

| Sous-tâche | Effort | Bloquant ? |
|---|---|---|
| Migration Postgres : 5 nouvelles tables + ALTER orders | 1 j | Oui |
| Seed `exit_rules_config` + `delta_hedge_config` | 0.5 j | Oui |
| Service position-monitor skeleton + cycle 60s | 1 j | Oui |
| `compute_mtm` (réutilise pricer step 3) | 0.5 j | Oui |
| `attribute_pnl_from_entry` (decomposition vega/gamma/theta) | 1.5 j | Oui — point différenciateur |
| 5 exit rules implementées (signal_reverse, time_based, stop_loss, ttm_critical, pre_event_regime) | 2 j | Oui |
| Priorisation rules + cooldown alertes | 0.5 j | Oui |
| Delta hedge logic + cooldown 5 min | 1 j | Oui |
| Closing structure orchestration (réutilise step 4 pipeline) | 1.5 j | Oui |
| Callback `_on_closing_structure_filled` (depuis execution-engine) | 0.5 j | Oui |
| WebSocket pubsub `/ws/positions`, `/ws/exit_alerts` | 1 j | Oui |
| Frontend Panel 4 (4 sections : structures, greeks aggregate, hedge status, exit alerts) | 2-3 j | Oui |
| Tests : MTM accuracy, attribution réconciliation, chacune des 5 rules, hedge trigger, position close end-to-end | 3 j | Oui |
| **Total MVP fonctionnel** | **~16 jours dev** | |

---

## 11. Stratégie de déploiement progressive

| Phase | Mode | Validation requise |
|---|---|---|
| Phase 1 — Read-only monitoring | Position monitor calcule MTM mais n'agit pas (no auto-exit, no auto-hedge) | Tests passent. Observer paper trading positions |
| Phase 2 — Auto-hedge only | Active delta hedge auto, exit reste manuel | 1 mois paper trading sans bug hedge |
| Phase 3 — Auto-hedge + auto-exit | Toutes les rules actives | 1 mois Phase 2 sans incident, backtest validé |
| Phase 4 — Live | Activer en live | Selon strategy étape 4 (live micro size avant full) |

---

## 12. Tests à écrire (acceptance criteria)

```python
# test_position_monitoring.py

def test_mtm_correct_for_long_straddle(db, mock_surface):
    """Long straddle ATM : si IV monte de 1%, MTM = +vega"""
    position = create_open_long_straddle(entry_iv=6.0, entry_vega=847)
    
    surface_with_higher_iv = shock_surface(mock_surface, iv_change=+1.0)
    mtm = compute_mtm(position, surface_with_higher_iv)
    
    assert abs(mtm.pnl_gross - 847) < 50  # à 50$ près (incl 2nd order)

def test_attribution_reconciles_to_total(db, mock_surface):
    """vega + gamma + theta + other = total P&L"""
    position = create_open_position(...)
    mtm = compute_mtm(position, mock_surface)
    attribution = attribute_pnl_from_entry(position, mtm, mock_surface)
    
    total = attribution.vega + attribution.gamma + attribution.theta + attribution.other
    assert abs(total - mtm.pnl_gross) < 1.0

def test_signal_reverse_rule_triggers_on_flip(db):
    position = create_open_position(entry_z=+1.8, pc=1)
    current_signals = {"pc1": PcaSignal(z_score=-0.3)}  # flipped
    
    rule = SignalReverseRule()
    decision = rule.evaluate(position, mtm=fake_mtm, current_signals=current_signals, surface={})
    
    assert decision.triggered is True
    assert decision.action == "EXIT"
    assert decision.detail["reason_subtype"] == "flipped"

def test_signal_reverse_rule_triggers_trim_on_50pct_weakening(db):
    position = create_open_position(entry_z=+2.0)
    current_signals = {"pc1": PcaSignal(z_score=+0.8)}  # 0.8/2.0 = 40%, > 50% weakened
    
    rule = SignalReverseRule()
    decision = rule.evaluate(position, mtm=fake_mtm, current_signals=current_signals, surface={})
    
    assert decision.triggered is True
    assert decision.action == "TRIM"

def test_time_based_rule_triggers(db):
    position = create_open_position(dte_at_entry=90, days_remaining=20)  # 20/90 = 0.22 < 0.3
    
    rule = TimeBasedRule()
    decision = rule.evaluate(position, mtm=fake_mtm, current_signals={}, surface={})
    
    assert decision.triggered is True
    assert decision.action == "EXIT"

def test_stop_loss_vega_rule_triggers(db):
    position = create_open_position(entry_vega=847)
    mtm = MtmResult(pnl_gross=-2700)  # loss = 3.19 × vega → over threshold
    
    rule = StopLossVegaRule()
    decision = rule.evaluate(position, mtm, current_signals={}, surface={})
    
    assert decision.triggered is True
    assert decision.detail["implied_iv_move_volpts"] == pytest.approx(-3.19, rel=0.01)

def test_ttm_critical_overrides_other_rules(db):
    """T<7 jours doit prendre priorité absolue"""
    position = create_open_position(days_remaining=5)
    
    decisions = [
        SignalReverseRule().evaluate(position, ...),   # priority 4
        TimeToExpiryCriticalRule().evaluate(position, ...),  # priority 5
    ]
    
    triggered = [d for d in decisions if d.triggered]
    winner = max(triggered, key=lambda d: d.priority)
    assert winner.rule_name == "time_to_expiry_critical"

def test_pre_event_regime_closes_all_open(db):
    open_positions = [create_open_position(...) for _ in range(3)]
    surface = {"_regime": {"label": "pre_event"}}
    
    for pos in open_positions:
        decision = PreEventRegimeRule().evaluate(pos, fake_mtm, {}, surface)
        assert decision.triggered is True
        assert decision.action == "EXIT"

def test_delta_hedge_triggered_above_threshold(db):
    position = create_open_position()
    greeks = NetGreeks(delta_unhedged=0.07)  # > 0.05 threshold
    
    decision = check_delta_hedge_needed(position, greeks, mock_surface)
    
    assert decision.needs_hedge is True
    assert decision.hedge_qty == 0  # 0.07 round to 0... à revoir : peut-être 1 dans realité
    # OK rejeter petit hedge

def test_delta_hedge_skipped_under_cooldown(db):
    position = create_open_position()
    # Last hedge il y a 2 min, cooldown = 5 min
    create_hedge_order(position_id=position.id, triggered_at=datetime.utcnow() - timedelta(minutes=2))
    
    monitor = PositionMonitor()
    await monitor._monitor_single_position(position, ...)
    
    # Pas de nouveau hedge créé
    n_hedges = db.query(HedgeOrder).filter_by(position_id=position.id).count()
    assert n_hedges == 1  # toujours 1, pas 2

def test_close_position_flow_end_to_end(db, mock_ib):
    position = create_open_position(...)
    
    # Trigger une exit rule
    monitor = PositionMonitor()
    await monitor._initiate_position_close(position, reason="signal_reverse")
    
    # Vérifier closing structure créée
    db.refresh(position)
    assert position.state == "closing"
    assert position.closing_structure_id is not None
    
    closing_structure = db.query(Structure).get(position.closing_structure_id)
    assert closing_structure.state == "submitted"
    
    # Simulate fills closing
    for order in closing_structure.orders:
        simulate_ib_execution(mock_ib, order.ib_order_id, qty=order.qty, price=...)
    
    # Position devient closed
    db.refresh(position)
    assert position.state == "closed"
    assert position.gross_pnl_usd is not None
    assert position.net_pnl_usd is not None

def test_alert_cooldown_5min(db):
    """Si même rule trigger 2 fois en 5 min, créer 1 seule alerte"""
    position = create_open_position(...)
    
    # Première trigger
    monitor._monitor_single_position(position, ...)
    assert db.query(ExitAlert).filter_by(position_id=position.id).count() == 1
    
    # Re-trigger immédiat (même cycle suivant)
    monitor._monitor_single_position(position, ...)
    assert db.query(ExitAlert).filter_by(position_id=position.id).count() == 1  # toujours 1
```

---

## 13. Ce qui n'est PAS dans cette étape

| Concept | Étape future / hors scope |
|---|---|
| Modification d'une position ouverte (ajouter / retirer une leg) | Hors MVP — accepter "close + new" comme workaround |
| Roll position vers expiry suivante | Hors MVP — V2 |
| Émergency stop-all manual button | V2 — UI polish |
| P&L attribution multi-jour avec recomputation history | MVP : attribution vs entry, pas continuous re-attribution |
| Predictive monitoring (alerter avant que rule trigger) | Out of scope, V2 |
| Profit-taking rule (ex: si P&L > 200% premium, exit) | Hors MVP — non mentionné dans user guide. Peut être ajouté facilement comme 6e règle |

---

## 14. Definition of done — étape 5

L'étape 5 est livrée quand :

- [ ] 5 nouvelles tables Postgres + 1 ALTER (orders.order_role)
- [ ] `exit_rules_config` + `delta_hedge_config` seedés
- [ ] Service position-monitor déployé, cycle 60s opérationnel
- [ ] Pour chaque cycle : MTM calculé, attribution P&L réconcilie au gross
- [ ] Les 5 exit rules s'évaluent correctement, priorité respectée si plusieurs trigger
- [ ] Alert cooldown 5 min empêche spam
- [ ] Delta hedge auto-déclenche si |delta| > 0.05, respecte cooldown 5 min
- [ ] Position close end-to-end : exit alert → closing structure → fills → position.state='closed' avec P&L final
- [ ] Panel 4 frontend affiche 4 sections fonctionnelles + WS updates temps réel
- [ ] Tests : MTM accuracy, attribution, 5 rules, hedge logic, position close
- [ ] Test E2E : ouvrir position en paper, observer 1h+ de monitoring, déclencher exit manuel → fermeture propre

---

## 15. Décisions de design notables (pour `DECISIONS.md`)

1. **Service position-monitor séparé** de execution-engine et vol-engine. Cycle 60s vs 180s pour vol-engine — réactivité critique sur exit rules.

2. **Cycle 60s pour monitoring**, pas event-driven sur fill. Trade-off : latence acceptable pour exit/hedge, simplicité d'implémentation. V2 pourrait passer event-driven sur regime change ou signal flip.

3. **Closing = nouvelle structure**, pas modification de l'existante. Simplicité audit + réutilisation pipeline étape 4. Coût : double row dans `structures` table (entry + closing), mais relation claire via `closing_structure_id`.

4. **5 exit rules en parallèle, priorité sur trigger conflict**. Évite ambiguïté. Priorité hardcodée dans config table pour audit.

5. **Cooldown 5 min sur alerts ET hedges**. Évite spam et double-action sur cycles consécutifs. Configurable.

6. **`order_role` ajouté à `orders` table** plutôt que table séparée pour closing_orders. Évite duplication schema, jointures plus simples.

7. **Attribution P&L vs entry only**, pas continuous. Limite computation cost. V2 : continuous re-attribution si besoin pour reporting fancy.

8. **Auto-execute en MVP, not human-confirmation**. Cohérent avec sizing 100% mécanique étape 3. Discrétion = leak bias humain. Mais : peut être désactivé par phase déploiement (Phase 1 read-only).

9. **Delta hedge cooldown 5 min** pour éviter overtrading sur micro-fluctuations spot. Peut être paramétré par tenor (longer cooldown sur 6M qui bouge moins vite).

10. **MTM partial granularité 60s suffit** : pour FOP qui bouge en seconds, on rate des moves intra-cycle, mais on les capture au cycle suivant. Acceptable vu l'horizon de trade (jours-semaines).

---

## 16. Ouvertures (limitations connues)

1. **Cycle 60s** rate des moves très rapides. Sur un flash crash type SNB 2015, le système pourrait laisser la position se dégrader 1 min avant trigger. Mitigation : ajouter event-driven trigger sur regime_change ou signal flip immédiat.

2. **Attribution analytique vs full re-pricing**. L'attribution utilise les greeks à entry × move (linéarisation). Pour gros moves spot/IV, écart vs full re-pricing peut être significatif. À monitorer en `other_pnl_usd`.

3. **Hedge cost récurrent peut éroder edge**. Si rebalancing trop fréquent + spreads élevés FOP wings, hedge cost > vega edge. À valider en backtest.

4. **Exit rule signal_reverse utilise PCA model courant**, pas celui d'origine. Si PCA refit a changé loadings entre entry et current, comparison de z-scores n'est pas strictement comparable. Edge case mais à noter.

5. **Pas de gestion intelligente du closing** : on close à n'importe quelle heure, même si liquidité faible. Mieux : queue close pour next active hour.

6. **Pas de margin check pendant lifetime position**. Si margin requirement augmente (régime stressed), pas de réaction auto. À ajouter en V2 via heartbeat avec ib_connection_state.

7. **Pas de prise en compte des dividendes/coupons** sur underlying (FX = pas applicable, mais si extension equity options à considérer).
