# Secrets

All project secrets live in **AWS SSM Parameter Store** under `/fxvol/prod/*`,
KMS-encrypted (`SecureString`). There is no `.env` file in git and no secret in
any container image. Related: [deployment.md](deployment.md),
[local-stack.md](local-stack.md).

## Inventory

The one list of what lives under `/fxvol/prod/*` (#346, audit 08#11 / 11#8).
"In SSM" is the state on 2026-11-10 (`aws ssm describe-parameters`, names and
types only). `remote-deploy.sh` reads every row below on the host; nothing
passes through GitHub.

| Parameter | Type | In SSM | Used by | When absent |
|---|---|---|---|---|
| `DB_PASSWORD` | SecureString | yes | Postgres + every `DATABASE_URL` | deploy fails (required) |
| `REDIS_PASSWORD` | SecureString | yes | Redis `--requirepass` + `REDIS_URL` | deploy refuses to run (#298) |
| `AUTH_SECRET` | SecureString | yes | signs the write-login cookie | api refuses to boot with the repo default (ENV=prod guard) |
| `AUTH_PASSWORD_HASH` | SecureString | yes | the trader's password hash | api refuses to boot (ENV=prod guard) |
| `AUTH_SALT` | SecureString | **no** | salt of `AUTH_PASSWORD_HASH` | api uses the code default and logs `auth_salt_is_repo_default`. Provision it **together with** a new `AUTH_PASSWORD_HASH` computed with that salt, or login breaks |
| `IB_USERID`, `IB_PASSWORD` | SecureString | yes | IB Gateway login | read only with the `ib` profile; the gateway cannot log in |
| `VNC_PASSWORD` | SecureString | yes | IB Gateway VNC (loopback) | read only with the `ib` profile |
| `GRAFANA_ADMIN_PASSWORD` | SecureString | yes | Grafana admin (obs profile) | deploy refuses the `obs` profile (#298) |
| `EXECUTION_INTERNAL_TOKEN` | SecureString | **no** | api -> execution-engine `/internal/*` (#300) | host generates `/etc/fxvol/internal_token` (root 0600) and reuses it; provision to rotate |
| `FRED_API_KEY` | String | yes | events pipeline (FRED calendar) | events from FRED skipped |
| `GHCR_TOKEN` | SecureString | **no** | `docker login ghcr.io` | not needed while the packages are public |
| `TRADING_MODE` | String | yes, **unused** | nothing: the deploy renders `TRADING_MODE=paper` itself | safe to delete in the console |

Check presence without reading a value:

```bash
MSYS_NO_PATHCONV=1 aws ssm describe-parameters --profile admin --region eu-west-1   --parameter-filters "Key=Name,Option=BeginsWith,Values=/fxvol/prod/"   --query 'Parameters[].[Name,Type,LastModifiedDate]' --output text
```

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
