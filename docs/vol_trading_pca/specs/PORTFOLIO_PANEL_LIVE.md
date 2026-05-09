# Portfolio Panel — refactor "live" (DB + Redis pub/sub + WS)

> **Objectif** : passer le Portfolio Panel d'un polling REST 5 s vers un
> pattern **hybride DB + Redis pub/sub + WebSocket** pour atteindre une
> latence de mise à jour ≈ 1 s, conforme à l'architecture canonique
> (`container_risk.md`, `container_execution_engine.md`).
>
> **Suppose** : `PORTFOLIO_PANEL.md` (P1 + P2 + P3) déjà livré et
> fonctionnel sur le path REST/DB.
>
> **Status** : draft — à valider avant code.
> **Date** : 2026-05-09
> **Phase** : P4 (suivi de P1/P2/P3)

---

## 1. Motivation

Le Portfolio Panel actuel poll REST `/api/v1/portfolio/*` toutes les 5 s.
Inconvénients :

- **Latence** : jusqu'à 5 s de retard sur la vérité (compute → DB → poll).
- **Charge DB** : 10 endpoints × 5 s × N onglets = SELECT redondants quand
  rien n'a changé.
- **Compute dupliqué côté reader** : agrégats SQL recalculés à chaque poll
  alors que le writer (risk-engine) a déjà fait le travail.

Cible : **DB reste system of record** (audit + history), **Redis pub/sub
devient l'event bus live**, frontend WS subscribe.

---

## 2. Architecture cible

```
                  cycle 1s
   ┌───────────┐                       ┌──────────────┐
   │ risk-eng  │ ─── BS compute ─────► │ Postgres     │   ← system of record
   │           │                       │ (audit + hist)│
   │           │ ─── PUBLISH ────────► │              │
   │           │     risk:*            │ Redis pub/sub│   ← live event bus
   └───────────┘                       └──────┬───────┘
                                               │
                  cycle 1s + events            │
   ┌───────────┐                               │
   │exec-engine│ ─── IB sync → DB ────────────►│
   │           │ ─── PUBLISH portfolio:* ─────►│
   └───────────┘                               │
                                       ┌───────┴────────┐
                                       │                │
                              REST (DB) on mount   WebSocket subscribe
                                       │                │
                                       └────┬───────────┘
                                            │
                                     ┌──────▼─────┐
                                     │  frontend  │
                                     └────────────┘
```

**Principes** :

1. **Writers double-write** : DB (history) + Redis PUBLISH (live event)
   dans le même tick. Si Redis tombe, DB seule est cohérente. Si DB tombe,
   c'est un fail global qui doit alerter.
2. **DB write d'abord, Redis ensuite** : si un client manque le topic, il
   verra le nouveau row au prochain poll fallback (REST 30 s).
3. **Frontend = bootstrap REST + subscribe WS + fallback poll lent** :
   3 chemins, redondants par design.

---

## 3. Mapping panels — 2 chemins parallèles

Pour chaque panel : qui écrit DB, qui pousse Redis, ce que consomme le front.

| Panel | Writer DB (history) | Pusher Redis (live) | Frontend |
|---|---|---|---|
| **A** Account header | `execution-engine` cycle 1 s → INSERT `account_snaps` | `execution-engine` PUBLISH `portfolio:account` | REST `/portfolio/account` au mount + WS `/ws/portfolio:account` |
| **B** Equity curve | (même writer que A) | `execution-engine` PUBLISH `portfolio:equity_point` (`{ts, net_liq}`) à chaque insert | REST `/portfolio/equity-curve?window=...` au mount (bulk hist) + WS append le nouveau point |
| **C** Aggregate greeks | `risk-engine` cycle 1 s → INSERT `position_snapshots` (greeks columns) | `risk-engine` PUBLISH `risk:greeks` (existe déjà) | REST `/portfolio/aggregate-greeks` au mount + WS sub |
| **D** Vega per tenor | (même writer que C) | `risk-engine` PUBLISH `risk:vega_per_tenor` (bucketing pré-calculé) | REST `/portfolio/vega-per-tenor` au mount + WS sub |
| **E** Open positions | `execution-engine` 1 s → UPSERT `positions` (qty, marketPrice, unrealizedPNL). `risk-engine` 1 s → INSERT `position_snapshots` (greeks) | `execution-engine` PUBLISH `positions:update`. `risk-engine` PUBLISH `risk:position_greeks` | REST `/positions/active` au mount + WS sub aux 2 topics, merge client-side |
| **F** Open orders | `execution-engine` event-driven IB `orderStatus` → UPDATE `orders` | `execution-engine` PUBLISH `orders:update` à chaque change | REST `/orders` au mount + WS sub event-driven (pas cycle) |
| **G** Trades / fills | `execution-engine` event-driven IB `execDetails` → INSERT `trades` | `execution-engine` PUBLISH `trades:new` à chaque fill | REST `/dev/tables/trades?limit=50` au mount + WS append |
| **H** Hedge orders | `risk-engine` décide → publie `action:hedge`. `execution-engine` exécute → INSERT `hedge_orders` post-fill. | `execution-engine` PUBLISH `hedges:update` à chaque INSERT/UPDATE | REST `/dev/tables/hedge_orders` + `/portfolio/hedge-summary` au mount + WS sub |
| **I** Snapshots history | `risk-engine` (même rows que C/D) | (rien — historique, pas live) | REST `/dev/tables/position_snapshots?limit=100&offset=N` paginé. Pas de WS. |

