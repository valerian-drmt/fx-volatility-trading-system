# MARKETS_OPEN_TODO — STEP4 phase 2 + STEP5 live wiring

> Inventaire de ce qui n'a **pas** pu être codé / testé pendant la session du
> dimanche 2026-05-03 parce que les marchés étaient fermés et IB Gateway non
> connecté en paper. À reprendre dans cet ordre quand IB Gateway tourne et
> qu'une surface vol fraîche arrive en Redis.
>
> Spec source : `docs/vol_trading_pca/specs/STEP4_EXECUTION.md` (DoD §12)
> + `docs/vol_trading_pca/specs/STEP5_ACTIVE_POSITIONS.md` (DoD §14).
>
> Tout ce qui est listé plus bas tombe dans la catégorie « besoin de fills
> IB réels » ou « besoin d'une surface IV live ». Les helpers purs et les
> tables sont déjà en place (cf. session du 03/05).

---

## 1. STEP4 — Phase 2 (Paper trading IB)

### 1.1 IB connectivity infra

- [ ] **Heartbeat loop dans execution-engine** (cf. spec §7.4).
  Update `ib_connection_state` toutes les 10s avec `available_funds_usd`,
  `buying_power_usd`, `margin_used_usd` extraits de `accountSummaryAsync()`.
  La table + le row singleton existent déjà (migration 015). À écrire dans
  `src/engines/execution/ib_heartbeat.py` et lancer depuis `main.py:lifespan`.
- [ ] **Gating Submit sur `is_connected=true`**. Aujourd'hui `/trade/submit`
  saute le mock direct. Brancher `IbConnectionState.is_connected` comme
  pré-condition dans `revalidate_preview` (ajouter un paramètre
  `ib_connected: bool`) et bloquer 503 si False.
- [ ] **Stuck-order watcher** (spec §7.4). Loop toutes les 60s qui détecte les
  orders en `submitted/acknowledged` depuis > 10 min ; alerte critical sans
  auto-cancel (V1) — V2 configurable.

### 1.2 Real submit flow (replace mock)

- [ ] **Construire les `Contract` + `Order` ib_insync** depuis le payload preview.
  Aujourd'hui `/trade/submit` fait du round-trip mock (preview → DB). Réécrire
  `submit_preview` en deux modes (`execution_mode='mock'|'live'`) et router
  vers le bon backend.
- [ ] **Combo orders** (spec §13 décision 3). Détecter les structures
  même expiry / même contract_type → BAG order. Les calendars (2 expiries)
  partent en orders séparés. Helper `can_use_combo(legs)` à écrire.
- [ ] **`limit_price = compute_limit_price(preview_price, side, 0.5)`**
  côté submit : helper pur `core.execution.slippage.compute_limit_price`
  déjà disponible — juste l'appeler.
- [ ] **Lock Redis sur `preview_id`** (spec §7.2 décision 6, TTL 10s) pour
  empêcher double-submit. Pattern `set(key, "1", ex=10, nx=True)`.

### 1.3 Fills handlers (event-driven)

- [ ] **`_on_order_status` callback** : map `trade.orderStatus.status` →
  state DB ('acknowledged','rejected'). Update `structure_orders.state`
  + `ib_perm_id`. Publish WS message `order_acknowledged` ou `order_rejected`.
- [ ] **`_on_execution` callback** : utiliser
  `core.execution.fills.apply_fill_idempotent` pour dedup, persister dans
  `structure_fills`, puis recomputer aggregates avec
  `update_order_aggregates`. Au passage to `state='filled'`, déclencher
  `on_leg_fully_filled`.
- [ ] **`on_leg_fully_filled` → `on_structure_fully_filled`** : agrégats
  cross-legs (total_premium_paid_usd, total_slippage_usd, total_commission_usd)
  + créer le row `trade_positions`. Aujourd'hui le mock fait tout ça en une
  transaction ; en live il faut séquencer sur les events.
- [ ] **Spot/bid/ask au moment du fill** (champs `spot_at_fill`, `bid_at_fill`,
  `ask_at_fill` dans `structure_fills`) — nécessite un cache market_data
  côté execution-engine ou un appel ib.reqMktData synchrone.

### 1.4 Rollback live

- [ ] **Brancher `core.execution.rollback.decide_rollback`** sur la pipeline.
  Helper pur déjà en place. Le wrapping côté execution-engine doit faire :
  pour chaque `CancelAction` → `ib.cancelOrder(get_ib_order_obj(leg.ib_order_id))`,
  pour chaque `UnwindAction` → `ib.placeOrder(opposite_side_order)`.
