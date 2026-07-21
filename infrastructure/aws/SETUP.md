# AWS Setup — fx-volatility-trading-system

> Complete procedure to configure AWS from scratch, adapted to the **current
> state of the project** (R3 in progress on main, R6 Docker planned ~05/05, R8 EC2
> deploy planned ~12/05).
>
> The project **is not in prod yet**. This doc tells you **what to do
> now** (the bare minimum for local SSM dev) and **what to hold off on**
> (EC2, deploy.yml, IAM role) until the corresponding PRs land.

---

## 0. Overview — where we stand

| Component | Project status | AWS action required | When |
|---|---|---|---|
| v1.x code (local PyQt) | ✅ running | None | — |
| Postgres schema + Alembic (R1) | ✅ merged | None | — |
| Async DB writer (R2) | ✅ merged | None | — |
| Redis broker (R3) | 🟡 in progress | None | — |
| FastAPI backend (R4) | 🔒 coded locally | None | — |
| React frontend (R5) | 🔒 coded locally | None | — |
| Docker compose prod (R6) | 🔒 coded locally | None | — |
| Services split (R7) | 🔒 coded locally | None | — |
| **R8 — EC2 deploy** | 🔒 coded locally | **EC2 + IAM role + SSM in prod** | ~12/05 |
| **R9 — SSM secrets** | 🟡 sandbox | **KMS + SSM + dev IAM user** | right now if you want to drop the local `.env` |

**Practical consequence**: you can **do everything on AWS today**, or you can
do **only phases 1-3 (local SSM dev)** and postpone EC2 to May. Both
paths are valid — the R9 secrets migration was designed so that dev
and prod coexist (dev moves to SSM without touching prod, and vice versa).

---

## 1. Prerequisites (before anything else)

### On your side

- [ ] Valid credit card (the AWS account requires one even though projected usage is ~$1/month)
- [ ] Mobile phone number for verification (SMS code)
- [ ] Email address **dedicated** to the root account (ideally `aws-fxvol@<your-domain>`
      or `valeriandarmente+aws@gmail.com` — alias separate from the personal account)
- [ ] A password manager (Bitwarden, 1Password, KeePass) to store the
      root passwords + access keys you are going to generate

### On the Windows machine

```powershell
# 1. AWS CLI v2 (>= 2.15)
winget install -e --id Amazon.AWSCLI
# Verify
aws --version
# Should print: aws-cli/2.x.x Python/3.x ...

# 2. (Optional but recommended) Session Manager plugin for SSM start-session
winget install -e --id Amazon.SessionManagerPlugin
```

No need for `boto3` on the machine: the scripts use the `aws` CLI.

---

## 2. Phase 1 — AWS account + IAM users (ALREADY DONE)

✅ **Current state**:
- AWS root account created (eu-west-1)
- IAM user `itadmin` (admin, for creating resources)
- IAM user `fxvol-dev` (to be used for runtime operations on SSM)

**To verify now if not already done** (CRITICAL):

- [ ] **MFA enabled on the root account**: Console (root) → Security credentials
      → Multi-factor authentication → Add MFA. Recovery codes in the
      password manager. **Never log in as root again except in an emergency**.
- [ ] **MFA enabled on `itadmin`**: Console (root) → IAM → Users → itadmin
      → Security credentials → Assign MFA device.
- [ ] **MFA enabled on `fxvol-dev`**: same. Without MFA, refuse any `aws iam
      put-user-policy` that grants sensitive rights (KMS decrypt).
- [ ] **Default region `eu-west-1`**: all scripts and commands
      in this doc are hardcoded to `eu-west-1`.

**Target IAM architecture (classic, not SSO)**:

```
Account <ID>
├─ root                      MFA, locked, emergency only
├─ itadmin                   MFA, AdministratorAccess (manage resources)
└─ fxvol-dev                 MFA, policy fxvol-dev-ssm (SSM read/write + KMS on scope /fxvol/prod/*)
```

`itadmin` creates the CMK + the SSM params + the policies (phases 2-4 below).
`fxvol-dev` consumes day-to-day via the `load_secrets.ps1` script (phase 5+).
**Editing SSM values = via the AWS console only** (no CLI script
provided — to avoid mishandling of secrets).

---

## 3. Phase 2 — KMS CMK to encrypt the secrets

> Everything in the console (eu-west-1, check top right).

