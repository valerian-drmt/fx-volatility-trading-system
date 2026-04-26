# AWS Deployment Prep — préparer le terrain avant R8

> **Contexte** : compte AWS en place avec root + IAM `itadmin` + IAM `fxvol-dev`.
> Cible : avoir **tout le terrain AWS prêt** pour qu'au moment où R8 arrive
> (~12/05), la mise en prod = `terraform apply` ou 4 commandes `aws cli`,
> sans découverte ni configuration de dernière minute.
>
> Cette doc complète `SETUP.md` (qui couvre KMS+SSM+SSO pour le dev local).
> Ici on prépare : **domaine, DNS, container registry, IAM EC2 role, S3 backups,
> security group, SSH key, budget alarm**. Tout est faisable maintenant et
> coûte **~$1/mois** tant qu'aucune EC2 ne tourne.

---

## 0. Choix d'architecture confirmés (extraits de `releases/architecture_finale_project/`)

| Élément | Choix | Pourquoi pas l'alternative |
|---|---|---|
| Compute | **1× EC2 t3.small** + docker-compose | ECS/Fargate = overkill pour 1 stack, EKS = $73/mo cluster |
| Container registry | **GHCR** (`ghcr.io/valerian-drmt/*`) | ECR = $0.10/GB-mois + transfert, GHCR gratuit pour repo public |
| TLS | **Let's Encrypt + nginx + certbot** dans le container nginx | ACM = uniquement avec ALB ($16/mo) ou CloudFront |
| DB | **Postgres container** (pas RDS) | RDS db.t3.micro = $13/mo, le container partage l'EC2 = $0 supplémentaire |
| Cache/bus | **Redis container** (pas ElastiCache) | idem, ElastiCache cache.t3.micro = $11/mo |
| Secrets | **SSM Parameter Store** + IAM role | déjà couvert (cf. `SETUP.md`) |
| Domain | **valerian.dev** (Route 53 ou Namecheap) | hardcodé dans architecture docs + nginx config |
| Backups DB | **S3 standard + lifecycle vers Glacier IR** | EBS snapshots = OK aussi mais S3 = portable, 5 GB gratuit |
| Logs | **CloudWatch Logs** (driver docker awslogs) | Loki self-hosted = +30min setup, pas pour 1 EC2 |
| Monitoring | **CloudWatch alarms + SNS email** | Datadog = $15/host, Grafana Cloud = OK mais split tools |
| CI/CD | **GitHub Actions → SSH EC2 → docker compose pull/up** | CodeDeploy = overkill, pas de blue/green pour 1 stack |

**Total prod attendu** : ~$20/mois (EC2 + EBS + Route 53 + transfert).

---

## 1. Inventaire de ce qui existe déjà

D'après ce que tu as :

- ✅ Compte AWS root (à ne plus toucher hors urgence)
- ✅ IAM user `itadmin` (admin, pour créer des ressources)
- ✅ IAM user `fxvol-dev` (lecture SSM, créé pour `SETUP.md`)
- ❓ KMS CMK `alias/fxvol-secrets` — créée ? (cf. `SETUP.md` § 3)
- ❓ 5 params SSM `/fxvol/prod/*` — créés ? (cf. `SETUP.md` § 4)

Si tu as suivi `SETUP.md` jusqu'à la phase 5, le bloc "secrets" est déjà fait.
Cette doc enchaîne sur tout le reste.

---

## 2. Phase A — Domaine + DNS (~30 min, ~$15/an)

C'est l'item à plus longue inertie : si tu veux `valerian.dev` ou
`fxvol.<ton-tld>`, achète-le **maintenant**, propagation DNS = 24-48h le jour
J pour les TLD récents.

### A.1 Acheter le domaine

Deux options :

