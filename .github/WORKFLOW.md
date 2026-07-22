# Workflow — from issue to merge

> The issue → branch → PR → merge lifecycle, followed for every deliverable.
>
> Tracking = GitHub (Issues + [Project #5](https://github.com/users/valerian-drmt/projects/5)).
> See also [`PROJECT_BOARD.md`](PROJECT_BOARD.md) and [`ISSUE_PROTOCOL.md`](ISSUE_PROTOCOL.md).

## 1. Lifecycle, step by step

| # | Step | Action | Board card |
|---|------|--------|------------|
| 1 | **Scope** | Open the issue via a template | 📥 Backlog |
| 2 | **Prepare** | Fill Type/Area/Release/Priority/Size + Definition of Done | 🟢 Ready |
| 3 | **Branch** | Create the branch from up-to-date `main` | ⚙️ In Progress |
| 4 | **Code** | Atomic Conventional Commits | ⚙️ In Progress |
| 5 | **Propose** | `gh pr create` with `Closes #N` + checklist | 🔍 In Review |
| 6 | **Validate** | CI green + manual test | 🔍 In Review |
| 7 | **Merge** | Squash into `main` | ✅ Done (auto) |

A blocker at any step → card ⛔ Blocked, with an issue comment explaining the blocker.

## 2. Branches

- **`main`**: permanent, protected. **Only receives PRs**, squash-merged. Never a direct commit.
- **Work branch**: ephemeral, one per issue. Naming:

  ```
  <type>/<issue-number>-<short-slug>
  feat/142-vrp-ssvi
  fix/151-gamma-nan-atm
  chore/160-nginx-template
  ```

  `<type>` ∈ `feat · fix · refactor · infra · docs · chore`.

- Created from up-to-date `main`; **rebase** onto `main` if it moves (never `git merge main` into the branch).
- Auto-deleted on merge (`--delete-branch`).

## 3. Commits — Conventional Commits

```
<type>(<scope>): <short imperative description>

[optional body: the why, not the what]
```

- **type**: `feat · fix · refactor · infra · docs · chore · test · perf`.
- **scope**: the component (`api`, `vol`, `risk`, `frontend`, `aws`, `ci`…).
- Atomic: one commit = one coherent change that compiles.
- **Forbidden**: `Co-Authored-By` trailers or any bot name in authorship.

```
feat(vol): add VRP computation on the SSVI surface
fix(risk): guard against NaN gamma at the ATM strike
```

## 4. Pull Request

1. `gh pr create`, filling the [template](pull_request_template.md).
2. The body **must** contain `Closes #N` (closes the issue + moves the card to ✅ Done on merge).
3. Fill the test checklist (automated + manual).
4. **CI must be green.** If red: fix **on the branch**, never on `main`.
5. **Squash** merge (linear history) → branch auto-deleted.

### Manual-test gate

Any PR touching observable behavior goes through a **manual test** described in the PR
(what to test, how, expected result) **before** merge. No merge on CI alone.

## 5. CI / CD (reminder)

- **CI** (`.github/workflows/ci.yml`): compileall + ruff + import-linter + pytest + frontend
  (lint/typecheck/build/vitest) + builds. All must be green.
- **CodeQL** + **security-scan**: no new critical alert.
- **Deploy** (`build.yml` → `deploy.yml`): on **push to `main`**, gated by the
  `DEPLOY_ENABLED` repo variable. When armed, a code push auto-builds the images
  and deploys to the EC2 (`.idea`/`scripts`/`docs` pushes are `paths-ignore`d).

## 6. Who triggers what

**Git operations** (commit / push / PR / merge / tag) are **explicitly triggered**
by the maintainer — never run automatically.

## 7. Releases

- A **Release** (board field: R11, R12…) groups a batch of PRs.
- Closed by a `vX.Y.Z` **tag** on `main` once all its cards are ✅ Done.