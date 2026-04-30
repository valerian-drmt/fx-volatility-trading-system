# Étape 4 — Execution (Submit + IB fills tracking)

> Spec de la quatrième étape du workflow trading vol.
>
> **Objectif fonctionnel** : transformer un `trade_preview` validé en ordres réels soumis à IB Gateway, suivre leur statut (acknowledged → filled → confirmed), réconcilier les fills (qty, prix, slippage) et créer les positions correspondantes dans la base de données qui servira à étape 5 (monitoring).
>
> **Prérequis** :
> - Étape 3 livrée (`trade_preview.state = "valid_for_submit"`)
> - IB Gateway running et connecté (account paper trading ou live)
> - Configuration IB account ID + permissions options
>
> **Pas dans cette étape** :
> - Monitoring P&L positions ouvertes (Étape 5)
> - Exit rules / sortie automatique (Étape 5)
> - Delta hedge récurrent (Étape 5)
> - Émergency manual stop (peut être ajouté en V2)
>
> **Audience** : agent code (Claude Code) qui implémente. Spec auto-suffisante.

---

## 1. Système formel

| Élément | Spec |
|---|---|
| Agents | Frontend (déclencheur Submit), execution-engine service, IB Gateway (broker), reconciler |
| États ordre | `order_state ∈ {pending, submitted, acknowledged, partially_filled, filled, rejected, cancelled, expired}` |
| États structure | `structure_state ∈ {submitted, partial_fill, fully_filled, partial_fail, fully_failed}` |
| Inputs requis | `trade_preview_id` (référence vers étape 3), connectivité IB |
| Outputs | `trades` row par leg + `positions` row par structure + reconciliation log |
| Contrainte temporelle | Submit → acknowledged < 2s. Acknowledged → filled : variable selon liquidité (typiquement < 30s pour FOP ATM, plus long pour wings) |
| Contrainte cohérence | Multi-leg structure : soit toutes legs filled, soit rollback (cancel + log partial_fail) |

---

## 2. Décision logic — du preview au position book

```python
def submit_trade_preview(preview_id: str) -> SubmitResult:
    
    # 1. Lecture preview + revalidation
    preview = db.query(TradePreview).filter_by(preview_id=preview_id).first()
    if preview is None:
        raise NotFoundError(f"preview {preview_id} not found")
    
    # 2. Re-run pre-submit checks (preview peut avoir expiré entre-temps)
    if preview.expires_at < datetime.utcnow():
        return SubmitResult(success=False, reason="preview_expired")
    if preview.state != "valid_for_submit":
        return SubmitResult(success=False, reason=f"preview_state={preview.state}")
    
    # 3. Re-validation finale (defense in depth)
    revalidation = revalidate_preview(preview)
    if not revalidation.passed:
        return SubmitResult(success=False, reason=f"revalidation_failed: {revalidation.reason}")
    
    # 4. Création structure DB (état initial 'submitted')
    structure = create_structure_record(preview)
    
    # 5. Pour chaque leg, créer ordre IB
    orders = []
    try:
        for leg_idx, leg in enumerate(preview.structure_full_payload["structure"]["legs"]):
            ib_order = build_ib_order(leg, structure_id=structure.id)
            order_record = persist_order_pending(structure.id, leg_idx, ib_order)
            orders.append((order_record, ib_order))
        
        # 6. Soumission groupée à IB
        # Strategy : combo order si toutes options même expiry, sinon orders séparés
        if can_use_combo_order(orders):
            ib_combo_id = ib_client.submit_combo_order(orders)
            link_orders_to_combo(orders, ib_combo_id)
        else:
            for order_record, ib_order in orders:
                ib_order_id = ib_client.submit_order(ib_order)
                order_record.ib_order_id = ib_order_id
                order_record.state = "submitted"
        
        db.commit()
        
    except IBSubmissionError as e:
        # Rollback : cancel toutes les orders déjà soumises
        rollback_partial_submission(structure, orders)
        return SubmitResult(success=False, reason=f"ib_submission_failed: {e}")
    
    # 7. Démarrer le tracking asynchrone
    async_task = start_fill_tracking_task(structure.id)
    
    return SubmitResult(
        success=True,
        structure_id=structure.id,
        n_orders_submitted=len(orders),
        tracking_task_id=async_task.id
    )
```

---

## 3. Le panel (UI) — pas de panel dédié, intégration dans workflow

L'étape 4 n'a **pas de panel dédié**. Elle est déclenchée par le bouton Submit du Panel 3 (Trade Preview, étape 3) et ses outputs alimentent directement le Panel 4 (Active Positions, étape 5).

Cependant, pendant la phase submission → fills, il faut un **état transitoire visible** dans l'UI, sinon l'utilisateur clique Submit et n'a aucun feedback. Spec :

| Zone UI | Contenu | Source data |
|---|---|---|
| Toast notification | "Order submitted, waiting fills..." (orange) puis "Filled at avg price X" (vert) ou "Rejected: reason" (rouge) | WebSocket subscription `/ws/orders/{structure_id}` |
| Panel 4 placeholder | Row temporaire "Pending fills" qui apparaît immédiatement, devient row réelle quand fills confirmés | `structures` table state |
| Order log dropdown | Détail des orders : leg, IB order id, state, fill price, slippage vs preview | `orders` table |

---

## 4. Schema des payloads et messages

### 4.1 Réponse API endpoint POST `/api/v1/trade/submit`

```jsonc
// Request body
{
  "preview_id": "tp_a1b2c3d4"
}

// Response synchrone (immédiate, avant fills)
{
  "success": true,
  "structure_id": 12345,
  "n_orders_submitted": 3,
  "ib_combo_order_id": "co_xyz789",                  // null si orders séparés
  "tracking_task_id": "task_abc123",
  "estimated_fill_time_sec": 15,                     // estimation depuis liquidité
  "websocket_topic": "orders/12345"
}

// OU si rejet
{
  "success": false,
  "reason": "preview_expired",                        // ou "revalidation_failed: max_loss_exceeded"
  "details": {...}
}
```

