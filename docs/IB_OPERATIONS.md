# IB Gateway operations

## Règle absolue : un seul set d'engines connecté à IB (R7)

IB Gateway supporte ~8 clientIds simultanés par session MAIS **refuse** deux connexions qui utilisent le même clientId. Depuis R7, deux sets d'engines peuvent vouloir se connecter :

1. **PyQt v1 in-process** — `Controller._start_engine_pool()` lance MarketData (clientId=1), VolEngine (clientId=2), RiskEngine (clientId=3) dans le process PyQt.
2. **Containers R7 standalone** — `services/market_data/` (clientId=1), `services/vol/` (clientId=2), `services/risk/` (clientId=3) dans `docker-compose --profile engines`.

Les deux utilisent les **mêmes clientIds**. Les démarrer en parallèle = collisions IB garanties → 2e connexion rejetée, 1er set affecté aléatoirement.

### Contrôle via `ENGINES_IN_PROCESS`

R7 PR #8 ajoute un flag `ENGINES_IN_PROCESS` (env var) sur le Controller :

- **`ENGINES_IN_PROCESS=true`** (défaut, backwards compat) : PyQt lance ses 3 engines in-process → ne PAS démarrer `docker compose --profile engines` en parallèle.
- **`ENGINES_IN_PROCESS=false`** : PyQt **ne démarre aucun** engine thread, consomme les données via Redis (produits par les containers R7). **Requis** si `docker compose --profile engines up`.

```bash
# Mode legacy : PyQt tout-en-un (dev)
python app.py
# → MarketData / Vol / Risk dans le process PyQt

# Mode R7 : PyQt en mode consumer, engines dans des containers
docker compose --profile engines --profile ib up -d
ENGINES_IN_PROCESS=false python app.py
# → PyQt lit Redis, containers IB-connected
```

Fail-safe : oublier de set `ENGINES_IN_PROCESS=false` en mode R7 → les 3 engines PyQt essayent de se connecter à IB Gateway, IB rejette la 2e tentative par clientId, logs d'erreur évidents dans le terminal PyQt.

---

## Modes de deploiement

Deux chemins de connexion à IB coexistent pendant la transition v1 → v2.

### Dev natif (v1 PyQt)

- IB Gateway **desktop** lancé manuellement par l'user sur la machine dev.
- L'app PyQt (`python app.py`) se connecte à `127.0.0.1:4002`.
- `IB_HOST` / `IB_PORT` non définis → défauts `_default_ib_host()` / `_default_ib_port()` en `127.0.0.1:4002`.

### Dev containerisé (R6 PR #4+)

- Service `ib-gateway` dans `docker-compose.yml` tourne `ghcr.io/unusualalpha/ib-gateway:stable`.
- IBC logge automatiquement avec `TWS_USERID` / `TWS_PASSWORD` / `TRADING_MODE=paper` depuis `.env`.
- Le port 4002 est bindé sur `127.0.0.1:4002` du host → **PyQt v1 continue à pointer sur `localhost:4002`** sans changement.
- Depuis les containers (`api`, futurs engines), utiliser `IB_HOST=ib-gateway IB_PORT=4002`.

## Cycle de vie IBC (auto-restart quotidien)

`AUTO_RESTART_TIME: "11:59 PM"` dans le compose configure IBC pour :
1. Logout + arrêter TWS chaque soir à 23:59 heure du container.
2. Relogin + restart automatiquement dans les 2-3 minutes qui suivent.

Pourquoi quotidien : IB **exige** une relogin journalière côté broker. Sans IBC, l'user doit cliquer manuellement chaque matin — non tenable en prod.

Le container écrit les logs IBC dans `/opt/ibc/logs/` — accessible via :
```bash
docker logs -f fxvol-ib-gateway
```

## 2FA et credentials

- **Paper trading** (actuel R6) : pas de 2FA → IBC fait tout seul.
- **Live trading** (R8 production) : IB exige 2FA obligatoire sauf exemption "Secure Login". Deux options :
  - Demander une exemption à IB (disponible pour les comptes pros avec volume)
  - Intégrer `IBC` + `Shared Login` (code 2FA partagé via email/SMS → automation fragile)

Pas de live trading prévu sur ce projet pour l'instant.

## VNC pour debug

