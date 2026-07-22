# `infrastructure/aws/` — index

> Documentation of the AWS infrastructure for the fxvol project.
> **Read this first.**

---

## Global state

**Status of record lives in `STATE.md`** (single source of truth for what exists).
Summary:

| Item | Status | Reference |
|---|---|---|
| Security hardening (KMS, IAM, MFA) | ✅ done | `STATE.md` § 1 |
| Storage backups (S3) | ✅ done | `STATE.md` § 2 |
| Network (Security group, SSH key) | ✅ done | `STATE.md` § 3 |
| Observability (CloudWatch, SNS) | ✅ done | `STATE.md` § 4 |
| Cost protection (Budget, Anomaly) | ✅ done | `STATE.md` § 5 |
| DNS (domain, hosted zone, delegation) | ✅ done | `STATE.md` § 6 |
| **EC2 instance + EIP + DNS A-record** | ✅ provisioned (`i-082e72f0186c9d019`) | `STATE.md` § 0, `EC2_DEPLOYMENT.md` |
| CD pipeline (`deploy.yml`, OIDC + SSM) | ⛔ restored on `main` but **gated OFF** (`DEPLOY_ENABLED=false`) | `EC2_DEPLOYMENT.md` |
| Site serving | ❌ **not serving** as of 2026-07-17 (CloudFront 504 observed) | `STATE.md` § 0 |
| GHCR setup (package visibility) | ✅ done by default | `public` repo → public packages |

> **EC2 deployment note**: the stack was deployed to prod EC2 in June 2026 (single URL
> `valeriandarmente.dev`, app under the `/fx-volatility-trading-system` subpath, direct
> Route53 A → EIP without CloudFront, core-only compose profile on t3.small). The CD
> pipeline is intact on `main` but disarmed: set the `DEPLOY_ENABLED` repo var (plus the
> EC2 secrets) to arm it. As of 2026-07-17 the site is not serving. Operating procedure:
> `EC2_DEPLOYMENT.md`; Postgres restore procedure: `../ec2/RESTORE.md`.

---

## What to read depending on the need

### If you want to understand **what exists right now**

→ `STATE.md` — precise snapshot of all provisioned AWS resources.

### If you are setting up an AWS account from scratch

→ `SETUP.md` — KMS + SSM + IAM users plan.

### If you are provisioning the secrets into SSM

→ `secrets-bootstrap.md` — SSM Parameter Store + KMS encryption procedure.

### If you are operating or redeploying the EC2 stack

→ `EC2_DEPLOYMENT.md` — OIDC + SSM runbook (no SSH), driven by `scripts/aws/ec2.ps1`.

---

## Update convention

For every AWS session that changes the infra:

1. Update `STATE.md` (resources that actually exist)
2. Update this `README.md` if the file organization changes

---

## AWS account

```
Account ID    : 552269855056
Target region : eu-west-1 (Ireland)
Project tag   : Project=fxvol (on all resources)
```

---

## Critical resources (identifiers to know)

```
KMS CMK           alias/fxvol-secrets, KeyId bbc7ef4a-0b3e-4019-a7db-4502c4662f30
S3 backup bucket  fxvol-backups
Security group    sg-0c96af5e3203ffeec
SSH keypair       fxvol-ec2-key (ID key-0ce890402b6ab6a47)
IAM role          fxvol-ec2-secrets-role + instance profile fxvol-ec2-instance-profile
SNS alert topic   fxvol-alarms
Domain            valeriandarmente.dev (Route 53 hosted zone, delegated from GoDaddy)
```

---

## Current monthly cost

| Period | Active resources | Cost |
|---|---|---|
| Instance running | EC2 t3.small + EBS + EIP + KMS + Route 53 | ~$22/mo |
| Instance stopped | EBS + EIP + KMS + Route 53 hosted zone | ~$7-8/mo |

Current budget cap: **$10/mo** with an alert at 80% (raise to $25 while the instance runs).

---

## External links

- **AWS console**: https://console.aws.amazon.com (login via `itadmin` or `fxvol-dev`, never root)
- **AWS region eu-west-1**: https://eu-west-1.console.aws.amazon.com
- **Route 53**: https://us-east-1.console.aws.amazon.com/route53/v2/hostedzones (Route 53 is global but the console opens in us-east-1)
- **AWS pricing calculator**: https://calculator.aws

---

**Last update**: 2026-07-18 — English translation + deployment status reconciliation.
