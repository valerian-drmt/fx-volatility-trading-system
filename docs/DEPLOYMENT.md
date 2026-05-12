# Production deployment runbook

## Architecture cible

```
  Internet (HTTPS)
        │
        ▼
   EC2 t3.medium (Ubuntu 22.04)
        │
        ├── Nginx container (ports 80 / 443, reverse proxy)
        ├── fx-options-frontend (nginx:alpine servant le bundle React)
        ├── fx-options-api (FastAPI + uvicorn :8000)
        ├── fx-options-market-data / -vol-engine / -risk-engine (R7 containers)
        ├── fx-options-db-writer
        ├── postgres:16-alpine + volume postgres_data
        ├── redis:7-alpine + volume redis_data
        └── ib-gateway (profile ib) — containerisé avec IBC auto-login
```

Domaine : `valerian.dev` (TLS via Let's Encrypt, renouvellement cron `certbot renew`).

## Secrets attendus sur GitHub repo

Settings → Secrets and variables → Actions :

| Secret | Format | Source |
|---|---|---|
| `EC2_HOST` | hostname ou IP publique | AWS EC2 console |
| `EC2_USER` | `ubuntu` (AMI officielle) | — |
| `EC2_SSH_KEY` | PEM privée (multi-ligne) | key pair généré sur AWS |
| `DB_PASSWORD` | ≥ 20 char aléatoires | `openssl rand -base64 30` |
| `VNC_PASSWORD` | 8-16 char (limite IBC) | `openssl rand -hex 8` |
| `IB_USERID` | username IB paper/live | compte IB |
| `IB_PASSWORD` | password IB paper/live | idem |

Jamais commit ces valeurs dans le repo. Le workflow `deploy.yml` les injecte via SSH heredoc dans `/opt/fxvol/.env`.

## Provisioning d'une nouvelle machine

Une seule fois, sur un host Ubuntu 22.04 x86_64 fresh :

```bash
# Depuis l'host EC2, user "ubuntu" ou root :
curl -fsS https://raw.githubusercontent.com/valerian-drmt/fx-volatility-trading-system/main/infrastructure/ec2/setup.sh | sudo bash

# Provisionne :
#  - docker + compose v2
#  - user fxvol + répertoire /opt/fxvol
#  - ufw : 22, 80, 443 only
#  - systemd unit fxvol-compose.service (auto-start au reboot)
#  - cron : certbot renew + Postgres backup nightly → S3
```

## TLS cert initial

```bash
sudo certbot --nginx -d valerian.dev
# Choisit redirect HTTP → HTTPS (recommandé)
# Renouvellement auto via /etc/cron.daily/fxvol-certbot-renew (installé par setup.sh)
```

## Déploiement d'une release

### Path 1 : tag semver (automatique)

```bash
# Sur ton poste dev, après merge sur main :
git pull origin main
git tag -a v1.9.0 -m "R8 — deprecation PyQt + deploy prod"
git push origin v1.9.0
```

GitHub Actions :
1. `deploy-prod.yml` se déclenche sur `v*.*.*`
2. Résout le sha du tag → `sha-<commit>` image tags
3. SSH sur `$EC2_HOST`, render `.env`, `docker compose pull && up -d`
4. Applique `alembic upgrade head`
5. Smoke check `GET /api/v1/health` → 200

### Path 2 : deploy manuel d'un sha (rollback)

```
Actions → deploy-prod → Run workflow
Input deploy_sha : <commit_sha_target> (40 char)
```

Utile pour revenir à une version précédente sans revert git.

## Rollback

Toujours préférer un **redeploy d'un sha connu bon** à un hotfix sur main :

```
1. Sur GitHub Actions → deploy-prod → Run workflow
2. deploy_sha : <last_known_good_sha>
3. Run — workflow pull les anciennes images GHCR (conservées jusqu'à retention GHCR)
4. ~5 min plus tard : stack revenue à la version précédente
```

Si la DB a migré entre les 2 versions, le rollback peut échouer sur `alembic downgrade` (migrations pas toujours réversibles). Procédure détaillée :

```bash
# Depuis l'host EC2
ssh ubuntu@<EC2_HOST>
cd /opt/fxvol
docker compose exec api python -m alembic -c persistence/alembic.ini history --verbose
docker compose exec api python -m alembic -c persistence/alembic.ini downgrade -1
# Puis redeploy le sha voulu via GHA
```

## Monitoring

- **Logs** : `docker compose logs -f` depuis `/opt/fxvol` pour tout, ou `docker logs fxvol-api -f` ciblé
- **Healthcheck** : `curl -I https://valerian.dev/api/v1/health` doit renvoyer 200
- **Extended** : `curl https://valerian.dev/api/v1/health/extended` montre l'état Redis + DB + engines heartbeats
- **Metrics** : `https://valerian.dev/metrics` (Prometheus format, scrapé par Grafana R8+)

## Backups

- **Nightly** : Postgres dump → S3 `fxvol-backups/postgres/fxvol-*.dump` via `/etc/cron.daily/fxvol-postgres-backup`
- **Retention** : S3 lifecycle policy (Glacier après 30j, expire 1 an)
- **Restore** : `aws s3 cp s3://fxvol-backups/postgres/<dump> /tmp/ && docker compose exec -T postgres pg_restore -U fxvol -d fxvol < /tmp/<dump>`

## Pannes fréquentes

| Symptôme | Cause probable | Fix |
|---|---|---|
| `502 Bad Gateway` pendant 10-30s après deploy | Nginx up avant api healthy | attendre, `depends_on: service_healthy` gère ça en < 1 min |
| IB Gateway "waiting for login" | credentials manquants ou 2FA requis | `docker logs fxvol-ib-gateway` + VNC sur 127.0.0.1:5900 |
| Postgres `FATAL: password authentication failed` | drift .env ↔ volume | stop + `docker volume rm fx-volatility-trading-system_postgres_data` + redeploy + restore backup |
| Cert Let's Encrypt expiré | cron renew failed | `sudo certbot renew` manuel + check `/var/log/letsencrypt/` |
| `docker: Error response from daemon: Get ghcr.io: 401` | GHCR login expiré sur EC2 | redeploy via GHA qui re-login, ou SSH + `docker login ghcr.io` manuel |