**Option 1 — Route 53 (tout-AWS)**
```
Console → Route 53 → Registered domains → Register domain
```
- Search `valerian.dev` (TLD `.dev` = $12/an, oblige HTTPS = bien)
- Auto-renew : ON
- Privacy protection : ON (gratuit, masque WHOIS)
- Hosted zone créée automatiquement
- **Avantage** : zéro config DNS, intégration Route 53 native
- **Inconvénient** : transfert vers autre registrar = chiant

**Option 2 — Namecheap / OVH / Gandi + Route 53 hosted zone**
- Acheter chez Namecheap (souvent moins cher : `.dev` ~$10/an)
- Côté AWS : `Route 53 → Hosted zones → Create` → `valerian.dev`
- Dans Namecheap : Custom DNS → coller les 4 nameservers `ns-*.awsdns-*.org`
  fournis par Route 53
- **Avantage** : flexibilité registrar
- **Inconvénient** : 2 dashboards à gérer, +24h propagation initiale

**Recommandation** : Option 1 si tu démarres, Option 2 si tu as déjà des
domaines ailleurs.

### A.2 Records DNS à préparer (à créer **après** EC2 launch)

Note-les dans une checklist, à ajouter le jour J dans Route 53 :

```
A     valerian.dev          → <EIP_EC2>     TTL 300
A     www.valerian.dev      → <EIP_EC2>     TTL 300
CAA   valerian.dev          → 0 issue "letsencrypt.org"   TTL 3600
```

Le record CAA empêche n'importe quelle CA autre que Let's Encrypt d'émettre
un cert pour ton domaine = défense en profondeur.

**Coût Route 53** : $0.50/mois par hosted zone + $0.40 par million de queries
(tu seras à $0.50/mois fixe).

---

## 3. Phase B — IAM role EC2 + instance profile (~10 min, $0)

Tu peux créer le role et l'instance profile maintenant. Quand tu lanceras l'EC2
en R8, tu n'auras qu'à `--iam-instance-profile Name=fxvol-ec2-instance-profile`.

### B.1 Créer le role (CLI, profil `itadmin`)

D'abord la trust policy (autorise EC2 à assumer le role) :

```bash
# Save to /tmp/trust-policy-ec2.json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "ec2.amazonaws.com" },
    "Action": "sts:AssumeRole"
  }]
}
```

```powershell
aws iam create-role `
  --role-name fxvol-ec2-secrets-role `
  --assume-role-policy-document file://trust-policy-ec2.json `
  --description "EC2 instance role: read SSM secrets, push S3 backups, write CloudWatch logs" `
  --profile itadmin
```

### B.2 Attacher la policy SSM read + KMS decrypt

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadSecretsSSM",
      "Effect": "Allow",
      "Action": [
        "ssm:GetParameter",
        "ssm:GetParameters",
        "ssm:GetParametersByPath"
      ],
      "Resource": "arn:aws:ssm:eu-west-1:<ACCOUNT_ID>:parameter/fxvol/prod/*"
    },
    {
      "Sid": "DecryptCMK",
      "Effect": "Allow",
      "Action": "kms:Decrypt",
      "Resource": "arn:aws:kms:eu-west-1:<ACCOUNT_ID>:key/<CMK_KEY_ID>"
    },
    {
      "Sid": "S3BackupsWrite",
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject"],
      "Resource": "arn:aws:s3:::fxvol-backups/*"
    },
    {
      "Sid": "CloudWatchLogs",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "logs:DescribeLogStreams"
      ],
      "Resource": "arn:aws:logs:eu-west-1:<ACCOUNT_ID>:log-group:/fxvol/*"
    },
    {
      "Sid": "SSMSessionManager",
      "Effect": "Allow",
      "Action": [
        "ssmmessages:CreateControlChannel",
        "ssmmessages:CreateDataChannel",
        "ssmmessages:OpenControlChannel",
        "ssmmessages:OpenDataChannel",
        "ssm:UpdateInstanceInformation"
      ],
      "Resource": "*"
    }
  ]
}
```

```powershell
aws iam put-role-policy `
  --role-name fxvol-ec2-secrets-role `
  --policy-name fxvol-ec2-permissions `
  --policy-document file://ec2-policy.json `
  --profile itadmin
