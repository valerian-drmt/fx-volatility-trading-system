# AWS State — fxvol infrastructure

> Source of truth for what exists in AWS. Base snapshot: 2026-04-27 18:00 CET
> (end of the full bootstrap session), § 0 tracks the deltas since.
> For the real-time state, run the `aws` CLI or check the console.

## 0. Status of record (delta since the 2026-04-27 snapshot)

**As of 2026-07-17:**

- EC2 instance **`i-082e72f0186c9d019`** (t3.small, eu-west-1) launched + EIP +
  Route53 A-record. v1 topology of record: **direct Route53 A → EIP, no CloudFront**.
  Single URL **`valeriandarmente.dev`**, app under the `/fx-volatility-trading-system`
  subpath. Core-only compose profile (nginx, frontend, api, postgres, redis).
- CD is **OIDC + SSM** (no SSH): GitHub OIDC provider + role **`fxvol-deploy-role`** +
  S3 bucket **`fxvol-deploy`**. The `deploy.yml` pipeline is restored on `main` but
  **gated OFF** — repo var `DEPLOY_ENABLED` is false, so pushes/tags skip cleanly.
  Arm it with the repo var + EC2 secrets to deploy.
- 2 SSM params added since bootstrap: **`AUTH_SECRET`**, **`AUTH_PASSWORD_HASH`**
  (write auth), plus **`FRED_API_KEY`** and **`REDIS_PASSWORD`** used by the local
  loaders. TLS Let's Encrypt issued.
- **Site currently NOT serving**: a CloudFront 504 was observed at the public URL on
  2026-07-17. The instance/stack must be checked (`scripts/aws/ec2.ps1 instance-status`,
  then `health`) before claiming the site is live.
- Real cost ≈ **$22/mo** while running (cf. §8); ≈ $7-8/mo with the instance stopped.
- The old repo secrets `EC2_HOST/USER/SSH_KEY` (SSH flow) are **dead**.
- Operating runbook: `EC2_DEPLOYMENT.md`. Postgres restore: `../ec2/RESTORE.md`.

The rest of this file is the 2026-04-27 bootstrap snapshot.

---

## 1. Security — IAM, KMS, secrets

### 1.1 IAM users

| User | MFA | Access | Usage |
|---|---|---|---|
| `Aegis-root` | ✅ virtual MFA | root, locked | emergencies only (root password reset, billing IAM access) |
| `itadmin` | ✅ MFA | `AdministratorAccess` policy | all routine admin operations (default) |
| `fxvol-dev` | ✅ MFA | inline policy `fxvol-dev-ssm` | local dev: reads/writes SSM `/fxvol/prod/*`, decrypts the CMK |

#### Access keys

- `Aegis-root`: none (root without access keys)
- `itadmin`: no permanent access key (console login + MFA only)
- `fxvol-dev`: 1 active access key, stored in `~/.aws/credentials` profile `fxvol-dev` on the Windows laptop

### 1.2 EC2 IAM role

```
Role name              : fxvol-ec2-secrets-role
Role ID                : AROAYBFOZHFIM6KA3IZAY
Instance profile name  : fxvol-ec2-instance-profile
Instance profile ID    : AIPAYBFOZHFIB46B5H7H5
```

Attached inline policy: `fxvol-ec2-permissions` (5 statements):

| Sid | Action | Resource |
|---|---|---|
| `ReadSecretsSSM` | `ssm:GetParameter*` | `/fxvol/prod/*` |
| `DecryptCMK` | `kms:Decrypt` | CMK `fxvol-secrets` |
| `S3BackupsWrite` | `s3:PutObject`, `s3:GetObject` | `fxvol-backups/*` (no Delete) |
| `CloudWatchLogs` | `logs:CreateLogStream`, `logs:PutLogEvents`, `logs:DescribeLogStreams` | `/fxvol/*` log groups |
| `SSMSessionManager` | `ssmmessages:*`, `ssm:UpdateInstanceInformation` | `*` |

Attached to the EC2 instance since the June 2026 deployment (cf. § 0).

### 1.3 KMS CMK

