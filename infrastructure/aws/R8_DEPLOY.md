# R8 deploy — playbook EC2 production (~12/05/2026)

> **Préalable côté AWS** : toutes les ressources support (KMS, IAM role, Security group, S3, CloudWatch, SNS, DNS) sont déjà créées au 27/04/2026. Voir `STATE.md` pour l'inventaire.
>
> **Préalable côté code** : R3-R8 sont déjà codées sur `sandbox/r9-pipeline-verif`. Ce playbook s'exécute **après** que la cadence quotidienne du `releases/git_management/PLAYBOOK.md` ait poussé les PRs R3-R8 sur `main` et posé le tag `v2.0.0` (date prévue 12/05/2026 selon le calendrier PR).
>
> Autrement dit : le code est prêt aujourd'hui, ce qui n'est pas prêt c'est l'historique git public sur `main`. Le déploiement attend que `v2.0.0` existe sur `origin/main`.
>
> **Estimation temps le jour J** : ~30 min de commandes + propagation Let's Encrypt (~2 min).

---

## Pré-checklist (avant de lancer)

- [ ] Les PRs R0 → R7 sont mergées sur `main` (cf. `releases/git_management/PLAYBOOK.md` § Calendrier)
- [ ] Le tag `v2.0.0` existe sur `origin/main` (posé après la dernière PR R8)
- [ ] Les images Docker sont publiées sur GHCR par le workflow `build.yml` (`ghcr.io/valerian-drmt/fx-options-*:latest`)
- [ ] Les packages GHCR sont passés en **public** (sinon EC2 a besoin d'un PAT pour pull)
- [ ] AWS CLI v2 installé sur le laptop : `aws --version`
- [ ] **Profile `itadmin` accessible en CLI** — comme `itadmin` n'a pas d'access keys permanentes (cf. `STATE.md` § 1.1), 2 options : (a) créer une access key temporaire `aws iam create-access-key --user-name itadmin` à supprimer en fin de session, ou (b) utiliser un STS assume-role MFA. Sans ça, les `--profile itadmin` ci-dessous échouent avec `Unable to locate credentials`.
- [ ] Profile `fxvol-dev` configuré (`aws sts get-caller-identity --profile fxvol-dev` retourne ton ARN)
- [ ] Tu as déjà augmenté le Budget cap à $25 (Edit `fxvol-monthly-cap` dans la console)

---

## Étape 1 — Lancer l'instance EC2

```powershell
# Variables (à mettre à jour si besoin)
# IMPORTANT : Ubuntu 22.04 LTS, PAS Amazon Linux 2023.
# Le script infrastructure/ec2/setup.sh est codé pour apt/ufw (Ubuntu) — il
# échoue sur Amazon Linux 2023 qui utilise dnf/firewalld.
# AMI Ubuntu 22.04 LTS eu-west-1 (Canonical) : vérifier le dernier ID au jour J via :
#   aws ec2 describe-images --owners 099720109477 \
#     --filters "Name=name,Values=ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*" \
#     --query "sort_by(Images, &CreationDate)[-1].{Id:ImageId,Name:Name,Date:CreationDate}" \
#     --region eu-west-1 --profile itadmin
$AMI_ID = "ami-XXXXXXXXXXXXXXXXX"   # remplacer par l'ID retourné ci-dessus
$INSTANCE_TYPE = "t3.small"
$KEY_NAME = "fxvol-ec2-key"
$SG_ID = "sg-0c96af5e3203ffeec"
$INSTANCE_PROFILE = "fxvol-ec2-instance-profile"

# Launch
aws ec2 run-instances `
  --image-id $AMI_ID `
  --instance-type $INSTANCE_TYPE `
  --key-name $KEY_NAME `
  --security-group-ids $SG_ID `
  --iam-instance-profile Name=$INSTANCE_PROFILE `
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=fxvol-prod},{Key=Project,Value=fxvol}]" `
  --user-data file://infrastructure/ec2/setup.sh `
  --metadata-options "HttpTokens=required,HttpEndpoint=enabled,HttpPutResponseHopLimit=2" `
  --region eu-west-1 `
  --profile itadmin
```

**Notes** :
- `HttpTokens=required` = IMDSv2 obligatoire (pas IMDSv1, anti-SSRF)
- `HttpPutResponseHopLimit=2` = autorise les containers Docker à atteindre IMDSv2 (1 hop pour aller au container + 1 hop pour le proxy)
- L'AMI Ubuntu 22.04 LTS de Canonical inclut SSM Agent **snap** par défaut (vérifier après boot avec `snap list amazon-ssm-agent`)
- `--user-data file://...setup.sh` exécute le bootstrap au premier boot : install docker + compose v2, crée user `fxvol`, ouvre UFW 22/80/443, prépare `/opt/fxvol`. **Le script clone aussi le repo et active le service systemd** quand il est invoqué avec les bonnes variables d'environnement (cf. en-tête de `setup.sh`).

**Résultat attendu** : la commande retourne un JSON contenant `InstanceId` (du type `i-XXXXXXXX`).

```powershell
# Note l'InstanceId pour la suite
$INSTANCE_ID = "i-XXXXXXXX"  # remplace
```

**Vérifier que l'instance est `running`** :

```powershell
aws ec2 describe-instances --instance-ids $INSTANCE_ID `
  --query "Reservations[0].Instances[0].State.Name" `
  --output text --region eu-west-1 --profile itadmin
# Attendre "running" (max 1-2 min)
```

---

## Étape 2 — Allouer et associer une Elastic IP

```powershell
# Allouer une EIP
$EIP_RESULT = aws ec2 allocate-address --domain vpc `
  --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=Name,Value=fxvol-prod-eip},{Key=Project,Value=fxvol}]" `
  --region eu-west-1 --profile itadmin | ConvertFrom-Json

