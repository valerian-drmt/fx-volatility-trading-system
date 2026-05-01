# Branch protection checklist

GitHub branch protection rules to enforce on `main`. Apply via **Settings > Branches > Add branch ruleset** (or legacy **Branch protection rules**) targeting `main`.

This checklist is the R0 baseline; additional status checks are added at each release (see "Future status checks" at the bottom).

## Required settings

- [x] **Restrict creations** — only collaborators can push new branches matching a protected pattern
- [x] **Require a pull request before merging**
  - [x] Require approvals: **1** (self-review allowed for a solo project; keep it enabled to enforce the review habit)
  - [x] Dismiss stale approvals when new commits are pushed
  - [x] Require review from Code Owners — off for now (no `CODEOWNERS` file yet)
- [x] **Require status checks to pass before merging**
  - [x] Require branches to be up to date before merging
  - Required checks (R0 baseline):
    - [x] `compileall + ruff + pytest` (job `quality` from `.github/workflows/ci.yml`)
- [x] **Require conversation resolution before merging**
- [x] **Require linear history** — prevents merge commits, forces squash or rebase merge
- [x] **Block force pushes**
- [x] **Restrict deletions** — `main` cannot be deleted
- [x] **Do not allow bypassing the above settings** (applies to admins too)

## Repository-wide settings

Go to **Settings > General > Pull Requests**:

- [x] **Allow squash merging** — enabled (default)
- [ ] **Allow merge commits** — disabled (combined with "Require linear history")
- [ ] **Allow rebase merging** — optional, disabled by default
- [x] **Automatically delete head branches** — enabled (cleans feature branches after merge)

## Default merge strategy

- **Squash and merge** is the default. See `releases/GIT_WORKFLOW.md` for rationale (linear history, one commit per PR on `main`).

## Future status checks (added at their respective releases)

| Release | New required check |
|---|---|
| R5 | `frontend-lint`, `frontend-test`, `openapi-typescript-check` |
| R6 | `docker-build`, `docker-compose-up` |
| R8 | `playwright-e2e`, `codeql-python`, `codeql-javascript` |

Update the "Required status checks" list on the branch ruleset at each release that adds a new workflow job.

## Verifying the rules

From the command line (with `gh` authenticated):

```bash
gh api repos/:owner/:repo/branches/main/protection
```

Should return a JSON describing the enforced rules. A `404 Not Found` means the branch is unprotected.

## Related documents

- Branching and commit strategy: `releases/GIT_WORKFLOW.md`
- Commit message conventions: `releases/COMMIT_METHODOLOGY.md`
- Release roadmap: `releases/README.md`