```
Alias       : alias/fxvol-secrets
Key ID      : bbc7ef4a-0b3e-4019-a7db-4502c4662f30
Region      : eu-west-1
Rotation    : yearly, next 2027-04-23
Tags        : Project=fxvol
```

**Hardened key policy (Option A applied on 04-27)**: 3 distinct statements instead of the default policy.

| Sid | Principal | Allowed actions |
|---|---|---|
| `EnableRootAccountControl` | root account | `kms:*` (safety net) |
| `AllowKeyAdministrationByItadmin` | user `itadmin` | admin actions (Create*, Disable*, Delete*, ScheduleKeyDeletion, UpdateAlias, etc.) **but NOT Encrypt/Decrypt** |
| `AllowKeyUsageByFxvolDevAndEC2Role` | user `fxvol-dev` + role `fxvol-ec2-secrets-role` | `Encrypt`, `Decrypt`, `ReEncrypt*`, `GenerateDataKey*`, `DescribeKey` |

→ Admin / user separation: `itadmin` can manage the key lifecycle but not encrypt/decrypt. Defense-in-depth against an `itadmin` compromise.

Backup of the original policy: `C:\aws-bootstrap-fxvol\fxvol-kms-key-policy-backup-2026-04-27.json`

### 1.4 SSM Parameter Store

Parameters under `/fxvol/prod/*`, all tagged `Project=fxvol` (5 at bootstrap; see § 0 for the ones added since):

| Name | Type | Encrypted by | Value |
|---|---|---|---|
| `/fxvol/prod/IB_USERID` | SecureString | `alias/fxvol-secrets` | (real creds) |
| `/fxvol/prod/IB_PASSWORD` | SecureString | `alias/fxvol-secrets` | (real creds) |
| `/fxvol/prod/DB_PASSWORD` | SecureString | `alias/fxvol-secrets` | (real creds) |
| `/fxvol/prod/VNC_PASSWORD` | SecureString | `alias/fxvol-secrets` | (real creds) |
| `/fxvol/prod/TRADING_MODE` | String (plain) | n/a | `paper` |

### 1.5 SSH keypair

```
Name        : fxvol-ec2-key
Type        : ED25519
ID          : key-0ce890402b6ab6a47
Fingerprint : SHA256:XqDbVllioryGFdkmKA4ZX7D+W9qVl0Mv7VR8tz9O4so
Tags        : Project=fxvol
```

**Private key storage**:
- 1Password vault — item `fxvol-ec2`, type SSH Key
- 1Password SSH Agent enabled on Windows (pipe `\\.\pipe\openssh-ssh-agent`)
- Windows `ssh-agent` service disabled (to avoid conflicting with 1Password)
- Local files `~/.ssh/fxvol-ec2{,.pub}` deleted from disk (Watchtower confirms "All clear")
- `~/.ssh/config` contains `IdentityAgent "\\\\.\\pipe\\openssh-ssh-agent"`

→ The key is never in cleartext on disk; access only via 1Password biometrics / master password.

---

## 2. Storage

### 2.1 S3 bucket — fxvol-backups

```
Bucket name              : fxvol-backups
Region                   : eu-west-1
Versioning               : Enabled
Encryption               : SSE-S3 (AES-256), Bucket Key Enabled
Public Access Block      : all checked (4/4 settings)
Tags                     : Project=fxvol
Created                  : 2026-04-27 12:34 UTC+2
```

Restore procedure for the Postgres dumps stored here: `infrastructure/ec2/RESTORE.md`.

### 2.2 Lifecycle rule — ArchiveOldBackups

```
Rule name                : ArchiveOldBackups
Status                   : Enabled
Filter prefix            : postgres/
Transition               : Standard → Glacier Instant Retrieval at 30 days
Expiration current       : 365 days
Noncurrent versions      : delete 90 days after noncurrent, retain 1 newer
Multipart uploads        : delete incomplete after 7 days
Delete markers           : delete expired (auto-managed with Expire current)
```

**Expected cost**: $0 while empty. Steady state: ~$0.20/mo (1 backup/day × 100MB × 30d Standard + 11 months Glacier IR).

---

## 3. Network

### 3.1 VPC