1. **KMS console** → Customer managed keys → Create key
2. Configuration:
   - Key type: **Symmetric**
   - Key usage: **Encrypt and decrypt**
   - Advanced: Single-region, **Key material origin = KMS**
3. Alias: `fxvol-secrets` → the ARN will be `arn:aws:kms:eu-west-1:<ACCOUNT_ID>:alias/fxvol-secrets`
4. Description: `CMK for FX vol trading system secrets in SSM Parameter Store`
5. Key administrators: IAM user `itadmin` (and no one else)
6. Key users: IAM user `fxvol-dev` (the EC2 role will be added in phase R8)
7. Review → Finish
8. Once created → **Key rotation** tab → check "Automatically rotate this
   KMS key every year"

📋 **Note the full ARN** in your password manager:
`arn:aws:kms:eu-west-1:<ACCOUNT_ID>:key/<UUID>` — you will need it
in phase 5 and later for the EC2 role.

📋 **Note your AWS Account ID** (12 digits) visible at the top right of the
console: `<ACCOUNT_ID>`. Also to be stored in the password manager.

**Cost**: $1/month flat for the CMK + $0.03 per 10k decrypt (negligible).

---

## 4. Phase 3 — SSM Parameter Store: create the empty parameters

You can do this in the console OR via CLI (you don't have the CLI configured yet, so
console for this first time).

> Console → Systems Manager → Parameter Store → Create parameter

Create 5 parameters with the following names and types. **Put a dummy value
for now**, we will fill them with the real values in phase 6.

| Name | Tier | Type | KMS key | Value (dummy) |
|---|---|---|---|---|
| `/fxvol/prod/IB_USERID` | Standard | SecureString | `alias/fxvol-secrets` | `placeholder` |
| `/fxvol/prod/IB_PASSWORD` | Standard | SecureString | `alias/fxvol-secrets` | `placeholder` |
| `/fxvol/prod/DB_PASSWORD` | Standard | SecureString | `alias/fxvol-secrets` | `placeholder` |
| `/fxvol/prod/VNC_PASSWORD` | Standard | SecureString | `alias/fxvol-secrets` | `placeholder` |
| `/fxvol/prod/TRADING_MODE` | Standard | String | (n/a) | `paper` |

**Why placeholders now**: the `load_secrets.ps1` scripts
test that the params exist. Having 5 entries in SSM makes it possible to validate the
IAM → KMS → SSM chain end-to-end before putting in the real secrets.

⚠️ Tier **Standard** only (free up to 10k params). NEVER pick
"Advanced" (paid).

✅ **Phase 3 checkpoint**: `Parameter Store` shows 5 entries under the path
`/fxvol/prod/`.

---

## 5. Phase 4 — IAM policy on `fxvol-dev`: read/write SSM + KMS

Now that we have the CMK ARN and the Account ID, we can write the IAM policy
attached to the IAM user `fxvol-dev` (inline policy).

1. Save the JSON below to `fxvol-dev-policy.json` (replacing
   `<ACCOUNT_ID>` and `<CMK_KEY_ID>` with the real values):

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

2. Attach the policy via CLI (run this from an `itadmin` session):

```powershell
aws iam put-user-policy `
  --user-name fxvol-dev `
  --policy-name fxvol-dev-ssm `
  --policy-document file://fxvol-dev-policy.json `
  --profile itadmin