### 4.2 Messages WebSocket `/ws/orders/{structure_id}`

```jsonc
// Message type 1 : ordre acknowledged par IB
{
  "type": "order_acknowledged",
  "timestamp": "2026-04-30T14:35:02Z",
  "structure_id": 12345,
  "leg_idx": 0,
  "ib_order_id": "ib_abc",
  "state": "acknowledged"
}

// Message type 2 : partial fill
{
  "type": "partial_fill",
  "timestamp": "2026-04-30T14:35:08Z",
  "structure_id": 12345,
  "leg_idx": 0,
  "ib_order_id": "ib_abc",
  "qty_filled_so_far": 5,
  "qty_total": 11,
  "avg_fill_price_so_far": 178.42,
  "state": "partially_filled"
}

// Message type 3 : fill complet d'une leg
{
  "type": "leg_filled",
  "timestamp": "2026-04-30T14:35:14Z",
  "structure_id": 12345,
  "leg_idx": 0,
  "ib_order_id": "ib_abc",
  "qty_filled": 11,
  "avg_fill_price": 178.45,
  "preview_price": 178.40,
  "slippage_per_contract": 0.05,
  "total_slippage_usd": 0.55,
  "commission_paid": 9.35,
  "state": "filled"
}

// Message type 4 : structure complète (toutes legs filled)
{
  "type": "structure_filled",
  "timestamp": "2026-04-30T14:35:21Z",
  "structure_id": 12345,
  "all_legs_filled": true,
  "total_premium_paid_usd": 3782.40,
  "preview_premium_usd": 3762.00,
  "total_slippage_usd": 20.40,
  "total_commission_usd": 28.05,
  "position_id": 6789                                 // créé en parallèle (étape 5 visibility)
}

// Message type 5 : rejection
{
  "type": "order_rejected",
  "timestamp": "2026-04-30T14:35:03Z",
  "structure_id": 12345,
  "leg_idx": 1,
  "ib_order_id": "ib_def",
  "rejection_code": "201",
  "rejection_text": "Order rejected - reason: insufficient buying power",
  "rollback_initiated": true
}
```

---

## 5. Tables Postgres nécessaires

### 5.1 `structures` — entité parent regroupant les legs d'un trade

Une structure = un trade complet (ex: un straddle = 1 structure, 2 legs).

```sql
CREATE TABLE structures (
    id              BIGSERIAL PRIMARY KEY,
    
    -- métadonnées
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- source
    preview_id              TEXT REFERENCES trade_previews(preview_id),
    pca_signal_id           BIGINT REFERENCES pca_signals(id),
    triggering_pc           INTEGER NOT NULL,
    armed_z_score           DOUBLE PRECISION NOT NULL,
    armed_signal_label      TEXT NOT NULL,
    
    -- definition
    structure_type          TEXT NOT NULL,              -- 'straddle_atm' | etc
    reference_tenor         TEXT NOT NULL,
    expiry_date             DATE NOT NULL,              -- même expiry pour toutes legs typiquement
    
    -- quantities
    base_qty                INTEGER NOT NULL,           -- qty unit (= leg qty / qty_factor)
    
    -- état de la structure
    state                   TEXT NOT NULL,              -- 'submitted' | 'partial_fill' | 'fully_filled' | 'partial_fail' | 'fully_failed' | 'closed'
    state_updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- IB combo si applicable
    ib_combo_order_id       TEXT,
    
    -- aggregated data après fills (denormalisé pour speed)
    total_premium_paid_usd      DOUBLE PRECISION,       -- + si paid (long net), - si received
    total_slippage_usd          DOUBLE PRECISION,
    total_commission_usd        DOUBLE PRECISION,
    total_entry_cost_usd        DOUBLE PRECISION,       -- slippage + commission
    
    -- timestamps fills
    first_fill_at           TIMESTAMPTZ,
    fully_filled_at         TIMESTAMPTZ,
    
    -- closure
    closed_at               TIMESTAMPTZ,
    close_reason            TEXT,
    
    CONSTRAINT chk_state CHECK (state IN (
        'submitted', 'partial_fill', 'fully_filled', 
        'partial_fail', 'fully_failed', 'closed'
    ))
);

CREATE INDEX ix_structures_state ON structures (state, created_at DESC);
CREATE INDEX ix_structures_active ON structures (created_at DESC) 
    WHERE state IN ('submitted', 'partial_fill', 'fully_filled');
CREATE INDEX ix_structures_pca_signal ON structures (pca_signal_id);
```

**Cardinalité** : variable selon usage. Estimation pour paper trading actif : 1-5 structures / jour = ~500-1500 / an. Trivial.

---

### 5.2 `orders` — un ordre IB par leg

