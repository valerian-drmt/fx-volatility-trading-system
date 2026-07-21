# AWS bootstrap — SSM Parameter Store + KMS + IAM for fx-vol secrets

**Manual** procedure to prepare the AWS account before enabling the
`load_secrets.ps1` / `load_secrets.sh` scripts that consume SSM Parameter Store.

Goal: store 5 sensitive parameters (`IB_USERID`, `IB_PASSWORD`,
`DB_PASSWORD`, `VNC_PASSWORD`, `TRADING_MODE`) in SSM, encrypted with a dedicated
KMS CMK, accessible:
- in local dev via an IAM user + AWS SSO
- in EC2 prod via an IAM instance role

To be executed **once**, by an admin user of the AWS account.

---

## 0. Prerequisites

- Active AWS account (root or IAM admin)
- AWS CLI v2 installed (`aws --version` ≥ 2.15)
- Target region: `eu-west-1` (everything lives here — SSM, KMS, EC2)
- AWS account ID retrievable via: `aws sts get-caller-identity`

Throughout the rest of this doc, replace:
- `<ACCOUNT_ID>` with your account ID (12 digits)
- `<REGION>` with `eu-west-1`

---

## 1. Create the KMS CMK `alias/fxvol-secrets`

```bash
# Create the key
aws kms create-key \
  --description "fx-vol secrets encryption key" \
  --key-usage ENCRYPT_DECRYPT \
  --region eu-west-1 \
  --tags TagKey=Project,TagValue=fxvol
```

Grab the returned `KeyId` (UUID) → it will be used as `<CMK_KEY_ID>`.

```bash
# Create the human-readable alias
aws kms create-alias \
  --alias-name alias/fxvol-secrets \
  --target-key-id <CMK_KEY_ID> \
  --region eu-west-1

# Enable automatic annual rotation
aws kms enable-key-rotation \
  --key-id <CMK_KEY_ID> \
  --region eu-west-1
```

**Verification**:
```bash
aws kms describe-key --key-id alias/fxvol-secrets --region eu-west-1
# should return KeyState=Enabled, KeyRotationEnabled in get-key-rotation-status
```

---

## 2. Create the 5 SSM parameters (placeholder values)

We create the parameters **empty** (or placeholder) here. The real values
will be pushed later via the AWS console (see `SETUP.md` § 7).
**Historical note**: this document initially mentioned a
`put_secrets.ps1` script, removed on 2026-04-28 — decision: secrets are edited
via the console only, to avoid CLI mishandling.

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

**Verification**:
```bash
aws ssm describe-parameters \
  --parameter-filters "Key=Name,Option=BeginsWith,Values=/fxvol/prod/" \
  --region eu-west-1
# should return 5 entries: 4 SecureString + 1 String
```

---

## 3. Create the dev IAM user `fxvol-dev`

### 3.1 User + programmatic access

```bash
aws iam create-user --user-name fxvol-dev \
  --tags Key=Project,Value=fxvol
```

### 3.2 Inline policy `fxvol-dev-ssm-rw`

Save the following content to `fxvol-dev-policy.json` (replace the
`<ACCOUNT_ID>` and `<CMK_KEY_ID>` placeholders):

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

Attach:
```bash
aws iam put-user-policy \
  --user-name fxvol-dev \
  --policy-name fxvol-dev-ssm-rw \
  --policy-document file://fxvol-dev-policy.json
```

### 3.3 Enable MFA (mandatory)

From the AWS console: IAM → Users → fxvol-dev → Security credentials →
Assign MFA device (Authenticator app). **Do not skip** — without MFA, a leak
of the access keys = direct access to the IB secrets.

### 3.4 (Recommended option) Replace with AWS SSO

If you already use AWS IAM Identity Center (ex-SSO), prefer:
1. Create a permission set `fxvol-dev-secrets` with the policy above
2. Attach it to your IdC user on the target account
3. On Windows: `aws configure sso` + profile name `fxvol-dev`

→ no static access keys, temporary sessions, automatic rotation.

---

## 4. Create the EC2 IAM role `fxvol-ec2-secrets-role`

### 4.1 Trust policy

Save to `fxvol-ec2-trust-policy.json`:

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

# Read-only policy (no PutParameter from EC2)
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

### 4.4 Attach to the EC2 — **deferred to R8** (2026-05-12)

No EC2 is deployed during R9. The role and the instance profile
remain dormant until the R8 release ("Deploy prod EC2"). At that point,
run the following with an admin profile (not `fxvol-dev`, which lacks
`ec2:AssociateIamInstanceProfile`):

```bash
# Retrieve the instance id (or from the console)
aws ec2 describe-instances \
  --filters "Name=tag:Project,Values=fxvol" \
  --query "Reservations[].Instances[].InstanceId" \
  --output text \
  --region eu-west-1 \
  --profile admin

aws ec2 associate-iam-instance-profile \
  --instance-id <i-xxxxxxx> \
  --iam-instance-profile Name=fxvol-ec2-instance-profile \
  --region eu-west-1 \
  --profile admin
```

**Verification from the EC2** (SSH into it, post-attach):
```bash
aws sts get-caller-identity
# should return an ARN of the form:
# arn:aws:sts::552269855056:assumed-role/fxvol-ec2-secrets-role/i-xxxxx
# IF you see an IAM user arn:aws:iam::.../user/..., the instance profile is not attached
```

---

## 5. AWS SSO setup on Windows (local dev)

In PowerShell:

```powershell
aws configure sso
# SSO start URL       : https://<your-org>.awsapps.com/start
# SSO Region          : eu-west-1
# Account             : <ACCOUNT_ID>
# Role                : fxvol-dev-secrets
# CLI default region  : eu-west-1
# CLI default output  : json
# CLI profile name    : fxvol-dev
```