---

## 4. Topics Redis — convention de nommage

```
portfolio:account              {timestamp, net_liq_usd, cash_usd, margin_*, cushion, ...}
portfolio:equity_point         {timestamp, net_liq_usd}
positions:update               {action: "upsert"|"close", row: {...}}
orders:update                  {action: "new"|"status"|"cancel", row: {...}}
trades:new                     {row: {...}}
hedges:update                  {action: "new"|"filled"|"failed", row: {...}}
risk:greeks                    {symbol, total_delta, total_gamma, total_vega, total_theta, n_positions, computed_at}   ← existe déjà
risk:vega_per_tenor            {symbol, buckets: [{bucket, dte_lo, dte_hi, vega_usd, n_positions}], computed_at}
risk:position_greeks           {position_id, delta_usd, gamma_usd, vega_usd, theta_usd, pnl_usd, computed_at}
```

**Format payload** : JSON, encoding via `bus.publisher` existant (réutiliser
le même pattern que `vol_update` / `risk_update`).

**TTL Redis** : pas applicable au pub/sub (fire-and-forget). Pour le `SET`
de "last value" (utile au bootstrap rapide sans toucher DB), TTL 5 min.

---

## 5. WebSocket bridge côté API

Pattern : un endpoint WS par topic, qui abonne au Redis pub/sub et forward
chaque message au client browser.

```
/ws/portfolio:account
/ws/portfolio:equity_point
/ws/positions:update
/ws/orders:update
/ws/trades:new
/ws/hedges:update
/ws/risk:greeks
/ws/risk:vega_per_tenor
/ws/risk:position_greeks
```

**Implémentation** : factoriser un helper générique `bridge_redis_to_ws(topic)`
dans `src/api/ws/redis_bridge.py` (existe déjà partiellement pour
`/ws/vol` et `/ws/risk` — étendre).

**Auth** : pas dans MVP. À ajouter en phase prod (token cookie).

**Backpressure** : si client browser slow, drop messages plutôt que buffer
infiniment. Standard WS practice.

---

## 6. Cadence 1 s — détail par container

| Container | Cycle actuel | Cycle cible | Notes |
|---|---|---|---|
| `execution-engine.position_sync_loop` | 30 s | **1 s** | IB rate-limit account_summary ≈ 3 s server-side ; `if changed_since_last` pour PUBLISH only on change. |
| `risk-engine.run_cycle` | 2 s (déjà fast) | **1 s** | BS compute pour ≤ 50 positions tient large ; > 200 positions → vectoriser via `core.risk.greeks.bs_price_vec`. |
| `vol-engine` | 180 s | (inchangé) | Surface vol change peu, polling sub-minute inutile. |
| `market-data` | event-driven (ticks) | (inchangé) | Déjà event-driven sur ticks IB. |

**Garde-fou** : ajouter une env var `LIVE_LOOP_INTERVAL_S=1.0` pour pouvoir
descendre / remonter en cas de surcharge sans rebuild.

---

## 7. Frontend pattern universel

Pour chaque panel sauf **I** :

