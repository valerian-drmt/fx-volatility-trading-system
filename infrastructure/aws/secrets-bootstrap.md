# AWS bootstrap — SSM Parameter Store + KMS + IAM for fx-vol secrets

Procédure **manuelle** pour préparer le compte AWS avant d'activer les scripts
`load_secrets.ps1` / `load_secrets.sh` qui consomment SSM Parameter Store.

Objectif : stocker 5 paramètres sensibles (`IB_USERID`, `IB_PASSWORD`,
`DB_PASSWORD`, `VNC_PASSWORD`, `TRADING_MODE`) dans SSM chiffrés par une CMK
KMS dédiée, accessibles :
- en dev local via un IAM user + AWS SSO
- en prod EC2 via un IAM instance role

À exécuter **une fois**, par un utilisateur admin du compte AWS.

---

## 0. Prérequis

- Compte AWS actif (root ou IAM admin)
- AWS CLI v2 installé (`aws --version` ≥ 2.15)
- Région cible : `eu-west-1` (tout cohabite ici — SSM, KMS, EC2)
- ID du compte AWS récupérable via : `aws sts get-caller-identity`

Dans toute la suite, remplace :
- `<ACCOUNT_ID>` par ton ID de compte (12 chiffres)
- `<REGION>` par `eu-west-1`

---

## 1. Créer la CMK KMS `alias/fxvol-secrets`

```bash
# Créer la clé
aws kms create-key \
  --description "fx-vol secrets encryption key" \
  --key-usage ENCRYPT_DECRYPT \
  --region eu-west-1 \
  --tags TagKey=Project,TagValue=fxvol
```

Récupère le `KeyId` renvoyé (UUID) → on l'utilisera comme `<CMK_KEY_ID>`.

```bash
# Créer l'alias humainement lisible
aws kms create-alias \
  --alias-name alias/fxvol-secrets \
  --target-key-id <CMK_KEY_ID> \
  --region eu-west-1

# Activer rotation annuelle auto
aws kms enable-key-rotation \
  --key-id <CMK_KEY_ID> \
  --region eu-west-1
```

**Vérification** :
```bash
aws kms describe-key --key-id alias/fxvol-secrets --region eu-west-1
# doit renvoyer KeyState=Enabled, KeyRotationEnabled dans get-key-rotation-status
```

---

## 2. Créer les 5 paramètres SSM (valeurs placeholder)

On crée les paramètres **vides** (ou placeholder) ici. Les vraies valeurs
seront poussées plus tard via `scripts/put_secrets.ps1` (commit #2).

```bash
for NAME in IB_USERID IB_PASSWORD DB_PASSWORD VNC_PASSWORD; do
  aws ssm put-parameter \
    --name "/fxvol/prod/$NAME" \
    --value "PLACEHOLDER_TO_REPLACE" \
    --type SecureString \
    --key-id alias/fxvol-secrets \
    --region eu-west-1 \
    --tags Key=Project,Value=fxvol
done

aws ssm put-parameter \
  --name /fxvol/prod/TRADING_MODE \
  --value paper \
  --type String \
  --region eu-west-1 \
  --tags Key=Project,Value=fxvol
```

**Vérification** :
```bash
aws ssm describe-parameters \
  --parameter-filters "Key=Name,Option=BeginsWith,Values=/fxvol/prod/" \
  --region eu-west-1
# doit renvoyer 5 entrées : 4 SecureString + 1 String
```

---

## 3. Créer l'IAM user dev `fxvol-dev`

### 3.1 User + programmatic access

```bash
aws iam create-user --user-name fxvol-dev \
  --tags Key=Project,Value=fxvol
```

### 3.2 Policy inline `fxvol-dev-ssm-rw`

Sauver le contenu suivant dans `fxvol-dev-policy.json` (remplacer les
placeholders `<ACCOUNT_ID>` et `<CMK_KEY_ID>`) :

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadSecrets",
      "Effect": "Allow",
      "Action": [
        "ssm:GetParameter",
        "ssm:GetParameters",
        "ssm:GetParametersByPath",
        "ssm:DescribeParameters"
      ],
      "Resource": "arn:aws:ssm:eu-west-1:<ACCOUNT_ID>:parameter/fxvol/prod/*"
    },
    {
      "Sid": "WriteSecrets",
      "Effect": "Allow",
      "Action": "ssm:PutParameter",
      "Resource": "arn:aws:ssm:eu-west-1:<ACCOUNT_ID>:parameter/fxvol/prod/*"
    },
    {
      "Sid": "UseCMK",
      "Effect": "Allow",
      "Action": [
        "kms:Decrypt",
        "kms:Encrypt",
        "kms:GenerateDataKey"
      ],
      "Resource": "arn:aws:kms:eu-west-1:<ACCOUNT_ID>:key/<CMK_KEY_ID>"
    }
  ]
}
```

Attacher :
```bash
aws iam put-user-policy \
  --user-name fxvol-dev \
  --policy-name fxvol-dev-ssm-rw \
  --policy-document file://fxvol-dev-policy.json