```sql
CREATE TABLE orders (
    id              BIGSERIAL PRIMARY KEY,
    structure_id    BIGINT NOT NULL REFERENCES structures(id) ON DELETE RESTRICT,
    leg_idx         INTEGER NOT NULL,
    
    -- IB metadata
    ib_order_id     TEXT,                               -- null si pas encore submit
    ib_perm_id      TEXT,                               -- IB permanent ID (post-acknowledge)
    
    -- contract specification
    contract_symbol     TEXT NOT NULL,                  -- 'EUR' (underlying)
    contract_type       TEXT NOT NULL,                  -- 'call' | 'put' | 'future'
    contract_expiry     DATE NOT NULL,
    contract_strike     DOUBLE PRECISION,               -- null pour future
    contract_exchange   TEXT NOT NULL DEFAULT 'CME',
    contract_currency   TEXT NOT NULL DEFAULT 'USD',
    
    -- order spec
    side                TEXT NOT NULL,                  -- 'BUY' | 'SELL'
    qty                 INTEGER NOT NULL,
    order_type          TEXT NOT NULL DEFAULT 'LMT',    -- 'LMT' | 'MKT' | 'MID'
    limit_price         DOUBLE PRECISION,               -- pour LMT
    time_in_force       TEXT NOT NULL DEFAULT 'DAY',
    
    -- preview data (référence)
    preview_iv_pct      DOUBLE PRECISION,
    preview_price       DOUBLE PRECISION,
    
    -- état
    state                   TEXT NOT NULL,              -- voir enum
    state_updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    submitted_at            TIMESTAMPTZ,
    acknowledged_at         TIMESTAMPTZ,
    rejected_at             TIMESTAMPTZ,
    rejection_code          TEXT,
    rejection_text          TEXT,
    
    -- agrégation des fills (denormalisé pour speed)
    qty_filled              INTEGER NOT NULL DEFAULT 0,
    qty_remaining           INTEGER GENERATED ALWAYS AS (qty - qty_filled) STORED,
    avg_fill_price          DOUBLE PRECISION,
    total_commission_usd    DOUBLE PRECISION DEFAULT 0,
    fully_filled_at         TIMESTAMPTZ,
    
    -- slippage analysis
    slippage_per_contract   DOUBLE PRECISION,           -- avg_fill - preview_price (signed)
    total_slippage_usd      DOUBLE PRECISION,
    
    UNIQUE (structure_id, leg_idx),
    CONSTRAINT chk_state CHECK (state IN (
        'pending', 'submitted', 'acknowledged', 'partially_filled', 
        'filled', 'rejected', 'cancelled', 'expired'
    )),
    CONSTRAINT chk_side CHECK (side IN ('BUY', 'SELL'))
);

CREATE INDEX ix_orders_structure ON orders (structure_id, leg_idx);
CREATE INDEX ix_orders_state ON orders (state, state_updated_at DESC);
CREATE INDEX ix_orders_ib_order ON orders (ib_order_id) WHERE ib_order_id IS NOT NULL;
CREATE INDEX ix_orders_pending ON orders (submitted_at) 
    WHERE state IN ('submitted', 'acknowledged', 'partially_filled');
```

**Cardinalité** : 2-3 legs par structure × 500-1500 structures = 1k-4.5k / an. Trivial.

---

### 5.3 `fills` — chaque exécution partielle

Une row par execution_id IB (un ordre peut avoir plusieurs fills si exécuté en partiel).

```sql
CREATE TABLE fills (
    id              BIGSERIAL PRIMARY KEY,
    order_id        BIGINT NOT NULL REFERENCES orders(id) ON DELETE RESTRICT,
    
    -- IB metadata
    ib_execution_id     TEXT NOT NULL UNIQUE,           -- ID unique de chaque execution chez IB
    
    -- fill details
    timestamp           TIMESTAMPTZ NOT NULL,           -- moment exact de l'execution chez IB
    qty_filled          INTEGER NOT NULL,
    fill_price          DOUBLE PRECISION NOT NULL,
    commission_usd      DOUBLE PRECISION NOT NULL,
    exchange            TEXT,
    
    -- side reference (denormalisé pour query speed)
    side                TEXT NOT NULL,
    
    -- contexte au moment du fill (pour analyse slippage)
    spot_at_fill        DOUBLE PRECISION,
    bid_at_fill         DOUBLE PRECISION,
    ask_at_fill         DOUBLE PRECISION,
    iv_implied_from_fill DOUBLE PRECISION,              -- IV qu'implique le fill price (BS reverse)
    
    -- enregistré quand
    received_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_fills_order ON fills (order_id, timestamp);
CREATE INDEX ix_fills_timestamp ON fills (timestamp DESC);
```

**Cardinalité** : 1-3 fills par order × 1k-4.5k orders = 1.5k-13k / an. Trivial.

---

### 5.4 `positions` — position ouverte pour étape 5 monitoring

Créée au moment où la structure passe en `fully_filled`. C'est l'entité que l'étape 5 va monitorer.

```sql
CREATE TABLE positions (
    id              BIGSERIAL PRIMARY KEY,
    structure_id    BIGINT NOT NULL UNIQUE REFERENCES structures(id),
    
    -- ouverture
    opened_at               TIMESTAMPTZ NOT NULL,
    entry_premium_usd       DOUBLE PRECISION NOT NULL,
    entry_total_cost_usd    DOUBLE PRECISION NOT NULL,
    
    -- position state (mise à jour par étape 5)
    state                   TEXT NOT NULL DEFAULT 'open',
    state_updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- aggregated greeks at entry (snapshot)
    entry_vega_usd_per_volpt    DOUBLE PRECISION,
    entry_gamma_usd_per_pip2    DOUBLE PRECISION,
    entry_theta_usd_per_day     DOUBLE PRECISION,
    
    -- entry market context
    entry_spot              DOUBLE PRECISION,
    entry_iv_avg            DOUBLE PRECISION,
    entry_regime            TEXT,
    
    -- closure (mise à jour par étape 5)
    closed_at               TIMESTAMPTZ,
    close_reason            TEXT,                       -- 'signal_reverse' | 'time_based' | 'stop_loss_vega' | 'expiry' | 'manual'
    exit_premium_usd        DOUBLE PRECISION,
    exit_total_cost_usd     DOUBLE PRECISION,
    
    -- final P&L (post-closure)
    gross_pnl_usd           DOUBLE PRECISION,
    net_pnl_usd             DOUBLE PRECISION,
    
    CONSTRAINT chk_state CHECK (state IN ('open', 'closing', 'closed', 'expired'))
);

CREATE INDEX ix_positions_state ON positions (state, opened_at DESC);
CREATE INDEX ix_positions_open ON positions (opened_at DESC) WHERE state = 'open';
```

**Cardinalité** : 1 position / structure fully filled. ~500-1500 / an. Trivial.

---

### 5.5 `execution_audit_log` — log granulaire de chaque événement execution

Critique pour debug post-mortem (pourquoi un ordre a été rejeté, séquence exacte des évènements).

