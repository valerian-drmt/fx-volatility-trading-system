# Docker cheat sheet

## Restart / recreate / rebuild

```powershell
docker compose restart <service>                                 # redémarre le process
docker compose up -d --force-recreate <service>                  # recrée le container (relit env)
docker compose up -d --build <service>                           # rebuild image + recrée
docker compose build --no-cache <service>                        # rebuild propre sans cache
```

## Stack entier

```powershell
docker compose --profile engines up -d --build                   # tout rebuild + lance
docker compose --profile engines down                            # stop + rm containers (volumes gardés)
docker compose --profile engines down -v                         # ⚠ wipe volumes
```

## Cas typiques

| Modif | Commande |
|---|---|
| `src/engines/risk/` | `docker compose --profile engines up -d --build risk-engine` |
| `src/engines/vol/` | `docker compose --profile engines up -d --build vol-engine` |
| `src/engines/market_data/` | `docker compose --profile engines up -d --build market-data` |
| `src/engines/execution/` | `docker compose up -d --build execution-engine` |
| `src/engines/db_writer/` | `docker compose --profile engines up -d --build db-writer` |
| `src/api/` | `docker compose up -d --build api` |
| `frontend/` | `docker compose up -d --build frontend` |
| `docker-compose.yml` | `docker compose up -d <service>` |
| `Dockerfile` | `docker compose build --no-cache <service>` puis `up -d` |

## Logs / debug

```powershell
docker compose ps                                                # état
docker compose logs --tail=80 <service>                          # historique
docker compose logs -f --tail=20 <service>                       # follow
docker compose exec <service> sh                                 # shell
docker compose exec -u 0 <service> sh                            # shell en root
```

## IB Gateway

```powershell
docker compose stop ib-gateway
docker compose rm -f ib-gateway
docker compose up -d ib-gateway                                  # recreate (volume jts persiste)
docker compose logs --tail=30 ib-gateway | Select-String "TrustedIPs|Login|socat"
```

## Redis inspection

```powershell
docker compose exec redis redis-cli MGET heartbeat:market_data heartbeat:vol_engine heartbeat:risk_engine
docker compose exec redis redis-cli HGETALL contract_marks:EUR
docker compose exec redis redis-cli GET latest_spot:EURUSD
```
