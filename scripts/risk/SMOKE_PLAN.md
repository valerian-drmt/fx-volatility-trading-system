# `scripts/risk-engine/` — plan smoke tests

> Sur le modèle des autres containers. Le `risk-engine` est un **engine
> long-running cycle 2s** qui calcule les Greeks de portefeuille et la
> courbe P&L pour alimenter les composants de risk du dashboard.

---

## 1. Pourquoi ce container existe (et pourquoi tester)

Le frontend a besoin de **Greeks live** (delta/gamma/vega/theta agrégés
sur le portefeuille) et d'une **courbe P&L** (P&L en fonction du spot ±2%
autour du niveau courant) pour les panels `BookPanel` et la stream
`/ws/risk`. Sans ces données, l'utilisateur ne sait pas combien il va
perdre si EUR/USD bouge de 50 pips, ou quel est son delta net total.

Chaîne de prod attendue :

```
ib-gateway       (data IB)
    │  (note: risk-engine ne fait PAS de reqMktData direct)
market-data → Redis latest_spot:EURUSD
vol-engine   → Redis latest_vol_surface:EURUSD
positions    → Postgres table positions (alimentée par db-writer + ordres
                                          futurs ; stubbed à empty list
                                          pour l'instant — cf. §2)
    │
risk-engine cycle 2s :
    1. lit spot Redis
    2. lit vol surface Redis
    3. fetch positions (stub vide pour l'instant)
    4. agrège Greeks portefeuille
    5. calcule P&L curve sur ±2% du spot (~120 points)
    6. PUBLISH risk_update + SET latest_greeks:portfolio / latest_pnl_curve
    │
api/ws/redis_bridge.py  → forward `risk_update` sur /ws/risk
api routers/portfolio.py → REST /api/v1/risk + /api/v1/pnl-curve (lit Redis)
    │
frontend useRiskStream() hook → BookPanel, etc.
```

**Si risk-engine est cassé** → l'UI risk reste figée ou affiche des valeurs
stale. Pas de mise à jour des delta/gamma en cas de mouvement marché.

**Important** : risk-engine **dépend de market-data ET vol-engine** pour
fonctionner. Sans `latest_spot:EURUSD` ou `latest_vol_surface:EURUSD` en
Redis, le cycle skip silencieusement (log DEBUG) et **ne publie rien**.
Le smoke doit valider ça aussi.

---

## 2. Comment le container est codé (résumé)

### Fichiers clés

| Fichier | Rôle |
|---|---|
| `src/services/risk/main.py` | Entrypoint asyncio + signal handlers SIGTERM/SIGINT |
| `src/services/risk/engine.py` | `RiskEngine` — boucle `while not stop_event` toutes les `CYCLE_SECONDS=2.0` |
| `src/shared/ib_connection.py` | Wrapper backoff exponentiel (1→2→4→...→60s, retries infinis) |
| `src/bus/keys.py` | `latest_greeks:portfolio`, `latest_pnl_curve`, `heartbeat:risk_engine` (tous TTL 30s) |
| `src/bus/channels.py` | Constante `CH_RISK_UPDATE = "risk_update"` |
| `src/bus/publisher.py` | `publish_risk_update`, `set_heartbeat` |
| `src/persistence/models.py` | ORM `Position`, `PositionSnapshot` (mais risk-engine ne lit/écrit pas la DB) |

### Lifecycle

1. **Boot** : `main.py` lit env (`IB_HOST=ib-gateway`, `IB_CLIENT_ID=3`,
   `REDIS_URL=...`, `SERVICE_NAME=risk_engine`).
2. **Connect IB** : backoff exponentiel jusqu'à succès. ClientId 3 réservé
   à risk-engine (market-data=1, vol-engine=2). **Note** : la connexion IB
   est ouverte mais **pas utilisée pour des appels reqMktData** dans la
   version actuelle. C'est un héritage du design — IB sera utilisée plus
   tard pour récupérer les positions live, mais pour l'instant
   `_positions_stub()` retourne `[]`.
3. **Boucle 2s** :
   - `spot = redis.get("latest_spot:EURUSD")` — si absent → skip cycle
   - `surface = redis.get("latest_vol_surface:EURUSD")` — si absent → skip cycle
   - `positions = _positions_stub()` (= `[]` actuellement)
   - Agrégation Greeks (deltas par strike pondérés par qty positions, etc.)
   - Calcul P&L curve : ~120 points de spot dans ±2% du courant
   - `publish_risk_update(greeks)` → SET cache + PUBLISH channel
   - `set_pnl_curve(curve)` → SET cache (pas de PUBLISH pour la courbe)
   - `set_heartbeat("risk_engine")` → SET heartbeat