```sql
CREATE TABLE execution_audit_log (
    id              BIGSERIAL PRIMARY KEY,
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- contexte
    structure_id    BIGINT REFERENCES structures(id),
    order_id        BIGINT REFERENCES orders(id),
    
    -- event
    event_type      TEXT NOT NULL,                      -- 'submission_attempt' | 'ib_acknowledge' | 'fill_received' | 'rejection' | 'rollback_initiated' | 'manual_intervention'
    severity        TEXT NOT NULL DEFAULT 'info',       -- 'debug' | 'info' | 'warning' | 'error' | 'critical'
    
    -- payload
    message         TEXT NOT NULL,
    payload         JSONB,                              -- détail complet (IB response, etc.)
    
    CONSTRAINT chk_severity CHECK (severity IN ('debug', 'info', 'warning', 'error', 'critical'))
);

CREATE INDEX ix_audit_timestamp ON execution_audit_log (timestamp DESC);
CREATE INDEX ix_audit_structure ON execution_audit_log (structure_id, timestamp);
CREATE INDEX ix_audit_severity ON execution_audit_log (severity, timestamp DESC) 
    WHERE severity IN ('warning', 'error', 'critical');
```

**Cardinalité** : variable. Estimation : 5-15 events par structure (submission, acks, fills, etc.) × 1500/an = ~20k. Trivial. Garde indéfiniment.

---

### 5.6 `ib_connection_state` — état de connectivité broker

Singleton (1 row par broker). Critique pour gating Submit.

```sql
CREATE TABLE ib_connection_state (
    id              SERIAL PRIMARY KEY,
    broker          TEXT NOT NULL UNIQUE DEFAULT 'IB',
    
    is_connected    BOOLEAN NOT NULL,
    last_heartbeat  TIMESTAMPTZ NOT NULL,
    
    -- account info
    account_id          TEXT,
    account_type        TEXT,                           -- 'paper' | 'live'
    available_funds_usd DOUBLE PRECISION,
    buying_power_usd    DOUBLE PRECISION,
    margin_used_usd     DOUBLE PRECISION,
    
    -- IB Gateway info
    gateway_version     TEXT,
    api_version         TEXT,
    
    -- diagnostics
    last_disconnect_at  TIMESTAMPTZ,
    n_disconnects_24h   INTEGER DEFAULT 0,
    
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

**Pattern** : un row, UPDATE en place. Background heartbeat toutes les 10s update `last_heartbeat`.

---

## 6. Schéma relationnel

```
┌──────────────────────────┐
│  trade_previews (étape 3)│
└──────────┬───────────────┘
           │
           │ Submit triggers creation
           ▼
┌──────────────────────────┐
│      structures          │◄─── pca_signals (FK)
│   (1 / trade complet)    │
│   state: submitted →     │
│   fully_filled           │
└──────────┬───────────────┘
           │ 1:N
           ▼
┌──────────────────────────┐
│       orders             │
│  (1 / leg)               │
│  state: pending →        │
│  filled                  │
└──────────┬───────────────┘
           │ 1:N
           ▼
┌──────────────────────────┐      ┌──────────────────────────┐
│       fills              │      │  execution_audit_log     │
│  (1 / IB execution_id)   │      │  (granular events)       │
└──────────────────────────┘      └──────────────────────────┘
           │ aggregates trigger
           ▼
┌──────────────────────────┐
│      positions           │
│  (1 / structure fully    │
│   filled)                │
│  → consumed par étape 5  │
└──────────────────────────┘

Sidecar:
┌──────────────────────────┐
│  ib_connection_state     │ ← gating Submit
│  (singleton)             │
└──────────────────────────┘
```

---

## 7. Pipeline backend par opération

### 7.1 Service execution-engine architecture

Nouveau service `execution-engine` distinct de `vol-engine`. Communique avec IB via `ib_insync` (Python). Souscrit à 4 streams IB :

```python
# src/services/execution_engine/main.py
class ExecutionEngine:
    def __init__(self):
        self.ib = IB()  # ib_insync client
        self.db = create_db_session()
        self.redis = create_redis_client()
    
    async def start(self):
        await self.ib.connectAsync('127.0.0.1', 7497, clientId=2)  # paper port
        
        # Subscribe to events
        self.ib.orderStatusEvent += self._on_order_status
        self.ib.execDetailsEvent += self._on_execution
        self.ib.commissionReportEvent += self._on_commission
        self.ib.errorEvent += self._on_error
        
        # Démarrer le heartbeat loop
        asyncio.create_task(self._heartbeat_loop())
        
        # Démarrer le polling state watcher
        asyncio.create_task(self._state_watcher_loop())
        
        # Listen on Redis pubsub for submit triggers
        await self._listen_for_submits()