Le container expose un serveur VNC sur `127.0.0.1:5900`. Password défini par `VNC_PASSWORD` dans `.env`. Utile pour :
- Voir la fenêtre TWS en cas d'alerte popup (mise à jour, confirm 2FA manuel)
- Inspecter les settings graphiques

```bash
# Client VNC (VNC Viewer, RealVNC, etc.) → 127.0.0.1:5900
```

Ne jamais exposer 5900 au LAN (c'est déjà le cas, bindé 127.0.0.1).

## Troubleshooting

| Symptôme | Cause probable | Fix |
|---|---|---|
| API refuse de se connecter (`ECONNREFUSED`) | IBC encore en phase login (90s au premier boot) | Attendre ou `docker logs fxvol-ib-gateway -f` pour voir l'état |
| `Daily restart in progress` dans les logs PyQt | Fenêtre 23:59-00:02 du reset quotidien | Reconnecter automatiquement via IBClient backoff |
| `IB_USERID not set` au boot du container | `.env` pas fourni ou vide | Remplir `IB_USERID` et `IB_PASSWORD` dans `.env` |
| Container redémarre en boucle | 2FA demandé côté IB | Connecter en VNC pour voir la popup, envisager l'exemption |

## Sécurité

- `IB_USERID` / `IB_PASSWORD` / `VNC_PASSWORD` **jamais** committés. `.env` est dans `.gitignore`.
- En prod (R8), migrer sur AWS Secrets Manager ou Docker secrets.
- Port 4002 (API) bindé `127.0.0.1` uniquement → pas d'exposition LAN.
- Network `fxvol-external` isolé : l'IB Gateway peut joindre Internet pour parler à IB, mais un attaquant qui compromet IB Gateway ne voit pas `postgres` / `redis`.

## Recovery procedures (R8)

### Scénario 1 : IB Gateway container down, engines retry frénétiquement

```bash
ssh ubuntu@<EC2_HOST> && cd /opt/fxvol
docker compose logs ib-gateway --tail=100
```

- **"Login failed"** → credentials invalides dans `.env`. Update secret + redeploy via GHA.
- **"2FA required"** → compte IB en Secure Login. Connecter en VNC (tunnel SSH 127.0.0.1:5900) pour valider, OU demander exemption à IB.
- **"Daily restart in progress"** → fenêtre 23:59-00:02 normale, engines backoff < 5 min.

### Scénario 2 : Engine healthy mais Redis vide

```bash
docker compose exec redis redis-cli GET latest_spot:EURUSD
docker compose exec redis redis-cli GET heartbeat:market_data
docker compose logs market-data --tail=50
```

- Heartbeat présent + spot absent → subscribe au mauvais ticker ou `reqMktData` en erreur.
- Heartbeat absent → engine stuck → `docker compose restart market-data`.
- IB error 322 (request rejected) → collision client_id avec PyQt host → set `ENGINES_IN_PROCESS=false` côté host.

### Scénario 3 : Rollback après deploy foireux

```
Actions → deploy-prod → Run workflow
  deploy_sha: <last_known_good_commit>
```

~5 min pour revenir sur les images `sha-<commit>` précédentes. Si la DB a migré :
```bash
docker compose exec api python -m alembic downgrade -1
# Ou restore depuis backup S3 :
aws s3 cp s3://fxvol-backups/postgres/fxvol-<yyyymmdd>.dump /tmp/
docker compose exec -T postgres pg_restore -U fxvol -d fxvol --clean < /tmp/fxvol-*.dump
```

### Scénario 4 : Cert Let's Encrypt expiré

```bash
sudo certbot renew --force-renewal
docker compose exec nginx nginx -s reload
```

Cause : cron `/etc/cron.daily/fxvol-certbot-renew` a échoué. Check `/var/log/letsencrypt/letsencrypt.log`.

### Scénario 5 : Post-deploy smoke échoue

```bash
PROD_SMOKE=1 PROD_HOST=https://valerian.dev pytest tests/test_post_deploy_smoke.py -v
```

Triage rapide :
- `test_health_endpoint` fail → api down, `docker compose ps`
- `test_health_extended` fail → redis/postgres down, check healthchecks
- `test_openapi_schema_matches_committed_frontend_types` fail → **drift prod** → rollback immédiat
- `test_tls_certificate` fail → voir Scénario 4
- `test_websocket_ticks_route_accepts_upgrade` fail → Nginx `nginx.conf` manque `proxy_set_header Upgrade`
