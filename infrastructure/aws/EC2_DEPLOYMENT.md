# Déploiement EC2 — fxvol (guide opérationnel)

> Comment déployer le stack sur EC2, **comment il doit être structuré**, et la
> liste des **trous à boucher dans le code** avant que `deploy.yml` marche.
> Pré-requis AWS (compte, KMS, SSM, IAM role, SG, DNS, S3) : **déjà provisionnés**
> — voir `STATE.md`. Ce guide ne couvre que la mise en ligne de l'instance + l'app.
> Indépendant du chantier voldesk (R11) : peut se faire en parallèle.

---

## 1. TL;DR — état de préparation

| Bloc | État |
|---|---|
| Compte AWS + KMS + SSM secrets + IAM role EC2 + SG + SSH key + S3 + DNS zone | ✅ provisionné (`STATE.md`) |
| Images GHCR (`build.yml` → 7 images sur push `main`) | ✅ pipeline en place |
| `deploy.yml` (tag `v*.*.*` → SSH EC2 → pull/up/alembic/smoke) | ✅ existe, **gated** `DEPLOY_ENABLED` |
| **Trous compose/deploy (§4)** : ship `infrastructure/`, nginx prod+TLS, domaine, profils, `.env`, cert bootstrap | ✅ **corrigés (#131)** |
| **Instance EC2 + EIP + DNS A-record** | ❌ à créer (toi) |
| Secrets GitHub repo (`EC2_HOST`, …) + `DEPLOY_ENABLED` | ❌ à poser (toi) |

**Conclusion** : le **code est prêt** (#131). Il ne reste que de l'**ops AWS côté toi** :
(a) lancer l'instance + EIP + DNS, (b) bootstrap host (`setup.sh`) + cert
(`init-letsencrypt.sh`), (c) poser les secrets repo + armer `DEPLOY_ENABLED` (§5).

---

## 2. Architecture cible (comment ça doit être structuré)

```
                Internet  (HTTPS 443 / HTTP 80→redirect)
                    │
        Route53  valeriandarmente.dev ──A──► EIP
                    │
            ┌───────▼─────────  EC2 t3.small (eu-west-1, Ubuntu 22.04)  ───────────┐
            │  instance profile : fxvol-ec2-instance-profile (SSM read + KMS)       │
            │  SG fxvol-ec2-sg : 80/443 public, 22 fermé (SSM Session Manager)      │
            │  /opt/fxvol/ : docker-compose.yml + .env + infrastructure/ + obs/     │
            │                                                                       │
            │  docker compose (réseaux : public / internal / external)             │
            │   ┌─ nginx:alpine  (TLS Let's Encrypt, :80/:443)  [fxvol-public]      │
            │   │     /api/ , /ws/ → api:8000 ;  / → frontend:8080                  │
            │   ├─ frontend (image GHCR)   :8080  [internal]                        │
            │   ├─ api       (image GHCR)  :8000  [public+internal]                 │
            │   ├─ postgres:16-alpine   [internal]   vol postgres_data              │
            │   ├─ redis:7-alpine       [internal]   vol redis_data                 │
            │   ├─ profile engines : market-data, vol-engine, risk-engine,          │
            │   │                     db-writer, execution  [internal(+external)]   │
            │   ├─ profile ib : ib-gateway (gnzsnz)  [internal+external]            │
            │   └─ profile obs : prometheus/cadvisor/loki/promtail/tempo/otel/graf  │
            └───────────────────────────────────────────────────────────────────────┘
                    │ outbound : GHCR (pull images), IB (TWS), ACME, AWS API (SSM/KMS)
```

**Flux de release (CI → prod) :**
```
push main → build.yml → 7 images ghcr.io/<owner>/fx-options-*:sha-<commit> (+ :latest)
tag v*.*.* (ou workflow_dispatch) → deploy.yml (si DEPLOY_ENABLED=true) :
   1. scp docker-compose.yml → /opt/fxvol/
   2. rend /opt/fxvol/.env (secrets repo + tags d'images sha-<commit>)
   3. ssh : docker login ghcr → compose pull → compose up -d → alembic upgrade head
   4. smoke : GET https://<EC2_HOST>/api/v1/health == 200
```

**Images** (`build.yml`, namespace `ghcr.io/<owner>/`) : `fx-options-api`,
`-frontend`, `-market-data`, `-vol-engine`, `-risk-engine`, `-db-writer`,
`-execution`. Tags : `sha-<commit>` (prod, reproductible) + `latest`.

**Secrets** : deploy.yml lit les **GitHub repo secrets** (pas SSM) pour rendre le
`.env`. L'instance profile SSM/KMS sert au runtime host (ex. backups, futur fetch
SSM). Les valeurs vivent dans SSM `/fxvol/prod/*` (source de vérité) → recopier
dans les repo secrets, ou (mieux, évolution) faire fetch SSM par le host.

---

## 3. Ressources AWS déjà en place (rappel, cf. `STATE.md`)

```
Account            552269855056         Region   eu-west-1
KMS CMK            alias/fxvol-secrets  (KeyId bbc7ef4a-0b3e-4019-a7db-4502c4662f30)
SSM secrets        /fxvol/prod/{IB_USERID,IB_PASSWORD,DB_PASSWORD,VNC_PASSWORD,TRADING_MODE}
IAM role EC2       fxvol-ec2-secrets-role  + instance profile fxvol-ec2-instance-profile
Security group     sg-0c96af5e3203ffeec  (80+443 public, 22 fermé → SSM)
SSH keypair        fxvol-ec2-key  (ED25519, key-0ce890402b6ab6a47)
S3 backups         fxvol-backups  (versioning + lifecycle Glacier)
DNS                valeriandarmente.dev  (Route53 hosted zone, déléguée)
Log groups         /fxvol/{api,engines,nginx}  (14j)
SNS alarms         fxvol-alarms  → valeriandarmente@gmail.com
Budget             $10/mo (à monter à $25 une fois l'EC2 lancée)
VPC                default vpc-08e90d78d8401b137 (172.31.0.0/16)
```

---

## 4. ✅ Code EC2-ready (fait via #131)

Les trous compose/deploy qui cassaient un déploiement sont **corrigés sur `main`** (#131) :
- **`deploy.yml`** ship `infrastructure/` + `obs/` au host (les confs bind-montées) +
  rend un **`.env` complet** : `NGINX_CONF_FILE` → conf TLS prod, `LETSENCRYPT_DIR`/
  `CERTBOT_WWW_DIR`, `IB_GATEWAY_IMAGE`, `TRADING_MODE=paper`, `READ_ONLY_API=yes`,
  `COMPOSE_PROFILES` (repo-var → engines/ib opt-in, core par défaut).
- **compose nginx** env-driven : dev = HTTP-only ; prod = `nginx.conf` (TLS) +
  `/etc/letsencrypt` + `/var/www/certbot`.
- **`nginx.conf`** : domaine corrigé `valeriandarmente.dev`.
- **`infrastructure/ec2/setup.sh`** : bootstrap host (docker, ufw, user `fxvol`,
  `/opt/fxvol`, systemd unit, cron renew certbot, cron backup Postgres→S3).
- **`infrastructure/ec2/init-letsencrypt.sh`** : bootstrap du cert TLS (placeholder
  self-signed → émission webroot → reload), résout le deadlock nginx↔certbot.

> **Reste 100 % ops AWS côté toi** (§5) — aucune modif code requise.
> L'app se déploie à la **racine** du domaine (`https://valeriandarmente.dev/`) ;
> le sous-chemin `/fx-volatility-trading-system` n'est pas implémenté (changement séparé).

---

## 5. Procédure de déploiement (pas à pas)

### Étape A — Lancer l'instance EC2 (console ou CLI, profil `itadmin`)
```bash
# AMI Ubuntu 22.04 LTS eu-west-1 (vérifier l'ID courant via SSM public param)
aws ec2 run-instances --region eu-west-1 \
  --image-id $(aws ssm get-parameter --region eu-west-1 \
     --name /aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id \
     --query Parameter.Value --output text) \
  --instance-type t3.small \
  --key-name fxvol-ec2-key \
  --security-group-ids sg-0c96af5e3203ffeec \
  --iam-instance-profile Name=fxvol-ec2-instance-profile \
  --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":30,"VolumeType":"gp3"}}]' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Project,Value=fxvol},{Key=Name,Value=fxvol-prod}]'
```
Puis **EIP** : `aws ec2 allocate-address` + `associate-address` sur l'instance.

### Étape B — DNS
Route53 : A-record `valeriandarmente.dev` → l'EIP (+ A `www` optionnel).
CAA recommandé : `0 issue "letsencrypt.org"`. Vérifier : `dig valeriandarmente.dev`.

### Étape C — Bootstrap host (via SSM Session Manager, port 22 fermé)
```bash
aws ssm start-session --target i-xxxx --region eu-west-1
# sur l'instance — un seul script fait tout (docker, user fxvol, /opt/fxvol,
# ufw, systemd unit, cron renew certbot, cron backup Postgres→S3) :
curl -fsS https://raw.githubusercontent.com/valerian-drmt/fx-volatility-trading-system/main/infrastructure/ec2/setup.sh | sudo bash
```
Vérifier le rôle IAM : `aws sts get-caller-identity` doit montrer
`assumed-role/fxvol-ec2-secrets-role/i-xxx`.

> **Étape D (corriger le code) : déjà faite** — #131 a rendu compose/deploy
> EC2-ready (cf. §4). Rien à coder, on passe directement à l'armement.

### Étape E — Poser les secrets GitHub + armer
```bash
gh secret set EC2_HOST     --body "valeriandarmente.dev"   # ou l'EIP
gh secret set EC2_USER     --body "fxvol"                  # setup.sh crée ce user + possède /opt/fxvol
gh secret set EC2_SSH_KEY  < chemin/clé-privée-fxvol-ec2.pem
gh secret set DB_PASSWORD  --body "<depuis SSM>"
gh secret set VNC_PASSWORD --body "<depuis SSM>"
gh secret set IB_USERID    --body "<depuis SSM>"
gh secret set IB_PASSWORD  --body "<depuis SSM>"
gh variable set DEPLOY_ENABLED --body true   # ARME le workflow
# (optionnel) données live : gh variable set COMPOSE_PROFILES --body "engines,ib"
#   — sinon core seul (api+frontend+nginx+pg+redis), le 1er deploy sûr.
```

### Étape F — Premier déploiement + bootstrap TLS
```bash
gh workflow run deploy-prod      # ship compose+infra → rend .env → login ghcr → pull → up → alembic → smoke
```
⚠️ **Au tout premier run, nginx (conf prod TLS) ne peut pas démarrer sans cert** →
le smoke `https` échoue **une fois** (api/pg/redis/frontend, eux, sont up). Bootstraper
le cert sur le host, puis re-déployer :
```bash
# SSM dans l'instance, le compose + .env + images y sont déjà (du run ci-dessus) :
cd /opt/fxvol && sudo DOMAIN=valeriandarmente.dev EMAIL=valeriandarmente@gmail.com \
  bash infrastructure/ec2/init-letsencrypt.sh        # placeholder → webroot → reload
gh workflow run deploy-prod                          # re-deploy → smoke /api/v1/health = 200 ✅
```
Renouvellements ensuite automatiques (cron `certbot renew` posé par setup.sh).

### Étape G — Setup one-shot IB Gateway (si `COMPOSE_PROFILES` inclut `ib`)
IB = **une seule session par userid** (le login web kick le container — cf. note IB).
Via VNC `127.0.0.1:5900` (tunnel SSM) : Configure → API → Settings : décocher
"localhost only", ajouter les Trusted IPs des engines (`172.20.0.10/11/12`),
Save Settings (persisté dans le volume `ib_gateway_jts`). `READ_ONLY_API=yes` (déjà
dans le `.env`) garde le desk read-only.

---

## 6. Exploitation

- **Redéploy** : re-tag `vX.Y.Z` ou `gh workflow run deploy-prod`.
- **Rollback** : `gh workflow run deploy-prod -f deploy_sha=<ancien-sha>` (déploie
  `sha-<…>` sans re-tag).
- **Logs** : `docker compose logs -f <svc>` sur le host, ou CloudWatch
  `/fxvol/{api,engines,nginx}` (driver awslogs à câbler).
- **Backups Postgres** → S3 `fxvol-backups/postgres/` (cron `pg_dump` + `aws s3 cp` ;
  lifecycle Glacier déjà en place).
- **Cost** : ~$22/mo (t3.small + EBS 30GB + EIP attachée + transfert). Monter le
  budget AWS à $25.
- **Monitoring** : profil `obs` (Grafana) en interne ; alarmes CloudWatch → SNS
  `fxvol-alarms` (CPU/disk/health) à créer.

---

## 7. Sécurité

- **Read-only public** : le desk est public en lecture ; le write (ordres/config) est
  derrière auth (Phase 2 R11) + `READ_ONLY_API=yes` côté IB. Ne pas armer le write
  avant la passe auth.
- **`/dev/*`** : console de validation — **bloquer en prod** au niveau nginx (403) ;
  elle n'a pas d'auth.
- **Port 22 fermé** : admin via SSM Session Manager uniquement.
- **Secrets** : jamais sur disque runner (le `.env` est rendu sur le host via heredoc) ;
  jamais echo (cf. `CLAUDE.md`). SSM = source de vérité.
- **IB** : `paper` jusqu'à décision explicite `live` ; session unique (cf. note IB).

---

## 8. Checklist de mise en ligne

- [ ] §4 trous corrigés (compose ship infra/ + nginx prod + domaine + TLS + profils + .env)
- [ ] EC2 t3.small lancée (instance profile + SG + key) + EIP
- [ ] A-record `valeriandarmente.dev` → EIP, propagé
- [ ] docker + compose installés, `/opt/fxvol/` créé, rôle IAM confirmé
- [ ] cert Let's Encrypt bootstrapé (`certbot certonly --webroot`)
- [ ] repo secrets posés (EC2_HOST/USER/SSH_KEY + 4 secrets app)
- [ ] `DEPLOY_ENABLED=true`
- [ ] `gh workflow run deploy-prod` → smoke `/api/v1/health` 200
- [ ] IB Gateway one-shot Trusted IPs (si profil ib)
- [ ] budget AWS monté à $25, alarmes CloudWatch créées
- [ ] `STATE.md` + `README.md` (§ État global) mis à jour : EC2 = déployé
