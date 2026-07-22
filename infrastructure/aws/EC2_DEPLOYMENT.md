# EC2 deployment — fxvol (operational runbook)

> **Status of record lives in `STATE.md`** — this file is the *procedure* runbook.
> Summary as of 2026-07-17: the AWS resources and the EC2 instance are provisioned,
> the CD pipeline (`deploy.yml`) is restored on `main` but **gated OFF**
> (`DEPLOY_ENABLED` repo var is false, so tags/pushes skip cleanly), and the site
> is currently **not serving** (a CloudFront 504 was observed at the public URL on
> 2026-07-17). The v1 topology of record is a **direct Route53 A record → EIP**
> (no CloudFront), single URL `valeriandarmente.dev` with the app under the
> `/fx-volatility-trading-system` subpath, **core-only** compose profile on t3.small.
> This runbook describes the **OIDC + SSM flow** (no SSH / port 22), how to operate
> the stack, and what remains in ops. AWS prerequisites (account, KMS, SSM, IAM,
> SG, DNS, S3): provisioned — see `STATE.md`.

---

## 1. State

| Block | State |
|---|---|
| AWS account + KMS + SSM secrets + SG + S3 + DNS zone | ✅ provisioned (`STATE.md`) |
| **OIDC**: GitHub provider + `fxvol-deploy-role` role + `fxvol-deploy` bucket + 5 repo vars | ✅ via `provision-deploy-oidc.ps1` |
| EC2 instance (`i-082e72f0186c9d019`, t3.small, eu-west-1) + EIP + Route53 A-record | ✅ launched |
| GHCR images (`build.yml` → 6 `sha-<commit>` images + `:latest` on each main push) | ✅ public |
| `deploy.yml` (`deploy-prod`) — auto CD on main push, gated on `DEPLOY_ENABLED=true` | ⛔ **gated OFF** (`DEPLOY_ENABLED` false — arm it to deploy) |
| TLS Let's Encrypt (`init-letsencrypt.sh`, renew cron) | ✅ issued |
| **Security**: `/dev`→404, write behind auth (`require_write`+login), `READ_ONLY_API=yes` | ✅ (#134/#140) |
| Prod login (SSM `AUTH_SECRET`/`AUTH_PASSWORD_HASH`) | ✅ provisioned + tested |
| Site serving | ❌ **down as of 2026-07-17** (CloudFront 504 observed at the URL) |

**Remaining ops items** (non-blocking): CloudWatch alarms (CPU/disk/health → SNS), AWS budget $10→$25,
`awslogs` driver (container logs → CloudWatch), verify the Postgres→S3 backup cron. Cf. §6.

---

## 2. Target architecture (how it is structured)

```
                Internet  (HTTPS 443 / HTTP 80→redirect)
                    │
        Route53  valeriandarmente.dev ──A──► EIP        (v1: direct, no CloudFront)
                    │
            ┌───────▼─────────  EC2 t3.small (eu-west-1, Ubuntu 22.04)  ───────────┐
            │  instance profile : fxvol-ec2-instance-profile (SSM read + KMS + S3) │
            │  SG fxvol-ec2-sg : 80/443 public, 22 CLOSED (admin via SSM Session Mgr)│
            │  /opt/fxvol/ : docker-compose.yml + .env(600) + infrastructure/ + obs/│
            │  systemd : fxvol-compose.service (up at boot)                         │
            │                                                                       │
            │  docker compose (public / internal / external networks)              │
            │   ┌─ nginx:alpine (TLS Let's Encrypt, :80/:443) ; /dev→404            │
            │   │     /api/ , /ws/ → api:8000 ;  / → frontend:8080                  │
            │   ├─ frontend (GHCR) :8080 · api (GHCR) :8000 · postgres · redis      │
            │   ├─ engines profile (opt-in via COMPOSE_PROFILES) : market-data,     │
            │   │     vol-engine, risk-engine, db-writer, execution                 │
            │   └─ ib profile : ib-gateway ; obs profile : prometheus/loki/grafana  │
            └───────────────────────────────────────────────────────────────────────┘
                    │ outbound : GHCR (pull), IB (TWS), ACME, AWS API (SSM/KMS/S3)
```

The deployed profile is **core-only** (nginx, frontend, api, postgres, redis) on the
t3.small; the engines / ib / obs profiles are opt-in and not part of the v1 footprint.

**Release flow (CD — automatic on main push once `DEPLOY_ENABLED=true`):**
```
push main → build.yml (build-and-push) → 6 images ghcr.io/valerian-drmt/fx-options-*:sha-<commit> (+ :latest)
          → deploy.yml (deploy-prod) triggers via workflow_run (only if DEPLOY_ENABLED=true) :
   1. OIDC : assume fxvol-deploy-role (no stored AWS key)
   2. tar payload (docker-compose.yml + infrastructure/ + obs/) → S3 fxvol-deploy
   3. SSM send-command (AWS-RunShellScript, runs under /bin/sh) on the instance :
        → fetch the S3 payload, extract into /opt/fxvol, run infrastructure/ec2/remote-deploy.sh
   4. remote-deploy.sh (host) : renders /opt/fxvol/.env from SSM (DB/VNC/IB + AUTH_*),
        docker login ghcr → compose pull → compose up -d → alembic upgrade head
   5. smoke : GET https://valeriandarmente.dev/fx-volatility-trading-system/api/v1/health
        must return API JSON (a bare 200 also passes on the SPA fallback — assert the body)
```
Port 22 is never open. Rollback: `gh workflow run deploy-prod -f deploy_sha=<old-sha>`.

**Secrets**: all in **SSM `/fxvol/prod/*`** (source of truth, KMS). `remote-deploy.sh` reads them
on the host through the instance role — they never transit through GitHub nor the SSM-command params.
The old repo secrets `EC2_HOST`/`EC2_USER`/`EC2_SSH_KEY` (SSH flow) are **dead** (to be deleted).

---

## 3. AWS resources (cf. `STATE.md` for the detail)

```
Account     552269855056              Region   eu-west-1
KMS CMK     alias/fxvol-secrets
SSM secrets /fxvol/prod/{IB_USERID,IB_PASSWORD,DB_PASSWORD,VNC_PASSWORD,TRADING_MODE,
                         AUTH_SECRET,AUTH_PASSWORD_HASH}
IAM (host)  fxvol-ec2-secrets-role + instance profile fxvol-ec2-instance-profile (SSM+KMS+S3)
IAM (CD)    fxvol-deploy-role (GitHub OIDC) — assumed by deploy.yml, no stored key
S3          fxvol-deploy (CD payload)  +  fxvol-backups (Postgres dumps)
SG          fxvol-ec2-sg (sg-0c96af5e3203ffeec) : 80+443 public, 22 closed → SSM
DNS         valeriandarmente.dev (Route53) → A → EIP
Repo vars   AWS_REGION, DEPLOY_ENABLED, + 3 set by provision-deploy-oidc.ps1
```

---

## 4. Provisioning (one-shot, already done)

`infrastructure/aws/provision-deploy-oidc.ps1` (AWS profile `admin`) creates: the GitHub OIDC
provider, `fxvol-deploy-role` (trust on the repo), the S3 bucket `fxvol-deploy`, adds S3+SSM to
the host role, sets the 5 repo vars. `infrastructure/ec2/setup.sh` bootstraps the host (docker,
fxvol user, `/opt/fxvol`, systemd `fxvol-compose.service`, certbot renew cron + Postgres→S3
backup cron). `init-letsencrypt.sh` issues the cert (webroot, idempotent canonical path).

---

## 5. Day-to-day operation

Everything is driven from the laptop via **`scripts/aws/ec2.ps1`** (SSM, no SSH):
```powershell
.\scripts\ops\ec2.ps1 health            # GET /api/v1/health
.\scripts\ops\ec2.ps1 deploy            # redeploy main HEAD (gh workflow run deploy-prod)
.\scripts\ops\ec2.ps1 deploy -Sha <sha> # rollback to a specific commit
.\scripts\ops\ec2.ps1 ps | logs <svc> | restart <svc> | up | down   # docker compose on the host
.\scripts\ops\ec2.ps1 connect           # interactive shell (SSM Session Manager)
.\scripts\ops\ec2.ps1 instance-stop     # CUTS the compute cost (~$15/mo) ; instance-start to relaunch
```
- **Redeploy**: automatic on each main push *when `DEPLOY_ENABLED=true`*. Manual: `ec2.ps1 deploy` or re-tag.
- **Logs**: `ec2.ps1 logs <svc>` (host) ; CloudWatch `/fxvol/{api,engines,nginx}` (awslogs driver still to wire).
- **Nginx config gotcha**: the nginx conf is bind-mounted — after a conf change,
  `ec2.ps1 restart nginx` is required for it to apply.
- **Cost**: ~$22/mo while running (t3.small + EBS + EIP + transfer). Stopped instance ≈ $7-8/mo (EBS+EIP+KMS+Route53).

---

## 6. Remaining ops items (non-blocking)

- [ ] CloudWatch alarms (CPU/disk/healthcheck) → SNS `fxvol-alarms`
- [ ] AWS budget $10 → $25
- [ ] `awslogs` driver on the containers (logs → CloudWatch, otherwise lost on `down`)
- [ ] Verify the Postgres→S3 backup cron (`fxvol-backups/postgres/`) runs + test a restore —
      restore procedure: `infrastructure/ec2/RESTORE.md`
- [ ] Delete the dead repo secrets `EC2_HOST`/`EC2_USER`/`EC2_SSH_KEY` (legacy SSH)
- [ ] (optional) deploy engines+ib (`COMPOSE_PROFILES=engines,ib`) for live data — one-shot IB Trusted IPs

---

## 7. Security (current state)

- **Read-only public**: the desk is publicly readable. **Write** (orders/config) is gated by
  `require_write` (HMAC cookie auth, #134/#140) — 401 without login. Login via SSM `AUTH_SECRET` +
  `AUTH_PASSWORD_HASH`. `READ_ONLY_API=yes` on the IB side until real-money is explicitly decided.
- **`/dev/*`**: `return 404` in the prod nginx conf (dev conf unchanged).
- **Port 22 closed**: admin only via SSM Session Manager.
- **Secrets**: never on the runner disk, never echoed. SSM = source of truth.
- **IB**: `paper` until an explicit `live` decision; single session (cf. the IB note).
- **OIDC**: no long-lived AWS key in GitHub; the role is assumed with a short-lived OIDC token.
