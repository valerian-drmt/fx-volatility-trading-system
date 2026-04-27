# AWS Setup — fx-volatility-trading-system

> Procédure complète pour configurer AWS en partant de zéro, adaptée à l'**état
> actuel du projet** (R3 en cours sur main, R6 Docker prévu ~05/05, R8 deploy
> EC2 prévu ~12/05).
>
> Le projet **n'est pas encore en prod**. Cette doc te dit **quoi faire
> maintenant** (le strict nécessaire pour le dev local SSM) et **quoi attendre**
> (EC2, deploy.yml, IAM role) jusqu'à ce que les PRs correspondantes arrivent.

---

## 0. Vue d'ensemble — où on en est

| Composant | Statut projet | Action AWS requise | Quand |
|---|---|---|---|
| Code v1.x (PyQt local) | ✅ tourne | Aucune | — |
| Schéma Postgres + Alembic (R1) | ✅ mergé | Aucune | — |
| Async DB writer (R2) | ✅ mergé | Aucune | — |
| Redis broker (R3) | 🟡 en cours | Aucune | — |
| FastAPI backend (R4) | 🔒 codé local | Aucune | — |
| React frontend (R5) | 🔒 codé local | Aucune | — |
| Docker compose prod (R6) | 🔒 codé local | Aucune | — |
| Services split (R7) | 🔒 codé local | Aucune | — |
| **R8 — deploy EC2** | 🔒 codé local | **EC2 + IAM role + SSM en prod** | ~12/05 |
| **R9 — secrets SSM** | 🟡 sandbox | **KMS + SSM + IAM user dev** | dès maintenant si tu veux drop le `.env` local |

**Conséquence pratique** : tu peux **tout faire AWS aujourd'hui**, ou tu peux
faire **uniquement la phase 1-3 (dev local SSM)** et reporter EC2 à mai. Les
deux chemins sont valides — la migration R9 secrets a été conçue pour que dev
et prod cohabitent (le dev passe à SSM sans toucher à la prod, et inversement).

---

## 1. Prérequis (avant tout)

### Côté toi

