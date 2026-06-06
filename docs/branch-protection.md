# Branch protection checklist

GitHub branch protection rules to enforce on `main`. Apply via **Settings > Branches > Add branch ruleset** (or legacy **Branch protection rules**) targeting `main`.

## Required settings

- [x] **Restrict creations** — only collaborators can push new branches matching a protected pattern
- [x] **Require a pull request before merging**
  - [x] Require approvals: **1** (self-review allowed for a solo project; keep it enabled to enforce the review habit)
  - [x] Dismiss stale approvals when new commits are pushed
  - [x] Require review from Code Owners — off for now (no `CODEOWNERS` file yet)
- [x] **Require status checks to pass before merging**
  - [x] Require branches to be up to date before merging
  - Required checks (current — keep in sync with `.github/workflows/*.yml`) :
    - [x] `compileall + ruff + pytest` (job `quality` in `ci.yml`)
    - [x] `frontend pipeline (openapi drift + lint + vitest + build artifact)` (job `frontend` in `ci.yml`)
    - [x] `nginx -t syntax + pytest parse` (job `nginx-config` in `ci.yml`)
    - [x] `frontend Playwright e2e` (job `frontend-e2e` in `ci.yml`)
    - [x] `Playwright e2e against ephemeral docker-compose stack` (job `frontend-e2e-compose` in `ci.yml`)
    - [x] `build frontend docker image` (job `frontend-image` in `ci.yml`)
    - [x] `alembic + writer + redis bus live tests` (job `live-integration` in `ci.yml`)
    - [x] `CodeQL (python)` + `CodeQL (javascript-typescript)` (`codeql.yml`)
- [x] **Require conversation resolution before merging**
- [x] **Require linear history** — prevents merge commits, forces squash or rebase merge
- [x] **Block force pushes**
- [x] **Restrict deletions** — `main` cannot be deleted
- [x] **Do not allow bypassing the above settings** (applies to admins too)

## Repository-wide settings

Go to **Settings > General > Pull Requests** :

- [x] **Allow squash merging** — enabled (default)
- [ ] **Allow merge commits** — disabled (combined with "Require linear history")
- [ ] **Allow rebase merging** — optional, disabled by default
- [x] **Automatically delete head branches** — enabled (cleans feature branches after merge)

## Default merge strategy

**Squash and merge** is the default — linear history on `main`, one commit per PR. Rationale + full git workflow live in `releases/git_management/WORKFLOW.md` (single source of truth).

## Verifying the rules

From the command line (with `gh` authenticated) :

```bash
gh api repos/:owner/:repo/branches/main/protection
```

Should return a JSON describing the enforced rules. A `404 Not Found` means the branch is unprotected.
