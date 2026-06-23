# Déploiement EC2 — fxvol (runbook opérationnel)

> **✅ DÉPLOYÉ ET LIVE** depuis le 2026-06-21 : https://valeriandarmente.dev/ (TLS valide,
> pipeline CD complet). Ce runbook décrit le **flux réel OIDC + SSM** (plus de SSH/port 22),
> comment exploiter le stack, et ce qui reste en ops. Pré-requis AWS (compte, KMS, SSM, IAM,
> SG, DNS, S3) : provisionnés — voir `STATE.md`.

---

## 1. État

| Bloc | État |
|---|---|
| Compte AWS + KMS + SSM secrets + SG + S3 + DNS zone | ✅ provisionné (`STATE.md`) |
| **OIDC** : provider GitHub + role `fxvol-deploy-role` + bucket `fxvol-deploy` + 5 repo vars | ✅ via `provision-deploy-oidc.ps1` |
| Instance EC2 (`i-082e72f0186c9d019`, t3.small, eu-west-1) + EIP + A-record Route53 | ✅ lancée |
| Images GHCR (`build.yml` → 6 images `sha-<commit>` + `:latest` sur chaque push main) | ✅ publiques |
| `deploy.yml` (`deploy-prod`) — CD auto sur push main, gaté `DEPLOY_ENABLED=true` | ✅ armé |
| TLS Let's Encrypt (`init-letsencrypt.sh`, renew cron) | ✅ émis |
| **Sécurité** : `/dev`→404, write derrière auth (`require_write`+login), `READ_ONLY_API=yes` | ✅ (#134/#140) |
| Login prod (SSM `AUTH_SECRET`/`AUTH_PASSWORD_HASH`) | ✅ provisionné + testé |

**Reste en ops** (non bloquant) : alarmes CloudWatch (CPU/disk/health → SNS), budget AWS $10→$25,
driver `awslogs` (logs containers → CloudWatch), vérif cron backup Postgres→S3. Cf. §6.

---

## 2. Architecture cible (comment c'est structuré)

```
                Internet  (HTTPS 443 / HTTP 80→redirect)
                    │
        Route53  valeriandarmente.dev ──A──► EIP
                    │
            ┌───────▼─────────  EC2 t3.small (eu-west-1, Ubuntu 22.04)  ───────────┐
            │  instance profile : fxvol-ec2-instance-profile (SSM read + KMS + S3) │
            │  SG fxvol-ec2-sg : 80/443 public, 22 FERMÉ (admin via SSM Session Mgr)│
            │  /opt/fxvol/ : docker-compose.yml + .env(600) + infrastructure/ + obs/│
            │  systemd : fxvol-compose.service (up au boot)                         │
            │                                                                       │
            │  docker compose (réseaux public / internal / external)               │
            │   ┌─ nginx:alpine (TLS Let's Encrypt, :80/:443) ; /dev→404            │
            │   │     /api/ , /ws/ → api:8000 ;  / → frontend:8080                  │
            │   ├─ frontend (GHCR) :8080 · api (GHCR) :8000 · postgres · redis      │
            │   ├─ profile engines (opt-in via COMPOSE_PROFILES) : market-data,     │
            │   │     vol-engine, risk-engine, db-writer, execution                 │
            │   └─ profile ib : ib-gateway ; profile obs : prometheus/loki/grafana  │
            └───────────────────────────────────────────────────────────────────────┘
                    │ outbound : GHCR (pull), IB (TWS), ACME, AWS API (SSM/KMS/S3)
```

**Flux de release (CD, 100 % automatique sur push main) :**
```
push main → build.yml (build-and-push) → 6 images ghcr.io/valerian-drmt/fx-options-*:sha-<commit> (+ :latest)
          → deploy.yml (deploy-prod) se déclenche via workflow_run (si DEPLOY_ENABLED=true) :
   1. OIDC : assume fxvol-deploy-role (pas de clé AWS stockée)
   2. tar payload (docker-compose.yml + infrastructure/ + obs/) → S3 fxvol-deploy
   3. SSM send-command (AWS-RunShellScript, tourne sous /bin/sh) sur l'instance :
        → fetch payload S3, extrait dans /opt/fxvol, lance infrastructure/ec2/remote-deploy.sh
   4. remote-deploy.sh (host) : rend /opt/fxvol/.env depuis SSM (DB/VNC/IB + AUTH_*),
        docker login ghcr → compose pull → compose up -d → alembic upgrade head
   5. smoke : GET https://valeriandarmente.dev/api/v1/health == 200
```
Port 22 n'est jamais ouvert. Rollback : `gh workflow run deploy-prod -f deploy_sha=<ancien-sha>`.

**Secrets** : tous dans **SSM `/fxvol/prod/*`** (source de vérité, KMS). `remote-deploy.sh` les lit
sur l'host via l'instance role — ils ne transitent jamais par GitHub ni par les params SSM-command.
Les anciens repo secrets `EC2_HOST`/`EC2_USER`/`EC2_SSH_KEY` (flow SSH) sont **morts** (à supprimer).

---

## 3. Ressources AWS (cf. `STATE.md` pour le détail)

```
Account     552269855056              Region   eu-west-1
KMS CMK     alias/fxvol-secrets
SSM secrets /fxvol/prod/{IB_USERID,IB_PASSWORD,DB_PASSWORD,VNC_PASSWORD,TRADING_MODE,
                         AUTH_SECRET,AUTH_PASSWORD_HASH}
IAM (host)  fxvol-ec2-secrets-role + instance profile fxvol-ec2-instance-profile (SSM+KMS+S3)
IAM (CD)    fxvol-deploy-role (OIDC GitHub) — assume par deploy.yml, pas de clé stockée
S3          fxvol-deploy (payload CD)  +  fxvol-backups (dumps Postgres)
SG          fxvol-ec2-sg (sg-0c96af5e3203ffeec) : 80+443 public, 22 fermé → SSM
DNS         valeriandarmente.dev (Route53) → A → EIP
Repo vars   AWS_REGION, DEPLOY_ENABLED, + 3 posées par provision-deploy-oidc.ps1
```

---

## 4. Provisionnement (one-shot, déjà fait)

`infrastructure/aws/provision-deploy-oidc.ps1` (profil AWS `admin`) crée : OIDC provider GitHub,
`fxvol-deploy-role` (trust sur le repo), bucket S3 `fxvol-deploy`, ajoute S3+SSM au host role, pose
les 5 repo vars. `infrastructure/ec2/setup.sh` bootstrappe l'host (docker, user fxvol, `/opt/fxvol`,
systemd `fxvol-compose.service`, cron certbot renew + backup Postgres→S3). `init-letsencrypt.sh`
émet le cert (webroot, chemin canonique idempotent).