- [ ] Carte bancaire valide (le compte AWS la demande même si l'usage projeté est ~$1/mois)
- [ ] Numéro de téléphone mobile pour la vérification (SMS code)
- [ ] Adresse email **dédiée** au compte root (idéalement `aws-fxvol@<ton-domaine>`
      ou `valeriandarmente+aws@gmail.com` — alias séparé du compte personnel)
- [ ] Un password manager (Bitwarden, 1Password, KeePass) pour stocker les
      mots de passe root + access keys que tu vas générer

### Côté machine Windows

```powershell
# 1. AWS CLI v2 (>= 2.15)
winget install -e --id Amazon.AWSCLI
# Vérifier
aws --version
# Doit afficher : aws-cli/2.x.x Python/3.x ...

# 2. (Optionnel mais recommandé) Session Manager plugin pour SSM start-session
winget install -e --id Amazon.SessionManagerPlugin
```

Pas besoin de `boto3` côté machine : les scripts utilisent l'`aws` CLI.

---

## 2. Phase 1 — Compte AWS + IAM users (DÉJÀ FAIT)

✅ **État actuel** :
- Compte AWS root créé (eu-west-1)
- IAM user `itadmin` (admin, pour créer les ressources)
- IAM user `fxvol-dev` (à utiliser pour les opérations runtime sur SSM)

**À vérifier maintenant si pas déjà fait** (CRITIQUE) :

- [ ] **MFA activée sur le compte root** : Console (root) → Security credentials
      → Multi-factor authentication → Add MFA. Codes de récupération en
      password manager. **Ne plus se connecter en root sauf urgence**.
- [ ] **MFA activée sur `itadmin`** : Console (root) → IAM → Users → itadmin
      → Security credentials → Assign MFA device.
- [ ] **MFA activée sur `fxvol-dev`** : idem. Sans MFA, refuser tout `aws iam
      put-user-policy` qui donne des droits sensibles (KMS decrypt).
- [ ] **Région par défaut `eu-west-1`** : tous les scripts/commandes de cette
      doc et de `DEPLOYMENT_PREP.md` sont hardcodés sur `eu-west-1`.

**Architecture IAM cible (classique, pas SSO)** :

```
Account <ID>
├─ root                      MFA, locked, urgence uniquement
├─ itadmin                   MFA, AdministratorAccess (gérer ressources)
└─ fxvol-dev                 MFA, policy fxvol-dev-ssm (lecture/écriture SSM + KMS sur scope /fxvol/prod/*)
```

`itadmin` crée la CMK + les params SSM + les policies (phases 2-4 ci-dessous).
`fxvol-dev` consomme au quotidien via le script `load_secrets.ps1` (phase 5+).
**Édition des valeurs SSM = via la console AWS uniquement** (pas de script
CLI fourni — pour éviter les fausses manipulations sur les secrets).

---

## 3. Phase 2 — KMS CMK pour chiffrer les secrets

> Tout en console (eu-west-1, vérifier en haut à droite).

1. **Console KMS** → Customer managed keys → Create key
2. Configuration :
   - Key type : **Symmetric**
   - Key usage : **Encrypt and decrypt**
   - Advanced : Single-region, **Key material origin = KMS**
3. Alias : `fxvol-secrets` → l'ARN sera `arn:aws:kms:eu-west-1:<ACCOUNT_ID>:alias/fxvol-secrets`
4. Description : `CMK for FX vol trading system secrets in SSM Parameter Store`
5. Key administrators : IAM user `itadmin` (et personne d'autre)
6. Key users : IAM user `fxvol-dev` (on ajoutera l'EC2 role en phase R8)
7. Review → Finish
8. Une fois créée → onglet **Key rotation** → cocher "Automatically rotate this
   KMS key every year"

📋 **Note l'ARN complet** dans ton password manager :
`arn:aws:kms:eu-west-1:<ACCOUNT_ID>:key/<UUID>` — tu vas en avoir besoin
en phase 5 et plus tard pour l'EC2 role.

📋 **Note ton AWS Account ID** (12 chiffres) visible en haut à droite de la
console : `<ACCOUNT_ID>`. Aussi à stocker dans le password manager.

**Coût** : $1/mois fixe pour la CMK + $0.03 par 10k decrypt (négligeable).

---

## 4. Phase 3 — SSM Parameter Store : créer les paramètres vides

Tu peux faire ça en console OU via CLI (tu n'as pas encore CLI configuré, donc
console pour cette première fois).

> Console → Systems Manager → Parameter Store → Create parameter

Crée 5 paramètres avec les noms et types suivants. **Mets une valeur bidon
maintenant**, on les remplira avec les vraies valeurs en phase 6.

| Name | Tier | Type | KMS key | Value (bidon) |
|---|---|---|---|---|
| `/fxvol/prod/IB_USERID` | Standard | SecureString | `alias/fxvol-secrets` | `placeholder` |
| `/fxvol/prod/IB_PASSWORD` | Standard | SecureString | `alias/fxvol-secrets` | `placeholder` |
| `/fxvol/prod/DB_PASSWORD` | Standard | SecureString | `alias/fxvol-secrets` | `placeholder` |
| `/fxvol/prod/VNC_PASSWORD` | Standard | SecureString | `alias/fxvol-secrets` | `placeholder` |
| `/fxvol/prod/TRADING_MODE` | Standard | String | (n/a) | `paper` |

**Pourquoi des placeholders maintenant** : les scripts `load_secrets.ps1`
testent que les params existent. Avoir 5 entrées en SSM permet de valider la
chaîne IAM → KMS → SSM end-to-end avant de mettre les vrais secrets.

⚠️ Tier **Standard** uniquement (gratuit jusqu'à 10k params). Ne JAMAIS choisir
"Advanced" (payant).

✅ **Checkpoint phase 3** : `Parameter Store` montre 5 entrées sous le path
`/fxvol/prod/`.

---

## 5. Phase 4 — Policy IAM sur `fxvol-dev` : lire/écrire SSM + KMS

Maintenant qu'on a la CMK ARN et l'Account ID, on peut écrire la policy IAM
attachée à l'IAM user `fxvol-dev` (inline policy).

1. Sauvegarder le JSON ci-dessous dans `fxvol-dev-policy.json` (en remplaçant
   `<ACCOUNT_ID>` et `<CMK_KEY_ID>` par les vraies valeurs) :

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
        "ssm:GetParametersByPath",
        "ssm:DescribeParameters"
      ],
      "Resource": "arn:aws:ssm:eu-west-1:<ACCOUNT_ID>:parameter/fxvol/prod/*"
    },
    {
      "Sid": "PutSecretsSSM",
      "Effect": "Allow",
      "Action": [
        "ssm:PutParameter",
        "ssm:DeleteParameter"
      ],
      "Resource": "arn:aws:ssm:eu-west-1:<ACCOUNT_ID>:parameter/fxvol/prod/*"
    },
    {
      "Sid": "EncryptDecryptKMS",
      "Effect": "Allow",
      "Action": [
        "kms:Decrypt",
        "kms:Encrypt",
        "kms:GenerateDataKey",
        "kms:DescribeKey"
      ],
      "Resource": "arn:aws:kms:eu-west-1:<ACCOUNT_ID>:key/<CMK_KEY_ID>"
    }
  ]
}
```

2. Attacher la policy via CLI (lance ça depuis une session `itadmin`) :

```powershell
aws iam put-user-policy `
  --user-name fxvol-dev `
  --policy-name fxvol-dev-ssm `
  --policy-document file://fxvol-dev-policy.json `
  --profile itadmin
