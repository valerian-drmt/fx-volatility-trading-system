# Project board — structure & conventions

> Board: **[FX Volatility Trading System — Project #5](https://github.com/users/valerian-drmt/projects/5)**
> It is the **single source of truth** for tracking. Every task has a card.

## 1. Fields

| Field | Type | Values | Purpose |
|-------|------|--------|---------|
| **Status** | single-select | 📥 Backlog · 🟢 Ready · ⚙️ In Progress · 🔍 In Review · ⛔ Blocked · ✅ Done | Lifecycle stage |
| **Type** | single-select | Feature · Bug · Refactor · Infra · Docs · Chore | Nature (= commit prefix) |
| **Area** | single-select | API · Core · Engines · Persistence · Bus · Frontend · Infra/AWS · Ops/CI | `src/` component touched |
| **Priority** | single-select | P0 — Critical · P1 — High · P2 — Normal · P3 — Low | Order of work |
| **Size** | single-select | XS · S · M · L · XL | Quick estimate |
| **Release** | single-select | R11 · R12 · R13 · Backlog | Release-cadence bucket |

## 2. Status workflow — definitions & transitions

| Status | Meaning | Enter when | Leave when |
|--------|---------|------------|------------|
| 📥 **Backlog** | Captured, not yet ready | Issue created | Fields filled + scope clear |
| 🟢 **Ready** | Scoped, ready to code | Area + Release + Priority + acceptance criteria set | Coding starts |
| ⚙️ **In Progress** | Being worked on | A branch is created and coding begins | The PR is opened |
| 🔍 **In Review** | PR open / manual-test gate | `gh pr create` done | CI green + manual test passed |
| ⛔ **Blocked** | Blocked | Missing dependency, secret, infra or decision | Blocker lifted (returns to prior status) |
| ✅ **Done** | Merged into `main` | PR merged (auto via `Closes #N`) | — (terminal) |

**WIP limit**: aim for **1–2 cards max** in ⚙️ In Progress at a time.

## 3. Views to create (manual — GitHub API limitation)

The API/CLI only creates the default **Table** view. Other views are created by hand in the
project tab via **"+ New view"**:

1. **Board · by Status** — *Board* type, group by `Status` → the daily Kanban.
2. **Table · by Release** — *Table* type, group by `Release`, sort by `Priority` → the roadmap.
3. **Board · Blocked** — *Board* type, filter `Status = ⛔ Blocked` → what's stuck.

## 4. What the assistant manages without approval

Create/edit an issue, add it to the board, move a card between columns, update fields:
these are **tracking** operations, **not** git operations → done freely.

**Commit / push / PR / merge** stay gated behind explicit triggers (see [`WORKFLOW.md`](WORKFLOW.md)).

## 5. Driving the board via CLI (reference)

```bash
OWNER=valerian-drmt; P=5
gh project view  $P --owner $OWNER --web          # open the board
gh project item-list  $P --owner $OWNER           # list cards
gh project field-list $P --owner $OWNER           # list fields + ids
# Add an issue to the board:
gh project item-add   $P --owner $OWNER --url <issue-url>
# Move a card (item-id + field-id + option-id from field-list / GraphQL):
gh project item-edit --id <item> --project-id <PVT_…> --field-id <PVTSSF_…> --single-select-option-id <opt>
```