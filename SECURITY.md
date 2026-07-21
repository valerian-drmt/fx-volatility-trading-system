# Security model

Single-trader system, deployed publicly at
`valeriandarmente.dev/fx-volatility-trading-system` in **public read-only**
mode. This file records the deliberate security decisions so they are
reviewable rather than implicit.

## Write boundary

Every mutating endpoint (orders, trade submit, closes, admin config) requires
the HMAC session cookie (`Depends(require_write)`, `src/api/auth.py` —
pbkdf2 200k + constant-time compares). A fail-closed unit test
(`tests/unit/api/routers/test_write_auth_gating.py`) asserts no write route
ships ungated. The `/api/v1/dev/*` console is gated the same way and
additionally 404'd at the nginx edge. `/login` is throttled at 5/min per IP
(slowapi); `ENV=prod` refuses to boot with the repo-default `AUTH_SECRET`.

## Public reads — deliberate acceptance (paper account)

REST reads and the WebSocket channels (`/ws/ticks|vol|risk|positions|orders|
exit_alerts`) are **public by design**, including the live book and P&L:

- The account is an IB **paper** account (`TRADING_MODE=paper`); live positions
  on simulated money are the demo's content, not a secret.
- Gating only the WS channels would be security theater while the same book is
  readable through the public REST reads — and gating all book reads would
  contradict the public read-only positioning.

**Hard trigger:** this acceptance is void the day `TRADING_MODE=live`. Before
any real-money session, gate the book WS channels *and* the matching REST
reads behind the session cookie (wrapper sketch in
`audit history / routers/ws.py`), or take the deployment private.

## OpenAPI docs — kept public

`/api/docs` + `/api/openapi.json` stay public and complete: the repo is public
so hiding routes from the schema provides no secrecy, the generated
`schema.d.ts` toolchain depends on the full schema, and interactive docs where
write endpoints return 401 demonstrate the auth boundary.

## Secrets

All secrets live in AWS SSM Parameter Store (`/fxvol/prod/*`, KMS-encrypted),
loaded to RAM/tmpfs by `scripts/local/load_secrets.ps1` (Windows) / `scripts/aws/load_secrets.sh` (EC2) or rendered on the
EC2 host by `infrastructure/ec2/remote-deploy.sh` via the instance role. No
secret is stored in the repo, GitHub secrets, or images. Redis requires auth
(`requirepass` from `REDIS_PASSWORD`); Postgres and Redis publish no host
ports in prod.

## Reporting

This is a personal portfolio project; report issues via GitHub issues on the
repository.