```

### 3.3 Activer MFA (obligatoire)

Depuis la console AWS : IAM → Users → fxvol-dev → Security credentials →
Assign MFA device (Authenticator app). **Ne pas skip** — sans MFA, un leak
des access keys = accès direct aux secrets IB.

### 3.4 (Option recommandée) Remplacer par AWS SSO

Si tu utilises déjà AWS IAM Identity Center (ex-SSO), préférer :
1. Créer un permission set `fxvol-dev-secrets` avec la policy ci-dessus
2. L'attacher à ton user IdC sur le compte cible
3. Côté Windows : `aws configure sso` + profile name `fxvol-dev`

→ pas d'access keys statiques, sessions temporaires, rotation auto.

---

## 4. Créer l'IAM role EC2 `fxvol-ec2-secrets-role`

### 4.1 Trust policy

Sauver dans `fxvol-ec2-trust-policy.json` :

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": "ec2.amazonaws.com"},
    "Action": "sts:AssumeRole"
  }]
}
```

### 4.2 Role + policy

```bash
aws iam create-role \
  --role-name fxvol-ec2-secrets-role \
  --assume-role-policy-document file://fxvol-ec2-trust-policy.json \
  --tags Key=Project,Value=fxvol

# Policy en lecture seule (pas de PutParameter depuis EC2)
cat > fxvol-ec2-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ssm:GetParameter",
        "ssm:GetParameters",
        "ssm:GetParametersByPath"
      ],
      "Resource": "arn:aws:ssm:eu-west-1:<ACCOUNT_ID>:parameter/fxvol/prod/*"
    },
    {
      "Effect": "Allow",
      "Action": "kms:Decrypt",
      "Resource": "arn:aws:kms:eu-west-1:<ACCOUNT_ID>:key/<CMK_KEY_ID>"
    }
  ]
}
EOF

aws iam put-role-policy \
  --role-name fxvol-ec2-secrets-role \
  --policy-name fxvol-ec2-ssm-read \
  --policy-document file://fxvol-ec2-policy.json
```

### 4.3 Instance profile

```bash
aws iam create-instance-profile \
  --instance-profile-name fxvol-ec2-instance-profile

aws iam add-role-to-instance-profile \
  --instance-profile-name fxvol-ec2-instance-profile \
  --role-name fxvol-ec2-secrets-role
```

### 4.4 Attacher à l'EC2 existante

```bash
# Récupérer l'instance id (ou depuis la console)
aws ec2 describe-instances \
  --filters "Name=tag:Project,Values=fxvol" \
  --query "Reservations[].Instances[].InstanceId" \
  --output text \
  --region eu-west-1

aws ec2 associate-iam-instance-profile \
  --instance-id <i-xxxxxxx> \
  --iam-instance-profile Name=fxvol-ec2-instance-profile \
  --region eu-west-1
```

**Vérification depuis l'EC2** (SSH dedans) :
```bash
aws sts get-caller-identity
# doit renvoyer un ARN du type :
# arn:aws:sts::<ACCOUNT_ID>:assumed-role/fxvol-ec2-secrets-role/i-xxxxx
# SI tu vois un IAM user arn:aws:iam::.../user/..., l'instance profile n'est pas attaché
```

---

## 5. Setup AWS SSO côté Windows (dev local)