```

**Pourquoi `SSMSessionManager`** : permet de te connecter en SSH-less via
`aws ssm start-session --target i-xxx`. Plus besoin d'ouvrir le port 22 sur
internet → security group beaucoup plus restrictif.

### B.3 Créer l'instance profile

```powershell
aws iam create-instance-profile --instance-profile-name fxvol-ec2-instance-profile --profile itadmin
aws iam add-role-to-instance-profile `
  --instance-profile-name fxvol-ec2-instance-profile `
  --role-name fxvol-ec2-secrets-role `
  --profile itadmin
```

✅ **Checkpoint B** : `aws iam get-instance-profile --instance-profile-name fxvol-ec2-instance-profile`
montre le role attaché.

---

## 4. Phase C — S3 bucket pour backups Postgres (~5 min, $0 jusqu'à usage)

```powershell
# 1. Créer le bucket (nom global, doit être unique AWS-wide)
aws s3api create-bucket `
  --bucket fxvol-backups `
  --region eu-west-1 `
  --create-bucket-configuration LocationConstraint=eu-west-1 `
  --profile itadmin

# 2. Bloquer tout accès public
aws s3api put-public-access-block `
  --bucket fxvol-backups `
  --public-access-block-configuration "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true" `
  --profile itadmin

# 3. Activer le versioning (anti-rm accidentel)
aws s3api put-bucket-versioning `
  --bucket fxvol-backups `
  --versioning-configuration Status=Enabled `
  --profile itadmin

# 4. Chiffrement SSE-S3 (gratuit, transparent)
aws s3api put-bucket-encryption `
  --bucket fxvol-backups `
  --server-side-encryption-configuration '{\"Rules\":[{\"ApplyServerSideEncryptionByDefault\":{\"SSEAlgorithm\":\"AES256\"}}]}' `
  --profile itadmin
```

### C.1 Lifecycle policy : Glacier après 30j, delete après 1 an

Crée `s3-lifecycle.json` :

```json
{
  "Rules": [
    {
      "ID": "ArchiveOldBackups",
      "Status": "Enabled",
      "Filter": { "Prefix": "postgres/" },
      "Transitions": [
        { "Days": 30, "StorageClass": "GLACIER_IR" }
      ],
      "Expiration": { "Days": 365 },
      "NoncurrentVersionExpiration": { "NoncurrentDays": 90 }
    }
  ]
}
```

```powershell
aws s3api put-bucket-lifecycle-configuration `
  --bucket fxvol-backups `
  --lifecycle-configuration file://s3-lifecycle.json `
  --profile itadmin
```

**Coût** : $0 tant qu'il est vide. Estimation 1 backup/jour × 100 MB × 30 jours
en S3 standard ($0.023/GB) + 11 mois en Glacier IR ($0.004/GB) = **~$0.10/mois**.

---

## 5. Phase D — Container registry : GHCR (pas ECR)

Pas de ressource AWS à créer. Mais 2 choses à préparer côté GitHub :

### D.1 Activer GHCR pour le repo

- GitHub → repo settings → Actions → General → Workflow permissions
  → "Read and write permissions" (pour que `GITHUB_TOKEN` puisse pusher)

### D.2 Créer un PAT classique pour pull depuis EC2

L'EC2 doit pouvoir `docker pull ghcr.io/valerian-drmt/fx-options-api:latest`.
Si tu rends les packages publics (recommandé), aucun token nécessaire.

**Recommandation** : packages **publics**. Avantages :
- Recruteur peut `docker pull` ton image sans compte GH
- EC2 sans secret docker auth
- Aucun coût bandwidth GitHub Actions

À configurer **après** le premier push d'image (R6) :
- GitHub → Profile → Packages → `fx-options-api` → Settings → Change visibility → Public

### D.3 Naming convention prévu

```
ghcr.io/valerian-drmt/fx-options-api:<git-sha>
ghcr.io/valerian-drmt/fx-options-api:latest
ghcr.io/valerian-drmt/fx-options-engines:<git-sha>
ghcr.io/valerian-drmt/fx-options-frontend:<git-sha>
ghcr.io/valerian-drmt/fx-options-ib-gateway:<git-sha>
```

Le `latest` = dernier tag git `v*.*.*` (mappé dans `deploy.yml` R8).

---

## 6. Phase E — SSH keypair (~5 min, $0)

Même si tu utilises Session Manager pour la prod, garde une keypair pour
l'urgence (panne SSM agent, debug réseau).

```powershell
# Sur Windows
ssh-keygen -t ed25519 -f $env:USERPROFILE\.ssh\fxvol-ec2 -C "valerian@fxvol-ec2"
# Pas de passphrase → simplifie l'automation. Garde la clé privée perso.

