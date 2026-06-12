# Trade-order tab — spec haut niveau

Onglet frontend à créer dans le dev panel. Combine 3 étapes du workflow trading :
**pre-trade preview → trade structure draft → send order to IB**.

Référence spec détaillée :
- Pre-trade computation + gating regime : `vol_trading_pca/specs/STEP3_TRADE_PREVIEW.md`
- Submission IB + order tracking : `vol_trading_pca/specs/STEP4_EXECUTION.md`

---

## Objectif

Un seul onglet (URL `/dev/trade-order` ou intégré dans `/dev`) qui couvre :

1. **Pre-trade** : sélection d'un signal PCA actif → calcul du structure draft (legs, qty, premium, vega, gamma, theta, max loss, breakevens)
2. **Validation** : check risk limits (max_book_vega, max_loss_per_trade, util margin) + regime gating (config par régime market)
3. **Send order** : submit en LIVE vers IB (clientId 5 = execution-engine) via api proxy

Tout dans la même UI, pas de switch d'onglet entre les 3 étapes.

---

## Composants frontend

| Composant | Rôle | Source data |
|---|---|---|
| `<SignalPicker />` | Dropdown des signals PCA actifs (z-score > seuil) | `GET /api/v1/signals/active` |
| `<StructureDraft />` | Affiche la structure proposée (legs CALL/PUT × qty) + greeks + cost estimés | `POST /api/v1/preview/structure` |
| `<RiskCheck />` | Validation regime + risk limits | embed dans response de `/preview/structure` |
| `<SendOrderBtn />` | CTA "Submit" — confirmation modal + POST | `POST /api/v1/orders` |
| `<OrderTracker />` | Suit l'order après submission (status NotFilled → Submitted → Filled), montre fills + commissions | WS `/ws/orders:*` ou poll `/orders/active` |

---

## Endpoints backend nécessaires

Existants à réutiliser (cf. STEP3 + STEP4 specs) :
- `GET /api/v1/signals/active` — signals PCA déclenchés
- `POST /api/v1/preview/structure` — preview avec greeks + cost + risk gates
- `POST /api/v1/orders` — submit IB via execution-engine
- `GET /api/v1/orders` — list orders actifs
- WS `/ws/orders:*` — events orderStatus

À créer si manquants :
- `POST /api/v1/preview/structure/{signal_id}` qui combine signal lookup + structure computation + risk validation en un appel (au lieu de 3 round-trips frontend)

---

## Flow utilisateur

```
1. User opens tab → SignalPicker se charge avec /signals/active
2. User clicks a signal → frontend POST /preview/structure → renvoie :
   {
     "legs": [{type, strike, qty, ...}, ...],
     "greeks": {delta, gamma, vega, theta},
     "cost_usd": 1234,
     "max_loss_usd": 5000,
     "breakevens": [1.165, 1.185],
     "risk_check": {
       "gating": {regime: "low_vol", allowed: true},
       "limits": [
         {name: "max_book_vega_usd", current: 4200, limit: 5000, ok: true},
         {name: "max_loss_per_trade_pct", current: 1.2, limit: 2.0, ok: true},
       ]
     }
   }
3. StructureDraft + RiskCheck rendent les données
4. Si tous risk_check.ok = true → SendOrderBtn activé
5. Click submit → confirmation modal "Send 4 contracts to IB ?" → POST /orders
6. OrderTracker prend le relais : WS /ws/orders:* push les status changes
   jusqu'à Filled (fills + commissions visibles)
7. Une fois Filled, la position apparaît dans onglet Portfolio (panel E)
```

---

## Dépendances avec l'existant

| Existe ? | Dépendance | État |
|---|---|---|
| ✓ | Endpoint `/signals/active` | OK |
| ✓ | TradePreview component (ancien onglet) | À récupérer/refactor depuis `frontend/src/pages/dev/Step3Trade.tsx` ou `TradePreview.tsx` |
| ✓ | Endpoint `/preview/structure` | OK (cf. STEP3 spec) |
| ✓ | Endpoint `/orders` POST | OK (cf. STEP4 spec, proxy vers execution-engine) |
| ✓ | WS `/ws/orders:*` bridge | OK (api/ws/redis_bridge.py) |
| ✗ | Refonte UI unifiée preview+send | À coder |

---

## Estimation effort

| Étape | Effort |
|---|---|
| Récup composants existants `<TradePreview>` et `<OrderSubmit>` | 0.5h |
| Refonte en un seul onglet `<TradeOrderTab>` avec layout SignalPicker → Draft → RiskCheck → Send | 2-3h |
| Hook WS `/ws/orders:*` pour OrderTracker live | 1h |
| Tests + polish | 1h |
| **Total** | **0.5-1 jour** |

À faire **après** la finalisation du chantier obs LGTM (sandbox/r10-obs), parce que l'instrumentation OTel de execution-engine sera utile pour debug les ordres qui foirent en live.

---

## Points d'attention

1. **Paper vs live** : aujourd'hui on est en paper account IB. Le bouton "Send order" submit pour de vrai côté IB paper. Pas d'effet réel sur le compte cash mais l'order arrive dans le book. À garder en tête côté UI : un badge "PAPER" en haut de l'onglet pour clarifier.
2. **Risk limits** : tout no-go côté risk_check doit **désactiver** le bouton Send, pas juste afficher un warning. Critère : `risk_check.limits.every(l => l.ok) && risk_check.gating.allowed`.
3. **Idempotence** : double-click sur Send ne doit pas créer 2 orders. Disable le bouton dès le 1er click jusqu'à response.
4. **Annulation** : si l'user voit que l'order traîne en Submitted trop longtemps, il doit pouvoir cancel via un bouton sur OrderTracker → `DELETE /orders/{id}`.