```
VPC ID          : vpc-08e90d78d8401b137 (default VPC eu-west-1)
IPv4 CIDR       : 172.31.0.0/16
State           : default, no custom configuration
```

### 3.2 Security group fxvol-ec2-sg

```
SG ID          : sg-0c96af5e3203ffeec
Name           : fxvol-ec2-sg
VPC            : vpc-08e90d78d8401b137
Description    : FX vol stack: HTTPS public, SSH via SSM only
Tags           : Project=fxvol
```

**Inbound rules** (2):

| Rule ID | Type | Protocol | Port | Source | Description |
|---|---|---|---|---|---|
| `sgr-04327b9c5ad83af29` | HTTPS | TCP | 443 | 0.0.0.0/0 | HTTPS public access |
| `sgr-02d5803c9aafe07e1` | HTTP | TCP | 80 | 0.0.0.0/0 | HTTP for ACME challenge and redirect to HTTPS |

**Outbound rules** (1): All traffic / All ports / 0.0.0.0/0 — `Allow all outbound (AWS API, GHCR, IB, ACME)`.

→ **Port 22 not open**. Emergency shell via SSM Session Manager (`aws ssm start-session --target i-xxx`).

---

## 4. Observability

### 4.1 CloudWatch log groups

| Log group | Retention | Tags |
|---|---|---|
| `/fxvol/api` | 14 days | Project=fxvol |
| `/fxvol/engines` | 14 days | Project=fxvol |
| `/fxvol/nginx` | 14 days | Project=fxvol |

→ Intended to receive the Docker containers' stdout/stderr via `--log-driver awslogs --log-opt awslogs-group=/fxvol/api`.

→ Cost: $0 while nothing writes. Steady state: <$1/mo.

### 4.2 SNS topic — fxvol-alarms

```
Topic name              : fxvol-alarms
Display name            : FX Vol Alarms
Type                    : Standard
Tags                    : Project=fxvol
Subscription            : email valeriandarmente@gmail.com (Status: Confirmed)
```

→ To be used as the SNS target of the CloudWatch alarms still to create (EC2 CPU, disk full, healthcheck KO).

---

## 5. Cost protection

### 5.1 Budget — fxvol-monthly-cap

```
Budget name             : fxvol-monthly-cap
Period                  : Monthly recurring
Start                   : Apr 2026
Budgeted amount         : $10/mo
Method                  : Fixed
Scope                   : All AWS services
Aggregate               : Unblended costs
Tags                    : Project=fxvol
```

**Attached alarms**:

| # | Threshold | Type | Trigger | Email |
|---|---|---|---|---|
| 1 | 80% ($8) | % of budgeted amount | Actual cost | valeriandarmente@gmail.com |
| 2 | 100% ($10) | % of budgeted amount | Forecasted cost | valeriandarmente@gmail.com |

→ To raise to $25/mo while the EC2 instance runs.

### 5.2 Cost Anomaly Detection

```
Monitor                 : Default-Services-Monitor (auto-created by AWS, monitor type AWS services)
Monitor ARN             : arn:aws:ce::552269855056:anomalymonitor/ebf620e5-6f39-4a96-914c-7181bf3ae850
Subscription            : Default-Services-Subscription
Subscription ARN        : arn:aws:ce::552269855056:anomalysubscription/10f8f98a-f42e-4733-9196-1246c5e033f5
Threshold               : $5 AND 40% (both conditions must be true)
Frequency               : Daily summaries
Email                   : valeriandarmente@gmail.com
```

→ Detects unexpected per-service AWS jumps, complements the global Budget.

---

## 6. DNS — valeriandarmente.dev

### 6.1 Domain

```
Domain                  : valeriandarmente.dev
Registrar               : GoDaddy
Purchase                : 2026-04-27 (1 year, 17.05€)
Expiration              : 2027-04-27
Auto-renewal            : ON
WHOIS Privacy           : ON
Domain Lock             : ON
```