```

### 7.2 Submit flow (synchrone, < 2s)

```python
# src/services/execution_engine/submit.py
async def handle_submit_request(preview_id: str) -> SubmitResult:
    # Lock pour éviter double-submit du même preview
    lock_key = f"submit_lock:{preview_id}"
    if not await redis.set(lock_key, "1", ex=10, nx=True):
        return SubmitResult(success=False, reason="already_being_submitted")
    
    try:
        # 1. Lecture preview
        preview = db.query(TradePreview).filter_by(preview_id=preview_id).first()
        if preview is None or preview.expires_at < datetime.utcnow():
            return SubmitResult(success=False, reason="preview_invalid_or_expired")
        
        # 2. Vérification connectivité IB
        ib_state = db.query(IbConnectionState).first()
        if not ib_state.is_connected:
            return SubmitResult(success=False, reason="ib_disconnected")
        
        # 3. Re-validation finale (re-run pre_submit_checks)
        revalidation = revalidate_preview_checks(preview)
        if not revalidation.passed:
            audit_log(structure_id=None, event_type="submission_blocked",
                     severity="warning", payload={"reason": revalidation.reason})
            return SubmitResult(success=False, reason=f"revalidation_failed: {revalidation.reason}")
        
        # 4. Création structure record (état initial submitted)
        structure = Structure(
            preview_id=preview.preview_id,
            pca_signal_id=preview.pca_signal_id,
            triggering_pc=preview.triggering_pc,
            armed_z_score=preview.armed_z_score,
            armed_signal_label=preview.armed_signal_label,
            structure_type=preview.structure_type,
            reference_tenor=preview.reference_tenor,
            expiry_date=extract_expiry_from_payload(preview.structure_full_payload),
            base_qty=preview.structure_full_payload["sizing"]["base_qty"],
            state="submitted",
        )
        db.add(structure)
        db.flush()  # need ID
        
        # 5. Créer orders pour chaque leg
        legs = preview.structure_full_payload["structure"]["legs"]
        orders_pairs = []
        
        for leg_idx, leg in enumerate(legs):
            order_record = Order(
                structure_id=structure.id,
                leg_idx=leg_idx,
                contract_symbol="EUR",
                contract_type=leg["contract_type"],
                contract_expiry=date.fromisoformat(leg["expiry"]),
                contract_strike=leg.get("strike"),
                side=leg["side"],
                qty=leg["qty"],
                order_type="LMT",
                limit_price=compute_limit_price(leg, slippage_tolerance_pct=0.5),
                preview_iv_pct=leg.get("entry_iv_pct"),
                preview_price=leg["entry_price_per_contract_usd"],
                state="pending",
            )
            db.add(order_record)
            db.flush()
            
            # Build IB Order object
            ib_contract = build_ib_contract(leg)
            ib_order = build_ib_order(leg, order_record.limit_price)
            
            orders_pairs.append((order_record, ib_contract, ib_order))
        
        # 6. Submit à IB (combo si possible, sinon séparé)
        if can_use_combo(orders_pairs):
            combo_id = await submit_as_combo(orders_pairs, structure.id)
            structure.ib_combo_order_id = combo_id
        else:
            for order_record, ib_contract, ib_order in orders_pairs:
                trade = ib.placeOrder(ib_contract, ib_order)
                order_record.ib_order_id = str(trade.order.orderId)
                order_record.state = "submitted"
                order_record.submitted_at = datetime.utcnow()
                
                audit_log(structure_id=structure.id, order_id=order_record.id,
                         event_type="submission_attempt",
                         payload={"ib_order_id": order_record.ib_order_id})
        
        db.commit()
        
        # 7. Publish WebSocket pour frontend
        await redis.publish(
            f"orders:{structure.id}",
            json.dumps({"type": "all_submitted", "structure_id": structure.id})
        )
        
        return SubmitResult(
            success=True,
            structure_id=structure.id,
            n_orders_submitted=len(orders_pairs),
            estimated_fill_time_sec=estimate_fill_time(legs)
        )
    
    finally:
        await redis.delete(lock_key)
```

### 7.3 Fill tracking handlers (asynchrone, event-driven)

```python
# src/services/execution_engine/handlers.py

async def _on_order_status(self, trade: Trade):
    """IB callback : order state change."""
    ib_order_id = str(trade.order.orderId)
    
    order = db.query(Order).filter_by(ib_order_id=ib_order_id).first()
    if order is None:
        log.warning(f"Order status for unknown ib_order_id={ib_order_id}")
        return
    
    new_state = map_ib_status_to_state(trade.orderStatus.status)
    
    if new_state != order.state:
        order.state = new_state
        order.state_updated_at = datetime.utcnow()
        
        if new_state == "acknowledged":
            order.acknowledged_at = datetime.utcnow()
            order.ib_perm_id = str(trade.order.permId)
        elif new_state == "rejected":
            order.rejected_at = datetime.utcnow()
            order.rejection_text = trade.orderStatus.lastFillPrice  # IB packs reason here sometimes
            
            audit_log(order_id=order.id, event_type="rejection", severity="error",
                     payload={"reason": order.rejection_text})
            
            # Trigger rollback de la structure
            await initiate_rollback(order.structure_id, reason=f"leg_{order.leg_idx}_rejected")
        
        db.commit()
        
        # Publish WS
        await redis.publish(
            f"orders:{order.structure_id}",
            json.dumps({
                "type": f"order_{new_state}",
                "structure_id": order.structure_id,
                "leg_idx": order.leg_idx,
                "ib_order_id": order.ib_order_id,
                "state": new_state
            })
        )

async def _on_execution(self, trade: Trade, fill: Fill):
    """IB callback : new execution (= fill ou partial fill)."""
    ib_order_id = str(trade.order.orderId)
    ib_execution_id = fill.execution.execId
    
    # Idempotence : vérifier si déjà enregistré
    existing = db.query(FillRecord).filter_by(ib_execution_id=ib_execution_id).first()
    if existing:
        return  # déjà reçu
    
    order = db.query(Order).filter_by(ib_order_id=ib_order_id).first()
    if order is None:
        log.error(f"Fill for unknown order ib_order_id={ib_order_id}")
        return
    
    # Récupérer contexte marché à l'instant du fill
    spot_now = await get_spot()
    bid, ask = await get_bid_ask(order.contract_symbol, order.contract_strike, order.contract_expiry)
    iv_implied = compute_iv_from_price(fill.execution.price, ...) if order.contract_type != "future" else None
    
    # Persist fill
    fill_record = FillRecord(
        order_id=order.id,
        ib_execution_id=ib_execution_id,
        timestamp=fill.execution.time,
        qty_filled=fill.execution.shares,
        fill_price=fill.execution.price,
        commission_usd=fill.commissionReport.commission if fill.commissionReport else 0,
        exchange=fill.execution.exchange,
        side=order.side,
        spot_at_fill=spot_now,
        bid_at_fill=bid,
        ask_at_fill=ask,
        iv_implied_from_fill=iv_implied,
    )
    db.add(fill_record)
    
    # Update aggregates sur l'order
    update_order_aggregates(order)  # recompute qty_filled, avg_fill_price, slippage
    
    # Vérifier si order entièrement filled
    if order.qty_filled >= order.qty:
        order.state = "filled"
        order.fully_filled_at = datetime.utcnow()
        await on_leg_fully_filled(order)
    elif order.qty_filled > 0:
        order.state = "partially_filled"
    
    db.commit()
    
    # WS publish
    await redis.publish(
        f"orders:{order.structure_id}",
        json.dumps({
            "type": "partial_fill" if order.state == "partially_filled" else "leg_filled",
            ...
        })
    )