```tsx
function usePanel<T>(restUrl: string, wsTopic: string, fallbackS = 30): T | null {
  const [data, setData] = useState<T | null>(null);

  // 1. Bootstrap au mount — état initial complet
  useEffect(() => {
    void fetch(restUrl).then((r) => r.json()).then(setData);
  }, [restUrl]);

  // 2. WS subscribe — deltas live
  useEffect(() => {
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${window.location.host}/ws/${wsTopic}`);
    ws.onmessage = (ev) => {
      try { setData(JSON.parse(ev.data)); } catch { /* nop */ }
    };
    return () => ws.close();
  }, [wsTopic]);

  // 3. Polling REST 30 s — fallback si WS down (ne devrait jamais fire en run normal)
  useEffect(() => {
    const id = window.setInterval(() => {
      void fetch(restUrl).then((r) => r.json()).then(setData);
    }, fallbackS * 1000);
    return () => window.clearInterval(id);
  }, [restUrl, fallbackS]);

  return data;
}
```

→ **0 poll 5 s gaspillé**, latence ≈ writer (1 s), fallback safe.

Pour les panels qui agrègent plusieurs streams (E qui mixe positions update +
risk:position_greeks), faire 2 hooks séparés et merger côté composant via
`useMemo`.

---

## 8. Plan d'implémentation — 5 phases

| Phase | Scope | Effort | Bloquant |
|---|---|---|---|
| **L1** Risk-engine accès DB | Ajouter sessionmaker async, écrire `position_snapshots` greeks columns | 0.5 j | Oui (pré-requis L3) |
| **L2** Cadence 1 s + env var | Passer `position_sync_loop` à 1 s, `risk-engine` à 1 s, ajouter `LIVE_LOOP_INTERVAL_S` | 0.25 j | Oui |
| **L3** Pubs Redis manquants | `portfolio:account/equity_point` (exec), `risk:vega_per_tenor/position_greeks` (risk), `positions/orders/trades/hedges:*` (exec) | 0.5 j | Oui |
| **L4** WS bridges API | 9 endpoints WS qui forward Redis topic → browser | 0.5 j | Oui |
| **L5** Frontend hook + refactor Portfolio.tsx | `usePanel<T>(rest, ws)` factory + remplacer le `setInterval(refreshAll, 5000)` actuel par 9 hooks | 0.75 j | Oui |
| **Total** | — | **~2.5 j** | — |

---

## 9. Definition of done

- [ ] `risk-engine` écrit `position_snapshots.{spot, iv, delta_usd, gamma_usd, vega_usd, theta_usd, pnl_usd}` à chaque cycle 1 s
- [ ] `execution-engine.position_sync.insert_snapshots` n'écrit **plus** les colonnes greeks (juste qty/marketPrice/unrealizedPNL → pnl_usd)
- [ ] `LIVE_LOOP_INTERVAL_S=1.0` honored par les 2 engines
- [ ] 9 topics Redis publiés selon §4 avec payload JSON valide
- [ ] 9 endpoints WS dans `/ws/*` actifs, intégration test ⇒ message reçu < 200 ms après écriture DB
- [ ] Portfolio panel frontend rebuild :
  - Mount → 9 fetches REST initial
  - Live → 9 abonnements WS
  - Fallback → setInterval 30 s (au cas où WS tombe)
  - Plus aucun poll 5 s
- [ ] Tests :
  - Unit : payload pub/sub serialisation cohérente DB ↔ Redis
  - Integration : kill Redis → frontend tombe en fallback REST sans crash
  - Integration : kill writer → frontend continue avec last DB value, badge stale
- [ ] Mesure latence end-to-end (writer commit → browser DOM update) < 1.5 s en healthy state

---

## 10. Décisions notables

1. **DB write avant Redis publish** : si Redis crash, DB reste source de vérité ; au prochain poll fallback, le client rattrape. L'ordre inverse créerait des deltas perdus.
2. **Topic granulaire par concept**, pas un seul `portfolio:update` global. Permet aux clients d'abonner finement et au routing Redis d'être efficient (pub/sub Redis a O(N) sur le nombre d'abonnés au topic, pas sur le nombre total de topics).
3. **JSON payload, pas msgpack/protobuf** : surcoût négligeable pour ces volumes, debug trivial via `redis-cli MONITOR`.
4. **Pas de queue persistante (pas de Redis Streams)** : on accepte les pertes d'event si client offline > quelques secondes. Le bootstrap REST + fallback poll 30 s couvrent ce cas.
5. **Greeks compute single-source** = `risk-engine` UNIQUEMENT (alignement avec `container_risk.md`). `execution-engine` arrête le BS compute. Cf. dette technique précédente.

---

## 11. Hors scope (V2+)

- **Redis Streams** pour replay event-driven (utile backtest UI replay).
- **Auth WS** (token cookie, scope `portfolio:read`).
- **Compression payload** WS (perDeflate, pour > 100 positions).
- **Multi-symbol** : tous les topics actuels assument EURUSD. Si extension à GBPUSD etc, namespacer `portfolio:account:EURUSD`, etc.
- **Server-Sent Events alternatif** : SSE plus simple que WS pour 1-way push, pourrait remplacer WS si la complexité devient un problème.
- **Historical replay sur l'equity curve** : actuellement REST refresh au window change ; pourrait s'enrichir d'un stream rolling.

---

## 12. Lien avec les autres specs

- `PORTFOLIO_PANEL.md` : spec REST/DB de référence (P1+P2+P3). Cette spec ajoute la couche live, ne la remplace pas.
- `container_risk.md` : alignement single-ownership greeks.
- `container_execution_engine.md` : élargit la responsabilité PUBLISH au-delà de `vol_update`.
- `STEP5_ACTIVE_POSITIONS.md` : Step 5 utilisera les mêmes WS topics (`positions:update`, `risk:position_greeks`).