Test:
```powershell
aws sso login --profile fxvol-dev
aws sts get-caller-identity --profile fxvol-dev
# should return the ARN of the assumed permission set
aws ssm get-parameter --name /fxvol/prod/TRADING_MODE --profile fxvol-dev --region eu-west-1
# should return {"Parameter": {..., "Value": "paper"}}
```

If SSO is not available on your account → use the `fxvol-dev` user's access keys:
```powershell
aws configure --profile fxvol-dev
# AWS Access Key ID     : <keys created in step 3.1>
# AWS Secret Access Key : ...
# Default region        : eu-west-1
# Default output format : json
```

⚠️ Store the access keys **in Windows Credential Manager** or a password
manager, not in `~/.aws/credentials` in plaintext if possible (even if
the file is `0600`).

---

## 6. Existing resources (account 552269855056, eu-west-1)

State as of 2026-04-23.

| Resource | Identifier / ARN |
|---|---|
| KMS CMK | `alias/fxvol-secrets` — KeyId `bbc7ef4a-0b3e-4019-a7db-4502c4662f30`, annual rotation ON |
| SSM params `/fxvol/prod/*` | `IB_USERID`, `IB_PASSWORD`, `DB_PASSWORD`, `VNC_PASSWORD` (SecureString), `TRADING_MODE` (String=`paper`) |
| Dev IAM user | `fxvol-dev` + inline policy `fxvol-dev-ssm-rw` (SSM rw + KMS Decrypt/GenerateDataKey) |
| EC2 IAM role | `fxvol-ec2-secrets-role` — `arn:aws:iam::552269855056:role/fxvol-ec2-secrets-role` (RoleId `AROAYBFOZHFIM6KA3IZAY`) |
| Role policy | Inline `fxvol-ec2-ssm-read` (SSM Get* + KMS Decrypt, **read-only**) |
| Instance profile | `fxvol-ec2-instance-profile` — `arn:aws:iam::552269855056:instance-profile/fxvol-ec2-instance-profile` (Id `AIPAYBFOZHFIB46B5H7H5`) |

`aws iam simulate-principal-policy` checks run on 2026-04-23:

- `ssm:GetParameter` on `/fxvol/prod/IB_USERID` → **allowed** ✓
- `kms:Decrypt` on the `fxvol-secrets` CMK → **allowed** ✓
- `ssm:PutParameter` on `/fxvol/prod/IB_USERID` → **implicitDeny** ✓ (read-only confirmed)
- `ssm:GetParameter` on `/other/something` → **implicitDeny** ✓ (wildcard properly scoped)

---

## 7. Bootstrap checklist

- [x] CMK `alias/fxvol-secrets` created, rotation enabled
- [x] 5 SSM parameters `/fxvol/prod/*` exist (real values pushed via AWS console)
- [x] IAM user `fxvol-dev` created with policy `fxvol-dev-ssm-rw`
- [ ] MFA enabled on `fxvol-dev` (decision pending)
- [x] IAM role `fxvol-ec2-secrets-role` + instance profile created (simulate-principal-policy OK)
- [ ] Instance profile attached to the fxvol EC2 — **deferred to R8** (no EC2 deployed)
- [x] `aws sts get-caller-identity --profile fxvol-dev` returns the user's ARN (access keys, SSO not used)
- [ ] `aws sts get-caller-identity` from EC2 returns the role's ARN — **deferred to R8**
- [x] `aws ssm get-parameter --name /fxvol/prod/TRADING_MODE --profile fxvol-dev` returns `paper`

**Boxes checkable in R9**: all except the 3 deferred ones (MFA, EC2 attach, EC2 sts).
Step 4 **closed** from the code-capitalization standpoint. On R8 deployment day
(~2026-05-12), run § 4.4 above and check the last 2 boxes.

---

## 7. Estimated cost (recap)

| Service | Monthly usage | Cost |
|---|---|---|
| SSM Parameter Store standard tier | 5 params, ~100 reads/day | free (< 10k) |
| Dedicated KMS CMK | 1 key + ~3000 decrypt/month | ~$1.03 |
| IAM user/role/policies | unlimited | free |
| CloudTrail management events | 90 days retention | free |
| **Total** | | **~$1/month** |

---

## 8. Rollback (if we want to undo everything)

```bash
# Delete the parameters
for NAME in IB_USERID IB_PASSWORD DB_PASSWORD VNC_PASSWORD TRADING_MODE; do
  aws ssm delete-parameter --name "/fxvol/prod/$NAME" --region eu-west-1
done

# Detach the instance profile from the EC2
aws ec2 disassociate-iam-instance-profile --association-id <assoc-id>
aws iam remove-role-from-instance-profile \
  --instance-profile-name fxvol-ec2-instance-profile \
  --role-name fxvol-ec2-secrets-role
aws iam delete-instance-profile --instance-profile-name fxvol-ec2-instance-profile

# Delete role + policies
aws iam delete-role-policy --role-name fxvol-ec2-secrets-role --policy-name fxvol-ec2-ssm-read
aws iam delete-role --role-name fxvol-ec2-secrets-role

# Delete user + policy
aws iam delete-user-policy --user-name fxvol-dev --policy-name fxvol-dev-ssm-rw
aws iam delete-user --user-name fxvol-dev

# Schedule CMK deletion (min 7-day window, cannot be immediate)
aws kms delete-alias --alias-name alias/fxvol-secrets --region eu-west-1
aws kms schedule-key-deletion --key-id <CMK_KEY_ID> --pending-window-in-days 7 --region eu-west-1
```
