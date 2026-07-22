# Decomposition — cutting local `main` work into PRs to `origin/main`

> The **slicing layer**: how to decide *what* becomes a PR and carve it out
> without regressing `main` or touching a concurrent session's WIP. The lifecycle
> layer (issue → branch → PR → merge) lives in [`WORKFLOW.md`](WORKFLOW.md); the
> hard rules (commit-on-local-main, `git update` trigger, 2–3 PR/day, **no
> worktree**, secrets) live in `CLAUDE.md § "Git / workflow"` — that file wins on
> any conflict.
>
> Applied at the **`git update`** trigger, never on the assistant's own
> initiative.

---

## 0. The one fact that governs everything

The backlog to ship is **`origin/main..main`** — commits made on **local `main`**
(the integration branch), not yet pushed. Local `main` runs *ahead* of
`origin/main`; work is pushed **at the dropper**, in **sequential stacked
branches**, only on `git update`.

- Inspect the backlog: `git log --oneline origin/main..main`.
- **No `git worktree`, ever** (past source of chaos: desynced images, missing
  scripts, broken stack). Carve branches in place.
- A second session may be committing to `main` too — `git add` **only your
  files**, never `-A`/`.`, and never blind `git commit --amend` (verify
  `git log -1` is yours first).

---

## 1. Classify every candidate slice (decide before you branch)

| Class | Signal | Action |
|-------|--------|--------|
| **Additive / purely-new** | new migration, new module, new endpoint that edits no shared file | **Ship.** Mechanical, CI-verifiable. |
| **Cross-cutting edit** | touches a file another in-flight slice also edits | **Order it** so each PR stays coherent; rebase the later branch after the earlier merges. |
| **Inapplicable** | presupposes structure `main` doesn't have | **Drop.** |
| **Unverifiable-without-runtime** | restructures a live compute/IO path; correctness needs a running stack | **Hold** for a smoke session — OR extract a narrow **behavior-preserving sub-slice** CI can prove. |

If unsure about a runtime slice while the user is away: **ship the
additive/behavior-preserving parts, hold the rest, and say so.** Never merge an
unverifiable refactor to public `main` on a guess.

---

## 2. Splitting a backlog across multiple PRs

Split axes, in priority order:

1. **Safe vs risky.** Carve the mechanical / behavior-preserving part into its own
   PR; defer the judgment part.
2. **Layer / concern.** Migration in one PR; its reader/consumer in another —
   *only if each is independently valid on `main`*. Keep a migration + its
   required reader **together** if `main` would otherwise break (no half-migrations).
3. **Per-view / per-endpoint.** Frontend view refreshes are one PR each (they hit
   the manual-smoke gate individually).

**Invariant for every resulting PR:** on its own it must compile, pass full CI,
and leave `main` green — no dangling imports, no half-applied schema, no dead refs.

---

## 3. Per-PR procedure

```bash
# 0. Pick the next slice; classify it (§1). Sync the base ref in place:
git fetch origin main -q

# 1. Tracking issue (1 issue = 1 PR = 1 deliverable)
gh issue create --title "<type>: <deliverable>" --label "type:<type>" --body "<why + scope>"

# 2. Sequential branch off the previous branch's tip (or origin/main for the
#    first). NO worktree.
git switch -c <type>/<n>-<slug>       # n = sequence number, continues after the last PR

# 3. Build the slice, then verify locally BEFORE committing:
python -m ruff check <paths>
python -m compileall -q src
PYTHONPATH=src lint-imports                          # if you touched src/ layering
PYTHONPATH=src python -m pytest <relevant subset> -q
cd frontend && npm run typecheck && npm run lint     # if you touched frontend/

# 4. Commit — Conventional Commit, GH-noreply author, NO Co-Authored-By, English.
git commit -m "<type>(<scope>): <summary>"

# 5. Push + PR with Closes #N
git push -u origin <branch>
gh pr create --base main --head <branch> --body "Closes #N"$'\n\n'"<what / behaviour / scope>"

# 6. Verify EVERY `gh pr checks <#>` line reads `pass` (or `skipping`).
#    Do NOT trust `--watch`'s exit code — read the lines, in a step of their own.

# 7. Merge IN ORDER (B's PR opens only after A merges, rebased on fresh origin/main).
gh pr merge <#> --squash --delete-branch
```

To sync local `main` after a merge without leaving your branch:
`git fetch origin main:main` — fast-forwards the ref in place.

---

## 4. Gotchas that only fail in CI (not locally)

- **Alembic revision id ≤ 32 chars.** `alembic_version.version_num` is
  `varchar(32)`. A longer id passes ruff/compile/local-head-check, then fails CI's
  `alembic upgrade head` with `StringDataRightTruncationError`. Keep the `NNN_`
  prefix, shorten the slug.
- **`down_revision` must point to `main`'s actual head.** Verify a single head
  first (the revision that is never any file's `down_revision`).
- **Non-required CI checks can be red on `main`** for pre-existing reasons —
  verify *your PR's* jobs specifically, don't be spooked by unrelated reds.
- **GH007 push block:** the commit author email must be the GH noreply, not a
  personal gmail. Amend `--reset-author` if a stray commit carries the wrong one.
- **No `Co-Authored-By`**; **English only** (the repo is public); **squash-only** into `main`.

---

## 5. What NOT to touch

- The **uncommitted working tree on `main`** may be a concurrent session's WIP.
  Stage only your own files; never `stash`/`reset`/`clean`/blind-`amend` over it.
- Never push / open a PR / merge / tag **outside a `git update`** and its 2–3/day cap.