# Importer la clé publique dans EC2
aws ec2 import-key-pair `
  --key-name fxvol-ec2-key `
  --public-key-material fileb://~/.ssh/fxvol-ec2.pub `
  --region eu-west-1 `
  --profile itadmin
```

**Backup** : copie `fxvol-ec2` (privée) dans le password manager. Si tu perds
le laptop, c'est ta seule porte d'entrée hors SSM.

---

## 7. Phase F — Security Group (~10 min, $0)

Crée le SG maintenant, attribuable à l'EC2 au moment du launch.

```powershell
# 1. Trouver le VPC default
$vpcId = aws ec2 describe-vpcs --filters "Name=is-default,Values=true" `
  --query "Vpcs[0].VpcId" --output text --profile itadmin --region eu-west-1

# 2. Créer le SG
aws ec2 create-security-group `
  --group-name fxvol-ec2-sg `
  --description "FX vol stack: 80/443 public, SSH via SSM only" `
  --vpc-id $vpcId `
  --region eu-west-1 `
  --profile itadmin

# 3. Note le sg-xxx renvoyé puis ajouter les rules
$sgId = "sg-XXXXXXXX"  # remplace

# HTTP (redirige vers HTTPS dans nginx)
aws ec2 authorize-security-group-ingress `
  --group-id $sgId `
  --protocol tcp --port 80 --cidr 0.0.0.0/0 `
  --region eu-west-1 --profile itadmin

# HTTPS
aws ec2 authorize-security-group-ingress `
  --group-id $sgId `
  --protocol tcp --port 443 --cidr 0.0.0.0/0 `
  --region eu-west-1 --profile itadmin
```

**Pas de port 22 ouvert**. SSH d'urgence = via Session Manager
(`aws ssm start-session --target i-xxx`).

---

## 8. Phase G — CloudWatch log group + SNS topic alarmes (~10 min, ~$0)

### G.1 Log group pour les logs Docker

```powershell
aws logs create-log-group --log-group-name /fxvol/api --region eu-west-1 --profile itadmin
aws logs create-log-group --log-group-name /fxvol/engines --region eu-west-1 --profile itadmin
aws logs create-log-group --log-group-name /fxvol/nginx --region eu-west-1 --profile itadmin

# Rétention 14 jours (gratuit sinon plein de GB-mois)
aws logs put-retention-policy --log-group-name /fxvol/api --retention-in-days 14 --region eu-west-1 --profile itadmin
aws logs put-retention-policy --log-group-name /fxvol/engines --retention-in-days 14 --region eu-west-1 --profile itadmin
aws logs put-retention-policy --log-group-name /fxvol/nginx --retention-in-days 14 --region eu-west-1 --profile itadmin
```

Au moment du `docker run`, ajouter `--log-driver awslogs --log-opt awslogs-group=/fxvol/api`.

### G.2 SNS topic pour alarmes mail