- [ ] **Audit log** des cancel/unwind dans `execution_audit_log` avec
  `event_type='order_cancelled'` et `'unwind_order_created'`.

### 1.5 WebSocket pub/sub

- [ ] **Topic Redis `orders:{structure_id}`** émis par execution-engine,
  ré-emis sur WS `/ws/orders/{structure_id}` par api. Schémas dans la spec §4.2.
  L'infra `redis_to_ws_bridge` (api/ws) accepte déjà des topics dynamiques —
  juste publier les bons messages.
- [ ] **Frontend toast notifications** sur Step3Trade après Submit (en cours
  / filled / rejected). Aujourd'hui le bouton « Book » ne fait que recharger.

### 1.6 Tests E2E à exécuter en paper

- [ ] `test_submit_creates_structure_and_orders` (spec §10) sur IB Paper :
  paper account ID, port 7497, vrai order LMT ATM 3M EUR/USD qty=1.
- [ ] `test_partial_fill_updates_state` — soumettre une qty plus large que
  la liquidité ATM disponible (ex. 50 contrats sur un tenor exotique).
- [ ] `test_rejection_triggers_rollback` — soumettre intentionnellement
  une order avec `qty > buying_power` → IB rejette → rollback se déclenche.
- [ ] `test_idempotence_duplicate_fills` — disconnect/reconnect IB pendant
  un fill, vérifier que la row `structure_fills` reste unique.
- [ ] `test_lock_prevents_double_submit` — double-clic sur Submit dans le
  panel Step 3, vérifier qu'une seule structure est créée.

---

## 2. STEP5 — phase 2 (live monitoring)

### 2.1 MTM réelle

- [ ] **Re-pricing live des legs** dans le cycle monitor. Aujourd'hui
  `position_monitor` utilise une attribution linéarisée à partir des greeks
  d'entrée ; ce n'est qu'une approximation. Implémenter `compute_current_mark`
  qui re-price chaque leg avec le BS pricer + IV courant lu de la surface
  Redis, puis `mark_value_usd = Σ leg.bs_price × qty × sign(side)`.
- [ ] **Greeks courants** : ré-évaluer vega/gamma/theta/delta sur la surface
  courante au lieu de réutiliser les greeks d'entry. Important pour exit
  rule `stop_loss_vega` (loss_in_vega_units × current_vega), pour le delta
  hedge, et pour l'attribution `other_pnl_usd`.
- [ ] **Surface & spot from Redis** : `_best_effort_market_snapshot`
  retourne aujourd'hui (None, None) si Redis vide → cycle utilise les
  valeurs entry comme fallback. À retirer une fois la vol-engine produit
  une surface fraîche.

### 2.2 Delta-hedge live

- [ ] **`current_delta_unhedged` réel**. Aujourd'hui le monitor passe `0.0`
  à `check_delta_hedge_needed` (placeholder pur-logique). Le hedge ne se
  déclenche donc jamais en sandbox. À brancher sur les greeks live.
- [ ] **Submit hedge order** (futures EUR/USD CME). Aujourd'hui `HedgeOrder`
  est créé en `state='pending'` sans appel IB. Le execution-engine doit
  exposer un endpoint `POST /internal/hedge` qui consomme le row et place
  un MKT/LMT future order.
- [ ] **Coût de hedge cumul** dans `compute_mtm.hedge_cost_cumul_usd`.
  Sommer `total_cost_usd` sur tous les `hedge_orders state='filled'` du
  position_id avant de passer à la fonction.

### 2.3 Closing structure end-to-end

- [ ] **`_initiate_position_close`** (spec §9.3). Quand un `ExitAlert
  action_recommended='EXIT'` est créé en auto, construire les closing legs
  (opposite side de chaque entry leg, qty = entry.qty_filled), puis créer
  une nouvelle `TradeStructure` + `StructureOrder`s avec `order_role='closing'`
  (la colonne existe — migration 015), submit via execution-engine.
- [ ] **`on_closing_structure_fully_filled` callback**. Quand la closing
  structure passe à `fully_filled`, finaliser la position : `state='closed'`,
  `closed_at`, `exit_premium_usd`, `gross_pnl_usd`, `net_pnl_usd` (incluant
  hedge_cost_cumul + entry_total_cost + exit_total_cost).
