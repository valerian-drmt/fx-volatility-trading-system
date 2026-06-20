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

## 4. ⚠️ Trous à boucher dans le code AVANT de déployer

Ces points cassent un déploiement tel quel — à corriger (chacun = petit PR backend/infra) :

### 4.1 `deploy.yml` ne ship que `docker-compose.yml`
Le compose monte des fichiers locaux : `./infrastructure/nginx/*.conf`,
`./infrastructure/postgres/init.sql`, `./infrastructure/redis/redis.conf`, et (profil
obs) `./obs/*`. Ils **n'existent pas** sur le host → `nginx`/`postgres` échouent au mount.
**Fix** : shipper le dossier `infrastructure/` (et `obs/` si profil obs) vers
`/opt/fxvol/` (ajouter un `scp -r infrastructure obs` dans deploy.yml), ou bake les
confs dans les images.

### 4.2 nginx = conf **dev** (HTTP-only), pas de TLS
Le compose monte `infrastructure/nginx/nginx-dev.conf` (`listen 80`, `server_name
localhost`, aucun TLS). Or le smoke deploy est en **https** et le TLD `.dev` force
HSTS/HTTPS. **Fix** : monter `nginx.conf` (prod) + câbler Let's Encrypt (cf. 4.3/4.4).

### 4.3 `nginx.conf` (prod) a le mauvais domaine + chemin cert
Il contient `server_name valerian.dev` et `ssl_certificate
/etc/letsencrypt/live/valerian.dev/…` → **doit être `valeriandarmente.dev`**.
**Fix** : corriger les 3 occurrences `valerian.dev` → `valeriandarmente.dev`.

### 4.4 TLS / Let's Encrypt non câblé dans le compose
Pas de service `certbot`, pas de volume `/etc/letsencrypt`, pas de `root
/var/www/certbot`. **Fix** : ajouter un certbot (ou certbot host) + monter
`letsencrypt:/etc/letsencrypt:ro` et `certbot_www:/var/www/certbot` dans nginx.
Bootstrap initial du cert (1 fois) :
`certbot certonly --webroot -w /var/www/certbot -d valeriandarmente.dev`.

### 4.5 Profils `engines` + `ib` non démarrés au déploiement
`deploy.yml` fait `docker compose up -d` **sans** `--profile`. Or les 5 engines et
ib-gateway sont sur les profils `engines`/`ib` → un déploiement nu = api+frontend+
nginx+pg+redis **sans pipeline data ni IB** (desk vide). **Fix** : `docker compose
--profile engines --profile ib up -d` (+ obs si voulu).

### 4.6 `.env` rendu incomplet
deploy.yml rend `API/FRONTEND/MARKET_DATA/VOL_ENGINE/RISK_ENGINE/DB_WRITER_IMAGE`
mais **pas** `EXECUTION_IMAGE` ni `IB_GATEWAY_IMAGE`, ni `TRADING_MODE`/`READ_ONLY_API`.
Si on active les profils, execution-engine prendrait `fx-options-execution:local`
(absent du host) → fail. **Fix** : ajouter au heredoc `.env` :
`EXECUTION_IMAGE=…/fx-options-execution:<tag>`, `IB_GATEWAY_IMAGE=ghcr.io/gnzsnz/ib-gateway:latest`,
`TRADING_MODE=paper`, `READ_ONLY_API=yes` (desk read-only : bloque tout ordre côté gateway).

### 4.7 (info) L'app se déploie à la **racine** du domaine
`web.Dockerfile` build en base `/` et nginx route `/`. Le sous-chemin
`/fx-volatility-trading-system` (évoqué ailleurs) **n'est pas** implémenté → ce
serait un changement séparé (vite `base` + nginx `location /sous-chemin/`). Pour ce
déploiement, l'app vit sur `https://valeriandarmente.dev/`.

> Recommandation : regrouper 4.1→4.6 dans **un PR `fix(deploy): EC2-ready compose + TLS`**
> avant d'armer. C'est le vrai contenu de la « PR D » du plan R11.

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
# sur l'instance :
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-plugin
sudo usermod -aG docker ubuntu
sudo mkdir -p /opt/fxvol && sudo chown ubuntu:ubuntu /opt/fxvol
# (certbot pour le bootstrap initial du cert, cf. 4.4)
```
Vérifier le rôle : `aws sts get-caller-identity` doit montrer
`assumed-role/fxvol-ec2-secrets-role/i-xxx`.

### Étape D — Corriger les trous §4 (PR infra) puis re-push `main`
`build.yml` reconstruit les 7 images avec les configs corrigées.

### Étape E — Poser les secrets GitHub + armer
```bash
gh secret set EC2_HOST     --body "valeriandarmente.dev"   # ou l'EIP
gh secret set EC2_USER     --body "ubuntu"
gh secret set EC2_SSH_KEY  < chemin/clé-privée-fxvol-ec2.pem
gh secret set DB_PASSWORD  --body "<depuis SSM>"
gh secret set VNC_PASSWORD --body "<depuis SSM>"
gh secret set IB_USERID    --body "<depuis SSM>"
gh secret set IB_PASSWORD  --body "<depuis SSM>"
gh variable set DEPLOY_ENABLED --body true   # ARME le workflow
```

### Étape F — Premier déploiement
```bash
gh workflow run deploy-prod                      # workflow_dispatch
#   ou : git tag v2.0.0 && git push origin v2.0.0
```
Le workflow : scp compose → rend `.env` → pull/up → alembic → smoke `https://…/api/v1/health`.

### Étape G — Setup one-shot IB Gateway (si profil `ib`)
IB = **une seule session par userid** (le login web kick le container — cf. note IB).
Via VNC `127.0.0.1:5900` (tunnel SSM) : Configure → API → Settings : décocher
"localhost only", ajouter les Trusted IPs des engines (`172.20.0.10/11/12`),
Save Settings (persisté dans le volume `ib_gateway_jts`). `READ_ONLY_API=yes` pour
un desk read-only.

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