→ The `.dev` TLD is HSTS-preloaded by Google Registry. HTTPS is mandatory (Let's Encrypt).

### 6.2 Route 53 hosted zone

```
Domain                  : valeriandarmente.dev
Type                    : Public hosted zone
Tags                    : Project=fxvol
```

**4 assigned AWS nameservers** (to use as-is in GoDaddy):

```
ns-1239.awsdns-26.org
ns-603.awsdns-11.net
ns-4.awsdns-00.com
ns-1801.awsdns-33.co.uk
```

### 6.3 GoDaddy → AWS delegation

Configured 2026-04-27 ~14:00 CET. The 4 AWS nameservers are set as custom nameservers in GoDaddy.

**DNS propagation**: confirmed in under 2h via `nslookup -type=NS valeriandarmente.dev 8.8.8.8` returning the 4 AWS nameservers.

### 6.4 DNS records

| Type | Name | Value | Notes |
|---|---|---|---|
| NS | valeriandarmente.dev | 4 AWS nameservers (cf. above) | managed by AWS, do not touch |
| SOA | valeriandarmente.dev | `ns-1239.awsdns-26.org. awsdns-hostmaster.amazon.com. 1 7200 900 1209600 86400` | managed by AWS, do not touch |
| A | valeriandarmente.dev | EIP of `i-082e72f0186c9d019` | added at the June 2026 deployment (cf. § 0) |

---

## 7. eu-west-3 (Paris)

State after the 2026-04-27 cleanup:

```
EC2 instances           : 0
EC2 volumes             : 0
EC2 snapshots           : 0
EC2 Elastic IPs         : 0
RDS DB instances        : 0
RDS manual snapshots    : 0
RDS retained backups    : 0
RDS system snapshots    : 0 (the 3 residual ones purged backend-side)
NAT Gateways            : 0
CloudFormation stacks   : 0
ECS / EKS clusters      : 0
CloudWatch log groups   : 0
Secrets Manager secrets : 0
S3 buckets              : 0
Lambda functions        : 0
```

→ Region fully empty. Savings: ~$15/mo of free-tier credits that were draining on the orphaned `livetrading-db` from the DevOps training.

---

## 8. Monthly cost

Baseline (instance stopped or terminated):

| Service | Cost/mo |
|---|---|
| KMS CMK `fxvol-secrets` | $1.00 |
| Route 53 hosted zone `valeriandarmente.dev` | $0.50 |
| SSM Parameter Store (Standard params) | $0 |
| S3 bucket (near-empty) | $0 |
| 3 empty CloudWatch log groups | $0 |
| SNS topic + 1 email subscription | $0 |
| Budget + Cost Anomaly | $0 |
| **Baseline total** | **~$1.50/mo** |

With the EC2 instance running:

| Additional service | Cost/mo |
|---|---|
| EC2 t3.small (24/7, eu-west-1) | ~$15 |
| EBS gp3 30 GB (EC2 root volume) | ~$2.50 |
| Attached EIP | $0 |
| Data transfer out (light JSON API estimate) | ~$1 |
| CloudWatch Logs ingestion (1GB/mo estimate) | ~$0.50 |
| S3 Standard backups (~3GB) | ~$0.10 |
| Annualized domain renewal (17€/12) | ~$1.60 |
| **Total while running** | **~$22/mo** |

---

## 9. Important local files (Windows laptop side)

```
C:\aws-bootstrap-fxvol\
├── cmk-info                                       # KeyId + public ARN
├── fxvol-dev-policy.json                         # backup of the dev IAM policy (old)
├── fxvol-ec2-policy.json                         # backup of the EC2 role policy (old)
├── fxvol-ec2-trust-policy.json                   # EC2 trust policy
├── fxvol-kms-key-policy-backup-2026-04-27.json   # key policy backup BEFORE hardening
└── fxvol-ec2-ssm-read-backup-2026-04-27.json     # backup of the old EC2 role policy

C:\Users\Valerian\.ssh\
└── config                                         # IdentityAgent → 1Password pipe
```

→ The SSH private key is no longer on disk; it lives only in the 1Password vault.

---

## 10. Minor pending items

- [ ] Enable "IAM user and role access to Billing" from root (so `itadmin` can see Cost Explorer without Access Denied)
- [ ] GHCR settings phase D: enabled by default (public repo → public packages)

→ All non-blocking.

---

**Last update**: 2026-07-18 — English translation + § 0 status-of-record delta.