```

3. Verify that the policy is properly attached:
```powershell
aws iam list-user-policies --user-name fxvol-dev --profile itadmin
# Should list: "fxvol-dev-ssm"
```

**Why `kms:Encrypt` too**: required for `ssm:PutParameter` on a
SecureString (KMS generates a data key). `kms:Decrypt` alone = you can read but
not write.

**Why `inline` rather than `managed`**: managed policies are reusable
but here we have 1 user and 1 scope. Inline keeps the policy glued to the identity,
immediately visible in `aws iam get-user`. Deleting the user = automatic
deletion of the policy.

---

## 6. Phase 5 — Configure the AWS CLI on Windows (classic access keys)

### 6.1 Generate the access keys for `fxvol-dev`

Run this from an `itadmin` session (which has the right to create access keys
for other users):

```powershell
aws iam create-access-key --user-name fxvol-dev --profile itadmin
```

The return:
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

⚠️ **The `SecretAccessKey` is only displayed once**. Copy it
immediately into your password manager. If lost: rotate via
`aws iam delete-access-key` + `create-access-key`.

⚠️ **Limit per user: max 2 active access keys**. If 2 already exist (e.g.
historical), list with `aws iam list-access-keys --user-name fxvol-dev
--profile itadmin` and delete the one no longer in use before creating
a new one.

### 6.2 Configure the CLI profile

```powershell
aws configure --profile fxvol-dev
# AWS Access Key ID     : AKIA...               (from the return above)
# AWS Secret Access Key : wJalr...              (from the password manager)
# Default region name   : eu-west-1
# Default output format : json
```

This writes to `~/.aws/credentials` (Windows: `C:\Users\<user>\.aws\credentials`).
**This file contains a plaintext secret on disk**: that is the trade-off
of classic access keys (vs SSO ephemeral). Mitigation:
- Windows ACL: `icacls $env:USERPROFILE\.aws /inheritance:r /grant:r "${env:USERNAME}:F"`
- Manual rotation every 90 days via `aws iam create-access-key` + delete the old one

### 6.3 Test

```powershell
aws sts get-caller-identity --profile fxvol-dev
# Should print:
# {
#   "UserId": "AIDA...",
#   "Account": "<ACCOUNT_ID>",
#   "Arn": "arn:aws:iam::<ACCOUNT_ID>:user/fxvol-dev"
# }
```

Test SSM read:
```powershell
aws ssm get-parameter --name /fxvol/prod/TRADING_MODE --profile fxvol-dev
# Should return: "Value": "paper"
```

Test KMS decrypt:
```powershell
aws ssm get-parameter --name /fxvol/prod/IB_USERID --with-decryption --profile fxvol-dev --query 'Parameter.Name' --output text
# Should return: /fxvol/prod/IB_USERID
# (we use --query to NOT display the value — CLAUDE.md rule)
```

✅ **Phase 5 checkpoint**: you can read the SSM params via CLI, decrypt
works, your visible ARN is `arn:aws:iam::<ACCOUNT_ID>:user/fxvol-dev`.

---

## 7. Phase 6 — Push the real secrets (via AWS console)

> **Project decision**: no writing of secrets via CLI. All
> SSM value modifications go through the **AWS console**. It is slower
> but zero risk of mishandling (plaintext typo in shell history,
> forgetting `--type SecureString`, forgetting `--key-id`...).

### Console procedure (for each secret)

1. Go to https://eu-west-1.console.aws.amazon.com/systems-manager/parameters
2. Log in as `fxvol-dev` (or `itadmin` for first-time setup before the
   `fxvol-dev-ssm` policy is attached).
3. Click on the parameter (e.g. `/fxvol/prod/IB_USERID`).
4. **Edit** button at the top right.
5. **Tier**: Standard (leave as is)
6. **Type**: SecureString (for the 4 secrets) or String (for `TRADING_MODE`)
7. **KMS key source**: `My current account` → `alias/fxvol-secrets`
8. **Value**: paste the new value. Input is masked by default on
   SecureStrings, and the field never appears in plaintext after save.
9. **Save changes**.

Repeat for: `IB_USERID`, `IB_PASSWORD`, `DB_PASSWORD`, `VNC_PASSWORD`.
For `TRADING_MODE`, leave `paper` until the switch to `live` (explicit
decision, not before).

### Post-modification verification (without exposing the value)

```powershell
# Confirms the version was incremented + new LastModifiedDate
aws ssm get-parameter --name /fxvol/prod/IB_USERID `
    --query '{Name:Parameter.Name,Version:Parameter.Version,Modified:Parameter.LastModifiedDate,Length:length(Parameter.Value)}' `
    --with-decryption --profile fxvol-dev
```

→ `Length` = number of characters, not the value. Lets you verify that the
new value is not empty / not too short without revealing it.

### Reload the secrets in the shell session after modification

```powershell
.\scripts\load_secrets.ps1
```

Re-fetch SSM → `$env:*` updated for the current session. Docker containers
started BEFORE this call keep the old value until the next
`docker compose up -d` which re-reads `$env:*`.

---

## 8. What NOT to do now

The project is not ready for these steps — wait for the corresponding PR:

| AWS action | When to enable | Reference PR |
|---|---|---|
| Create an EC2 instance | R8 (~12/05) | R8 PR #51 `ci/r8-deploy-ec2` |
| Create the IAM role `fxvol-ec2-secrets-role` | R8 | same |
| Create an instance profile + attach it | R8 | same |
| Configure Route 53 / domain | post-R8 | out of migration scope |
| ACM cert for HTTPS | post-R8 | out of scope |
| Managed RDS Postgres | never (we keep the container) | — |
| Managed Elasticache Redis | never (same) | — |
| Secrets Manager (instead of SSM) | never (overkill, +$2/month) | — |
| GitHub Actions OIDC to AWS | if CI must read SSM | out of R9 scope |
| CloudWatch alarms on GetParameter | R10+ | out of migration scope |
| AWS Backup for EC2 EBS | R10+ | out of scope |

---

## 9. Global verification (phases 1-5 completed)

Run this full smoke check — everything must pass:

```powershell
# 1. Active profile (Arn must be user/fxvol-dev)
aws sts get-caller-identity --profile fxvol-dev | ConvertFrom-Json | Select-Object Account, Arn

