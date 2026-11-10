# Changelog

Notable changes per release. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow the policy in [`.github/WORKFLOW.md`](.github/WORKFLOW.md#7-releases)
(Semantic Versioning, one version in `pyproject.toml`). Each entry links the
pull requests; the issues they close carry the detail.

## [Unreleased]

Merged on `main`, deployed continuously, not yet tagged.

### Added
- Book-vs-broker break alerting: warning log, counter, open-breaks gauge and a
  `ReconciliationBreakOpen` alert; stale-booking auto-close is a warning event (#323).
- Schema-aware rollback: the previous release's images stay on the host, a
  rollback past a migration needs `allow_schema_downgrade` (#345).
- Ops runbooks (reboot, rollback, restore, IB recovery) and one SSM inventory;
  `ec2.ps1 inventory` (#346).
- Per-package coverage floors in CI, view smoke tests, an unmocked e2e round
  trip in the compose job, a read-only IB paper smoke (#340, #341).
- API reference: every operation described, the trader cookie as a security
  scheme, documented error responses; WebSocket reference generated from the
  canonical payloads (#352).
- `.env.example` and a no-AWS local path; README prerequisites and Known
  limitations (#351).
- A tag-triggered release workflow, this changelog, one version source shown
  in `/health` and the desk footer (#353).

### Changed
- Frontend: every endpoint typed from the schema, one error boundary per view,
  fetch failures shown as errors, hidden source maps, keyboard focus, dialog
  semantics, contrast (#348, #349).
- Docs: counts, diagram and screenshots match the 12-container stack (#350).

### Security
- Python images drop the unused base pip / setuptools / wheel, the only HIGH
  scanner findings on our images (#304).

## [1.0.0-rc.1] - unreleased

The pre-release audit campaign (12 audits, 2026-08-01) and its fixes, shipped
to prod between 2026-11-06 and 2026-11-10. This is the first version tagged
under the release procedure; earlier local tags (`v1.0`, `v1.1.0` to
`v1.12.0`, `oms-v1.0`) were never pushed and are archived as `archive/*`.

### Added
- Fail-closed pre-trade risk gate and a kill switch (`/admin/halt`); alerting
  through Alertmanager to SNS; host disk guard (#389).
- IB session resilience and a ready gate; position, account and spot-order
  pushes on the bus that the desk patches from (#390).
- Materialised daily rollups and P&L attribution; db-writer natural keys,
  dead letter and counters (#391).
- Desk latency measured end to end; one Black-Scholes kernel and tenor map in
  `core` (#392).
- Schema drift gate and migration round trip in CI; staleness contract on vol
  reads; NUMERIC money and aware datetimes (#393).
- Critical-path tests (risk cycle, reconciliation, writer on Postgres, WS
  bridge); one VaR helper; closed-market banner and card badges (#394).
- Convergent `VolConfig` hot reload with a capacity dial; one env-var schema;
  risk-history write hygiene and nightly retention (#401).
- `uv.lock` as the one resolved set; base images pinned by digest on Node 22;
  weekly dependency scan with license gates (#405).

### Changed
- Static-arbitrage checks on SVI / SSVI with a cross-tenor calendar check;
  sanitised Yang-Zhang, HAR and GARCH; standardised GMM features; one signal
  threshold at Arm and Submit (#402).
- Deterministic, isolated `core`; one logging pipeline; supervised engine
  loops; mypy gated on shared, bus and persistence (#402).
- fastapi 0.162 / starlette 1.10; vite 8, vitest 5, eslint 10 (#405).

### Security
- Shared secret on the execution engine's internal routes, typed and bounded
  write bodies, rate limit keyed on the real client, login lockout, WebSocket
  Origin allowlist, nginx hardening, non-root images, fail-closed deploy
  secrets, SHA-pinned actions (#395).
- Secrets redacted in every log line, opaque 500s (#382).

[Unreleased]: https://github.com/valerian-drmt/fx-volatility-trading-system/compare/v1.0.0-rc.1...main
[1.0.0-rc.1]: https://github.com/valerian-drmt/fx-volatility-trading-system/commits/main
