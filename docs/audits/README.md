# Pre-Release Audit Plan

This folder defines the full audit campaign to run **before tagging the first release (v1.0)**.
Each audit has its own file describing *why it exists*, *what it covers*, *how it will be run*,
and *what "pass" means*. Audits are executed by dedicated review agents, one per file,
and their findings are tracked as GitHub issues before being fixed.

## Why audit before v1.0

This system trades (paper) real market data through a live IB session, runs 24/5 on EC2,
and exposes a public read-only API. A release tag is a statement that the system is safe to
run unattended. The three highest-stakes failure classes are:

1. **Losing money / bad trades** — wrong greeks, wrong vol surface, duplicate orders.
2. **Leaking secrets or write access** — public URL with an auth-gated write boundary.
3. **Silent data corruption** — bad persistence, drifted migrations, stale caches.

Each audit below maps to at least one of these failure classes.

## The audits

| # | Audit | File | Primary risk covered |
|---|-------|------|----------------------|
| 1 | Security & attack surface | [01-security.md](01-security.md) | Secret leaks, auth bypass, exposed endpoints |
| 2 | Quant model correctness | [02-quant-models.md](02-quant-models.md) | Wrong prices/vols → bad signals & trades |
| 3 | Risk engine & limits | [03-risk-engine.md](03-risk-engine.md) | Unbounded exposure, wrong VaR/greeks aggregation |
| 4 | Execution safety | [04-execution.md](04-execution.md) | Duplicate/orphan orders, IB session failures |
| 5 | Data integrity & persistence | [05-data-integrity.md](05-data-integrity.md) | Corrupt/stale data, migration drift |
| 6 | Architecture & code quality | [06-architecture-code-quality.md](06-architecture-code-quality.md) | Erosion of layer boundaries, dead code |
| 7 | Test coverage | [07-test-coverage.md](07-test-coverage.md) | Critical paths shipping untested |
| 8 | Infrastructure & operations | [08-infra-ops.md](08-infra-ops.md) | Outages, unrecoverable state, deploy failures |
| 9 | Frontend | [09-frontend.md](09-frontend.md) | Broken UI states, stale data shown as live |
| 10 | Dependencies & licensing | [10-dependencies.md](10-dependencies.md) | Known CVEs, incompatible licenses |
| 11 | Documentation & release readiness | [11-docs-release-readiness.md](11-docs-release-readiness.md) | Un-runnable / un-recoverable system |

## Suggested execution order

1. **Wave 1 (independent, can run in parallel):** 1 Security, 2 Quant, 6 Architecture, 10 Dependencies.
2. **Wave 2 (build on wave 1 findings):** 3 Risk, 4 Execution, 5 Data integrity, 9 Frontend.
3. **Wave 3 (after fixes land):** 7 Test coverage (gaps revealed by fixes), 8 Infra/ops, 11 Docs & release readiness.

Rationale: security and quant findings often *change* code, which invalidates coverage and
docs audits if those run first. Docs/release-readiness is always last — it verifies the
post-fix state.

## Severity scale (shared by all audits)

| Severity | Definition | Release gate |
|---|---|---|
| **Blocker** | Can lose money, leak a secret, or corrupt data in normal operation | Must fix before v1.0 |
| **High** | Same impact but requires an unusual-but-plausible condition | Must fix or explicitly waive |
| **Medium** | Degrades reliability/maintainability, no direct money/data impact | Fix or file issue for v1.x |
| **Low** | Cosmetic, style, minor docs | File issue, no gate |

## Process

- Each audit produces a findings report (one GitHub issue per Blocker/High, grouped
  issues for Medium/Low), labeled `audit` + the audit's area label.
- No fixes are applied during an audit run — findings first, fixes as separate commits/PRs.
- An audit is **closed** when its exit criteria (bottom of each file) are met.