```

3. Vérifier que la policy est bien attachée :
```powershell
aws iam list-user-policies --user-name fxvol-dev --profile itadmin
# Doit lister : "fxvol-dev-ssm"
```

**Pourquoi `kms:Encrypt` aussi** : nécessaire pour `ssm:PutParameter` sur une
SecureString (KMS génère une data key). `kms:Decrypt` seul = tu peux lire mais
pas écrire.

**Pourquoi `inline` plutôt que `managed`** : managed policies sont réutilisables
mais ici on a 1 user et 1 scope. Inline garde la policy collée à l'identité,
visible immédiatement dans `aws iam get-user`. Suppression du user = suppression
auto de la policy.

---

## 6. Phase 5 — Configurer AWS CLI côté Windows (access keys classiques)

### 6.1 Générer les access keys pour `fxvol-dev`

Lance ça depuis une session `itadmin` (qui a le droit de créer des access keys
pour d'autres users) :

```powershell
aws iam create-access-key --user-name fxvol-dev --profile itadmin
```

Le retour :
```json
{
  "AccessKey": {
    "UserName": "fxvol-dev",
    "AccessKeyId": "AKIA...",
    "SecretAccessKey": "wJalr...",
    "Status": "Active"
  }
}
```

⚠️ **Le `SecretAccessKey` n'est affiché qu'une seule fois**. Copie-le
immédiatement dans ton password manager. Si perdu : rotate via
`aws iam delete-access-key` + `create-access-key`.

⚠️ **Limite par user : 2 access keys actives max**. Si déjà 2 existent (ex:
historique), liste avec `aws iam list-access-keys --user-name fxvol-dev
--profile itadmin` et delete celle qui n'est plus utilisée avant d'en créer
une nouvelle.

### 6.2 Configurer le profile CLI

```powershell
aws configure --profile fxvol-dev
# AWS Access Key ID     : AKIA...               (du retour ci-dessus)
# AWS Secret Access Key : wJalr...              (du password manager)
# Default region name   : eu-west-1
# Default output format : json
```

Cela écrit dans `~/.aws/credentials` (Windows : `C:\Users\<user>\.aws\credentials`).
**Ce fichier contient un secret en clair sur disque** : c'est le compromis
des access keys classiques (vs SSO ephemeral). Mitigation :
- ACL Windows : `icacls $env:USERPROFILE\.aws /inheritance:r /grant:r "${env:USERNAME}:F"`
- Rotation manuelle tous les 90 jours via `aws iam create-access-key` + delete ancienne

### 6.3 Tester

```powershell
aws sts get-caller-identity --profile fxvol-dev
# Doit afficher :
# {
#   "UserId": "AIDA...",
#   "Account": "<ACCOUNT_ID>",
#   "Arn": "arn:aws:iam::<ACCOUNT_ID>:user/fxvol-dev"
# }
```

Tester la lecture SSM :
```powershell
aws ssm get-parameter --name /fxvol/prod/TRADING_MODE --profile fxvol-dev
# Doit renvoyer : "Value": "paper"
```

Tester decrypt KMS :
```powershell
aws ssm get-parameter --name /fxvol/prod/IB_USERID --with-decryption --profile fxvol-dev --query 'Parameter.Name' --output text
# Doit renvoyer : /fxvol/prod/IB_USERID
# (on utilise --query pour ne PAS afficher la valeur — règle CLAUDE.md)
```

✅ **Checkpoint phase 5** : tu peux lire les params SSM en CLI, le decrypt
fonctionne, ton ARN visible est `arn:aws:iam::<ACCOUNT_ID>:user/fxvol-dev`.

---

## 7. Phase 6 — Pousser les vrais secrets (via console AWS)

> **Décision projet** : aucune écriture de secrets en CLI. Toutes les
> modifications de valeurs SSM passent par la **console AWS**. C'est plus lent
> mais zéro risque de fausse manipulation (typo en clair dans le shell history,
> oubli de `--type SecureString`, oubli de `--key-id`...).

### Procédure console (pour chaque secret)

1. Aller sur https://eu-west-1.console.aws.amazon.com/systems-manager/parameters
2. Login en `fxvol-dev` (ou `itadmin` pour first-time setup avant que la
   policy `fxvol-dev-ssm` soit attachée).
3. Cliquer sur le paramètre (ex: `/fxvol/prod/IB_USERID`).
4. Bouton **Edit** en haut à droite.
5. **Tier** : Standard (laisser tel quel)
6. **Type** : SecureString (pour les 4 secrets) ou String (pour `TRADING_MODE`)
7. **KMS key source** : `My current account` → `alias/fxvol-secrets`
8. **Value** : coller la nouvelle valeur. La saisie est masquée par défaut sur
   les SecureString, et le champ n'apparaît jamais en clair après save.
9. **Save changes**.

À répéter pour : `IB_USERID`, `IB_PASSWORD`, `DB_PASSWORD`, `VNC_PASSWORD`.
Pour `TRADING_MODE`, laisser `paper` jusqu'au passage en `live` (décision
explicite, pas avant).

### Vérification post-modif (sans exposer la valeur)

```powershell
# Confirme que la version a été incrémentée + nouveau LastModifiedDate
aws ssm get-parameter --name /fxvol/prod/IB_USERID `
    --query '{Name:Parameter.Name,Version:Parameter.Version,Modified:Parameter.LastModifiedDate,Length:length(Parameter.Value)}' `
    --with-decryption --profile fxvol-dev
