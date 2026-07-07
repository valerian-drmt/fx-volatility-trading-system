# fxvol - provision the OIDC + SSM deploy path.
# Run in PowerShell as the 'admin' profile. Safe to re-run (idempotent checks).
# Windows PowerShell 5.1: we branch on $LASTEXITCODE, not try/catch, because
# native CLI errors don't raise PowerShell exceptions there.

$env:AWS_PROFILE = "admin"
$REGION     = "eu-west-1"
$ACCOUNT    = "552269855056"
$REPO       = "valerian-drmt/fx-volatility-trading-system"
$BUCKET     = "fxvol-deploy"
$INSTANCE   = "i-082e72f0186c9d019"
$DOMAIN     = "valeriandarmente.dev"
$HOSTROLE   = "fxvol-ec2-secrets-role"     # existing instance role
$DEPLOYROLE = "fxvol-deploy-role"          # new, assumed by GitHub via OIDC

Write-Host "identity:" (aws sts get-caller-identity --query Account --output text)

# 1) GitHub OIDC provider ----------------------------------------------------
$oidcArn = "arn:aws:iam::$ACCOUNT`:oidc-provider/token.actions.githubusercontent.com"
aws iam get-open-id-connect-provider --open-id-connect-provider-arn $oidcArn 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    aws iam create-open-id-connect-provider `
        --url https://token.actions.githubusercontent.com `
        --client-id-list sts.amazonaws.com `
        --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1 | Out-Null
    Write-Host "created OIDC provider"
} else { Write-Host "OIDC provider exists (ok)" }

# 2) deploy role: trust (pinned to repo + production environment) -------------
$trust = @"
{ "Version":"2012-10-17","Statement":[{
  "Effect":"Allow",
  "Principal":{"Federated":"$oidcArn"},
  "Action":"sts:AssumeRoleWithWebIdentity",
  "Condition":{"StringEquals":{
    "token.actions.githubusercontent.com:aud":"sts.amazonaws.com",
    "token.actions.githubusercontent.com:sub":"repo:$REPO`:environment:production"
  }}
}]}
"@
Set-Content -Path trust.json -Value $trust -Encoding ascii
aws iam get-role --role-name $DEPLOYROLE 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    aws iam create-role --role-name $DEPLOYROLE --assume-role-policy-document file://trust.json | Out-Null
    Write-Host "created role $DEPLOYROLE"
} else {
    aws iam update-assume-role-policy --role-name $DEPLOYROLE --policy-document file://trust.json
    Write-Host "updated trust on $DEPLOYROLE"
}

# 3) deploy role: permissions (S3 put, SSM send + read result) ---------------
$perms = @"
{ "Version":"2012-10-17","Statement":[
  {"Sid":"PutPayload","Effect":"Allow","Action":["s3:PutObject"],
   "Resource":"arn:aws:s3:::$BUCKET/*"},
  {"Sid":"SendDeployCommand","Effect":"Allow","Action":["ssm:SendCommand"],
   "Resource":[
     "arn:aws:ec2:$REGION`:$ACCOUNT`:instance/$INSTANCE",
     "arn:aws:ssm:$REGION`::document/AWS-RunShellScript"]},
  {"Sid":"ReadCommandResult","Effect":"Allow",
   "Action":["ssm:GetCommandInvocation","ssm:ListCommandInvocations","ssm:ListCommands"],
   "Resource":"*"}
]}
"@
Set-Content -Path deploy-perms.json -Value $perms -Encoding ascii
aws iam put-role-policy --role-name $DEPLOYROLE --policy-name fxvol-deploy-perms --policy-document file://deploy-perms.json
Write-Host "attached deploy permissions"

# 4) S3 deploy bucket (private) ----------------------------------------------
aws s3api head-bucket --bucket $BUCKET 2>$null
if ($LASTEXITCODE -ne 0) {
    aws s3api create-bucket --bucket $BUCKET --region $REGION `
        --create-bucket-configuration LocationConstraint=$REGION | Out-Null
    Write-Host "created bucket $BUCKET"
} else { Write-Host "bucket $BUCKET exists (ok)" }
aws s3api put-public-access-block --bucket $BUCKET `
    --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
Write-Host "locked public access on $BUCKET"

# 5) host instance-role additions: S3 read + SSM read + KMS decrypt ----------
$kmsArn = (aws kms describe-key --key-id alias/fxvol-secrets --query KeyMetadata.Arn --output text --region $REGION)
$hostpol = @"
{ "Version":"2012-10-17","Statement":[
  {"Sid":"GetDeployPayload","Effect":"Allow","Action":["s3:GetObject"],
   "Resource":"arn:aws:s3:::$BUCKET/*"},
  {"Sid":"ReadProdParams","Effect":"Allow","Action":["ssm:GetParameter","ssm:GetParameters"],
   "Resource":"arn:aws:ssm:$REGION`:$ACCOUNT`:parameter/fxvol/prod/*"},
  {"Sid":"DecryptSecrets","Effect":"Allow","Action":["kms:Decrypt"],
   "Resource":"$kmsArn"}
]}
"@
Set-Content -Path host-additions.json -Value $hostpol -Encoding ascii
aws iam put-role-policy --role-name $HOSTROLE --policy-name fxvol-deploy-host-additions --policy-document file://host-additions.json
Write-Host "attached host-role additions to $HOSTROLE"

# 6) repo variables ----------------------------------------------------------
gh variable set AWS_DEPLOY_ROLE_ARN -R $REPO -b "arn:aws:iam::$ACCOUNT`:role/$DEPLOYROLE"
gh variable set AWS_REGION          -R $REPO -b "$REGION"
gh variable set DEPLOY_BUCKET       -R $REPO -b "$BUCKET"
gh variable set EC2_INSTANCE_ID     -R $REPO -b "$INSTANCE"
gh variable set DEPLOY_DOMAIN       -R $REPO -b "$DOMAIN"
Write-Host "set repo variables"

Write-Host ""
Write-Host "DONE (AWS + repo vars). Remaining manual steps:"
Write-Host "  A. Parameter Store (console): create SecureString /fxvol/prod/GHCR_TOKEN"
Write-Host "     KMS key alias/fxvol-secrets, value = a GitHub PAT with read:packages."
Write-Host "  B. Commit the two repo files (deploy.yml, infrastructure/ec2/remote-deploy.sh),"
Write-Host "     open the PR on sandbox -> squash to main."
