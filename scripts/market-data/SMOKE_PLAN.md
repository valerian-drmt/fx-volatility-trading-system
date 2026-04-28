# `scripts/market-data/` — plan smoke tests

> Sur le modèle de `scripts/ib-gateway/SMOKE_PLAN.md`. Le container
> `market-data` est un **engine long-running** qui ponte IB Gateway
> ↔ Redis pour alimenter le dashboard React en ticks live.

---

## 1. Pourquoi ce container existe (et pourquoi tester)

Le frontend a besoin de **prix live qui défilent** dans le `ChartPanel`
(la courbe de spot EUR/USD du dashboard). Le frontend ne parle **jamais
directement à IB Gateway** — c'est `market-data` qui fait le travail
pour lui :

```
IB Gateway (TWS API)
    │  reqMktData(EURUSD Forex spot)
    ▼
market-data engine (async loop, 100ms poll)
    │  publish JSON {symbol, bid, ask, mid, ts}
    ▼
Redis channel `ticks`  +  cache `latest_spot:EURUSD` (TTL 30s)
    │  subscribe
    ▼
api/ws/redis_bridge.py (FastAPI WebSocket bridge)
    │  forward JSON via /ws/ticks
    ▼
frontend useTicks() hook → ChartPanel → TickChart (Plotly line)
```

**Si market-data est cassé** → le `ChartPanel` du dashboard reste figé
ou vide. Le test 03 d'ib-gateway montre qu'IB répond ; ce smoke
market-data doit prouver que **la chaîne de transmission jusqu'au front
fonctionne**.

---

## 2. Comment le container est codé (résumé)

### Fichiers clés

| Fichier | Rôle |
|---|---|
| `src/services/market_data/main.py` | Entrypoint asyncio, signal handlers SIGTERM/SIGINT, `reqMarketDataType(3)` (delayed), `reqMktData(EURUSD Forex)` |
| `src/services/market_data/engine.py` | `MarketDataEngine` — boucle `while not stop_event` toutes les 100ms (`POLL_INTERVAL_S=0.1`) |
| `src/services/market_data/Dockerfile` | Image build `fx-options-market-data:local` |
| `src/shared/ib_connection.py` | Wrapper de connexion IB avec backoff exponentiel (1→2→4→...→60s, retries infinis en prod) |
| `src/bus/keys.py` | Templates de clés Redis (`latest_spot:{symbol}`, `heartbeat:market_data`, etc.) |
| `src/bus/channels.py` | Constantes des channels pub/sub (`ticks`, `vol_update`, etc.) |
| `src/bus/publisher.py` | Helpers `publish_tick`, `set_heartbeat` (avec throttle 200ms par symbole côté PUBLISH, mais SET cache toujours frais) |

### Lifecycle d'une instance

1. **Boot** : `main.py` lit env vars (`MARKET_SYMBOL=EURUSD`,
   `IB_HOST=ib-gateway`, `IB_CLIENT_ID=1`, `REDIS_URL=...`).
2. **Connect IB** : backoff exponentiel jusqu'à succès. ClientId 1
   réservé à market-data (vol-engine = 2, risk-engine = 3).
3. **Subscribe** : `reqMarketDataType(3)` (delayed) → `reqMktData()` sur
   un contrat `Forex("EURUSD")` qualifié. ib_insync expose un `Ticker`
   qui se met à jour via callbacks IB.
4. **Boucle 100ms** :
   - Lit `ticker.bid` / `.ask` / `.midpoint()` / `.last`
   - Construit le payload `{symbol, bid, ask, mid, timestamp}`
   - **Cache toujours** : `SET latest_spot:EURUSD <mid>` + `latest_bid` + `latest_ask` (TTL 30s)
   - **PUBLISH throttlé 200ms** : `PUBLISH ticks <json>` (1 fois sur 2 maxi pour ne pas saturer le WS)
   - Tous les 10 polls (~1s) : `set_heartbeat("market_data")` → `SET heartbeat:market_data <iso8601> EX 30`