```

→ `Length` = nombre de caractères, pas la valeur. Permet de vérifier que la
nouvelle valeur n'est pas vide / pas trop courte sans la révéler.

### Recharger les secrets dans la session shell après modif

```powershell
.\scripts\load_secrets.ps1
```

Re-fetch SSM → `$env:*` mis à jour pour la session courante. Les containers
docker démarrés AVANT cet appel gardent l'ancienne valeur jusqu'au prochain
`docker compose up -d` qui relit `$env:*`.

---

## 8. Ce qu'il NE FAUT PAS faire maintenant

Le projet n'est pas prêt pour ces étapes — attends la PR correspondante :

| Action AWS | Quand l'activer | PR de référence |
|---|---|---|
| Créer une instance EC2 | R8 (~12/05) | R8 PR #51 `ci/r8-deploy-ec2` |
| Créer le rôle IAM `fxvol-ec2-secrets-role` | R8 | idem |
| Créer un instance profile + l'attacher | R8 | idem |
| Configurer Route 53 / domaine | post-R8 | hors scope migration |
| ACM cert pour HTTPS | post-R8 | hors scope |
| RDS Postgres managé | jamais (on garde le container) | — |
| Elasticache Redis managé | jamais (idem) | — |
| Secrets Manager (au lieu de SSM) | jamais (overkill, +$2/mois) | — |
| GitHub Actions OIDC vers AWS | si CI doit lire SSM | hors scope R9 |
| CloudWatch alarms sur GetParameter | R10+ | hors scope migration |
| AWS Backup pour EBS EC2 | R10+ | hors scope |

---

## 9. Vérification globale (phases 1-5 terminées)

Lance ce smoke check complet — tout doit passer :

```powershell
# 1. Profile actif (Arn doit être user/fxvol-dev)
aws sts get-caller-identity --profile fxvol-dev | ConvertFrom-Json | Select-Object Account, Arn