async def on_leg_fully_filled(order: Order):
    """Triggered quand une leg est complètement filled."""
    structure = db.query(Structure).get(order.structure_id)
    
    # Vérifier si toutes les legs de la structure sont filled
    all_legs = db.query(Order).filter_by(structure_id=structure.id).all()
    
    if all(l.state == "filled" for l in all_legs):
        # Tout filled : passer structure à fully_filled et créer position
        structure.state = "fully_filled"
        structure.fully_filled_at = datetime.utcnow()
        
        # Aggregate
        structure.total_premium_paid_usd = compute_total_premium(all_legs)
        structure.total_slippage_usd = sum(l.total_slippage_usd or 0 for l in all_legs)
        structure.total_commission_usd = sum(l.total_commission_usd or 0 for l in all_legs)
        structure.total_entry_cost_usd = structure.total_slippage_usd + structure.total_commission_usd
        
        # Create position record (consumed by étape 5)
        position = Position(
            structure_id=structure.id,
            opened_at=structure.fully_filled_at,
            entry_premium_usd=structure.total_premium_paid_usd,
            entry_total_cost_usd=structure.total_entry_cost_usd,
            entry_vega_usd_per_volpt=...,  # snapshot greeks now
            entry_gamma_usd_per_pip2=...,
            entry_theta_usd_per_day=...,
            entry_spot=await get_spot(),
            entry_iv_avg=compute_avg_entry_iv(all_legs),
            entry_regime=await get_current_regime(),
            state="open",
        )
        db.add(position)
        
        # Trigger book_state_snapshots refresh
        refresh_book_state_snapshot()
        
        db.commit()
        
        await redis.publish(
            f"orders:{structure.id}",
            json.dumps({
                "type": "structure_filled",
                "structure_id": structure.id,
                "position_id": position.id,
                ...
            })
        )

async def initiate_rollback(structure_id: int, reason: str):
    """Une leg a échoué : tenter d'annuler les autres et fermer celles déjà filled."""
    structure = db.query(Structure).get(structure_id)
    structure.state = "partial_fail"
    
    audit_log(structure_id=structure_id, event_type="rollback_initiated",
             severity="warning", payload={"reason": reason})
    
    # Pour chaque order non-filled : cancel
    for order in structure.orders:
        if order.state in ("submitted", "acknowledged", "partially_filled"):
            try:
                ib.cancelOrder(get_ib_order_obj(order.ib_order_id))
                order.state = "cancelled"
                audit_log(order_id=order.id, event_type="order_cancelled",
                         severity="info")
            except Exception as e:
                audit_log(order_id=order.id, event_type="cancel_failed",
                         severity="error", payload={"error": str(e)})
    
    # Pour les legs partiellement filled : créer un closing order opposite side
    for order in structure.orders:
        if order.state == "partially_filled" and order.qty_filled > 0:
            await create_unwind_order(order, qty=order.qty_filled)
            audit_log(order_id=order.id, event_type="unwind_order_created",
                     severity="warning", 
                     payload={"unwind_qty": order.qty_filled})
    
    db.commit()
```

### 7.4 Heartbeat & state watcher

```python
async def _heartbeat_loop(self):
    """Update ib_connection_state every 10s."""
    while True:
        try:
            account_summary = await self.ib.accountSummaryAsync()
            
            db.execute(
                update(IbConnectionState).values(
                    is_connected=True,
                    last_heartbeat=datetime.utcnow(),
                    available_funds_usd=extract_available_funds(account_summary),
                    buying_power_usd=extract_buying_power(account_summary),
                    margin_used_usd=extract_margin(account_summary),
                )
            )
            db.commit()
        except Exception as e:
            db.execute(
                update(IbConnectionState).values(
                    is_connected=False,
                    last_disconnect_at=datetime.utcnow(),
                    n_disconnects_24h=IbConnectionState.n_disconnects_24h + 1,
                )
            )
            db.commit()
            log.error(f"IB heartbeat failed: {e}")
        
        await asyncio.sleep(10)


async def _state_watcher_loop(self):
    """Watch for stuck orders (acknowledged but not filled after N minutes)."""
    while True:
        stuck_orders = db.query(Order).filter(
            Order.state.in_(["submitted", "acknowledged"]),
            Order.submitted_at < datetime.utcnow() - timedelta(minutes=10)
        ).all()
        
        for order in stuck_orders:
            audit_log(order_id=order.id, event_type="stuck_order_detected",
                     severity="warning",
                     payload={"submitted_at": order.submitted_at.isoformat()})
            
            # Decision : auto-cancel ? ou alert humain ?
            # MVP : alert humain via log critical, no auto-cancel
            log.critical(f"Order {order.id} stuck for >10 min, manual intervention may be needed")
        
        await asyncio.sleep(60)
