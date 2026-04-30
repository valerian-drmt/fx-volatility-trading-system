# container — `market-data`

**Image** : maison (`docker/market-data/Dockerfile`)
**Container** : `fxvol-market-data`, IP `172.20.0.10`, IB clientId `1`
**État** : ✅ existe
**Steps** : 1 (régime, données spot/OHLC), 2 (OHLC pour PCA via snapshot-collector), 5 (spot live MTM)

---

## Rôle

Seul service connecté au flux ticks IB. Source unique pour :
- spot EURUSD (FX) → publié Redis 200ms throttle
- futures 6E (front month) bid/ask/mid → publié Redis
- chain FOP options EURUSD (strikes × tenors filtrés) → publié pour vol-engine
- (cible v0.1) OHLC daily backfill + incremental → écrit `ohlc_daily` (Yang-Zhang inputs)

Le service ne calcule **rien** de quantitatif. C'est un proxy IB → Redis/Postgres.

## Inputs

- IB ticks via `ib_insync` (Forex + ContFuture + FuturesOption chain)

## Outputs

| Cible | Topic / Table | Cadence |
|---|---|---|
| Redis | `ticks:eurusd` (PUBLISH + SET) | 200ms throttle |
| Redis | `ticks:6e:front` | idem |
| Redis | `chain:eurusd:<tenor>` | recompute on chain reqs |
| Redis | `heartbeat:market-data` | 1s |
| Postgres | `ohlc_daily` (cible v0.1) | 1× / jour 00:05 UTC |

## Mapping steps

- **Step 1** — fournit OHLC pour Yang-Zhang RV consumed by vol-engine. Si gap > 1 jour, regime = `INSUFFICIENT_DATA` (cf. STEP1 §3.2).
- **Step 2** — OHLC daily backfill (~252 obs) prérequis avant PCA fit (cf. README §Phase Foundation step 3).
- **Step 5** — spot live alimente MTM positions (P&L mark + delta drift trigger).

## Failure modes

- IB déconnect → publish flag `degraded:market-data:1` Redis ; vol-engine bascule en mode skip.
- OHLC backfill incomplet → vol-engine refuse de publier régime stable (gate explicite).

## À faire pour v1.0

- [ ] Daily OHLC writer (job scheduler interne ou cron container).
- [ ] Backfill script `scripts/backfill_ib_historical.py` (cf. README §3 Phase Foundation).
- [ ] Trusted IP `.10` auto-confirm via VNC (manuel pour l'instant).