```powershell
$topicArn = aws sns create-topic --name fxvol-alarms --region eu-west-1 --profile itadmin --query "TopicArn" --output text

aws sns subscribe `
  --topic-arn $topicArn `
  --protocol email `
  --notification-endpoint valeriandarmente@gmail.com `
  --region eu-west-1 --profile itadmin
# → Confirme via le mail "AWS Notification - Subscription Confirmation"
```

Tu attacheras des alarmes à ce topic en R8 (CPU EC2, disk full, healthcheck KO).

---

## 9. Phase H — AWS Budgets : alarme à $10/mois (~5 min, $0)

Hyper important pour ne pas se faire surprendre.

```powershell
$budget = @"
{
  "BudgetName": "fxvol-monthly-cap",
  "BudgetLimit": { "Amount": "10", "Unit": "USD" },
  "TimeUnit": "MONTHLY",
  "BudgetType": "COST"
}
"@

$notif = @"
[{
  "Notification": {
    "NotificationType": "ACTUAL",
    "ComparisonOperator": "GREATER_THAN",
    "Threshold": 80,
    "ThresholdType": "PERCENTAGE"
  },
  "Subscribers": [{
    "SubscriptionType": "EMAIL",
    "Address": "valeriandarmente@gmail.com"
  }]
}]
"@

# Note : nécessite l'account ID
$accountId = aws sts get-caller-identity --query Account --output text --profile itadmin

$budget | Out-File budget.json -Encoding ascii
$notif  | Out-File notif.json  -Encoding ascii

aws budgets create-budget `
  --account-id $accountId `
  --budget file://budget.json `
  --notifications-with-subscribers file://notif.json `
  --profile itadmin
```

→ Mail dès que la facture mensuelle dépasse $8 (80% × $10). Le cap réel
attendu est $20 → ajuste à $25 quand l'EC2 tournera (R8).

### H.4 Cost Anomaly Detection (gratuit, complète Budget)

Budget = seuil fixe. Cost Anomaly Detection = ML qui détecte un saut inattendu
même sous le cap (ex: $1 → $5 du jour au lendemain).

```powershell
# 1. Créer le monitor (couvre tous les services)
$monitorArn = aws ce create-anomaly-monitor `
  --anomaly-monitor '{\"MonitorName\":\"fxvol-all-services\",\"MonitorType\":\"DIMENSIONAL\",\"MonitorDimension\":\"SERVICE\"}' `
  --profile itadmin --query 'MonitorArn' --output text

# 2. Créer une subscription qui notifie le SNS topic créé en G.2 (seuil $5)
aws ce create-anomaly-subscription `
  --anomaly-subscription "{\"SubscriptionName\":\"fxvol-anomaly-mail\",\"MonitorArnList\":[\"$monitorArn\"],\"Subscribers\":[{\"Type\":\"EMAIL\",\"Address\":\"valeriandarmente@gmail.com\"}],\"Threshold\":5,\"Frequency\":\"DAILY\"}" `
  --profile itadmin
```

**Coût** : $0. Notifie quand l'anomalie dépasse $5 (sous le Budget cap).

### H.5 Vérifier MFA sur tous les IAM users (CRITIQUE)

```powershell
# Liste les users sans MFA — doit retourner vide
aws iam list-users --profile itadmin --query 'Users[].UserName' --output text |
  ForEach-Object {
    $mfa = aws iam list-mfa-devices --user-name $_ --profile itadmin --query 'MFADevices[].SerialNumber' --output text
    if (-not $mfa) { Write-Host "MISSING MFA: $_" -ForegroundColor Red }
  }