4. **Arrêt propre** : SIGTERM → `stop_event.set()` → `ib.disconnect()` → exit 0.

### Robustesse

- **Spot/surface manquants** : cycle skip avec log DEBUG, loop continue. Pas
  de FAIL ni de crash. `latest_greeks:portfolio` ne se rafraîchit pas mais
  TTL 30s expire normalement → côté frontend, données disparaissent.
- **IB déconnecté** : la connexion IB est ouverte mais pas utilisée par les
  cycles actuels, donc une déconnexion n'impacte pas la production de
  greeks/PnL. Le backoff retry tournera quand même en arrière-plan.
- **Redis down** : try/except autour de chaque publish, log error, loop
  continue. Cycles droppés silencieusement.
- **Healthcheck Docker** : `heartbeat:risk_engine` âge < 30s.

### Configuration compose

```yaml
risk-engine:
  profile: ["engines"]
  depends_on:
    redis:        { condition: service_healthy }
    vol-engine:   { condition: service_started }      # critical : surface
    ib-gateway:   { condition: service_started, required: false }
  environment:
    SERVICE_NAME: risk_engine
    IB_HOST: ib-gateway
    IB_PORT: 4002
    IB_CLIENT_ID: 3
    REDIS_URL: redis://redis:6379/0
  healthcheck:
    interval: 15s, timeout: 5s, retries: 3, start_period: 30s
```

---

## 3. Quelles informations le container produit

### Sur Redis (cache, TTL 30s)

| Clé | Valeur (JSON) | Cadence | Consumer |
|---|---|---|---|
| `latest_greeks:portfolio` | `{"timestamp": "ISO", "greeks": {"delta": .., "gamma": .., "vega": .., "theta": .., "spot": ..}}` | toutes les 2s | api `/api/v1/risk` (REST), useRiskStream (via WS) |
| `latest_pnl_curve` | `{"timestamp": "ISO", "curve": {"spots": [..120 points..], "pnls": [..], "spot": F}}` | toutes les 2s | api `/api/v1/pnl-curve` (REST) |
| `heartbeat:risk_engine` | ISO-8601 string | toutes les 2s | healthcheck Docker, api `/api/v1/health/extended` |

### Sur Redis (pub/sub)

| Channel | Payload | Cadence | Consumer |
|---|---|---|---|
| `risk_update` | greeks JSON (mêmes champs que `latest_greeks:portfolio`) | toutes les 2s | `api/ws/redis_bridge.py` → `/ws/risk` |

### Postgres : **rien**

risk-engine ne lit ni n'écrit en DB. Le `db-writer` consume le channel
`risk_update` côté Redis pour persister dans `position_snapshots`. Cet
aspect sera testé dans le smoke db-writer (pas encore fait).

### IB API : **rien à la connexion actuelle**

La connexion IB est ouverte mais aucun `reqMktData` n'est fait (héritage
de design, sera utilisé plus tard pour positions live).

---

## 4. Ce qui doit marcher pour que le frontend soit clean

Cibles UI consumant les sorties de risk-engine :

- **`useRiskStream()` hook** (`frontend/src/hooks/useRiskStream.ts`) — WS
  `/ws/risk`, expects `{delta, gamma, vega, theta, ts?}`
- **`BookPanel`** et `PortfolioPanel` lisent les positions via REST mais
  pas directement les greeks risk-engine — ils affichent surtout des
  positions stockées (alimentées par db-writer / ordres). Le **flux risk
  live** vient via le hook ci-dessus.
- **REST endpoints** : `/api/v1/risk` (greeks), `/api/v1/pnl-curve` —
  fallback REST si on veut un snapshot ponctuel (ex: page reload).

Pour que **`useRiskStream` reçoive du delta/gamma/vega/theta live**, il
faut **toute la chaîne** :

1. **market-data** publie `latest_spot:EURUSD` → ✅ déjà validé (notebooks 01-05)
2. **vol-engine** publie `latest_vol_surface:EURUSD` → ⚠️ pas encore validé (smoke à venir)
3. **risk-engine** lit ces 2, calcule, publie sur Redis et channel `risk_update`
4. **api ws bridge** forward channel `risk_update` → `/ws/risk`
5. **frontend** consume

