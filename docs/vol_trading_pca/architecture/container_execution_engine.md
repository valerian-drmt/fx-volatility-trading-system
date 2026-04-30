# container — `execution-engine`

**Image** : maison (`docker/execution/Dockerfile`)
**Container** : `fxvol-execution`, IP `172.20.0.14`, IB clientId `5`
**État** : ✅ existe (split d'`api` au 2026-04-29)
**Steps** : 4 (submit + fills tracking), 5 (hedge / close orders)

---

## Rôle

Seul container autorisé à parler IB pour l'execution. FastAPI privé (port `8001`,
exposé uniquement sur le réseau Docker, jamais via nginx).

Deux responsabilités :
1. **Endpoints internes** consommés par `api` (proxy stateless) :
   `POST/DELETE /internal/orders`, `GET /internal/positions`, `POST /internal/positions/{con_id}/close`.
2. **Boucle de sync 1s** (`position_sync_loop`) : pull IB → upsert 6 tables Postgres + heartbeat Redis.

## Outputs

| Cible | Schema / Table | Cadence |
|---|---|---|
| Postgres | `orders` (state machine) | 1s |
| Postgres | `trades` (fills) | 1s |
| Postgres | `positions` (open/closed) | 1s |
| Postgres | `position_snapshots` (timeseries) | 1s |
| Postgres | `order_events` (audit log) | event-driven |
| Postgres | `account_snaps` (cash, NLV, margin) | 1s |
| Redis | `heartbeat:execution` | 1s |

## Mapping steps

- **Step 4** — submit single-leg + multi-leg (à étendre : currently single-leg seulement),
  rollback partial fill (à implémenter), idempotence via client_order_id.
- **Step 5** — consume `action:hedge` / `action:close` Redis topics → submit order →
  ack via `order_events` audit. Dégradation : si Redis subscription down, risk ne perd
  rien (audit en SQL est la source de vérité, retry safe).

## Configuration

`exec_config` (à créer, versioned) :
- max_orders_per_minute (rate limit)
- mock_mode (bool) — pour tests E2E sans IB
- partial_fill_timeout_s

## Failure modes

- IB déconnect → orders en flight passent `state = unknown`, alert ; sync loop continue à
  poller (l'état converge dès reconnect).
- Postgres lag → publish flag `degraded:execution:1` mais ne bloque pas les submits
  (l'audit IB local reste source de vérité court terme).

## À faire pour v1.0

- [ ] Multi-leg orders (combos FOP).
- [ ] Rollback strategy partial fill (cf. STEP4 §6).
- [ ] Subscriber `action:hedge` / `action:close` (event-driven).
- [ ] Idempotence par `client_order_id`.
- [ ] Mode `mock` pour tests E2E sans IB Gateway.
