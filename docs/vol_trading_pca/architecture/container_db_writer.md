# container — `db-writer`

**Image** : maison (`docker/db-writer/Dockerfile`)
**Container** : `fxvol-db-writer`
**État** : ✅ existe
**Steps** : tous (sink async transverse)

---

## Rôle

Sink batch async pour les writes non-critiques en latence. Subscribe sur des topics Redis
spécifiques, accumule en buffer, flush vers Postgres par lots.

Évite que les services métier (vol-engine, risk) bloquent sur des INSERTs lents.

> Les writes **critiques** (orders, positions, trades) restent **synchrones** dans
> `execution-engine` (il faut savoir si l'INSERT a réussi avant d'ACK l'order).

## Topics Redis subscribés

| Topic | Source | Table cible |
|---|---|---|
| `vol:surface` | vol-engine | `vol_surfaces` (déjà persisté sync — duplicate à arbitrer) |
| `signal:vol` | vol-engine | `signals` |
| `signal:pca` | vol-engine (step 2) | `signals_pca` |
| `regime:state` | vol-engine (step 1) | `regime_states` |
| `risk:greeks` | risk | `position_pnl_snapshots` |
| `exit:decision` | risk | `exit_decisions` |

## Mapping steps

- **Step 1** — sink `regime_states`.
- **Step 2** — sink `signals_pca`.
- **Step 5** — sink `position_pnl_snapshots` + `exit_decisions`.
- **Backtest** — bypass : le harness écrit directement Postgres (le pubsub serait artificiel
  en replay offline).

## Stratégie batching

- Buffer 100 rows ou 1s (whichever first).
- COPY si N > 50, sinon INSERT batch.
- DLQ table `db_writer_failures` si flush échoue 3x.

## Failure modes

- Postgres down → buffer en mémoire jusqu'à `max_buffer_rows = 10_000` puis drop + alerte.
- Subscriber lag > 5s → publish flag `degraded:db-writer:1`.

## À faire pour v1.0

- [ ] Topics step 1 / 2 / 5 ajoutés.
- [ ] DLQ + replay tool (`scripts/db_writer_replay.py`).