Le smoke risk-engine couvre les **étapes 3 et 4** (3 = cache+pub Redis,
4 = WS bridge end-to-end). L'étape 1 dépend de market-data déjà validé.
**L'étape 2 (vol-engine) n'étant pas encore couverte**, on devra **seeder
manuellement** un `latest_vol_surface:EURUSD` factice dans Redis dans le
notebook 02 du smoke pour que le cycle ne skip pas.

---

## 5. Smoke tests à créer

| # | Fichier | Couvre | Format |
|---|---|---|---|
| 01 | `01_test_container.ipynb` | Container UP, healthcheck heartbeat passe healthy, env vars correctement injectées (`SERVICE_NAME`, `IB_HOST`, `IB_PORT`, `IB_CLIENT_ID=3`, `REDIS_URL`, `LOG_LEVEL`) | `.ipynb` (Docker only) |
| 02 | `02_test_redis_outputs.ipynb` | **Seed `latest_vol_surface:EURUSD` factice** (sinon skip cycle), wait 5s, vérifie `heartbeat:risk_engine` frais, `latest_greeks:portfolio` JSON valide avec champs `{delta, gamma, vega, theta, spot}`, `latest_pnl_curve` JSON valide avec `{spots, pnls, spot}`, valeurs cohérentes (delta/gamma/vega/theta tous numeric finite, P&L curve même longueur sur les deux axes) | `.ipynb` (Redis only) |
| 03 | `03_test_pubsub_chain.ipynb` | Subscribe `risk_update`, reçoit ≥ 2 messages en 5s (cycle 2s = 2-3 messages max théorique), payload schema `{delta, gamma, vega, theta, spot, ts ou timestamp}` | `.ipynb` (Redis pub/sub) |
| 04 | `04_test_ws_bridge.ipynb` | Connect WS `ws://localhost/ws/risk`, reçoit ≥ 2 messages en 5s, payload identique au pub/sub Redis (preuve que le bridge ne transforme pas) | `.ipynb` (websocket-client) |
| 05 | `05_test_resilience.ipynb` | `docker restart fxvol-risk-engine` → nouveau heartbeat ≠ baseline en < 30s, greeks reprennent leur publication | `.ipynb` |

**Pourquoi `.ipynb`** : risk-engine ne fait pas d'appel `ib_insync` direct
côté test client (pas d'instance `IB()` dans nos notebooks de test). Donc
zéro incompat asyncio Jupyter, retour au pattern standard.

**Particularité §2 — seed vol surface** : sans `latest_vol_surface:EURUSD`,
le cycle risk-engine skip et `latest_greeks:portfolio` n'est jamais publié.
Le notebook 02 doit donc **injecter une surface factice avant de tester** :

```python
import json
fake_surface = {
    "timestamp": "2026-04-28T15:00:00Z",
    "spot": 1.17,
    "atm_iv": 0.065,
    "rr_25d": 0.001,
    "bf_25d": 0.0005,
    # …structure simplifiée, à aligner avec ce que vol-engine produit
}
r.set("latest_vol_surface:EURUSD", json.dumps(fake_surface), ex=60)
```

À ajuster selon le format réel produit par vol-engine — à vérifier dans
`src/services/vol/engine.py` au moment d'écrire le 02.

---

## 6. Préreq avant de lancer les smokes

```powershell
.\scripts\start_stack.ps1
```

Ou minimum :
```powershell
.\scripts\load_secrets.ps1
docker compose --profile ib --profile engines up -d ib-gateway redis api nginx market-data risk-engine
```

(market-data nécessaire pour `latest_spot:EURUSD` ; vol-engine **pas
nécessaire** parce que le notebook 02 seed manuellement la surface.)

Vérifier avant smoke :
- `redis healthy`
- `ib-gateway healthy`
- `market-data healthy` (sinon pas de spot, cycle risk-engine skip)
- `risk-engine healthy` (ou en cours de boot)

---

## 7. Ordre de création (sandbox r9)

Sur `sandbox/r9-pipeline-verif`, créer 01 → 05 en séquence, valider chacun
en `Restart & Run All`, commiter atomiquement par notebook. **Ne pas
créer de branche feature mid-sandbox** (cf. memory `feedback_sandbox_no_branching`).

---

## 8. Critère d'acceptation global

- `Restart & Run All` des 5 notebooks passe bout-à-bout sur une stack
  fraîche en < 5 min cumulé.
- Notebook 02 prouve que risk-engine produit bien des Greeks dès qu'il a
  spot+surface en Redis.
- Notebook 04 prouve que la chaîne `risk-engine → Redis → api → WS
  /ws/risk` fonctionne — préreq pour `useRiskStream` côté frontend.
- Aucune régression vs notebooks market-data déjà commités.