$ALLOCATION_ID = $EIP_RESULT.AllocationId
$PUBLIC_IP = $EIP_RESULT.PublicIp

Write-Host "EIP allouée : $PUBLIC_IP (allocation $ALLOCATION_ID)"

# Associer à l'instance
aws ec2 associate-address `
  --instance-id $INSTANCE_ID `
  --allocation-id $ALLOCATION_ID `
  --region eu-west-1 --profile itadmin
```

**Coût** : EIP attachée à une instance running = **$0**. EIP non-attachée = **$3.60/mo** → ne pas allouer avant l'instance.

---

## Étape 3 — Ajouter les records DNS dans Route 53

```powershell
# Récupère l'ID de la hosted zone
$HOSTED_ZONE_ID = aws route53 list-hosted-zones-by-name `
  --dns-name valeriandarmente.dev `
  --query "HostedZones[0].Id" --output text --profile itadmin
# Format : /hostedzone/Z0XXXXXXXXXX, on garde tel quel

# Crée le change set DNS
$CHANGE_BATCH = @"
{
  "Comment": "R8 deploy: point valeriandarmente.dev to EC2 EIP $PUBLIC_IP",
  "Changes": [
    {
      "Action": "CREATE",
      "ResourceRecordSet": {
        "Name": "valeriandarmente.dev",
        "Type": "A",
        "TTL": 300,
        "ResourceRecords": [{"Value": "$PUBLIC_IP"}]
      }
    },
    {
      "Action": "CREATE",
      "ResourceRecordSet": {
        "Name": "www.valeriandarmente.dev",
        "Type": "A",
        "TTL": 300,
        "ResourceRecords": [{"Value": "$PUBLIC_IP"}]
      }
    },
    {
      "Action": "CREATE",
      "ResourceRecordSet": {
        "Name": "valeriandarmente.dev",
        "Type": "CAA",
        "TTL": 3600,
        "ResourceRecords": [{"Value": "0 issue \"letsencrypt.org\""}]
      }
    }
  ]
}
"@

$CHANGE_BATCH | Out-File -Encoding ascii dns-change.json

aws route53 change-resource-record-sets `
  --hosted-zone-id $HOSTED_ZONE_ID `
  --change-batch file://dns-change.json `
  --profile itadmin
```

**Vérifier la propagation** (TTL 300s donc rapide) :

```powershell
nslookup valeriandarmente.dev 8.8.8.8
# Doit retourner $PUBLIC_IP en moins de 5 min
```

---

## Étape 4 — Bootstrap de l'EC2 via SSM Session Manager

> Si le `user-data` setup.sh à l'étape 1 a déjà fait son travail, tout ce qui suit jusqu'à `systemctl enable --now fxvol-compose` est **idempotent et déjà fait**. On le repasse à la main pour confirmer / fixer en cas d'échec du user-data.

```powershell
# Connexion shell sans SSH (Session Manager via IAM role)
aws ssm start-session --target $INSTANCE_ID --profile itadmin --region eu-west-1
```

Une fois dans le shell EC2 (Ubuntu 22.04, user `ubuntu`) :

```bash
# 1. Vérifier que setup.sh a tourné via user-data
sudo cat /var/log/cloud-init-output.log | tail -50
# Doit montrer les lignes "[setup] ..." de infrastructure/ec2/setup.sh