---

## 5. Exploitation au quotidien

Tout se pilote depuis le laptop via **`scripts/ops/ec2.ps1`** (SSM, pas de SSH) :
```powershell
.\scripts\ops\ec2.ps1 health            # GET /api/v1/health
.\scripts\ops\ec2.ps1 deploy            # redeploy main HEAD (gh workflow run deploy-prod)
.\scripts\ops\ec2.ps1 deploy -Sha <sha> # rollback sur un commit précis
.\scripts\ops\ec2.ps1 ps | logs <svc> | restart <svc> | up | down   # docker compose sur l'host
.\scripts\ops\ec2.ps1 connect           # shell interactif (SSM Session Manager)
.\scripts\ops\ec2.ps1 instance-stop     # COUPE le coût compute (~$15/mo) ; instance-start pour relancer
```
- **Redéploy** : automatique sur chaque push main. Manuel : `ec2.ps1 deploy` ou re-tag.
- **Logs** : `ec2.ps1 logs <svc>` (host) ; CloudWatch `/fxvol/{api,engines,nginx}` (driver awslogs à câbler).
- **Coût** : ~$22/mo en marche (t3.small + EBS + EIP + transfert). Instance arrêtée ≈ $7-8/mo (EBS+EIP+KMS+Route53).

---

## 6. Reste en ops (non bloquant)

- [ ] Alarmes CloudWatch (CPU/disk/healthcheck) → SNS `fxvol-alarms`
- [ ] Budget AWS $10 → $25
- [ ] Driver `awslogs` sur les containers (logs → CloudWatch, sinon perdus au `down`)
- [ ] Vérifier que le cron backup Postgres→S3 (`fxvol-backups/postgres/`) tourne + tester une restore
- [ ] Supprimer les repo secrets morts `EC2_HOST`/`EC2_USER`/`EC2_SSH_KEY` (legacy SSH)
- [ ] (optionnel) sous-chemin `/fx-volatility-trading-system/` — l'app est à la **racine** aujourd'hui
- [ ] (optionnel) déployer engines+ib (`COMPOSE_PROFILES=engines,ib`) pour de la data live — one-shot IB Trusted IPs

---

## 7. Sécurité (état actuel)

- **Read-only public** : le desk est public en lecture. Le **write** (ordres/config) est gaté par
  `require_write` (cookie auth HMAC, #134/#140) — 401 sans login. Login via SSM `AUTH_SECRET` +
  `AUTH_PASSWORD_HASH`. `READ_ONLY_API=yes` côté IB tant qu'on n'a pas décidé le real-money.
- **`/dev/*`** : `return 404` dans la conf nginx prod (conf dev inchangée).
- **Port 22 fermé** : admin uniquement via SSM Session Manager.
- **Secrets** : jamais sur disque runner, jamais echo (cf. `CLAUDE.md`). SSM = source de vérité.
- **IB** : `paper` jusqu'à décision explicite `live` ; session unique (cf. note IB).
- **OIDC** : pas de clé AWS longue-durée dans GitHub ; le role est assumé par token court OIDC.
