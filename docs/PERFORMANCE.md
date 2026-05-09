# Performance notes

## R7 split : RAM overhead

L'architecture R7 remplace un seul process Python (monolithe v1 avec 3 threads engines + writer) par **4 containers Python distincts**. Overhead RAM attendu :

| Container | RAM typique | Commentaire |
|---|---|---|
| `market-data` | ~100 MB | Python base + ib_insync + redis client + numpy |
| `vol-engine` | ~150 MB | market-data + arch (GARCH) + scipy vectorisé |
| `risk-engine` | ~120 MB | market-data + scipy (pas d'arch) |
| `db-writer` | ~80 MB | Python base + SQLAlchemy + asyncpg (pas d'ib_insync, pas d'arch) |
| **Total** | **~450 MB** | vs ~150 MB monolithe v1 |

Overhead : +300 MB. Sur **t3.medium** (4 GB RAM target prod R8), représente **7.5%** → négligeable.

### Mesure post-déploiement

Commande pour valider en prod :
```bash
docker compose --profile engines --profile ib up -d
# Attendre 3-5 min (cycle vol initial)
docker stats --no-stream --format "table {{.Container}}\t{{.MemUsage}}\t{{.CPUPerc}}" \
  fxvol-market-data fxvol-vol-engine fxvol-risk-engine fxvol-db-writer
```

Résultats attendus (ordre de grandeur) :
```
CONTAINER           MEM USAGE / LIMIT    CPU %
fxvol-market-data   85MiB / 4GiB         1.5%
fxvol-vol-engine    140MiB / 4GiB        0.3%  (pics à 40% pendant GARCH fit)
fxvol-risk-engine   110MiB / 4GiB        2.0%
fxvol-db-writer     75MiB / 4GiB         0.2%
```

**Red flags** à investiguer :
- `market-data` > 200 MB → probable leak dans la queue tick ou historique non purgé
- `vol-engine` > 300 MB → retentions arch trop longues (OHLC history) ou memoization abusive
- `db-writer` > 150 MB → batch queue qui déborde (postgres down ?) → check `writer_queue_full` dans les logs

## Latences attendues

| Flux | Latence bout-en-bout | Composants |
|---|---|---|
| Tick IB → browser WebSocket | ~20-50 ms | IB TCP + MarketData Redis publish + FastAPI WS bridge + browser render |
| REST `/api/v1/vol/surface` | < 30 ms | Nginx + FastAPI + Redis GET (cache) |
| Vol scan cycle complet | 2-4 s | FOP chain IB + PCHIP + GARCH fit + Redis publish |
| Risk cycle (portfolio ~20 pos) | 5-15 ms | Redis GET spot + surface + BS Greeks loop + publish |
| db-writer batch commit | < 100 ms | Queue drain + SQLAlchemy bulk INSERT (50 rows) |

## Budget CPU

| Container | CPU idle | CPU peak |
|---|---|---|
| market-data | < 5% | 15% (spike at 5 ticks/s burst) |
| vol-engine | < 1% | 40% pendant 2-5s de GARCH fit (toutes les 3 min) |
| risk-engine | < 5% | 10% si book > 100 positions |
| db-writer | < 1% | 5% pendant batch commit |
| postgres | < 1% | 10% pendant vol_scan INSERT |
| redis | < 1% | Jamais au-dessus |

Total stack ~10% CPU en idle sur t3.medium (2 vCPU). Confortable.

## Scaling horizontaux futurs

### Multi-symbol (5-10 FX pairs)
- `market-data` : 1 replica par symbol (clientId distinct par env var). 10 replicas × 100 MB = 1 GB.
- `vol-engine` : idem, sauf si vol features partagent (rare en multi-asset).
- `risk-engine` : 1 seul (calcul portfolio unifié).
- `db-writer` : 1 seul (batch partagé, OK jusqu'à ~500 rows/s).

### Bottleneck attendu
- IB Gateway à partir de 6-8 clientIds → acheter un 2e IB account ou login session séparée.
- Postgres : `account_snaps` table grossit rapidement (1 row/s/container × 10 = 864k rows/j). Index + partitioning nécessaire à partir de 30M rows.