# 2. Si ça n'a pas tourné, le repasser à la main :
sudo bash -c 'curl -fsS https://raw.githubusercontent.com/valerian-drmt/fx-volatility-trading-system/main/infrastructure/ec2/setup.sh | bash'

# 3. Cloner le repo dans /opt/fxvol (si setup.sh ne l'a pas déjà fait)
sudo -u fxvol git clone --depth 1 --branch v2.0.0 \
  https://github.com/valerian-drmt/fx-volatility-trading-system.git /opt/fxvol

# 4. Installer le service systemd qui orchestre TOUT le pipeline
#    (load_secrets.sh ExecStartPre + docker compose up + cleanup ExecStopPost)
sudo cp /opt/fxvol/infrastructure/ec2/fxvol-compose.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fxvol-compose
```

**Ce que fait le service `fxvol-compose`** (cf. `infrastructure/ec2/fxvol-compose.service`) :
1. `ExecStartPre=/opt/fxvol/scripts/ops/load_secrets.sh` — fetch SSM via le IAM role, écrit `/run/fxvol.env` (tmpfs, root:fxvol 0640).
2. `EnvironmentFile=/run/fxvol.env` + `EnvironmentFile=-/opt/fxvol/images.env` (image tags écrits par le workflow `deploy.yml`).
3. `ExecStart=/usr/bin/docker compose up -d --remove-orphans` — utilise `docker-compose.yml` du repo. **Pas de `-f docker-compose.prod.yml`** : le projet a un seul compose de prod (`docker-compose.yml`) + un override pour dev (`docker-compose.override.yml`) qui n'est pas présent en prod. Vérifier que `docker-compose.override.yml` est dans `.dockerignore` ou qu'il a été retiré du clone côté prod (sinon docker-compose le merge automatiquement).
4. `ExecStopPost=/bin/rm -f /run/fxvol.env` — wipe le fichier de secrets quand le service stoppe.

**Vérification** :

```bash
# Dans le shell EC2
sudo systemctl status fxvol-compose      # active (exited) — type=oneshot
docker ps                                 # 4-5 containers running (api, engines, nginx, postgres, redis)
sudo cat /run/fxvol.env | wc -l           # 5-6 lignes (4 secrets + DATABASE_URL + REDIS_URL)
curl -I http://localhost                  # nginx 200 ou 301 vers HTTPS
```

---

## Étape 5 — Vérifier le pipeline Let's Encrypt + HTTPS

Avec la propagation DNS faite (étape 3), nginx peut faire le challenge ACME via le port 80.

```bash
# Dans le shell EC2
docker logs fxvol_nginx 2>&1 | grep -i "certbot\|let's encrypt\|certificate"
# Doit montrer le succès du challenge ACME et la création du cert
```

Test depuis le laptop :

```powershell
# Vérifier que HTTPS marche
curl -I https://valeriandarmente.dev
# Doit retourner 200 et un cert Let's Encrypt valide

# Vérifier la redirection HTTP → HTTPS
curl -I http://valeriandarmente.dev
# Doit retourner 301 ou 308 vers https://
```

---

## Étape 6 — Push tag git → GitHub Actions deploy.yml

À ce stade, l'EC2 est UP avec la stack qui tourne. Pour les futures releases, le déploiement se fait via le pipeline CI :

```powershell
# Sur le laptop, depuis le repo
git tag -a v2.0.0 -m "R8: prod deploy on EC2 fxvol-prod"
git push origin v2.0.0
```

→ GitHub Actions `deploy.yml` se déclenche, SSH dans l'EC2 (via SSM ou clé), `docker compose pull && docker compose up -d`.

---

## Étape 7 — Augmenter le Budget cap à $25

```powershell
# OU via console : Billing → Budgets → fxvol-monthly-cap → Edit → Amount = 25
$accountId = aws sts get-caller-identity --query Account --output text --profile itadmin

aws budgets update-budget `
  --account-id $accountId `
  --new-budget '{\"BudgetName\":\"fxvol-monthly-cap\",\"BudgetLimit\":{\"Amount\":\"25\",\"Unit\":\"USD\"},\"TimeUnit\":\"MONTHLY\",\"BudgetType\":\"COST\"}' `
  --profile itadmin
```

→ Steady state R8 attendu = ~$22/mo. Cap à $25 = marge de 14%.

---

## Étape 8 — Smoke check final

Lance ce script depuis le laptop :