5. **Arrêt propre** : SIGTERM → `stop_event.set()` → `ib.disconnect()` → exit 0.

### Robustesse

- **IB déconnecté** : backoff retries forever (production = pas de
  max_attempts). Le ticker en mémoire reste, mais `bid`/`ask` ne se
  mettent plus à jour → cache Redis expire après 30s → frontend voit
  un trou.
- **Redis down** : try/except autour de chaque PUBLISH/SET, log d'erreur,
  loop continue. Ticks droppés silencieusement.
- **Healthcheck Docker** : `python -c "import redis... heartbeat:market_data ..."` toutes les 30s → fail si la clé est absente ou vieille de > 60s.

### Configuration compose

```yaml
market-data:
  profile: ["engines"]            # opt-in (sinon default ne le lance pas)
  depends_on:
    redis:        { condition: service_healthy }
    ib-gateway:   { condition: service_started, required: false }
  environment:
    SERVICE_NAME: market_data
    IB_HOST: ib-gateway           # DNS interne au réseau Docker
    IB_PORT: 4002
    IB_CLIENT_ID: 1
    MARKET_SYMBOL: EURUSD
    REDIS_URL: redis://redis:6379/0
  healthcheck:
    interval: 30s, timeout: 5s, retries: 3, start_period: 30s
```

---

## 3. Quelles informations le container produit

### Sur Redis (cache)

| Clé | Type | TTL | Source | Consumer |
|---|---|---|---|---|
| `latest_spot:EURUSD` | string (mid as float) | 30s | écrit toutes les 100ms | `api` `/api/v1/spot/{symbol}` (REST), engines vol/risk |
| `latest_bid:EURUSD` | string (bid as float) | 30s | écrit toutes les 100ms | api endpoints |
| `latest_ask:EURUSD` | string (ask as float) | 30s | écrit toutes les 100ms | api endpoints |
| `heartbeat:market_data` | string (ISO-8601 UTC) | 30s | écrit toutes les ~1s | healthcheck Docker, `api` `/api/v1/health/extended` |

### Sur Redis (pub/sub)

| Channel | Payload | Cadence | Consumer |
|---|---|---|---|
| `ticks` | `{"symbol": "EURUSD", "bid": 1.0852, "ask": 1.0853, "mid": 1.08525, "ts": "2026-04-28T13:42:01.123+00:00"}` | throttle 200ms par symbol | `api/ws/redis_bridge.py` qui forward sur WebSocket `/ws/ticks` |

### Postgres : **rien**

market-data est purement Redis. La persistence des ticks (si elle a
lieu un jour) sera la responsabilité du `db-writer` qui consume le
même channel `ticks`. Confirmation : pas d'imports `sqlalchemy` dans
`engine.py`.

---

## 4. Ce qui doit marcher pour que le frontend soit clean

Cible UI = `ChartPanel` (`frontend/src/components/panels/ChartPanel.tsx`)
qui affiche la courbe de mid-price EUR/USD avec 300 points d'historique.

Pour que cette courbe défile en live, il faut **que TOUS ces maillons
fonctionnent en chaîne** (un seul cassé → courbe figée) :

1. **IB Gateway connecté** ↔ **market-data** : confirmé par heartbeat
   `market_data` frais en Redis.
2. **market-data** publie sur Redis : confirmé par `latest_spot:EURUSD`
   qui se rafraîchit toutes les 100-200ms.
3. **Redis pub/sub** fonctionnel : confirmé par un subscriber test qui
   reçoit des messages sur `ticks` (cf. `scripts/redis/01_test_pubsub.ipynb`).
4. **api WebSocket bridge** : `/ws/ticks` accepte une connexion et forward
   ce que Redis envoie. À tester via `websocket-client` ou un curl WS.
5. **Frontend useTicks hook** parse correctement le JSON. Test purement
   browser, validé visuellement.

Le smoke market-data couvre les **niveaux 1, 2, 3, 4**. Le niveau 5
sera couvert par le smoke `frontend` (le dernier de la série).