# 2. CMK accessible
aws kms describe-key --key-id alias/fxvol-secrets --profile fxvol-dev --query 'KeyMetadata.{Id:KeyId, Enabled:Enabled, Rotation:KeyRotationStatus}'

# 3. Les 5 params SSM existent
aws ssm describe-parameters --parameter-filters "Key=Name,Option=BeginsWith,Values=/fxvol/prod/" --profile fxvol-dev --query 'Parameters[].{Name:Name, Type:Type}'

# 4. Decrypt fonctionne (sans afficher la valeur)
aws ssm get-parameter --name /fxvol/prod/IB_USERID --with-decryption --profile fxvol-dev --query 'length(Parameter.Value)'

# 5. (Optionnel) Vérifier âge des access keys (rotate si > 90 jours)
aws iam list-access-keys --user-name fxvol-dev --profile itadmin --query 'AccessKeyMetadata[].{Id:AccessKeyId,Created:CreateDate,Status:Status}'
```

Si les 4 premières commandes renvoient un résultat propre, tu es prêt pour
la suite (R9 PRs ou simplement laisser dormir AWS jusqu'à R8).

---

## 10. Coûts mensuels attendus (pendant le développement)

| Service | Usage | Coût |
|---|---|---|
| KMS CMK | 1 clé | **$1** |
| KMS decrypt | ~100 calls/jour dev = ~3000/mois | **~$0.01** |
| SSM Parameter Store | 5 params standard | **$0** |
| IAM Identity Center | jusqu'à 50 users | **$0** |
| CloudTrail (90 jours rétention) | events management | **$0** |
| **Total dev (R9 actif)** | | **~$1.01/mois** |

Quand R8 sera mergée et l'EC2 lancée :

| Service additionnel | Spec | Coût |
|---|---|---|
| EC2 t3.small | 24/7 | **~$15** |
| EBS gp3 30 GB | volume root | **~$2.50** |
| Data transfer out | léger (API JSON) | **~$1** |
| Elastic IP | 1 IP statique | **$0** (attachée à instance running) |
| **Total prod (R8 actif)** | | **~$20/mois** |

---

## 11. Checklist finale

Phase 1-5 (le strict nécessaire pour le dev) :

- [ ] MFA activée sur root, `itadmin`, `fxvol-dev`
- [ ] Password root + access keys `fxvol-dev` en password manager
- [ ] CMK `alias/fxvol-secrets` créée, rotation annuelle activée
- [ ] ARN CMK + Account ID notés dans password manager
- [ ] 5 params SSM créés sous `/fxvol/prod/*` avec valeur placeholder
- [ ] Policy `fxvol-dev-ssm` attachée à user `fxvol-dev` avec les ARN réels
- [ ] AWS CLI v2 installé sur Windows
- [ ] Profile `fxvol-dev` configuré (`aws configure --profile fxvol-dev`)
- [ ] ACL Windows posée sur `~/.aws/credentials`
- [ ] Smoke check § 9 passe entièrement
- [ ] Cette doc relue avant de toucher aux vrais secrets en phase 6

Phase 6 et plus : à reprendre quand les scripts R9 seront mergés sur main, ou
en CLI manuel si tu veux tester avant.

---

## 12. Liens utiles

- **Plan détaillé R9 secrets** : `releases/r9-sandbox-secrets-ssm.md` (gitignored)
- **Architecture cible v2** : `releases/architecture_finale_project/00-architecture-main.md`
- **Règles secrets dans Claude** : `CLAUDE.md` § "Règle absolue : zéro exposition des secrets"
- **AWS console** : https://console.aws.amazon.com/ (login via `itadmin` ou `fxvol-dev`, jamais root)
- **AWS pricing calculator** : https://calculator.aws/

---

**Dernière mise à jour** : 2026-04-25 — projet à R3 sur main, R9 secrets en sandbox, R8 deploy non commencé.
