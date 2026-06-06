# AWS State — fxvol infrastructure (snapshot 2026-04-27)

> Source de vérité sur ce qui existe en AWS au 27/04/2026 18h00 CET, après la session bootstrap complète.
> Pour l'état réel-temps, lancer `aws cli` ou consulter la console.
> Note : le déploiement EC2 prod n'a pas été activé — voir `README.md` § État global.

---

## 1. Sécurité — IAM, KMS, secrets

### 1.1 IAM users

| User | MFA | Accès | Usage |
|---|---|---|---|
| `Aegis-root` | ✅ virtual MFA | racine, locked | urgences uniquement (root password reset, billing IAM access) |
| `itadmin` | ✅ MFA | `AdministratorAccess` policy | toutes opérations admin courantes (par défaut) |
| `fxvol-dev` | ✅ MFA | inline policy `fxvol-dev-ssm` | dev local : lit/écrit SSM `/fxvol/prod/*`, decrypt CMK |

#### Access keys

- `Aegis-root` : aucune (root sans access keys)
- `itadmin` : aucune access key permanente (login console + MFA seulement)
- `fxvol-dev` : 1 access key active, stockée dans `~/.aws/credentials` profile `fxvol-dev` côté Windows laptop

### 1.2 IAM role EC2

```
Role name              : fxvol-ec2-secrets-role
Role ID                : AROAYBFOZHFIM6KA3IZAY
Instance profile name  : fxvol-ec2-instance-profile
Instance profile ID    : AIPAYBFOZHFIB46B5H7H5
```

Inline policy attachée : `fxvol-ec2-permissions` (5 statements):

| Sid | Action | Resource |
|---|---|---|
| `ReadSecretsSSM` | `ssm:GetParameter*` | `/fxvol/prod/*` |
| `DecryptCMK` | `kms:Decrypt` | CMK `fxvol-secrets` |
| `S3BackupsWrite` | `s3:PutObject`, `s3:GetObject` | `fxvol-backups/*` (no Delete) |
| `CloudWatchLogs` | `logs:CreateLogStream`, `logs:PutLogEvents`, `logs:DescribeLogStreams` | `/fxvol/*` log groups |
| `SSMSessionManager` | `ssmmessages:*`, `ssm:UpdateInstanceInformation` | `*` |

Pas encore attaché à une EC2 (différé R8).

### 1.3 KMS CMK

```
Alias       : alias/fxvol-secrets
Key ID      : bbc7ef4a-0b3e-4019-a7db-4502c4662f30
Region      : eu-west-1
Rotation    : annuelle, prochaine 23/04/2027
Tags        : Project=fxvol
```

**Key policy hardenée (Option A appliquée le 27/04)** : 3 statements distincts au lieu de la policy par défaut.

| Sid | Principal | Actions autorisées |
|---|---|---|
| `EnableRootAccountControl` | root account | `kms:*` (filet de sécurité) |
| `AllowKeyAdministrationByItadmin` | user `itadmin` | actions admin (Create*, Disable*, Delete*, ScheduleKeyDeletion, UpdateAlias, etc.) **mais PAS Encrypt/Decrypt** |
| `AllowKeyUsageByFxvolDevAndEC2Role` | user `fxvol-dev` + role `fxvol-ec2-secrets-role` | `Encrypt`, `Decrypt`, `ReEncrypt*`, `GenerateDataKey*`, `DescribeKey` |

→ Séparation admin / users : `itadmin` peut gérer le cycle de vie de la clé mais pas chiffrer/déchiffrer. Defense-in-depth contre une compromission `itadmin`.

Backup de la policy d'origine : `C:\aws-bootstrap-fxvol\fxvol-kms-key-policy-backup-2026-04-27.json`

### 1.4 SSM Parameter Store

5 paramètres sous `/fxvol/prod/*`, tous tagués `Project=fxvol` :

| Name | Type | Encrypted by | Valeur |
|---|---|---|---|
| `/fxvol/prod/IB_USERID` | SecureString | `alias/fxvol-secrets` | (vraies creds) |
| `/fxvol/prod/IB_PASSWORD` | SecureString | `alias/fxvol-secrets` | (vraies creds) |
| `/fxvol/prod/DB_PASSWORD` | SecureString | `alias/fxvol-secrets` | (vraies creds) |
| `/fxvol/prod/VNC_PASSWORD` | SecureString | `alias/fxvol-secrets` | (vraies creds) |
| `/fxvol/prod/TRADING_MODE` | String (plain) | n/a | `paper` |

### 1.5 SSH keypair

```
Name        : fxvol-ec2-key
Type        : ED25519
ID          : key-0ce890402b6ab6a47
Fingerprint : SHA256:XqDbVllioryGFdkmKA4ZX7D+W9qVl0Mv7VR8tz9O4so
Tags        : Project=fxvol
```