```

---

## 8. Estimation effort par sous-tâche

| Sous-tâche | Effort | Bloquant ? |
|---|---|---|
| Migration Postgres : 6 tables + indices | 1 j | Oui |
| Service execution-engine skeleton (ib_insync setup) | 1 j | Oui |
| `handle_submit_request` synchrone + revalidation | 1.5 j | Oui |
| Combo order builder vs separate orders logic | 1 j | Oui |
| `_on_order_status` + `_on_execution` handlers | 2 j | Oui |
| Rollback logic (cancel + unwind partial) | 2 j | Oui — point sensible |
| Heartbeat loop + ib_connection_state | 0.5 j | Oui |
| State watcher (stuck orders detection) | 0.5 j | Oui |
| Audit log persistence module | 0.5 j | Oui |
| WebSocket pubsub for frontend | 1 j | Oui |
| Frontend toast notifications + Panel 3 Submit handler | 1 j | Oui |
| Frontend Panel 4 placeholder row "Pending fills" | 0.5 j | Non (peut venir avec étape 5) |
| Tests : submission flow happy path, rejection rollback, partial fill, idempotence fills | 3 j | Oui |
| **Total MVP fonctionnel** | **~15.5 jours dev** | |

---

## 9. Stratégie de déploiement progressive

L'execution est l'étape la plus risquée du système (bug = perte d'argent réelle). Stratégie :

| Phase | Account IB | Capital risqué | Validation requise |
|---|---|---|---|
| Phase 1 — Mock execution | mock IB client (pas de vraie connection) | $0 | Tests passent, pas de submission réelle |
| Phase 2 — Paper trading | IB Paper account (port 7497) | $0 | Walk-forward backtest validé. Run pendant 1-2 mois. Fills, rejections, slippage observés |
| Phase 3 — Live micro size | IB Live account, max qty=1 par leg | < $500 max loss / trade | Paper trading sans bug pendant 1 mois. Cost model validé sur paper data |
| Phase 4 — Live full size | IB Live, sizing normal | selon risk_limits | Phase 3 sans incident pendant 3 mois |

**Tu n'iras pas en phase 3+ avant longtemps.** Comme tu l'as confirmé : trading live uniquement avec backtest+alpha validé. Ce doc spec phases 1-2 essentiellement.

---

## 10. Tests à écrire (acceptance criteria)

```python
# test_execution_pipeline.py

def test_submit_creates_structure_and_orders(db, mock_ib):
    """Submit valid preview → 1 structure + N orders avec state='submitted'"""
    preview = create_valid_preview(db, n_legs=2)
    
    result = submit_trade_preview(preview.preview_id)
    
    assert result.success is True
    assert result.n_orders_submitted == 2
    
    structure = db.query(Structure).filter_by(preview_id=preview.preview_id).first()
    assert structure.state == "submitted"
    assert len(structure.orders) == 2
    for o in structure.orders:
        assert o.state == "submitted"
        assert o.ib_order_id is not None

def test_expired_preview_rejected(db):
    preview = create_valid_preview(db, expires_in_seconds=-10)
    
    result = submit_trade_preview(preview.preview_id)
    
    assert result.success is False
    assert result.reason == "preview_expired"

def test_revalidation_blocks_if_signal_flipped(db, mock_ib):
    preview = create_valid_preview(db, armed_z=1.8)
    # Simulate signal flip post-arm
    create_pca_signal(z_score=-0.5, label="FAIR")  # latest signal
    
    result = submit_trade_preview(preview.preview_id)
    
    assert result.success is False
    assert "signal_still_actionable" in result.reason

def test_partial_fill_updates_state(db, mock_ib):
    preview = create_valid_preview(db)
    submit_trade_preview(preview.preview_id)
    structure = get_latest_structure(db)
    order = structure.orders[0]
    
    # Simulate partial fill
    simulate_ib_execution(mock_ib, order.ib_order_id, qty=5, price=178.42)
    
    db.refresh(order)
    assert order.state == "partially_filled"
    assert order.qty_filled == 5
    assert order.avg_fill_price == 178.42

def test_full_fill_creates_position(db, mock_ib):
    preview = create_valid_preview(db, n_legs=2, qty_per_leg=10)
    submit_trade_preview(preview.preview_id)
    structure = get_latest_structure(db)
    
    # Simulate full fills on both legs
    for order in structure.orders:
        simulate_ib_execution(mock_ib, order.ib_order_id, qty=10, price=178.40)
    
    db.refresh(structure)
    assert structure.state == "fully_filled"
    
    position = db.query(Position).filter_by(structure_id=structure.id).first()
    assert position is not None
    assert position.state == "open"

def test_rejection_triggers_rollback(db, mock_ib):
    preview = create_valid_preview(db, n_legs=2)
    submit_trade_preview(preview.preview_id)
    structure = get_latest_structure(db)
    
    # Simulate first leg filled, second rejected
    simulate_ib_execution(mock_ib, structure.orders[0].ib_order_id, qty=10, price=178.40)
    simulate_ib_rejection(mock_ib, structure.orders[1].ib_order_id, reason="insufficient_buying_power")
    
    db.refresh(structure)
    assert structure.state == "partial_fail"
    
    # First leg should have an unwind order created
    audit_entries = db.query(ExecutionAuditLog).filter_by(
        structure_id=structure.id
    ).all()
    assert any(a.event_type == "unwind_order_created" for a in audit_entries)

def test_idempotence_duplicate_fills(db, mock_ib):
    """Recevoir le même execution_id deux fois ne doit pas double-compter"""
    preview = create_valid_preview(db)
    submit_trade_preview(preview.preview_id)
    order = get_latest_structure(db).orders[0]
    
    fill_data = build_test_fill(execution_id="exec_xyz", qty=5, price=178.40)
    
    # Receive twice
    handle_execution_event(fill_data)
    handle_execution_event(fill_data)
    
    n_fills = db.query(FillRecord).filter_by(order_id=order.id).count()
    assert n_fills == 1  # pas 2

def test_ib_disconnect_blocks_submit(db, mock_ib):
    preview = create_valid_preview(db)
    db.execute(update(IbConnectionState).values(is_connected=False))
    
    result = submit_trade_preview(preview.preview_id)
    
    assert result.success is False
    assert result.reason == "ib_disconnected"

def test_lock_prevents_double_submit(db, mock_ib):
    preview = create_valid_preview(db)
    
    # Lance 2 submits en parallèle
    results = await asyncio.gather(
        submit_trade_preview(preview.preview_id),
        submit_trade_preview(preview.preview_id),
        return_exceptions=True
    )
    
    # Un seul doit réussir
    successes = sum(1 for r in results if isinstance(r, SubmitResult) and r.success)
    assert successes == 1

