# Secrets

All project secrets live in **AWS SSM Parameter Store** under `/fxvol/prod/*`,
KMS-encrypted (`SecureString`). There is no `.env` file in git and no secret in
any container image. Related: [deployment.md](deployment.md),
[local-stack.md](local-stack.md).

## Parameters

| Parameter | Used by |
|---|---|
| `/fxvol/prod/DB_PASSWORD` | Postgres + every service's `DATABASE_URL`. |
| `/fxvol/prod/REDIS_PASSWORD` | Redis `--requirepass` + `REDIS_URL`. |
| `/fxvol/prod/IB_USERID`, `/IB_PASSWORD` | IB Gateway login (ib profile only). |
| `/fxvol/prod/VNC_PASSWORD` | IB Gateway VNC server. |
| `/fxvol/prod/AUTH_SECRET`, `/AUTH_PASSWORD_HASH` | The single-trader write-login boundary. |
| `/fxvol/prod/FRED_API_KEY` | Events pipeline (optional). |
| `/fxvol/prod/GHCR_TOKEN` | GHCR pull, only if the packages are private (optional). |

## Load-to-RAM, never on disk

Secrets are loaded into a process environment and never written to disk locally:

- **Local (Windows)** — [`scripts/local/load_secrets.ps1`](../../scripts/local/load_secrets.ps1)
  calls `aws ssm get-parameters --with-decryption` and injects the values into the
  current PowerShell session as env vars. Docker Compose inherits them from the
  parent process. Nothing is persisted; closing the shell clears them.
- **EC2 (host)** — [`scripts/aws/load_secrets.sh`](../../scripts/aws/load_secrets.sh)
  is the shell equivalent for manual host work.

The one file that *does* hold rendered values is `/opt/fxvol/.env` on the prod
host — written mode `0600` (`umask 077`) by `remote-deploy.sh`, read only by the
compose stack. It is regenerated from SSM on every deploy and never leaves the box.

## The host renders `.env` from SSM, never through GitHub

Secrets never pass through GitHub Actions. The deploy runner federates into AWS via
OIDC and only passes **non-secret** config (image tag, compose profiles, owner) in
the SSM command. The host itself reads the encrypted parameters using its own
instance role (`fxvol-ec2-secrets-role`) and renders `/opt/fxvol/.env`:

```bash
ssm() { aws ssm get-parameter --name "$1" --with-decryption \
          --query Parameter.Value --output text --region "$REGION"; }
DB_PASSWORD="$(ssm /fxvol/prod/DB_PASSWORD)"
```

IB + VNC creds are fetched **only** when the `ib` compose profile is armed, so a
core-only public box never holds broker credentials on disk. Auth params are
appended only when present in SSM, so an unprovisioned host keeps the api's
fail-closed defaults rather than serving with a forgeable key.

## Writing secrets

Parameters are created and edited **exclusively via the AWS console**
(Systems Manager → Parameter Store) — there is no CLI write path in this repo.
`aws ssm put-parameter --overwrite` on the affected parameter is the rotation
command when a secret must be rotated.

## The never-echo rule

Secret **values** are never printed by any command, directly or indirectly. This
covers every shell: no `echo $DB_PASSWORD`, no `printenv` / `env` / `Get-ChildItem
Env:` (a full env dump leaks everything), no `cat .env` or `cat ~/.aws/credentials`,
no `aws ssm get-parameter ... --query Parameter.Value` outside the host render
path, and no `docker inspect` of a container's `Config.Env`.

To check a secret is *present* without revealing it, test only its length:

```powershell
if ($env:IB_PASSWORD) { "set, $($env:IB_PASSWORD.Length) chars" } else { "MISSING" }
```

```bash
aws ssm get-parameter --name /fxvol/prod/DB_PASSWORD \
  --query 'Parameter.Name' --output text --profile fxvol-dev
```

If a value is ever exposed in output, rotate it immediately via
`aws ssm put-parameter --overwrite` and purge the shell history.