Dans PowerShell :

```powershell
aws configure sso
# SSO start URL       : https://<ton-orga>.awsapps.com/start
# SSO Region          : eu-west-1
# Account             : <ACCOUNT_ID>
# Role                : fxvol-dev-secrets
# CLI default region  : eu-west-1
# CLI default output  : json
# CLI profile name    : fxvol-dev
```

Test :
```powershell
aws sso login --profile fxvol-dev
aws sts get-caller-identity --profile fxvol-dev
# doit renvoyer l'ARN du permission set assumé
aws ssm get-parameter --name /fxvol/prod/TRADING_MODE --profile fxvol-dev --region eu-west-1
# doit renvoyer {"Parameter": {..., "Value": "paper"}}
```

Si SSO non dispo sur ton compte → utiliser les access keys du user `fxvol-dev` :
```powershell
aws configure --profile fxvol-dev
# AWS Access Key ID     : <keys créés à l'étape 3.1>
# AWS Secret Access Key : ...
# Default region        : eu-west-1
# Default output format : json
```

⚠️ Stocker les access keys **dans Windows Credential Manager** ou un gestionnaire
de mots de passe, pas dans `~/.aws/credentials` en clair si possible (même si
le fichier est `0600`).

---

## 6. Checklist avant de passer au commit #2

- [ ] CMK `alias/fxvol-secrets` créée, rotation activée
- [ ] 5 paramètres SSM `/fxvol/prod/*` existent (placeholders)
- [ ] IAM user `fxvol-dev` créé avec policy `fxvol-dev-ssm-rw`
- [ ] MFA activée sur `fxvol-dev` (ou SSO en place)
- [ ] IAM role `fxvol-ec2-secrets-role` + instance profile créés
- [ ] Instance profile attaché à l'EC2 fxvol
- [ ] `aws sso login --profile fxvol-dev` fonctionne sur Windows
- [ ] `aws sts get-caller-identity` depuis EC2 renvoie bien l'ARN du role
- [ ] `aws ssm get-parameter --name /fxvol/prod/TRADING_MODE --profile fxvol-dev`
      renvoie `paper`

Une fois toutes les cases cochées : passer au commit #2 (`scripts/load_secrets.ps1`
+ `scripts/put_secrets.ps1` pour pousser les vraies valeurs des 4 SecureString).

---

## 7. Coût estimé (récap)

| Service | Usage mensuel | Coût |
|---|---|---|
| SSM Parameter Store standard tier | 5 params, ~100 reads/jour | gratuit (< 10k) |
| KMS CMK dédiée | 1 clé + ~3000 decrypt/mois | ~$1.03 |
| IAM user/role/policies | illimité | gratuit |
| CloudTrail management events | 90 jours rétention | gratuit |
| **Total** | | **~$1/mois** |

---

## 8. Rollback (si on veut tout annuler)

```bash
# Supprimer les paramètres
for NAME in IB_USERID IB_PASSWORD DB_PASSWORD VNC_PASSWORD TRADING_MODE; do
  aws ssm delete-parameter --name "/fxvol/prod/$NAME" --region eu-west-1
done

# Détacher l'instance profile de l'EC2
aws ec2 disassociate-iam-instance-profile --association-id <assoc-id>
aws iam remove-role-from-instance-profile \
  --instance-profile-name fxvol-ec2-instance-profile \
  --role-name fxvol-ec2-secrets-role
aws iam delete-instance-profile --instance-profile-name fxvol-ec2-instance-profile

# Supprimer role + policies
aws iam delete-role-policy --role-name fxvol-ec2-secrets-role --policy-name fxvol-ec2-ssm-read
aws iam delete-role --role-name fxvol-ec2-secrets-role

# Supprimer user + policy
aws iam delete-user-policy --user-name fxvol-dev --policy-name fxvol-dev-ssm-rw
aws iam delete-user --user-name fxvol-dev

# Programmer suppression CMK (délai min 7 jours, ne peut être immédiat)
aws kms delete-alias --alias-name alias/fxvol-secrets --region eu-west-1
aws kms schedule-key-deletion --key-id <CMK_KEY_ID> --pending-window-in-days 7 --region eu-west-1
```