- [ ] **Auto-execute toggle** sur les `ExitAlert`. Aujourd'hui tous les alerts
  sont créés avec `auto_executed=False` (review humain). À piloter par phase
  de déploiement (cf. spec §11) :
    - Phase 1 read-only (aujourd'hui)
    - Phase 2 auto-hedge only
    - Phase 3 auto-hedge + auto-exit

### 2.4 Régime gating dans exit rules

- [ ] **Brancher `regime` sur `evaluate_all_rules`**. Aujourd'hui le monitor
  passe `regime=None` ; la `PreEventRegimeRule` ne se déclenche donc
  jamais. À lire depuis `regime_snapshots` (latest) et passer en argument.
  Cette règle est priorité 6 (max) — critique pour event hedging.

### 2.5 WebSocket `/ws/positions` & `/ws/exit_alerts`

- [ ] **Pub/sub Redis** depuis le monitor : à chaque `_monitor_one`, publier
  `position_update` et — si trigger — `exit_alert`. Schémas dans STEP5 §6.
- [ ] **Bridge WS** : ajouter les deux topics au `redis_to_ws_bridge`.
- [ ] **Frontend Step5Positions** : remplacer le polling 5s par une
  subscription WS (le `useEffect setInterval` reste comme fallback).

### 2.6 Tests E2E à exécuter avec position ouverte en paper

- [ ] `test_mtm_correct_for_long_straddle` — ouvrir straddle ATM 3M qty=1
  en paper, choquer la vol via mock surface, vérifier MTM = +vega à 50$ près.
- [ ] `test_attribution_reconciles_to_total` — vega + gamma + theta + other ≈
  pnl_gross sur 1h de monitoring (tolérance < 5$ pour des moves <2σ).
- [ ] `test_signal_reverse_triggers_exit_in_paper` — armer un trade sur PC1
  z=2.0, attendre que la vol pipeline produise un signal z<-1 (ou seed
  manuellement via /api/v1/signals/seed), vérifier création de l'`ExitAlert`
  + (en phase 3) closing structure fully_filled.
- [ ] `test_delta_hedge_triggered_paper` — ouvrir position avec delta
  intentionnellement non-neutre (e.g. risk reversal), attendre cycle 60s,
  vérifier création `HedgeOrder` en state='filled' avec future order ID
  IB valide.
- [ ] `test_close_position_flow_end_to_end` — auto-exit via signal_reverse
  → closing structure créée → fills → position.state='closed' avec
  gross_pnl_usd et net_pnl_usd populés.
- [ ] `test_alert_cooldown_5min` — déclencher manuellement la même rule
  deux cycles consécutifs, vérifier qu'une seule `ExitAlert` est créée
  dans la fenêtre 5 min.

---

## 3. Surface dépendante — non-blocking mais souhaitable

- [ ] **Régime gating dans pre-submit checks** (`api/routers/trade.py`
  `_load_regime`). Aujourd'hui Step3 passe `regime=None` à
  `run_pre_submit_checks` → la gate `regime_not_pre_event` passe d'office.
- [ ] **Vraies risk_limits par régime**. Le seed migration 013 a des values
  hardcodées identiques pour tous régimes. Spec STEP3 §5.5 prescrit des
  multipliers calm/stressed/pre_event différents.
- [ ] **`book_state_snapshots is_current=true` rafraîchi** après chaque
  fully_filled / closed. Aujourd'hui c'est un row vide — toute la logique
  `book_alpha` dans le sizing utilise donc total_vega=0.

---

## 4. Run smoke notebook quand markets sont ouverts

- [ ] **`scripts/smoke/api/step4_phase2.ipynb`** — séquence : connect IB Paper,
  submit 1 trade ATM, observer fills, vérifier rows DB (5 tables STEP4).
- [ ] **`scripts/smoke/api/step5_monitoring.ipynb`** — observer 1h de
  monitoring sur une position ouverte, vérifier la croissance de
  `position_mtm_history`, déclencher manuellement chacune des 5 exit rules.
- [ ] **Update `releases/SESSION_LOG.md`** avec l'observation paper trading
  (slippage moyen, latence ack, % rejection sur la session).

---

## 5. Remarques de Valérian — à confirmer avant phase 2

1. **Account paper IB ID** — toujours `DU…` ? Confirmer avant le premier
   submit (sinon risque d'envoyer sur live par config oubliée).
2. **Pre-event vs HOLIDAY** : la rule `pre_event_regime` ferme tout. Doit-on
   distinguer les holidays "no trading" des events à forte volatilité ? Pour
   l'instant tout va dans le même bucket.
3. **Hedge cooldown 5 min** par défaut — peut-être trop court sur 6M (très
   peu de mouvement). À calibrer empiriquement après 1 mois de paper.
4. **Auto-execute des EXIT alerts** : phase 3 par défaut, mais un user-toggle
   global dans `/api/v1/admin/config` (`exit_auto_execute_enabled: bool`)
   serait utile pour switcher rapidement off en cas d'incident.