---

## 5. Smoke tests à créer

| # | Fichier | Couvre | Format |
|---|---|---|---|
| 01 | `01_test_container.ipynb` | Container UP, healthcheck Redis-based passe healthy, env vars correctement injectées | `.ipynb` (pas de IB direct, pas d'incompat ib_insync) |
| 02 | `02_test_redis_outputs.ipynb` | Heartbeat frais (< 30s), 3 clés cache (`latest_spot/bid/ask:EURUSD`) présentes et **fraîchissent** entre 2 lectures à 1s d'intervalle, valeurs numériques cohérentes (spot dans [0.5, 2.0]) | `.ipynb` (Redis only) |
| 03 | `03_test_pubsub_chain.ipynb` | Subscribe au channel `ticks`, vérifie qu'on reçoit ≥ 5 messages JSON valides en 5s, payload contient toutes les clés attendues, throttle ~200ms respecté | `.ipynb` (Redis pub/sub) |
| 04 | `04_test_ws_bridge.ipynb` | Connect WebSocket sur `ws://localhost/ws/ticks`, reçoit ≥ 5 messages, parse JSON identique au format market-data → on prouve que la chaîne **engine → Redis → api WS bridge** fonctionne bout-à-bout | `.ipynb` (websocket-client) |
| 05 | `05_test_resilience.ipynb` | Restart container market-data → heartbeat repart en < 30s, ticks reprennent. Optionnel : restart ib-gateway → market-data backoff puis reprend une fois IB de retour | `.ipynb` |

**Pourquoi `.ipynb` partout (vs `.py` chez ib-gateway)** : market-data ne
fait JAMAIS d'appel direct `ib_insync` côté test (le client de test parle
à Redis et à l'API WebSocket). Donc pas d'incompat asyncio Jupyter à
gérer. On retombe sur le pattern standard des autres containers.

---

## 6. Préreq avant de lancer les smokes

```powershell
# Stack complète (postgres + redis + api + ib-gateway + market-data)
.\scripts\start_stack.ps1

# Ou minimum requis pour ces smokes :
.\scripts\load_secrets.ps1
docker compose --profile ib --profile engines up -d ib-gateway redis api market-data
```

Attendre :
- `docker ps` → `redis` healthy
- `docker ps` → `ib-gateway` healthy (~90s start_period)
- `docker ps` → `market-data` healthy (~30s start_period — il attend juste qu'IB Gateway pousse les premiers ticks)

Vérification rapide avant de smoke :

```powershell
docker compose exec redis redis-cli GET heartbeat:market_data
# → "2026-04-28T13:42:01.123+00:00" (timestamp récent, < 30s)
```

---

## 7. Ordre de création (sandbox r9)

1. Sur `sandbox/r9-pipeline-verif` (branche actuelle), créer les 5
   notebooks dans l'ordre 01 → 05, valider chacun en `Restart & Run All`,
   commiter atomiquement par notebook.
2. Documenter dans chaque notebook la troubleshooting cheat sheet
   (heartbeat manquant, channel vide, WS qui ne reçoit rien, etc.).
3. Le décompactage en PRs propres se fera plus tard via
   `releases/git_management/PLAYBOOK.md`. **Ne pas créer de branche
   feature mid-sandbox** (cf. memory `feedback_sandbox_no_branching`).

---

## 8. Critère d'acceptation global

- `Restart & Run All` des 5 notebooks passe bout-à-bout sur une stack
  fraîchement démarrée en < 5 min cumulé.
- Chaque notebook autonome, troubleshooting cheat sheet en fin.
- Aucun secret n'apparaît dans une sortie de cellule (Redis n'a pas
  de secrets exposés, mais on ne fait pas non plus de `Get-ChildItem
  Env:` sur le container).
- Confirmation que **la chaîne complète vers le frontend est saine** —
  c'est le pré-requis pour que le smoke `frontend` puisse passer en
  conditions réelles.