# 2. CMK accessible
aws kms describe-key --key-id alias/fxvol-secrets --profile fxvol-dev --query 'KeyMetadata.{Id:KeyId, Enabled:Enabled, Rotation:KeyRotationStatus}'

# 3. The 5 SSM params exist
aws ssm describe-parameters --parameter-filters "Key=Name,Option=BeginsWith,Values=/fxvol/prod/" --profile fxvol-dev --query 'Parameters[].{Name:Name, Type:Type}'

# 4. Decrypt works (without displaying the value)
aws ssm get-parameter --name /fxvol/prod/IB_USERID --with-decryption --profile fxvol-dev --query 'length(Parameter.Value)'

# 5. (Optional) Check the age of the access keys (rotate if > 90 days)
aws iam list-access-keys --user-name fxvol-dev --profile itadmin --query 'AccessKeyMetadata[].{Id:AccessKeyId,Created:CreateDate,Status:Status}'
```

If the first 4 commands return a clean result, you are ready for
what comes next (R9 PRs or simply letting AWS sit idle until R8).

---

## 10. Expected monthly costs (during development)

| Service | Usage | Cost |
|---|---|---|
| KMS CMK | 1 key | **$1** |
| KMS decrypt | ~100 calls/day dev = ~3000/month | **~$0.01** |
| SSM Parameter Store | 5 standard params | **$0** |
| IAM Identity Center | up to 50 users | **$0** |
| CloudTrail (90 days retention) | management events | **$0** |
| **Total dev (R9 active)** | | **~$1.01/month** |

When R8 is merged and the EC2 launched:

| Additional service | Spec | Cost |
|---|---|---|
| EC2 t3.small | 24/7 | **~$15** |
| EBS gp3 30 GB | root volume | **~$2.50** |
| Data transfer out | light (JSON API) | **~$1** |
| Elastic IP | 1 static IP | **$0** (attached to a running instance) |
| **Total prod (R8 active)** | | **~$20/month** |

---

## 11. Final checklist

Phases 1-5 (the bare minimum for dev):

- [ ] MFA enabled on root, `itadmin`, `fxvol-dev`
- [ ] Root password + `fxvol-dev` access keys in the password manager
- [ ] CMK `alias/fxvol-secrets` created, annual rotation enabled
- [ ] CMK ARN + Account ID noted in the password manager
- [ ] 5 SSM params created under `/fxvol/prod/*` with placeholder values
- [ ] Policy `fxvol-dev-ssm` attached to user `fxvol-dev` with the real ARNs
- [ ] AWS CLI v2 installed on Windows
- [ ] Profile `fxvol-dev` configured (`aws configure --profile fxvol-dev`)
- [ ] Windows ACL set on `~/.aws/credentials`
- [ ] Smoke check § 9 passes entirely
- [ ] This doc re-read before touching the real secrets in phase 6

Phase 6 and beyond: to be resumed when the R9 scripts are merged to main, or
manually via CLI if you want to test before.

---

## 12. Useful links

- **Detailed R9 secrets plan**: `releases/r9-sandbox-secrets-ssm.md` (gitignored)
- **Target v2 architecture**: `releases/architecture_finale_project/00-architecture-main.md`
- **Secrets rules in Claude**: `CLAUDE.md` § "Absolute rule: zero exposure of secrets"
- **AWS console**: https://console.aws.amazon.com/ (log in via `itadmin` or `fxvol-dev`, never root)
- **AWS pricing calculator**: https://calculator.aws/

---

**Last updated**: 2026-04-25 — project at R3 on main, R9 secrets in sandbox, R8 deploy not started.
