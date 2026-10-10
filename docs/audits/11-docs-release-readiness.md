# Audit 11 — Documentation & Release Readiness

## Why

v1.0 means someone (future-you, six months from now, mid-incident at 2am) can operate,
recover, and extend this system from what's written down. The docs have churned through
big refactors (R8 desktop removal, v2 microservices split, module renames), so drift
between documentation and reality is near-certain. This audit runs **last**: it
verifies the post-fix state of everything the other audits touched.

## Scope

- `README.md` (root + frontend), `CLAUDE.md`, `docs/` (architecture, diagrams,
  execution, observability, ops, strategy, vol-modeling), `tests/STRUCTURE.md`.
- `.github/` protocol docs (CONTRIBUTING, WORKFLOW, ISSUE_PROTOCOL, templates).
- OpenAPI spec quality (served docs are user-facing documentation too).
- Release mechanics: versioning, changelog, tag.

## What to check

### Accuracy (docs vs reality)
- [ ] README container table, network diagram, port list vs actual `docker-compose.yml`.
- [ ] CLAUDE.md source-layout tree vs actual `src/` (post-audit-fixes state); commands
      section: every listed command actually runs.
- [ ] Architecture docs/diagrams reflect the v2 topology — no surviving references to
      the desktop app, `src/services/`, old module names.
- [ ] `docs/strategy/` specs match implementation *after* Audits 2–3 fixes (the
      spec-vs-code matrices produced there get their final check here).
- [ ] Screenshots in README/docs current with the shipped UI.

### Completeness (can you operate it?)
- [ ] Fresh-machine setup path: clone → env setup → local stack up → tests green,
      following only written docs. Actually walk it.
- [ ] Runbook from Audit 8 committed and linked: reboot, rollback, restore, IB recovery.
- [ ] Secrets inventory: every SSM parameter listed by name + purpose (values never).
- [ ] Known limitations section: paper-only, single IB session, markets-closed serving
      behavior, alerting gaps accepted for v1.0 — honesty section.

### API documentation
- [ ] OpenAPI: every public endpoint has a description, response model, and auth
      requirement visible; generated `schema.d.ts` in sync (drift gate green).
- [ ] WS endpoints documented somewhere findable (not in OpenAPI by nature).

### Release mechanics
- [ ] Versioning scheme chosen and recorded (semver? calver?); version surfaced in the
      API (`/health` or similar) and UI footer so a running deployment identifies itself.
- [ ] CHANGELOG for v1.0 written (high-level, from the PR history).
- [ ] Tag + GitHub Release procedure defined; deploy pipeline behavior on tags vs main
      pushes decided.
- [ ] All audit findings from Audits 1–10 resolved or explicitly waived in a tracked
      issue — this checklist is the release gate itself.

## Method

Doc-by-doc diff against the live tree, plus the fresh-setup walkthrough executed for
real (local machine or scratch VM), following only written instructions and noting every
gap hit.

## Deliverable & exit criteria

- Findings report; the v1.0 release checklist issue (aggregating waivers + gates).
- **Exit:** fresh-setup walkthrough completes without tribal knowledge; no doc
  contradicts reality; changelog + version + tag procedure ready. Then tag v1.0.