```powershell
# Profil par défaut pour le smoke check
$profile = "fxvol-dev"

# 1. EC2 est running
aws ec2 describe-instances `
  --filters "Name=tag:Name,Values=fxvol-prod" "Name=instance-state-name,Values=running" `
  --query "Reservations[].Instances[].{Id:InstanceId,IP:PublicIpAddress,State:State.Name}" `
  --output table --region eu-west-1 --profile itadmin

# 2. EIP attachée
aws ec2 describe-addresses `
  --filters "Name=tag:Project,Values=fxvol" `
  --query "Addresses[].{IP:PublicIp,Instance:InstanceId,Allocation:AllocationId}" `
  --output table --region eu-west-1 --profile itadmin

# 3. DNS résout
nslookup valeriandarmente.dev 8.8.8.8

# 4. HTTPS répond
curl -I https://valeriandarmente.dev

# 5. SSM Session Manager fonctionne (test connexion+sortie)
$INSTANCE_ID = "i-XXXXXXXX"  # remplace
aws ssm start-session --target $INSTANCE_ID --document-name AWS-StartInteractiveCommand --parameters command="echo OK_FROM_EC2 && exit" --profile itadmin

# 6. SSH break-glass marche (via 1Password agent)
ssh ec2-user@valeriandarmente.dev "echo OK_FROM_SSH"
# 1Password popup biométrie, puis "OK_FROM_SSH"

# 7. Logs CloudWatch reçoivent
aws logs tail /fxvol/api --since 5m --profile itadmin
aws logs tail /fxvol/nginx --since 5m --profile itadmin
```

→ Si les 7 commandes passent : ✅ R8 déployée et opérationnelle.

---

## Rollback express (si quelque chose foire)

### Option A — l'EC2 ne démarre pas / l'app crash

```powershell
# Stopper sans détruire (gardes EBS pour debug)
aws ec2 stop-instances --instance-ids $INSTANCE_ID --profile itadmin
# Investiguer logs CloudWatch ou reprendre via SSM
```

### Option B — bug applicatif après deploy.yml

```powershell
# Revert au tag précédent
git revert v2.0.0
git tag v2.0.0-rollback
git push origin v2.0.0-rollback
# GitHub Actions redéploie l'image précédente
```

### Option C — tout casser et recommencer

```powershell
# Terminer l'instance, libérer l'EIP
aws ec2 terminate-instances --instance-ids $INSTANCE_ID --profile itadmin
aws ec2 release-address --allocation-id $ALLOCATION_ID --profile itadmin
# Supprimer les records A/CAA dans Route 53
# Reprendre étape 1
```

→ Coût d'un rollback complet : <$1 (quelques minutes EBS).

---

## Items différés post-R8

| Item | Quand | Note |
|---|---|---|
| CloudWatch alarms (CPU, disk, healthcheck) | semaine après R8 stable | utiliser SNS topic `fxvol-alarms` déjà créé |
| AWS Backup pour EBS root | si la prod tourne >2 mois | $0.05/GB-mo, ~$1.50/mo pour 30GB EBS |
| Snapshot manuel EBS hebdo | si pas AWS Backup | gratuit, à scripter |
| ACM cert + ALB | si un jour scale > 1 EC2 | hors scope perso |
| Stop/start scheduling weekend | si tu veux économiser | ~$5/mo économisés |

---

## Erreurs connues à éviter

| Erreur | Cause | Mitigation |
|---|---|---|
| `Access Denied` sur SSM start-session | role EC2 sans `ssmmessages:*` | déjà OK dans `fxvol-ec2-permissions` |
| `pull access denied` GHCR | packages encore privés | passer en public (Profile → Packages → settings) |
| `setup.sh` échoue avec `apt-get: command not found` | AMI Amazon Linux choisie par erreur | redémarrer l'instance avec une AMI Ubuntu 22.04 LTS Canonical (cf. étape 1) |
| `docker-compose.override.yml` chargé en prod | clone du repo embarque le fichier | soit le retirer du clone (`git sparse-checkout` ou `rm` post-clone), soit forcer `COMPOSE_FILE=docker-compose.yml` dans `/opt/fxvol/images.env` |
| Let's Encrypt rate limit | trop de retries en quelques heures | attendre 1h, debug en staging avec `--staging` |
| EIP non attachée pendant l'investigation | `release-address` oublié | facturé $3.60/mo, vérifier régulièrement |
| HTTPS refuse (HSTS sur `.dev`) | cert expiré ou auto-renew cassé | logs nginx + certbot dans `/fxvol/nginx` log group |

---

**Dernière mise à jour** : 2026-04-27 — playbook préparé, à exécuter ~12/05/2026.
