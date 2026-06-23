# Contributing — working protocol

> **Task tracking lives on GitHub, not in local `.md` files.** All work lives in
> **Issues** + the **[Project board #5](https://github.com/users/valerian-drmt/projects/5)**.
> `.md` files only describe **the protocol** (this folder) — never the current status.

This `.github/` folder holds the **whole** procedure, intentionally strict so every task
follows the exact same path.

## Documents

| Document | Purpose |
|----------|---------|
| [`CONTRIBUTING.md`](CONTRIBUTING.md) (this file) | Overview + the working loop |
| [`PROJECT_BOARD.md`](PROJECT_BOARD.md) | Board structure: fields, Status workflow, views to create |
| [`ISSUE_PROTOCOL.md`](ISSUE_PROTOCOL.md) | How to write an issue: title, fields, Definition of Done |
| [`WORKFLOW.md`](WORKFLOW.md) | End-to-end procedure: issue → branch → commits → PR → merge |
| [`ISSUE_TEMPLATE/`](ISSUE_TEMPLATE) | Standardized forms (Feature / Bug / Chore) |
| [`pull_request_template.md`](pull_request_template.md) | Mandatory template for every PR |

## The working loop

```
1. Scope     →  open an Issue via a template            → card 📥 Backlog
2. Ready     →  fields filled (Area, Release, Priority) → card 🟢 Ready
3. Code      →  dedicated branch, Conventional commits  → card ⚙️ In Progress
4. Propose   →  PR with "Closes #N" + checklist         → card 🔍 In Review
5. Merge     →  squash into main, CI green              → card ✅ Done (auto)
```

One **issue = one deliverable ≈ one PR**. The `Closes #N` link in the PR closes the issue
**and** moves the card to ✅ Done automatically on merge.

## Non-negotiable rules

- **`main` only receives PRs**, squash-merged — never a direct commit.
- **Conventional Commits** required (`feat:`, `fix:`, `refactor:`, `infra:`, `docs:`, `chore:`).
- **No secrets** anywhere (issue, PR, commit, log). Secrets live in AWS SSM.
- **No `Co-Authored-By`** and no bot/assistant name in commit authorship.
- **CI green** before any merge.

Step-by-step details: [`WORKFLOW.md`](WORKFLOW.md).