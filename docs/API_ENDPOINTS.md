# API endpoints — fx-volatility-trading-system

> **Backend** : FastAPI sous Uvicorn (`src/api/main.py`).
> **Préfixe global** : `/api/v1` (sauf WebSockets).
> **OpenAPI auto-doc** : `http://localhost:8000/docs` (Swagger UI) et `/redoc` (ReDoc).
> **Source de vérité** : `src/api/routers/` + `src/api/models/`.

---

## Sommaire

| Tag | Endpoints | Préfixe | Source |
|---|---|---|---|
| [health](#1-health) | 2 | `/api/v1` | `health.py` |
| [pricing](#2-pricing) | 3 | `/api/v1` | `pricing.py` |
| [vol](#3-vol) | 4 | `/api/v1/vol` | `vol.py` |
| [portfolio](#4-portfolio) | 5 | `/api/v1` | `portfolio.py` |
| [analytics](#5-analytics) | 4 | `/api/v1` | `analytics.py` |
| [cockpit](#6-cockpit) | 4 | `/api/v1/vol` | `cockpit.py` |
| [admin](#7-admin) | 5 | `/api/v1/admin` | `admin.py` |
| [websocket](#8-websocket) | 3 | `/ws/*` | `ws.py` |
| **Total** | **30** | | |

---

## 1. Health

Liveness + readiness probes consommées par : healthcheck Docker, AWS ALB target health, Uptime Robot externe, dashboard `ConnectionIndicator`.

| Méthode | Path | Réponse | Description | Utilité |
|---|---|---|---|---|
| `GET` | `/api/v1/health` | `{"status": "OK"}` | **Liveness probe** — répond 200 tant que le process FastAPI tourne. Aucune dépendance externe vérifiée. | Docker `HEALTHCHECK`, restart policy, monitoring externe basique. |
| `GET` | `/api/v1/health/extended` | `{"status": "OK\|DEGRADED", "redis": ..., "postgres": ..., "engines": {...}}` | **Readiness probe** — vérifie Redis (PING), Postgres (SELECT 1), heartbeats des 3 engines via Redis (`heartbeat:market_data`/`vol`/`risk`). Status `DEGRADED` si un sous-système KO. | Dashboard health badge, alerting CloudWatch (si DEGRADED >5min), gate de mise en prod (deploy.yml attend OK avant de couper l'ancien). |

---

## 2. Pricing

Outils Black-Scholes purs, **stateless**, sans accès DB ni Redis. Inputs explicites, sortie déterministe. Utilisés par le `OrderTicketPanel` pour preview live et par les notebooks de recherche.

| Méthode | Path | Body | Réponse | Description | Utilité |
|---|---|---|---|---|---|
| `POST` | `/api/v1/price` | `PriceRequest` (`spot`, `strike`, `maturity_days`, `option_type`, `volatility`) | `PriceResponse` (`price`) | **BS price** d'une option européenne FX. Pure compute via `core/pricing/bs.py`. `spot` est en fait le **forward** (F) ; `volatility` en décimal (0.075 = 7.5%) ; `option_type` ∈ {`CALL`, `PUT`}. | Order Ticket : afficher le prix théorique avant envoi de l'ordre. |
| `POST` | `/api/v1/greeks` | `GreeksRequest` (mêmes champs que `PriceRequest`) | `GreeksResponse` (`price`, `delta`, `gamma`, `vega`, `theta`) | **Greeks BS** instantanés. Pure compute. Pas de `rho` (FX → r=0). | Order Ticket : afficher les greeks préview pour sizing (delta hedge). |
| `POST` | `/api/v1/iv` | `ImpliedVolRequest` (`spot`, `strike`, `maturity_days`, `option_type`, `market_price`) | `ImpliedVolResponse` (`implied_vol`) | **Implied vol** par inversion BS sur Brent dans `[1e-6, 5.0]`. **422** si `market_price` hors bracket (option DITM/DOTM, parité call/put violée). | Reverse-engineer la vol implicite d'un prix observé hors-IB chain (ex: BBG quote). |

---

## 3. Vol

Lecture de l'état vol courant + historique. **Cache-first** sur Redis pour le live (TTL 600s), **PG-fallback** pour les requêtes historiques.

| Méthode | Path | Query | Réponse | Description | Utilité |
|---|---|---|---|---|---|
| `GET` | `/api/v1/vol/surface` | `symbol=EURUSD` | `SurfaceResponse` (timestamp, spot, forward, surface_data JSONB, fair_vol_data) | **Latest vol surface** depuis Redis (`latest_vol_surface:<symbol>`). 404 si VolEngine n'a pas encore publié (boot froid). | `SmileChartPanel` + `TermStructurePanel` au mount. Refresh sur événement WebSocket `/ws/vol`. |
| `GET` | `/api/v1/vol/surface/at/{ts}` | path: `ts` ISO-8601, `symbol=EURUSD` | `SurfaceResponse` | **Surface historique** au timestamp exact depuis `vol_surfaces` PG. 404 si aucune row à ce ts. | Replay de scénario, audit, comparaison avant/après changement de config. |
| `GET` | `/api/v1/vol/term-structure` | `symbol=EURUSD` | `TermStructureResponse` (`{tenor: atm_vol}` mapping) | **Term structure ATM** dérivée de la latest surface Redis. Extrait juste les ATM vols des pillars. | `TermStructureChart` — courbe IV ATM par tenor (1W → 1Y). |
| `GET` | `/api/v1/vol/smile/{tenor}` | path: `tenor`, `symbol=EURUSD` | `SmileResponse` (5 points : 10P, 25P, ATM, 25C, 10C) | **Smile 5-points** d'un tenor donné. Lecture PG (latest row dans `signals`+`svi_params`). | `SmileChartPanel` → courbe smile par tenor. |

---

## 4. Portfolio

État portfolio en lecture. **Mix Redis live (greeks/PnL) + PG historique (positions/snapshots/trades)**.

| Méthode | Path | Query | Réponse | Description | Utilité |
|---|---|---|---|---|---|
| `GET` | `/api/v1/positions` | `status=OPEN\|CLOSED\|EXPIRED` (opt), `limit=100` | `list[PositionView]` | **Toutes les positions** PG (most-recent first). Filtre `status` optionnel. | `BookPanel` : 3 sous-tables (open / closed / expired). |
| `GET` | `/api/v1/positions/{position_id}` | path: int | `PositionView` | **Position unitaire** PG. 404 si inconnue. | Detail view au clic sur une row du book. |
| `GET` | `/api/v1/risk` | — | `GreeksAggregated` (delta_usd, vega_usd, gamma_usd, theta_usd au niveau portfolio) | **Greeks agrégés** depuis Redis (`latest_greeks:portfolio`, TTL 30s, écrits par RiskEngine). 404 si jamais publié. | `PortfolioPanel` — la cible des greeks bornés (la thèse trading repose sur leur stabilité). |
| `GET` | `/api/v1/pnl-curve` | — | `PnLCurve` (timeseries des pnl_usd) | **PnL curve** depuis Redis (`latest_pnl_curve`, TTL 30s). 404 si pas encore calculée. | Graphique PnL session du dashboard. |
| `GET` | `/api/v1/history` | `position_id` (req), `limit=500` | `HistoryResponse` (snapshots oldest-first) | **Timeline d'une position** depuis `position_snapshots` PG (oldest → newest pour plot). | Sparkline / chart PnL par position dans `BookPanel` detail. |

---

## 5. Analytics

Reads agrégés sur PG pour le **Vol Scanner** + Backtest UI. Filtres combinables, pagination via `limit`.

| Méthode | Path | Query | Réponse | Description | Utilité |
|---|---|---|---|---|---|
| `GET` | `/api/v1/signals` | `underlying`, `tenor`, `signal_type=CHEAP\|EXPENSIVE\|FAIR`, `since` (datetime), `limit=200`, `latest_per_tenor=false` | `list[SignalRow]` | **Signaux les plus récents** depuis `signals` PG, filtres combinables. **Mode `latest_per_tenor=true`** = 1 row par (underlying, tenor) — utilisé par le Vol Scanner pour ne pas afficher 100× le même tenor. | `VolScannerPanel` — table principale du dashboard. |
| `GET` | `/api/v1/vol-history` | `symbol=EURUSD`, `limit=50` | `list[VolHistoryRow]` | **N dernières snapshots vol_surfaces** (headline fields uniquement, pas le JSONB) — pour graph timeseries léger. | Mini-timeline IV ATM dans le dashboard. |
| `GET` | `/api/v1/backtest` | `strategy_name` (opt), `limit=50` | `list[BacktestRunRow]` | **Runs backtest** avec métriques headline (Sharpe, MDD, return, n_trades). Filtre par strategy. | Page Backtest — vue tabulaire des runs précédents. |
| `GET` | `/api/v1/system-stats` | — | `SystemStats` (PG row counts par table + heartbeat ages Redis) | **Vue système combinée** : PG counts (positions OPEN/CLOSED, snapshots/jour, signals/jour, vol_surfaces total) + heartbeats engines (age en secondes). | Page Settings/Diagnostics — health profond du système. |

---

## 6. Cockpit (vol-aware)

Endpoints **opinionated** qui combinent core compute + Redis live + PG historique. C'est ici que vit la *thèse trading* (régime, PCA, fair value).

| Méthode | Path | Body / Query | Réponse | Description | Utilité |
|---|---|---|---|---|---|
| `GET` | `/api/v1/vol/regime` | `symbol=EURUSD` | `RegimeResponse` (regime label + per-tenor expected VRP) | **Régime de marché actuel** + variance risk premium attendu par tenor (via `core/vol/vrp.py:detect_regime`). | `RegimeDetectorPanel` — décide si on vend ou on achète de la vol selon le régime. |
| `GET` | `/api/v1/vol/pca-signals` | `symbol=EURUSD`, `lookback_hours=24` | `PcaSignalsResponse` | **Fit PCA** sur les `vol_surfaces` du lookback + projection de la latest snapshot. **Dégradé** si <50 surfaces dans la fenêtre (warning dans la réponse, pas d'erreur). | `PCASignalPanel` — détecte les surfaces "anormales" vs structure récente. |
| `POST` | `/api/v1/vol/trade-preview` | `TradePreviewRequest` (`structure` ∈ {`StraddleATM`, `RiskReversal25d`, `Butterfly25d`, `CalendarSpread`}, `tenor`, `side`, `qty`, `tenor_far` requis si `CalendarSpread`) | `TradePreviewResponse` (`legs[]`, `net_vega`, `net_gamma`, `net_theta`, `net_delta`, `total_premium`, `bootstrap`) | **Preview d'un trade complexe**. Lit la latest surface Redis + applique BS pricing sur chaque jambe via `services/execution/structures.py`. **404** si pas de surface en Redis (vol-engine pas encore tourné). | `TradePreviewPanel` + `OrderTicketPanel` — voir greeks et cost AVANT envoi de l'ordre. |
| `GET` | `/api/v1/vol/model-health` | — | `ModelHealthResponse` (counts d'observations vs minimums requis pour W1/VRP/PCA/fair_smile) | **Santé des modèles vol** : suis-je sous le seuil minimum d'observations pour chaque model component ? | `ModelHealthPanel` — alerte quand un model risque de produire un signal non-fiable (ex: après reset DB). |

---

## 7. Admin

Configuration **versionnée append-only** dans la table `vol_config` (PK = `version`, jamais d'UPDATE). Chaque PUT crée une nouvelle version. Audit trail complet.

| Méthode | Path | Body | Réponse | Description | Utilité |
|---|---|---|---|---|---|
| `GET` | `/api/v1/admin/config` | — | `ConfigResponse` (version + config + meta) | **Latest config active**. | Page Settings au load — montre la config en vigueur. |
| `GET` | `/api/v1/admin/config/schema` | — | JSON Schema de `core.config.VolTradingConfig` | **JSON Schema brut** pour piloter le React JSON Schema Form du frontend. | Settings UI : génération automatique du formulaire à partir du schema (pas de duplication frontend/backend). |
| `PUT` | `/api/v1/admin/config` | `ConfigPatchRequest` (patch dict + user + comment) | `ConfigResponse` (nouvelle version créée) | **Update config** via patch JSON. Validations Pydantic v2 → 422 si invalide. Sur succès : INSERT new version PG + PUBLISH `config_updated` Redis (consommé par VolEngine pour hot-reload). | Settings UI : Save button. **Hot reload** = pas de restart engine. |
| `GET` | `/api/v1/admin/config/history` | `limit=50` (1-500) | `list[ConfigResponse]` | **Historique** des N dernières versions (newest first). | Settings UI : panneau "History" pour voir qui a changé quoi quand. |
| `POST` | `/api/v1/admin/config/revert/{version}` | `ConfigRevertRequest` (user + comment) | `ConfigResponse` (nouvelle version = copie de `version` cible) | **Revert** : crée une nouvelle version qui reprend le contenu de `version` cible. **Pas de DELETE** : on ajoute une row, pas on en supprime. | Settings UI : bouton "Revert to this version" sur l'historique. |

---

## 8. WebSocket

Pub/sub Redis exposé en WebSocket pour les panels qui doivent se rafraîchir en temps réel. **Pas de préfixe `/api/v1`** (les WS vivent à la racine).

| Path | Channel Redis | Fréquence | Description | Utilité |
|---|---|---|---|---|
| `/ws/ticks` | `ticks` | ~5 msg/s par symbole (throttled 200ms) | **Subscribe au stream tick** : bid/ask/mid pour tous les symboles trackés. | `useTicks` hook → `TickChart` mise à jour live. Pas de polling. |
| `/ws/vol` | `vol_update` | ~1 msg / 180s (à la fin de chaque vol scan) | **Subscribe aux updates vol** : nouvelle surface, nouveaux signals. Le payload contient la surface (JSONB <2KB) ; les signals sont à re-fetch via `GET /signals` après réception. | `useVolStream` hook (à créer post-R5) → `SmileChartPanel` + `TermStructurePanel` + `VolScannerPanel` se rafraîchissent à la fin de chaque scan. |
| `/ws/risk` | `risk_update` | ~1 msg / 60s (à la fin de chaque cycle Risk) | **Subscribe aux updates risk** : nouveaux greeks portfolio + PnL curve. | `useRiskStream` hook → `PortfolioPanel` + le P&L chart vivent en temps réel. |

**Lifecycle WebSocket** : connexion HTTP upgradée par nginx → FastAPI `WebSocket` → `_serve(channel, ws)` (`ws.py`) ouvre une subscription Redis `pubsub.subscribe(channel)` et forward chaque message au client en JSON. Détection de déconnexion via `WebSocketDisconnect`. Le frontend gère la reconnexion auto (cf. `frontend/src/hooks/useWebSocket.ts`).

---

## Conventions transverses

### Statuts HTTP

- `200` — succès, payload retourné
- `404` — ressource non trouvée (position inconnue, surface jamais publiée, version config inexistante)
- `422` — validation Pydantic échouée (champ manquant, valeur hors range, IV non solvable dans le bracket)
- `500` — bug serveur (loggué en CloudWatch + alerte SNS si fréquent)

### Pagination

Tous les endpoints retournant des listes ont un `limit` query param avec borne `Query(ge=1, le=N)` (N = 200, 500, 1000, 2000, 5000 selon la table). Pas de cursor pagination — le volume est petit (1 jour ≤ ~3.4k signals) et `ORDER BY timestamp DESC LIMIT N` est indexé.

### Authentification

**Aucune** sur cette version (mono-utilisateur, projet perso). Pour ajouter de l'auth : middleware FastAPI + JWT + dépendance sur les routes admin uniquement. Hors-scope migration v1→v2.

### CORS

Configuré dans `src/api/main.py` pour autoriser le frontend Vite dev (`http://localhost:5173`) + l'origine prod (`https://valeriandarmente.dev`). Tout autre origin → 403 sur preflight.

### Rate limit

Middleware basique dans `src/api/middleware/rate_limit.py` : 100 req/min par IP par défaut. Volontairement permissif — le risque réel n'est pas le brute force mais le user qui fait F5 en boucle pendant un crash.

### Observability

Chaque request loggue (via `src/api/middleware/logging.py` + `timing.py`) : méthode, path, status, durée ms, bytes IN/OUT. Logs stdout → Docker → driver `awslogs` → CloudWatch Log group `/fxvol/api`.

---

## Comment ajouter un endpoint

1. **Définir le response model** dans `src/api/models/<domain>.py` (Pydantic v2).
2. **Écrire le handler** dans `src/api/routers/<domain>.py` (decorator `@router.get/post/put/delete`).
3. **Si state Redis** : utiliser `RedisDep = Annotated[aioredis.Redis, Depends(get_redis)]`.
4. **Si state PG** : utiliser `DbDep = Annotated[AsyncSession, Depends(get_db_session)]`.
5. **Logique métier** dans `src/api/services/<domain>_service.py` (jamais dans le router — facilite tests).
6. **Test** dans `tests/test_api_<domain>.py` (httpx.AsyncClient avec `app=app`).
7. **Régen schema TypeScript frontend** : `python scripts/dump_openapi.py` + `npm run gen:api` côté frontend (sinon le frontend ne voit pas le nouveau endpoint).
8. **Update cette doc** (la table de la section concernée) — sinon dérive doc/code garantie.

---

**Référence architecture** : `releases/architecture_finale_project/03-fastapi.md`.
**Source de vérité** : `src/api/routers/`. En cas de divergence avec cette doc, le code gagne.
