# Étape 3 — Trade Preview (Panel 3)

> Spec de la troisième étape du workflow trading vol.
>
> **Objectif fonctionnel** : à partir d'un signal actionable produit par étape 2, construire la structure de trade recommandée, calculer pricing et greeks nets, simuler scenarios, déterminer sizing final, et exposer les checks bloquants avant Submit.
>
> **Prérequis** :
> - Étape 1 livrée (`gate_decision.authorized = True`)
> - Étape 2 livrée (`pca_signal.actionable = True` + `recommended_structure` populated)
>
> **Pas dans cette étape** :
> - Exécution effective (Étape 4)
> - Monitoring positions ouvertes (Étape 5)
> - Validation OOS du sizing par backtest (cf. doc backtest)
>
> **Audience** : agent code (Claude Code) qui implémente. Spec auto-suffisante.

---

## 1. Système formel

| Élément | Spec |
|---|---|
| Agents | Frontend (déclencheur "Arm trade"), structure builder, pricer, greeks computer, scenario engine, sizer, pre-submit validator |
| États | `trade_preview_state ∈ {empty, armed, valid_for_submit, blocked, expired}` |
| Inputs requis | `pca_signal` actionable, surface IV courante, position book courant (vega total, delta total), capital total, regime state |
| Output | Structure complète (legs + greeks + costs + sizing) prête pour soumission, ou raison de blocage |
| Contrainte de fraîcheur | IV data utilisée < 2 minutes |
| Contrainte de cohérence | Signal armed doit être encore actionable au moment du submit (z-score n'a pas flippé) |

---

## 2. Décision logic — du signal au trade prêt

```python
def build_trade_preview(signal: PcaSignal, surface: dict, book: PositionBook, 
                        regime: RegimeState, config: TradePreviewConfig) -> TradePreview:
    
    # 1. Construire structure depuis recommendation
    structure = build_structure_from_recommendation(
        recommendation=signal.recommended_structure,
        tenor=signal.default_tenor,
        surface=surface
    )
    
    # 2. Pricer la structure
    pricing = price_structure(structure, surface)
    
    # 3. Calculer greeks nets
    greeks = compute_net_greeks(structure, surface)
    
    # 4. Simuler scenarios
    scenarios = simulate_scenarios(structure, surface, config.scenario_grid)
    
    # 5. Déterminer sizing
    sizing = compute_sizing(
        signal=signal,
        structure=structure,
        book=book,
        regime=regime,
        config=config.sizing
    )
    
    # 6. Pre-submit validation gates
    checks = run_pre_submit_checks(
        regime=regime,
        signal=signal,
        sizing=sizing,
        pricing=pricing,
        book=book,
        surface_freshness=compute_freshness(surface),
        config=config.validation
    )
    
    return TradePreview(
        structure=structure,
        pricing=pricing,
        greeks=greeks,
        scenarios=scenarios,
        sizing=sizing,
        checks=checks,
        state="valid_for_submit" if all(c.passed for c in checks) else "blocked"
    )
```

---

## 3. Le panel (UI) — structure 5 sections + bouton Submit

| Section | Contenu | Source data | Statut implémentation |
|---|---|---|---|
| A — Legs | Tableau legs : contract, strike, DTE, qty, side, IV | `structure.legs` enrichi avec IV depuis surface | À implémenter |
| B — Greeks net | Vega, gamma, theta, delta agrégés | `compute_net_greeks(structure, surface)` | À implémenter |
| C — Pricing | Premium, breakeven, max loss, vega edge | `price_structure()` + estimation IV reprice attendu | À implémenter |
| D — Scenarios | Tableau 3 colonnes (Sc.A/B/C) avec spot move × IV reprice → P&L | `simulate_scenarios()` | À implémenter |
| E — Sizing | Base size, multiplicateurs, qty finale | `compute_sizing()` avec formule explicite | À implémenter |
| Submit/Cancel | Bouton avec checks bloquants visibles | `run_pre_submit_checks()` | À implémenter |

---

## 4. Schema du payload `trade_preview` (réponse API, pas dans `latest_vol_surface`)

Endpoint API : `POST /api/v1/trade/preview` avec body `{signal_id: str, override_tenor?: str, override_qty?: int}`. Réponse :

```jsonc
{
  "preview_id": "tp_a1b2c3d4",                      // identifiant unique du preview, valide 2 min
  "created_at": "2026-04-30T14:32:15Z",
  "expires_at": "2026-04-30T14:34:15Z",
  
  "signal_source": {
    "signal_id": "sig_xyz789",
    "pca_model_version": "pca_v1_2026_05_03",
    "triggering_pc": 1,
    "z_score": 1.8,
    "label": "CHEAP"
  },
  
  "structure": {
    "type": "straddle_atm",                         // mapping signal_recommendations_map
    "reference_tenor": "3M",
    "legs": [
      {
        "leg_idx": 0,
        "contract_type": "call",
        "expiry": "2026-07-30",
        "dte": 90,
        "strike": 1.1800,
        "qty": 10,
        "side": "BUY",
        "entry_iv_pct": 6.05,
        "entry_price_per_contract_usd": 178.40
      },
      {
        "leg_idx": 1,
        "contract_type": "put",
        "expiry": "2026-07-30",
        "dte": 90,
        "strike": 1.1800,
        "qty": 10,
        "side": "BUY",
        "entry_iv_pct": 6.05,
        "entry_price_per_contract_usd": 163.60
      },
      {
        "leg_idx": 2,
        "contract_type": "future",
        "expiry": "2026-07-30",
        "dte": 90,
        "strike": null,
        "qty": 3,
        "side": "SELL",
        "entry_iv_pct": null,
        "entry_price_per_contract_usd": 14750.00
      }
    ]
  },
  
  "greeks_net": {
    "vega_usd_per_volpt":   847.0,
    "gamma_usd_per_pip2":   2.3,
    "theta_usd_per_day":    -89.0,
    "delta_unhedged":       0.05,
    "delta_post_hedge":     0.00
  },
  
  "pricing": {
    "premium_paid_usd":     3420.00,
    "breakeven_pips_each_side": 380,
    "max_loss_usd":         3420.00,                 // = premium pour long structures
    "max_loss_at_expiry_only": true,
    "vega_edge_expected_usd": 680.00,                // signal-implied IV move × vega
    "expected_iv_reprice_volpts": 0.8                // estimation depuis z-score
  },
  
  "scenarios": [
    {
      "label": "favorable",
      "spot_move_pct":      2.0,
      "iv_reprice_volpts":  +1.0,
      "pnl_gamma_theta_usd": +1200,
      "pnl_vega_usd":        +847,
      "pnl_total_usd":       +2047
    },
    {
      "label": "neutral",
      "spot_move_pct":      0.0,
      "iv_reprice_volpts":  0.0,
      "pnl_gamma_theta_usd": -800,                   // theta bleed
      "pnl_vega_usd":        0,
      "pnl_total_usd":       -800
    },
    {
      "label": "adverse",
      "spot_move_pct":      0.5,                     // small move
      "iv_reprice_volpts":  -1.0,                    // IV down
      "pnl_gamma_theta_usd": -500,
      "pnl_vega_usd":        -847,
      "pnl_total_usd":      -1347
    }
  ],
  
  "sizing": {
    "base_qty":             10,
    "multipliers": {
      "z_score_factor":     1.20,                    // |z|/threshold = 1.8/1.5
      "book_penalty":       0.90,                    // déjà long vega
      "event_dampener":     1.00,                    // OFF
      "regime_multiplier":  1.00                     // calm
    },
    "final_qty_per_leg":    11,
    "final_premium_usd":    3762.00,
    "sizing_formula":       "base × z_factor × book_penalty × event_dampener × regime_mult"
  },
  
  "costs": {
    "entry_spread_cost_usd":     145.00,
    "entry_commission_usd":       28.05,
    "expected_hedge_cost_usd":    85.00,             // estimé sur lifetime
    "expected_total_cost_usd":   258.05,
    "vega_edge_minus_cost_usd":  421.95              // edge - cost net expected
  },
  
  "pre_submit_checks": [
    {"name": "regime_not_pre_event",  "passed": true},
    {"name": "signal_still_actionable", "passed": true, "current_z": 1.7, "armed_z": 1.8},
    {"name": "max_loss_under_2pct_capital", "passed": true, "max_loss_pct": 0.34},
    {"name": "vega_under_book_limit", "passed": true, "post_trade_vega": 1247, "limit": 5000},
    {"name": "iv_data_fresh", "passed": true, "data_age_seconds": 87},
    {"name": "no_arb_violation_on_legs", "passed": true},
    {"name": "minimum_liquidity", "passed": true, "min_quoted_size": 25}
  ],
  
  "state": "valid_for_submit",                       // valid_for_submit | blocked | expired
  "blocking_reasons": []                              // populated si state=blocked
}
```

---

## 5. Tables Postgres nécessaires

### 5.1 `structure_definitions` — catalogue des structures supportées

Définit les structures que le système sait construire et pricer. Externalisé pour permettre extension sans deploy.

```sql
CREATE TABLE structure_definitions (
    id              SERIAL PRIMARY KEY,
    
    structure_type      TEXT NOT NULL UNIQUE,       -- 'straddle_atm' | 'butterfly_25d' | ...
    display_name        TEXT NOT NULL,              -- 'Straddle ATM' (pour UI)
    
    -- définition algorithmique des legs
    leg_template        JSONB NOT NULL,             -- liste de templates de legs
    
    -- contraintes
    min_legs            INTEGER NOT NULL,
    max_legs            INTEGER NOT NULL,
    requires_delta_hedge BOOLEAN NOT NULL DEFAULT true,
    
    -- caractéristiques greeks (signe attendu)
    typical_vega_sign   TEXT NOT NULL,              -- 'positive' | 'negative' | 'neutral'
    typical_gamma_sign  TEXT NOT NULL,
    typical_theta_sign  TEXT NOT NULL,
    
    description         TEXT,
    rationale_for_pc    TEXT,                       -- pourquoi cette structure pour PC1/PC2/PC3
    
    is_active           BOOLEAN NOT NULL DEFAULT true,
    
    CONSTRAINT chk_vega_sign CHECK (typical_vega_sign IN ('positive', 'negative', 'neutral')),
    CONSTRAINT chk_gamma_sign CHECK (typical_gamma_sign IN ('positive', 'negative', 'neutral')),
    CONSTRAINT chk_theta_sign CHECK (typical_theta_sign IN ('positive', 'negative', 'neutral'))
);

-- Seed initial pour les 6 structures du signal_recommendations_map
INSERT INTO structure_definitions 
    (structure_type, display_name, leg_template, min_legs, max_legs, 
     requires_delta_hedge, typical_vega_sign, typical_gamma_sign, typical_theta_sign,
     description, rationale_for_pc) VALUES

('straddle_atm',
 'Long straddle ATM',
 '[
    {"contract_type": "call", "delta_pillar": "atm", "side": "BUY", "qty_factor": 1},
    {"contract_type": "put",  "delta_pillar": "atm", "side": "BUY", "qty_factor": 1}
 ]',
 2, 2, true, 'positive', 'positive', 'negative',
 'Buy ATM call + ATM put same expiry, delta hedged with future',
 'PC1 CHEAP : level vol bas → buy vol via ATM straddle, max convexity'),

('short_strangle',
 'Short OTM strangle',
 '[
    {"contract_type": "call", "delta_pillar": "25dc", "side": "SELL", "qty_factor": 1},
    {"contract_type": "put",  "delta_pillar": "25dp", "side": "SELL", "qty_factor": 1}
 ]',
 2, 2, true, 'negative', 'negative', 'positive',
 'Sell 25d OTM call + 25d OTM put, delta hedged',
 'PC1 EXPENSIVE : level vol haut → sell vol via strangle, contained tail'),

('calendar_long',
 'Calendar buy long-dated',
 '[
    {"contract_type": "call", "delta_pillar": "atm", "tenor_role": "near", "side": "SELL", "qty_factor": 1},
    {"contract_type": "call", "delta_pillar": "atm", "tenor_role": "far",  "side": "BUY",  "qty_factor": 1}
 ]',
 2, 2, true, 'positive', 'neutral', 'neutral',
 'Sell near tenor ATM call, buy far tenor ATM call',
 'PC2 CHEAP : term inverted → buy long, sell short'),

('calendar_short',
 'Calendar sell long-dated',
 '[
    {"contract_type": "call", "delta_pillar": "atm", "tenor_role": "near", "side": "BUY",  "qty_factor": 1},
    {"contract_type": "call", "delta_pillar": "atm", "tenor_role": "far",  "side": "SELL", "qty_factor": 1}
 ]',
 2, 2, true, 'negative', 'neutral', 'neutral',
 'Buy near, sell far',
 'PC2 EXPENSIVE : term steep → sell long, buy short'),

('long_butterfly_25d',
 'Long 25d butterfly',
 '[
    {"contract_type": "call", "delta_pillar": "10dc", "side": "BUY",  "qty_factor": 1},
    {"contract_type": "call", "delta_pillar": "atm",  "side": "SELL", "qty_factor": 2},
    {"contract_type": "call", "delta_pillar": "10dp", "side": "BUY",  "qty_factor": 1}
 ]',
 3, 3, true, 'neutral', 'neutral', 'neutral',
 'Long wings 10d, short body ATM (2x). Captures smile reversion.',
 'PC3 CHEAP : wings cheap vs ATM → buy butterfly'),

('short_butterfly_25d',
 'Short 25d butterfly',
 '[
    {"contract_type": "call", "delta_pillar": "10dc", "side": "SELL", "qty_factor": 1},
    {"contract_type": "call", "delta_pillar": "atm",  "side": "BUY",  "qty_factor": 2},
    {"contract_type": "call", "delta_pillar": "10dp", "side": "SELL", "qty_factor": 1}
 ]',
 3, 3, true, 'neutral', 'neutral', 'neutral',
 'Short wings, long body. Captures smile widening.',
 'PC3 EXPENSIVE : wings rich vs ATM → sell butterfly');
```

**Cardinalité** : 6 rows initial. Statique, peu d'évolution.

---

### 5.2 `trade_previews` — log des previews générés

Toutes les fois que l'utilisateur clique "Arm trade", on log un preview. Permet audit, métriques (combien de previews soumis vs cancel), et debugging.

```sql
CREATE TABLE trade_previews (
    id              BIGSERIAL PRIMARY KEY,
    preview_id      TEXT NOT NULL UNIQUE,           -- "tp_a1b2c3d4"
    
    -- temporal
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ NOT NULL,           -- created + 2 min
    
    -- signal source
    pca_signal_id           BIGINT REFERENCES pca_signals(id),
    triggering_pc           INTEGER,
    armed_z_score           DOUBLE PRECISION,
    armed_signal_label      TEXT,
    
    -- structure (full payload pour reconstruct)
    structure_type          TEXT NOT NULL,
    reference_tenor         TEXT NOT NULL,
    structure_full_payload  JSONB NOT NULL,         -- contient legs, pricing, greeks, scenarios, sizing
    
    -- pre-submit validation
    state                   TEXT NOT NULL,          -- 'valid_for_submit' | 'blocked' | 'expired' | 'submitted' | 'cancelled'
    pre_submit_checks       JSONB NOT NULL,
    blocking_reasons        JSONB,
    
    -- outcome (populated quand action utilisateur)
    user_action             TEXT,                    -- 'submitted' | 'cancelled' | null si expired silently
    user_action_at          TIMESTAMPTZ,
    
    -- si submitted, lien vers le trade créé
    submitted_trade_id      BIGINT,                  -- FK vers trades table (étape 4) si existe
    
    CONSTRAINT chk_state CHECK (state IN ('valid_for_submit', 'blocked', 'expired', 'submitted', 'cancelled')),
    CONSTRAINT chk_user_action CHECK (user_action IS NULL OR user_action IN ('submitted', 'cancelled'))
);

CREATE INDEX ix_trade_previews_created ON trade_previews (created_at DESC);
CREATE INDEX ix_trade_previews_state ON trade_previews (state, created_at DESC);
CREATE INDEX ix_trade_previews_pca_signal ON trade_previews (pca_signal_id);
```

**Cardinalité** : variable selon usage. Estimation : 10-50 previews / jour pendant phase active = ~5k-15k / an. Trivial.

---

### 5.3 `pricing_cache` — cache pricing intermédiaire (optionnel mais recommandé)

Pour éviter de re-pricer la même surface plusieurs fois (preview généré toutes les minutes pendant que l'utilisateur réfléchit).

```sql
CREATE TABLE pricing_cache (
    id              BIGSERIAL PRIMARY KEY,
    
    surface_timestamp   TIMESTAMPTZ NOT NULL,       -- timestamp de la surface utilisée
    structure_type      TEXT NOT NULL,
    reference_tenor     TEXT NOT NULL,
    
    -- résultat cached
    pricing_payload     JSONB NOT NULL,             -- premium, greeks, scenarios
    
    cached_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at          TIMESTAMPTZ NOT NULL,       -- cached + 60s
    
    UNIQUE (surface_timestamp, structure_type, reference_tenor)
);

CREATE INDEX ix_pricing_cache_lookup ON pricing_cache (structure_type, reference_tenor, surface_timestamp);
CREATE INDEX ix_pricing_cache_expires ON pricing_cache (expires_at);
```

**Pattern de purge** : background job toutes les 5 min, DELETE WHERE expires_at < NOW().

**Cardinalité** : ~6 structures × 6 tenors × ~480 surface_timestamps cachés = ~17k. Marginal.

---

### 5.4 `book_state_snapshots` — état du book pour sizing

Le sizing dépend du book courant (vega total, etc.). Plutôt que recomputer à chaque preview, on cache un snapshot léger mis à jour quand le book change.

```sql
CREATE TABLE book_state_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbol          TEXT NOT NULL DEFAULT 'EURUSD',
    
    -- aggregate greeks
    total_vega_usd          DOUBLE PRECISION NOT NULL,
    total_gamma_usd         DOUBLE PRECISION NOT NULL,
    total_theta_usd         DOUBLE PRECISION NOT NULL,
    total_delta             DOUBLE PRECISION NOT NULL,
    
    -- ventilation
    vega_by_tenor           JSONB,                  -- {1M: X, 2M: Y, ...}
    vega_by_pc_source       JSONB,                  -- {pc1: X, pc2: Y, pc3: Z}
    
    -- positions count
    n_open_structures       INTEGER NOT NULL DEFAULT 0,
    n_open_legs             INTEGER NOT NULL DEFAULT 0,
    
    -- capital
    notional_engaged_usd    DOUBLE PRECISION,
    capital_total_usd       DOUBLE PRECISION,       -- snapshot du capital disponible
    margin_used_usd         DOUBLE PRECISION,
    
    is_current              BOOLEAN NOT NULL DEFAULT true
);

CREATE UNIQUE INDEX ix_book_state_current ON book_state_snapshots (symbol, is_current) WHERE is_current = true;
CREATE INDEX ix_book_state_ts ON book_state_snapshots (timestamp DESC);
```

**Pattern** : un seul row `is_current=true` par symbole. UPDATE plutôt que INSERT pour le row courant. INSERT historique chaque heure pour audit.

---

### 5.5 `risk_limits` — paramètres de risque hot-reloadable

Limites configurables sans deploy. Consommé par sizing et pre-submit checks.

```sql
CREATE TABLE risk_limits (
    id              SERIAL PRIMARY KEY,
    
    limit_name              TEXT NOT NULL UNIQUE,
    limit_value             DOUBLE PRECISION NOT NULL,
    unit                    TEXT NOT NULL,          -- 'usd' | 'pct_capital' | 'volpts' | 'count'
    
    description             TEXT,
    is_active               BOOLEAN NOT NULL DEFAULT true,
    
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by              TEXT
);

INSERT INTO risk_limits (limit_name, limit_value, unit, description) VALUES
    ('max_loss_per_trade_pct',      2.0,    'pct_capital', 'Max loss per single trade as % of capital'),
    ('max_book_vega_usd',           5000.0, 'usd',         'Max total book vega in USD per vol point'),
    ('max_book_vega_per_tenor_usd', 2000.0, 'usd',         'Max vega per single tenor'),
    ('max_n_open_structures',       8,      'count',       'Max simultaneous open structures'),
    ('max_iv_data_age_seconds',     120,    'count',       'IV must be < 2 min old'),
    ('min_liquidity_quoted_size',   10,     'count',       'Minimum quoted size on legs'),
    ('preview_validity_seconds',    120,    'count',       'Trade preview valid for 2 min');
```

**Cardinalité** : ~10-20 rows. Statique.

---

## 6. Schéma relationnel

```
┌─────────────────────────┐      ┌──────────────────────────┐
│  pca_signals (étape 2)  │      │ structure_definitions    │
│  (recommended_structure)│      │ (catalogue)              │
└──────────┬──────────────┘      └──────────┬───────────────┘
           │                                 │
           │ build_structure                 │ leg_template
           ▼                                 ▼
┌─────────────────────────────────────────────────────┐
│         Trade Preview Engine                        │
│  build → price → greeks → scenarios → sizing → checks│
└──────────┬──────────────────────────────┬───────────┘
           │ reads                         │ reads
           ▼                               ▼
┌─────────────────────────┐      ┌──────────────────────────┐
│  book_state_snapshots   │      │  risk_limits             │
│  (current=true row)     │      │  (config hot-reload)     │
└─────────────────────────┘      └──────────────────────────┘
           │                               │
           │ used in sizing                │ used in checks
           ▼                               ▼
┌─────────────────────────┐
│   trade_previews        │
│  (1 row par "Arm")      │
└──────────┬──────────────┘
           │ if user clicks Submit
           ▼
   trades table (étape 4)
```

---

## 7. Pipeline backend par appel API

### 7.1 Endpoint principal

```python
# src/api/routes/trade.py
@router.post("/api/v1/trade/preview")
async def create_trade_preview(
    request: TradePreviewRequest,
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis)
) -> TradePreviewResponse:
    
    # 1. Vérifier signal source existe et est actionable
    signal = db.query(PcaSignal).filter_by(id=request.signal_id).first()
    if signal is None:
        raise HTTPException(404, "signal not found")
    if not signal.actionable:
        raise HTTPException(400, f"signal not actionable: {signal.actionable_reason}")
    
    # 2. Lecture surface courante
    surface_payload = await redis.get("latest_vol_surface:EURUSD")
    if surface_payload is None:
        raise HTTPException(503, "surface unavailable")
    surface = json.loads(surface_payload)
    
    # 3. Lecture book state courant
    book = db.execute(
        select(BookStateSnapshot).where(BookStateSnapshot.is_current == True)
    ).scalar_one_or_none()
    if book is None:
        book = empty_book_state()  # bootstrap : pas de positions
    
    # 4. Lecture régime courant
    regime = surface["surface"]["_regime"]
    
    # 5. Lecture risk limits actuelles
    limits = {row.limit_name: row.limit_value 
              for row in db.execute(select(RiskLimit).where(RiskLimit.is_active == True))}
    
    # 6. Build preview
    preview = build_trade_preview(
        signal=signal,
        surface=surface["surface"],
        book=book,
        regime=regime,
        config=TradePreviewConfig(
            tenor_override=request.override_tenor,
            qty_override=request.override_qty,
            limits=limits
        )
    )
    
    # 7. Persist preview
    preview_record = TradePreview(
        preview_id=generate_preview_id(),
        expires_at=datetime.utcnow() + timedelta(seconds=limits["preview_validity_seconds"]),
        pca_signal_id=signal.id,
        triggering_pc=signal.pc_id,
        armed_z_score=signal.z_score,
        armed_signal_label=signal.label,
        structure_type=preview.structure.type,
        reference_tenor=preview.structure.reference_tenor,
        structure_full_payload=preview.to_dict(),
        state=preview.state,
        pre_submit_checks=[c.to_dict() for c in preview.checks],
        blocking_reasons=[c.reason for c in preview.checks if not c.passed],
    )
    db.add(preview_record)
    db.commit()
    
    return TradePreviewResponse(preview_id=preview_record.preview_id, **preview.to_dict())
```

### 7.2 Builder de structure

```python
# src/core/structures/builder.py
def build_structure_from_recommendation(
    recommendation: str,            # ex: "straddle_atm_3m"
    tenor: str,                     # "3M"
    surface: dict
) -> Structure:
    
    # Parse recommendation : "straddle_atm" + "3m" → {type: "straddle_atm", tenor: "3M"}
    structure_type, tenor_from_rec = parse_recommendation(recommendation)
    if tenor:  # override
        tenor_from_rec = tenor
    
    # Lecture template depuis structure_definitions
    definition = db.query(StructureDefinition).filter_by(
        structure_type=structure_type, is_active=True
    ).first()
    if definition is None:
        raise ValueError(f"unknown structure: {structure_type}")
    
    # Pour chaque leg du template, résoudre les paramètres concrets
    legs = []
    for leg_template in definition.leg_template:
        # Tenor : use reference unless tenor_role override (calendar)
        actual_tenor = resolve_tenor(leg_template, tenor_from_rec)
        
        # Strike : depuis delta pillar dans la surface
        delta_pillar = leg_template["delta_pillar"]
        strike = surface[actual_tenor][delta_pillar]["strike"]
        iv = surface[actual_tenor][delta_pillar]["iv"]
        
        # DTE depuis tenor
        dte = TENOR_TO_DTE[actual_tenor]
        expiry = compute_expiry(dte)
        
        legs.append(Leg(
            leg_idx=len(legs),
            contract_type=leg_template["contract_type"],
            expiry=expiry,
            dte=dte,
            strike=strike,
            qty_factor=leg_template["qty_factor"],   # multiplied par base_qty au sizing
            side=leg_template["side"],
            entry_iv_pct=iv * 100
        ))
    
    return Structure(
        type=structure_type,
        reference_tenor=tenor_from_rec,
        legs=legs,
        requires_delta_hedge=definition.requires_delta_hedge
    )
```

### 7.3 Pricer

```python
# src/core/pricing/structure_pricer.py
def price_structure(structure: Structure, surface: dict, spot: float) -> PricingResult:
    """Compute premium per contract for each leg, total premium of structure."""
    
    leg_prices = []
    total_premium = 0.0
    
    for leg in structure.legs:
        if leg.contract_type in ("call", "put"):
            # Black-Scholes pricing
            price_per_contract = black_scholes_price(
                S=spot,
                K=leg.strike,
                T=leg.dte / 365,
                r=get_risk_free_rate(leg.dte),
                sigma=leg.entry_iv_pct / 100,
                option_type=leg.contract_type
            ) * CONTRACT_MULTIPLIER  # 100 pour FOP CME
            
            sign = +1 if leg.side == "BUY" else -1
            leg_prices.append(price_per_contract)
            total_premium += sign * price_per_contract * leg.qty_factor
        
        elif leg.contract_type == "future":
            # Future = no premium, but margin required
            leg_prices.append(0)
    
    # Breakeven calculation (approximation, true breakeven needs solving)
    breakeven_pips = compute_breakeven_pips(structure, total_premium, surface, spot)
    
    # Max loss
    max_loss = compute_max_loss(structure, total_premium)
    
    return PricingResult(
        leg_prices_usd=leg_prices,
        total_premium_usd=total_premium,
        breakeven_pips_each_side=breakeven_pips,
        max_loss_usd=max_loss,
    )
```

### 7.4 Greeks computer

```python
# src/core/pricing/greeks.py
def compute_net_greeks(structure: Structure, surface: dict, spot: float) -> NetGreeks:
    """Compute aggregated greeks across all legs."""
    
    total_vega = 0.0
    total_gamma = 0.0
    total_theta = 0.0
    total_delta = 0.0
    
    for leg in structure.legs:
        if leg.contract_type in ("call", "put"):
            T = leg.dte / 365
            sigma = leg.entry_iv_pct / 100
            r = get_risk_free_rate(leg.dte)
            
            vega = bs_vega(spot, leg.strike, T, r, sigma) * leg.qty_factor
            gamma = bs_gamma(spot, leg.strike, T, r, sigma) * leg.qty_factor
            theta = bs_theta(spot, leg.strike, T, r, sigma, leg.contract_type) * leg.qty_factor
            delta = bs_delta(spot, leg.strike, T, r, sigma, leg.contract_type) * leg.qty_factor
            
            sign = +1 if leg.side == "BUY" else -1
            total_vega  += sign * vega  * CONTRACT_MULTIPLIER
            total_gamma += sign * gamma * CONTRACT_MULTIPLIER
            total_theta += sign * theta * CONTRACT_MULTIPLIER
            total_delta += sign * delta * leg.qty_factor
        
        elif leg.contract_type == "future":
            sign = +1 if leg.side == "BUY" else -1
            total_delta += sign * leg.qty_factor      # future delta = 1 par contract
    
    return NetGreeks(
        vega_usd_per_volpt=total_vega,
        gamma_usd_per_pip2=total_gamma,
        theta_usd_per_day=total_theta,
        delta_unhedged=total_delta,
    )
```

### 7.5 Scenario engine

```python
# src/core/scenarios/engine.py
def simulate_scenarios(structure: Structure, surface: dict, spot: float,
                       grid: list[ScenarioConfig]) -> list[ScenarioResult]:
    """For each scenario (spot move × IV reprice), compute P&L decomposed."""
    
    results = []
    base_pricing = price_structure(structure, surface, spot)
    
    for scenario in grid:
        # Construct shocked spot and surface
        new_spot = spot * (1 + scenario.spot_move_pct / 100)
        shocked_surface = shock_surface(surface, scenario.iv_reprice_volpts)
        
        # Re-price structure under shocked conditions
        shocked_pricing = price_structure(structure, shocked_surface, new_spot)
        
        # Total P&L = shocked - base (for long structure, structure value increase = P&L)
        total_pnl = shocked_pricing.total_premium_usd - base_pricing.total_premium_usd
        
        # Decomposition (approximation analytique via greeks)
        greeks = compute_net_greeks(structure, surface, spot)
        spot_move_usd = (new_spot - spot) * spot * 10000  # pips
        
        pnl_gamma = 0.5 * greeks.gamma_usd_per_pip2 * (spot_move_usd ** 2)
        pnl_theta = greeks.theta_usd_per_day * 1  # 1 day later assumption (peut être config)
        pnl_vega = greeks.vega_usd_per_volpt * scenario.iv_reprice_volpts
        pnl_other = total_pnl - pnl_gamma - pnl_theta - pnl_vega  # higher order
        
        results.append(ScenarioResult(
            label=scenario.label,
            spot_move_pct=scenario.spot_move_pct,
            iv_reprice_volpts=scenario.iv_reprice_volpts,
            pnl_gamma_theta_usd=pnl_gamma + pnl_theta,
            pnl_vega_usd=pnl_vega,
            pnl_other_usd=pnl_other,
            pnl_total_usd=total_pnl,
        ))
    
    return results

# Default scenario grid
DEFAULT_SCENARIOS = [
    ScenarioConfig(label="favorable", spot_move_pct=2.0,  iv_reprice_volpts=+1.0),
    ScenarioConfig(label="neutral",   spot_move_pct=0.0,  iv_reprice_volpts=0.0),
    ScenarioConfig(label="adverse",   spot_move_pct=0.5,  iv_reprice_volpts=-1.0),
]
```

### 7.6 Sizer

```python
# src/core/sizing/sizer.py
def compute_sizing(signal: PcaSignal, structure: Structure, book: BookStateSnapshot,
                   regime: RegimeState, config: SizingConfig) -> SizingResult:
    """Apply sizing formula and constraints."""
    
    base_qty = config.base_qty  # ex: 10 contracts
    
    # Multiplicateur z-score (conviction scaling)
    z_factor = abs(signal.z_score) / config.threshold_min
    z_factor = min(z_factor, config.max_z_multiplier)  # cap pour éviter sizing fou sur z extrême
    
    # Book penalty : si déjà long vega, réduire
    structure_vega_sign = get_typical_vega_sign(structure.type)
    if same_sign(book.total_vega_usd, structure_vega_sign):
        book_ratio = abs(book.total_vega_usd) / config.book_vega_neutral_threshold
        book_penalty = max(0.5, 1 - config.book_alpha * book_ratio)
    else:
        book_penalty = 1.0
    
    # Event dampener
    event_dampener_mult = 0.5 if regime.event_dampener else 1.0
    
    # Regime multiplier
    regime_mult = {"calm": 1.0, "stressed": 0.7, "pre_event": 0.0}[regime.label]
    
    # Final qty (round to integer)
    final_qty = int(round(base_qty * z_factor * book_penalty * event_dampener_mult * regime_mult))
    final_qty = max(1, final_qty)  # min 1 contract
    
    # Apply qty_factor de chaque leg
    leg_quantities = {leg.leg_idx: final_qty * leg.qty_factor for leg in structure.legs}
    
    return SizingResult(
        base_qty=base_qty,
        multipliers={
            "z_score_factor": round(z_factor, 2),
            "book_penalty": round(book_penalty, 2),
            "event_dampener": event_dampener_mult,
            "regime_multiplier": regime_mult,
        },
        final_qty_per_leg=final_qty,
        leg_quantities=leg_quantities,
        sizing_formula="base × z_factor × book_penalty × event_dampener × regime_mult",
    )
```

### 7.7 Pre-submit validator

```python
# src/core/validation/pre_submit.py
def run_pre_submit_checks(regime: dict, signal: PcaSignal, sizing: SizingResult,
                          pricing: PricingResult, book: BookStateSnapshot,
                          surface_freshness: float, config: ValidationConfig) -> list[Check]:
    """Run all pre-submit checks. Each returns Check(name, passed, details)."""
    
    checks = []
    
    # Check 1 : régime not pre_event
    checks.append(Check(
        name="regime_not_pre_event",
        passed=regime["label"] != "pre_event",
    ))
    
    # Check 2 : signal still actionable
    current_signal = db.query(PcaSignal).filter_by(
        pca_model_id=signal.pca_model_id, pc_id=signal.pc_id
    ).order_by(PcaSignal.timestamp.desc()).first()
    
    z_flipped = (signal.z_score > 0) != (current_signal.z_score > 0)
    z_too_weak = abs(current_signal.z_score) < config.threshold_min * 0.7
    checks.append(Check(
        name="signal_still_actionable",
        passed=not z_flipped and not z_too_weak,
        details={"current_z": current_signal.z_score, "armed_z": signal.z_score}
    ))
    
    # Check 3 : max loss under capital limit
    max_loss = pricing.max_loss_usd * sizing.final_qty_per_leg / sizing.base_qty
    max_loss_pct = max_loss / book.capital_total_usd * 100
    checks.append(Check(
        name="max_loss_under_2pct_capital",
        passed=max_loss_pct <= config.max_loss_per_trade_pct,
        details={"max_loss_pct": round(max_loss_pct, 2)}
    ))
    
    # Check 4 : vega under book limit
    structure_vega = compute_structure_vega(...) * sizing.final_qty_per_leg
    post_trade_vega = book.total_vega_usd + structure_vega
    checks.append(Check(
        name="vega_under_book_limit",
        passed=abs(post_trade_vega) <= config.max_book_vega_usd,
        details={"post_trade_vega": post_trade_vega, "limit": config.max_book_vega_usd}
    ))
    
    # Check 5 : IV data fresh
    checks.append(Check(
        name="iv_data_fresh",
        passed=surface_freshness <= config.max_iv_data_age_seconds,
        details={"data_age_seconds": surface_freshness}
    ))
    
    # Check 6 : no_arb violations on legs
    has_arb_violation = check_legs_for_arb_violations(structure, surface)
    checks.append(Check(
        name="no_arb_violation_on_legs",
        passed=not has_arb_violation,
    ))
    
    # Check 7 : minimum liquidity
    min_quoted_size = check_minimum_liquidity(structure, surface)
    checks.append(Check(
        name="minimum_liquidity",
        passed=min_quoted_size >= config.min_liquidity_quoted_size,
        details={"min_quoted_size": min_quoted_size}
    ))
    
    return checks
```

---

## 8. Estimation effort par sous-tâche

| Sous-tâche | Effort | Bloquant ? |
|---|---|---|
| Migration Postgres : 5 tables + indices | 0.5 j | Oui |
| Seed `structure_definitions` (6 structures) | 0.5 j | Oui |
| Seed `risk_limits` | 0.5 j | Oui |
| Module `core/structures/builder.py` | 1 j | Oui |
| Module `core/pricing/structure_pricer.py` (multi-leg pricing) | 1.5 j | Oui |
| Module `core/pricing/greeks.py` (vega, gamma, theta, delta agrégés) | 1.5 j | Oui |
| Module `core/scenarios/engine.py` | 1 j | Oui |
| Module `core/sizing/sizer.py` | 1 j | Oui |
| Module `core/validation/pre_submit.py` (7 checks) | 1.5 j | Oui |
| Module `core/book/state_tracker.py` (maintien book_state_snapshots) | 1 j | Oui |
| Endpoint API `/api/v1/trade/preview` + WebSocket invalidation | 1 j | Oui |
| Frontend Panel 3 (5 sections + Submit grayed sur checks) | 2-3 j | Oui |
| Pricing cache (optionnel) | 0.5 j | Non |
| Tests : pricing réconciliation BS, greeks signs, sizing formula, all checks | 2 j | Oui |
| **Total MVP fonctionnel** | **~15 jours dev** | |

---

## 9. Stratégie de bootstrap (premier panel sans positions)

Au premier démarrage, `book_state_snapshots` n'a pas de row `is_current=true`. Stratégie :

```python
def empty_book_state() -> BookStateSnapshot:
    return BookStateSnapshot(
        timestamp=datetime.utcnow(),
        total_vega_usd=0,
        total_gamma_usd=0,
        total_theta_usd=0,
        total_delta=0,
        vega_by_tenor={},
        vega_by_pc_source={},
        n_open_structures=0,
        n_open_legs=0,
        notional_engaged_usd=0,
        capital_total_usd=config.STARTING_CAPITAL_USD,  # depuis config
        margin_used_usd=0,
        is_current=True,
    )
```

Ce row est créé au premier appel preview. Capital initial vient de config (placeholder, ex: $100k pour paper trading).

---

## 10. Tests à écrire (acceptance criteria)

```python
# test_trade_preview.py

def test_straddle_atm_legs_correct(db):
    """Build straddle_atm should produce 2 ATM legs (call + put) + 1 hedge future"""
    structure = build_structure_from_recommendation(
        recommendation="straddle_atm_3m", tenor="3M", surface=mock_surface
    )
    assert len(structure.legs) == 2  # le hedge future est ajouté plus tard
    assert structure.legs[0].contract_type == "call"
    assert structure.legs[0].strike == structure.legs[1].strike  # ATM
    assert structure.legs[0].dte == 90

def test_pricing_premium_matches_bs_sum(mock_surface):
    """Total premium = sum of leg prices avec signe correct"""
    structure = build_test_straddle()
    pricing = price_structure(structure, mock_surface, spot=1.18)
    
    # Long straddle = paid premium
    assert pricing.total_premium_usd > 0

def test_greeks_signs_match_definition(mock_surface):
    """Long straddle : vega+, gamma+, theta- (always)"""
    structure = build_test_straddle(side="LONG")
    greeks = compute_net_greeks(structure, mock_surface, spot=1.18)
    
    assert greeks.vega_usd_per_volpt > 0
    assert greeks.gamma_usd_per_pip2 > 0
    assert greeks.theta_usd_per_day < 0

def test_short_strangle_greeks_opposite():
    """Short strangle : vega-, gamma-, theta+"""
    structure = build_test_strangle(side="SHORT")
    greeks = compute_net_greeks(structure, mock_surface, spot=1.18)
    
    assert greeks.vega_usd_per_volpt < 0
    assert greeks.gamma_usd_per_pip2 < 0
    assert greeks.theta_usd_per_day > 0

def test_sizing_formula_applied():
    """Final qty = base × z × book × event × regime"""
    sizing = compute_sizing(
        signal=PcaSignal(z_score=1.8),
        structure=long_straddle,
        book=BookStateSnapshot(total_vega_usd=0, ...),  # empty book
        regime=RegimeState(label="calm", event_dampener=False),
        config=SizingConfig(base_qty=10, threshold_min=1.5)
    )
    
    expected_z_factor = 1.8 / 1.5  # = 1.2
    expected_qty = round(10 * 1.2 * 1.0 * 1.0 * 1.0)  # = 12
    assert sizing.final_qty_per_leg == 12

def test_check_pre_event_blocks_submit():
    """Régime pre_event → check 'regime_not_pre_event' fails"""
    checks = run_pre_submit_checks(
        regime={"label": "pre_event"}, ...
    )
    regime_check = next(c for c in checks if c.name == "regime_not_pre_event")
    assert regime_check.passed is False

def test_check_max_loss_blocks_oversized():
    """Si max_loss > 2% capital, check fails"""
    # Setup: capital = 100k, sizing pousse max_loss à 3000
    checks = run_pre_submit_checks(
        pricing=PricingResult(max_loss_usd=3000),
        book=BookStateSnapshot(capital_total_usd=100000),
        config=ValidationConfig(max_loss_per_trade_pct=2.0)
    )
    max_loss_check = next(c for c in checks if c.name == "max_loss_under_2pct_capital")
    assert max_loss_check.passed is False
    assert max_loss_check.details["max_loss_pct"] > 2.0

def test_check_signal_flipped_blocks():
    """Si z-score a flippé entre arm et submit, check fails"""
    armed_signal = PcaSignal(z_score=+1.8, label="CHEAP")
    current_signal = PcaSignal(z_score=-0.5, label="FAIR")  # flipped
    
    # Mock le query DB
    checks = run_pre_submit_checks(...)
    signal_check = next(c for c in checks if c.name == "signal_still_actionable")
    assert signal_check.passed is False

def test_preview_expires_after_2_min(db):
    """Preview state passe à 'expired' après 2 minutes"""
    preview = create_test_preview(db, created_at=datetime.utcnow() - timedelta(minutes=3))
    
    # Background job purge / state update
    update_expired_previews(db)
    
    refreshed = db.query(TradePreview).get(preview.id)
    assert refreshed.state == "expired"

def test_scenario_pnl_decomposition_reconciles():
    """vega_pnl + gamma_pnl + theta_pnl + other ≈ total_pnl"""
    scenario = simulate_scenarios(...)[0]
    total_decomposed = (scenario.pnl_gamma_theta_usd + scenario.pnl_vega_usd 
                        + scenario.pnl_other_usd)
    assert abs(total_decomposed - scenario.pnl_total_usd) < 1.0  # à 1 USD près
```

---

## 11. Ce qui n'est PAS dans cette étape (et où ça ira)

| Concept | Étape future |
|---|---|
| Submit effective vers IB | Étape 4 (Execution) |
| Tracking position après submit | Étape 5 (Active Positions) |
| Exit rules monitoring | Étape 5 |
| Delta hedge automatique récurrent | Étape 5 |
| Slippage modeling pour grosse size | Étape backtest (capacity analysis) |
| Multi-leg execution dans un seul order (combo order IB) | Étape 4 raffinement |
| Override manuel des sizing factors | Pas dans MVP — sizing = mécanique |

Étape 3 ne fait que **construire et valider** un trade. La soumission et le suivi viennent après.

---

## 12. Definition of done — étape 3

L'étape 3 est livrée quand :

- [ ] 5 tables Postgres créées (structure_definitions, trade_previews, pricing_cache, book_state_snapshots, risk_limits)
- [ ] `structure_definitions` seedée (6 structures)
- [ ] `risk_limits` seedée (10+ limits)
- [ ] Modules core implémentés : builder, pricer, greeks, scenarios, sizer, validator
- [ ] Endpoint `/api/v1/trade/preview` retourne payload complet conformément schema §4
- [ ] Frontend Panel 3 affiche 5 sections + bouton Submit
- [ ] Submit grayed-out automatiquement quand `state != "valid_for_submit"`, raison de blocage visible
- [ ] Preview expire bien après 2 min (state passe à `expired`)
- [ ] book_state_snapshots maintient row `is_current=true` cohérent avec positions ouvertes
- [ ] Tests : pricing réconciliation, greeks signs, sizing formula, tous les pre-submit checks, scenario decomposition

---

## 13. Décisions de design notables (pour `DECISIONS.md`)

1. **`structure_definitions` comme catalogue externe** plutôt que hardcoded en Python. Permet ajout structures sans deploy. Trade-off : plus de complexité initiale. Choix : flexibilité long-terme.

2. **Preview avec expiration 2 min**. Force user à reconfirmer si trop lent à submit. Évite trades sur surface stale.

3. **Sizing 100% mécanique, pas de discretion**. Formule explicite, pas d'override manuel. Cohérent avec l'objectif "pipeline pro" — discretion = leak de bias humain.

4. **Greeks calculés par bump-and-revalue OU analytique BS** (selon temps). MVP : analytique (rapide), V2 : bump pour structures complexes ou near-the-money exotic.

5. **Scenario grid hardcoded** (3 scenarios par défaut : favorable/neutral/adverse). Pas configurable par user. Simplicité MVP. V2 : grid dynamique selon vol_of_vol courant.

6. **Risk limits dans table dédiée** plutôt que config.py. Permet hot-reload + audit. Surcoût : query DB par preview, mais cacheable.

7. **Pre-submit checks séparés en module** plutôt que inline dans endpoint. Permet tests unitaires + réutilisation par backtest harness.

8. **Pas de modal de confirmation supplémentaire** entre Submit et execution. Le preview EST la confirmation. Submit = engagement.

9. **Book state maintenu via `is_current=true` row unique** plutôt que computation à chaque appel. Trade-off cache invalidation vs query speed. Maintenance via background job sur changements de positions.

10. **Hedge future modélisé comme leg** (même schema que call/put) pour uniformité. Distinction via `contract_type='future'`.

---

## 14. Ouvertures (limitations connues)

1. **Pricing utilise IV depuis le payload `latest_vol_surface`** qui vient de IB. Si IB IV est mal calibré (e.g. mid stale), pricing wrong. Mitigation : revérifier IV via ré-implémentation BS depuis prix observés dans backtest validation.

2. **Pas de modélisation des dividendes / cost of carry** au-delà de l'interest rate parity de base. Pour FX vanilla c'est ok, pour autre asset class à revoir.

3. **Sizing book_penalty utilise vega total** sans considérer corrélation entre structures. Deux straddles 1M et 3M ne sont pas indépendants vega-wise. Approximation acceptable pour MVP.

4. **Greeks calculés à un instant t** (au moment du preview). Aucune projection des greeks futurs. Pour gérer evolution path, voir étape 5 (monitoring).

5. **Pas de gestion liquidité dynamique** : check minimum_liquidity utilise quoted size statique. Vraie liquidité dépend de profondeur book, à raffiner si trade size devient significative.

6. **Capital total `capital_total_usd` placeholder** depuis config. À relier à un compte broker réel (étape execution) pour vraie validation max_loss.