**Stockage de la clé privée** :
- 1Password vault — item `fxvol-ec2` type SSH Key
- 1Password SSH Agent activé sur Windows (pipe `\\.\pipe\openssh-ssh-agent`)
- Service Windows `ssh-agent` désactivé (pour éviter conflit avec 1Password)
- Fichiers locaux `~/.ssh/fxvol-ec2{,.pub}` supprimés du disque (Watchtower confirme "All clear")
- Config `~/.ssh/config` contient `IdentityAgent "\\\\.\\pipe\\openssh-ssh-agent"`

→ Clé jamais en clair sur disque, accès uniquement via biométrie / master password 1Password.

---

## 2. Storage

### 2.1 S3 bucket — fxvol-backups

```
Bucket name              : fxvol-backups
Region                   : eu-west-1
Versioning               : Enabled
Encryption               : SSE-S3 (AES-256), Bucket Key Enabled
Public Access Block      : tout coché (4/4 settings)
Tags                     : Project=fxvol
Created                  : 2026-04-27 12:34 UTC+2
```

### 2.2 Lifecycle rule — ArchiveOldBackups

```
Rule name                : ArchiveOldBackups
Status                   : Enabled
Filter prefix            : postgres/
Transition               : Standard → Glacier Instant Retrieval à 30 jours
Expiration current       : 365 jours
Noncurrent versions      : delete 90 jours après noncurrent, retain 1 newer
Multipart uploads        : delete incomplete après 7 jours
Delete markers           : delete expired (auto-géré avec Expire current)
```

**Coût attendu** : $0 tant que vide. Steady state R8 : ~$0.20/mo (1 backup/jour × 100MB × 30j Standard + 11 mois Glacier IR).

---

## 3. Network

### 3.1 VPC

```
VPC ID          : vpc-08e90d78d8401b137 (default VPC eu-west-1)
IPv4 CIDR       : 172.31.0.0/16
État            : default, aucune configuration custom
```

### 3.2 Security group fxvol-ec2-sg

```
SG ID          : sg-0c96af5e3203ffeec
Name           : fxvol-ec2-sg
VPC            : vpc-08e90d78d8401b137
Description    : FX vol stack: HTTPS public, SSH via SSM only
Tags           : Project=fxvol
```

**Inbound rules** (2) :

| Rule ID | Type | Protocol | Port | Source | Description |
|---|---|---|---|---|---|
| `sgr-04327b9c5ad83af29` | HTTPS | TCP | 443 | 0.0.0.0/0 | HTTPS public access |
| `sgr-02d5803c9aafe07e1` | HTTP | TCP | 80 | 0.0.0.0/0 | HTTP for ACME challenge and redirect to HTTPS |

**Outbound rules** (1) : All traffic / All ports / 0.0.0.0/0 — `Allow all outbound (AWS API, GHCR, IB, ACME)`.

→ **Port 22 non ouvert**. SSH d'urgence via SSM Session Manager (`aws ssm start-session --target i-xxx`).

---

## 4. Observability

### 4.1 CloudWatch Log groups

| Log group | Retention | Tags |
|---|---|---|
| `/fxvol/api` | 14 jours | Project=fxvol |
| `/fxvol/engines` | 14 jours | Project=fxvol |
| `/fxvol/nginx` | 14 jours | Project=fxvol |

→ Destinés à recevoir les stdout/stderr des containers Docker via `--log-driver awslogs --log-opt awslogs-group=/fxvol/api`.

→ Coût : $0 tant que rien n'écrit. Steady state R8 : <$1/mo.

### 4.2 SNS topic — fxvol-alarms

```
Topic name              : fxvol-alarms
Display name            : FX Vol Alarms
Type                    : Standard
Tags                    : Project=fxvol
Subscription            : email valeriandarmente@gmail.com (Status: Confirmed)
```

→ À utiliser comme target SNS dans les CloudWatch Alarms qui seront créées en R8 (CPU EC2, disk full, healthcheck KO).

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

**Alarmes attachées** :

| # | Threshold | Type | Trigger | Email |
|---|---|---|---|---|
| 1 | 80% ($8) | % of budgeted amount | Actual cost | valeriandarmente@gmail.com |
| 2 | 100% ($10) | % of budgeted amount | Forecasted cost | valeriandarmente@gmail.com |

→ À monter à $25/mo en R8 quand l'EC2 démarre.

### 5.2 Cost Anomaly Detection

```
Monitor                 : Default-Services-Monitor (auto-créé AWS, monitor type AWS services)
Monitor ARN             : arn:aws:ce::552269855056:anomalymonitor/ebf620e5-6f39-4a96-914c-7181bf3ae850
Subscription            : Default-Services-Subscription
Subscription ARN        : arn:aws:ce::552269855056:anomalysubscription/10f8f98a-f42e-4733-9196-1246c5e033f5
Threshold               : $5 AND 40% (les 2 conditions doivent être vraies)
Frequency               : Daily summaries
Email                   : valeriandarmente@gmail.com
```

→ Détecte les sauts inattendus par service AWS, complète le Budget global.

---

## 6. DNS — valeriandarmente.dev

### 6.1 Domaine