def test_slippage_calculation():
    order = Order(preview_price=178.40, qty=10, side="BUY")
    fills = [
        Fill(qty_filled=5, fill_price=178.42),
        Fill(qty_filled=5, fill_price=178.48),
    ]
    
    update_order_aggregates(order, fills)
    
    expected_avg = (5*178.42 + 5*178.48) / 10  # = 178.45
    expected_slippage_per_contract = 178.45 - 178.40  # = 0.05
    
    assert order.avg_fill_price == expected_avg
    assert order.slippage_per_contract == expected_slippage_per_contract
```

---

## 11. Ce qui n'est PAS dans cette étape (et où ça ira)

| Concept | Étape future |
|---|---|
| MTM tracking position après fill | Étape 5 (Active Positions) |
| Exit rules monitoring (signal reverse, time-based, stop-loss) | Étape 5 |
| Delta hedge récurrent automatique | Étape 5 |
| Émergency stop-all (close all positions immediately) | V2, hors MVP |
| Multi-broker (Tradier, etc.) | Hors scope (IB only) |
| Order modification (modify limit price, qty) | V2 — MVP : cancel + new order |
| Smart order routing intra-IB (multiple exchanges) | Délégué à IB SmartRouting (default) |
| Fees breakdown détaillé (exchange fee, regulatory fee, etc.) | V2 — MVP : commission_usd agrégé |

---

## 12. Definition of done — étape 4

L'étape 4 est livrée quand :

- [ ] 6 tables Postgres créées (structures, orders, fills, positions, execution_audit_log, ib_connection_state)
- [ ] Service execution-engine déployé, connecte à IB Paper Account
- [ ] Heartbeat loop maintient `ib_connection_state.is_connected` à jour
- [ ] Endpoint `/api/v1/trade/submit` opérationnel
- [ ] Combo order envoyé à IB pour structures même expiry, orders séparés sinon
- [ ] Fills handlers reçoivent et persistent fills, gèrent partial fills correctement
- [ ] Idempotence : duplicate fills via reconnect IB ne sont pas double-comptés
- [ ] Rollback fonctionnel : leg rejetée → cancel autres + unwind des partial fills
- [ ] Position record créée automatiquement quand structure passe à `fully_filled`
- [ ] WebSocket `orders/{structure_id}` publie events temps réel pour frontend
- [ ] Audit log capture tous les événements (submit, ack, fill, rejection, rollback)
- [ ] Tests : happy path, rejection, partial fill, idempotence, lock concurrent, IB disconnect
- [ ] Test end-to-end paper trading : 1 trade complet de submit à position created sans intervention manuelle

---

## 13. Décisions de design notables (pour `DECISIONS.md`)

1. **Service execution-engine séparé** de vol-engine et api. Séparation des concerns : execution est event-driven (fills callbacks), vol-engine est cycle-driven. Mixer = race conditions potentielles.

2. **`ib_insync` plutôt que ibapi raw**. Plus pythonic, async-friendly, mature. Coût : dépendance externe non-officielle, mais largement adoptée.

3. **Combo order si possible, séparé sinon**. Combo orders IB exécutent les legs ensemble ou pas du tout — réduit risque partial fill. Mais limité à legs même expiry et même contract type. Pour calendar (deux expiries) : orders séparés obligatoires.

4. **Rollback inclut unwind des partial fills**. Si leg 1 filled à 50% et leg 2 rejected, on ne laisse pas la position naked — on crée un closing order opposite side pour fermer le 50%. Coût : double slippage. Acceptable car c'est un edge case rare.

5. **Position créée seulement quand structure `fully_filled`**, pas dès le premier fill. Éviter d'avoir une "position fantôme" partiellement constituée dans le book. Si partial_fail : pas de position créée, juste log audit.

6. **Lock Redis sur preview_id pour empêcher double-submit**. TTL 10s. Évite double-clic ou retry réseau qui crée 2 trades.

7. **Order limit price avec tolerance 0.5%** vs preview price. Pas market order (risque slippage extrême FOP wings). Pas mid-price (risque pas de fill). Compromise.

8. **Stuck order detection sans auto-cancel**. Si order pending > 10 min, alert critical mais pas d'action automatique. Décision humaine requise. V2 : peut être configurable.

9. **Audit log keep indéfiniment**. Stockage négligeable (~20k rows/an), valeur post-mortem élevée. Pas de purge.

10. **`positions` créé par execution-engine**, mais `state` géré par étape 5 (monitoring). Séparation des responsabilités : execution finit son job au fully_filled.

---

## 14. Ouvertures (limitations connues)

1. **Pas de gestion partial_fill stratégique** : si on veut filler 11 contracts mais marché en a que 8 dispo, on prend les 8 et waiting. Alternative : annuler et resubmit à prix plus agressif. Hors MVP.

2. **Slippage fixe tolerance 0.5%** dans limit price. Peut être trop strict en régime stressé (no fill) ou trop lâche en régime calm (slippage évitable). À calibrer empiriquement.

3. **Pas de re-pricing dynamique** entre submit et fill. Si mid bouge entre les 2, le limit reste à l'ancien price. Pour FOP avec fills < 30s typiquement OK, mais edge cases possibles.

4. **Heartbeat 10s**. Si IB disconnect entre 2 heartbeats, on peut accepter un Submit puis échouer. Mitigé par re-vérification dans handle_submit_request, mais race condition possible.

5. **Combo order detection** simplifiée (check même expiry + même contract type). Peut rater optimisations possibles. À raffiner.

6. **Pas de modélisation des hours de trading** : le système peut soumettre off-hours, IB rejettera, et notre rollback se déclenche. Mieux : check trading hours pre-submit.

7. **Pas d'intégration des frais reg US** (SEC fees, taker fees) au-delà de commission IB agrégée. Marginal pour FOP CME mais à modéliser si extension US equity options.