```

Si `itadmin` ou `fxvol-dev` apparaît : MFA à activer **immédiatement** via la
console (Users → user → Security credentials → Assign MFA).

---

## 10. Phase I — Elastic IP (à NE PAS faire maintenant)

⚠️ **Attendre R8 launch EC2**. Une EIP non attachée = **$3.60/mois**.
Une EIP attachée à une instance running = $0.

Au moment du launch en R8 :
```powershell
aws ec2 allocate-address --domain vpc --region eu-west-1 --profile itadmin
aws ec2 associate-address --instance-id i-xxx --allocation-id eipalloc-xxx --profile itadmin
```

---

## 11. Diagramme — état AWS après cette doc (avant R8)

```
                  AWS Account <ID>
                  ────────────────

   IAM users                    KMS                       SSM
   ─────────                    ───                       ───
   root (MFA, locked)           CMK alias/fxvol-secrets   /fxvol/prod/IB_USERID
   itadmin   (admin)            └─ rotation annuelle      /fxvol/prod/IB_PASSWORD
   fxvol-dev (SSM read+write)                             /fxvol/prod/DB_PASSWORD
                                                          /fxvol/prod/VNC_PASSWORD
   IAM role                                               /fxvol/prod/TRADING_MODE
   ────────
   fxvol-ec2-secrets-role
   └─ instance profile fxvol-ec2-instance-profile
      ├─ ssm:GetParameter*  /fxvol/prod/*
      ├─ kms:Decrypt        CMK fxvol-secrets
      ├─ s3:PutObject       fxvol-backups/*
      ├─ logs:PutLogEvents  /fxvol/*
      └─ ssmmessages:*      (Session Manager)

   Storage                                                Network
   ───────                                                ───────
   S3 bucket fxvol-backups                                Default VPC
   ├─ versioning ON                                       Security group fxvol-ec2-sg
   ├─ public access blocked                               ├─ ingress 80  0.0.0.0/0
   ├─ SSE-S3                                              ├─ ingress 443 0.0.0.0/0
   └─ lifecycle: 30j → Glacier IR, 365j → delete          └─ egress  all
                                                          Key pair fxvol-ec2-key
   Observability
   ─────────────
   CloudWatch log groups                                  Cost
   ├─ /fxvol/api    (14d)                                 ────
   ├─ /fxvol/engines (14d)                                Budget cap $10/mo
   └─ /fxvol/nginx  (14d)                                 SNS alert valeriandarmente@gmail.com
   SNS topic fxvol-alarms

   DNS                                                    GHCR (côté GitHub)
   ───                                                    ─────
   Route 53 hosted zone valerian.dev ($0.50/mo)           ghcr.io/valerian-drmt/
   ├─ NS records (4× awsdns)                              ├─ fx-options-api      (à push en R6/R8)
   └─ A/CAA records   (à créer après EC2 launch)          ├─ fx-options-engines  (à push en R7/R8)
                                                          ├─ fx-options-frontend (à push en R5/R8)
                                                          └─ fx-options-ib-gateway (à push en R6)

   PAS ENCORE
   ──────────
   EC2 instance      → R8, 12/05
   Elastic IP        → R8 (coûte $0 attachée)
   ACM cert          → jamais (Let's Encrypt + nginx container)
   ALB / CloudFront  → jamais (overkill 1 EC2)
   RDS / ElastiCache → jamais (containers OK)
```

---

## 12. Ce qui restera à faire le jour J (R8 deploy)

Quand toutes les PRs R0→R7 seront mergées et R8 commencera :

```powershell
# 1. Launch EC2 (1 commande, instance profile + SG + key préparés)
aws ec2 run-instances `
  --image-id ami-0c1ac8a41498c1a9c `      # Amazon Linux 2023 eu-west-1, vérifier date
  --instance-type t3.small `
  --key-name fxvol-ec2-key `
  --security-group-ids sg-xxx `
  --iam-instance-profile Name=fxvol-ec2-instance-profile `
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=fxvol-prod}]" `
  --user-data file://infrastructure/ec2/setup.sh `
  --region eu-west-1 --profile itadmin

# 2. Allocate + attach EIP
aws ec2 allocate-address --domain vpc ...
aws ec2 associate-address --instance-id i-xxx --allocation-id eipalloc-xxx ...

# 3. Update Route 53 A record vers l'EIP

# 4. SSH (via SSM) et bootstrap
aws ssm start-session --target i-xxx
sudo bash /opt/fxvol/infrastructure/ec2/setup.sh
sudo systemctl enable --now fxvol-compose

# 5. Push tag git → GH Actions deploy.yml fait le reste
git tag -a v2.0.0 -m "..."
git push origin v2.0.0
```

Tout ce qui n'est pas dans la liste ci-dessus est **déjà fait** par cette
doc — pas de découverte, pas de création de ressource le jour de la prod.

---

## 13. Coûts cumulés mois par mois

| Période | Ressources actives | Coût |
|---|---|---|
| Maintenant (phases A-H faites) | KMS + SSM + S3 vide + Route 53 zone | **~$1.50** |
| R6 mergé (~04/05) — images sur GHCR | + nada (GHCR gratuit) | **~$1.50** |
| R8 mergé (~12/05) — EC2 lancée | + EC2 t3.small + EBS 30 GB + EIP attachée | **~$20** |
| Steady state | tout ci-dessus + backups S3 + logs CW | **~$22** |

Si tu arrêtes l'EC2 (`stop-instances`) le weekend : **-$5/mois** (EC2 stoppée
= pas de compute, EBS gardé). EIP reste attachée à l'instance stoppée → $0.

---

## 14. Ordre d'exécution recommandé (cette semaine)

Tu peux tout faire en 1 session de ~2h :

1. **Phase A** — domaine (le faire en premier, propagation lente)
2. **Phase B** — IAM role EC2 + instance profile
3. **Phase C** — S3 bucket backups + lifecycle
4. **Phase E** — SSH keypair
5. **Phase F** — security group
6. **Phase G** — CloudWatch log groups + SNS topic
7. **Phase H** — Budget alarm (le faire **avant** d'oublier)
8. ⏸️ Phase I (EIP) — **NE PAS faire maintenant**, attendre R8

Phases D (GHCR settings) = 5 min, peut se faire au moment de R6 quand le
premier push d'image arrive — pas urgent maintenant.

---

## 15. Checklist finale (à cocher au fur et à mesure)

- [ ] Phase A.1 : domaine `valerian.dev` acheté
- [ ] Phase A.2 : records A/CAA notés (à créer le jour J)
- [ ] Phase B.1-3 : role + policy + instance profile créés
- [ ] Phase C : bucket `fxvol-backups` créé, versioning + encryption + lifecycle
- [ ] Phase E : keypair `fxvol-ec2-key` importée + privée backupée
- [ ] Phase F : SG `fxvol-ec2-sg` créé avec 80/443
- [ ] Phase G.1 : 3 log groups créés avec rétention 14j
- [ ] Phase G.2 : SNS topic `fxvol-alarms` + email confirmé
- [ ] Phase H : Budget $10 alarme à 80% confirmée par mail
- [ ] Phase H.4 : Cost Anomaly Detection actif (monitor + subscription)
- [ ] Phase H.5 : MFA active sur root + `itadmin` + `fxvol-dev` (script vérifié)
- [ ] AWS Budgets visible dans la console — vérifie que ça ne dérive pas

Une fois tout coché : **AWS est prêt à recevoir l'EC2**. Le jour de R8, tu
auras littéralement 5 commandes à lancer (cf. § 12) + le push tag git.

---

## 16. Liens utiles

- **Setup secrets** (KMS+SSM+SSO dev) : `infrastructure/aws/SETUP.md`
- **Plan migration secrets sandbox** : `releases/r9-sandbox-secrets-ssm.md`
- **Architecture cible v2** : `releases/architecture_finale_project/00-architecture-main.md`
- **Spec R8 deploy** : `releases/r8-deprecation-pyqt-deploy-prod.md`
- **AWS console** : https://console.aws.amazon.com/
- **AWS pricing calculator** : https://calculator.aws/
- **AMI Amazon Linux 2023 eu-west-1** : https://aws.amazon.com/amazon-linux-2023/

---

**Dernière mise à jour** : 2026-04-25 — projet à R3 sur main, R8 deploy prévu ~12/05/2026.