```
Domain                  : valeriandarmente.dev
Registrar               : GoDaddy
Achat                   : 27/04/2026 (1 an, 17.05€)
Expiration              : 27/04/2027
Auto-renewal            : ON
WHOIS Privacy           : ON
Domain Lock             : ON
```

→ TLD `.dev` = HSTS preload forcé par Google Registry. HTTPS obligatoire dès R8 (Let's Encrypt en R8).

### 6.2 Route 53 hosted zone

```
Domain                  : valeriandarmente.dev
Type                    : Public hosted zone
Tags                    : Project=fxvol
```

**4 nameservers AWS attribués** (à utiliser tels quels dans GoDaddy) :

```
ns-1239.awsdns-26.org
ns-603.awsdns-11.net
ns-4.awsdns-00.com
ns-1801.awsdns-33.co.uk
```

### 6.3 Délégation GoDaddy → AWS

Configurée le 27/04/2026 ~14h CET. Les 4 nameservers AWS sont définis comme custom nameservers dans GoDaddy.

**Propagation DNS** : confirmée en moins de 2h via test `nslookup -type=NS valeriandarmente.dev 8.8.8.8` qui retourne les 4 nameservers AWS.

### 6.4 Records DNS actuels

| Type | Name | Value | Notes |
|---|---|---|---|
| NS | valeriandarmente.dev | 4 nameservers AWS (cf. ci-dessus) | géré par AWS, ne pas toucher |
| SOA | valeriandarmente.dev | `ns-1239.awsdns-26.org. awsdns-hostmaster.amazon.com. 1 7200 900 1209600 86400` | géré par AWS, ne pas toucher |

→ Records A/CAA pour pointer vers l'EC2 = à ajouter si un déploiement EC2 reprend (pas planifié pour l'instant).

---

## 7. eu-west-3 (Paris)

État après nettoyage du 27/04/2026 :

```
EC2 instances           : 0
EC2 volumes             : 0
EC2 snapshots           : 0
EC2 Elastic IPs         : 0
RDS DB instances        : 0
RDS manual snapshots    : 0
RDS retained backups    : 0
RDS system snapshots    : 0 (les 3 résiduels purgés en backend)
NAT Gateways            : 0
CloudFormation stacks   : 0
ECS / EKS clusters      : 0
CloudWatch log groups   : 0
Secrets Manager secrets : 0
S3 buckets              : 0
Lambda functions        : 0
```

→ Région complètement vide. Économie réalisée : ~$15/mo de free tier credits qui se vidaient sur la `livetrading-db` orpheline de la formation DevOps.

---

## 8. Coût mensuel actuel

| Service | Coût/mo |
|---|---|
| KMS CMK `fxvol-secrets` | $1.00 |
| Route 53 hosted zone `valeriandarmente.dev` | $0.50 |
| SSM Parameter Store (5 params Standard) | $0 |
| S3 bucket vide | $0 |
| 3 CloudWatch log groups vides | $0 |
| SNS topic + 1 email subscription | $0 |
| Budget + Cost Anomaly | $0 |
| **Total maintenant** | **~$1.50/mo** |

Après R8 :

| Service additionnel | Coût/mo |
|---|---|
| EC2 t3.small (24/7, eu-west-1) | ~$15 |
| EBS gp3 30 GB (volume root EC2) | ~$2.50 |
| EIP attachée | $0 |
| Data transfer out (estimation API JSON léger) | ~$1 |
| CloudWatch Logs ingestion (estimation 1GB/mo) | ~$0.50 |
| S3 Standard backups (~3GB) | ~$0.10 |
| Domain renewal annualisé (17€/12) | ~$1.60 |
| **Total post-R8** | **~$22/mo** |

---

## 9. Fichiers locaux importants (côté laptop Windows)

```
C:\aws-bootstrap-fxvol\
├── cmk-info                                       # KeyId + ARN public
├── fxvol-dev-policy.json                         # backup policy IAM dev (ancienne)
├── fxvol-ec2-policy.json                         # backup policy role EC2 (ancienne)
├── fxvol-ec2-trust-policy.json                   # trust policy EC2
├── fxvol-kms-key-policy-backup-2026-04-27.json   # backup key policy AVANT hardening
└── fxvol-ec2-ssm-read-backup-2026-04-27.json     # backup ancienne policy role EC2

C:\Users\Valerian\.ssh\
└── config                                         # IdentityAgent → 1Password pipe
```

→ La clé privée SSH n'est plus sur disque, elle vit uniquement dans 1Password vault.

---

## 10. Items mineurs en attente

- [ ] Activer "IAM user and role access to Billing" depuis root (pour que `itadmin` puisse voir Cost Explorer sans Access Denied)
- [ ] Vérifier dans 24h que les 3 system snapshots résiduels eu-west-3 ont disparu de la console (purge backend déjà initiée, $0 facturé)
- [ ] Phase D GHCR settings : à activer post-R6 quand premier push d'image arrive

→ Tous non-bloquants pour R8.

---

**Dernière mise à jour** : 2026-04-27 18:00 CET — fin de session bootstrap complète.
