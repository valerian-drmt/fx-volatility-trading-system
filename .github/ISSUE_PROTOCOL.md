# Issue protocol

> How to write a workable issue. One **issue = one deliverable ≈ one PR**.

## 1. Always use a template

Open an issue **only** through a form (`New issue` → pick the type):

| Template | For |
|----------|-----|
| ✨ **Feature** | New feature / functional change |
| 🐛 **Bug** | Incorrect behavior to fix |
| 🔧 **Chore** | Refactor / Infra / Docs / Chore (no user-facing feature) |

Blank issues are disabled (`config.yml`) to enforce the format.

## 2. Title

Format: `[Type] imperative verb + object`, short and actionable.

```
[Feature] Add VRP computation on the SSVI surface
[Bug] Fix NaN in greeks.gamma at the ATM strike
[Chore] Migrate nginx confs to the shared template
```

No vague titles ("vol-engine problem", "frontend TODO").

## 3. Required fields (= board fields)

Every issue must carry these once it leaves 📥 Backlog for 🟢 Ready:

- **Type** — nature (maps the commit prefix).
- **Area** — main `src/` component: API · Core · Engines · Persistence · Bus · Frontend · Infra/AWS · Ops/CI.
- **Milestone** — R11 / R12 / R13 (set from the issue sidebar; gives a due date + progress bar).
- **Priority** — P0…P3.
- **Size** — XS…XL (Feature/Chore).

> The template dropdowns fill the issue **body**; the **board fields** are then set via
> `gh project item-edit` (or the UI). The two must stay consistent.

## 4. Definition of Done

Every issue carries a **verifiable acceptance checklist**. The linked PR is only mergeable
when **all** items are checked. Include at minimum:

- [ ] The described behavior works
- [ ] Unit tests added / updated
- [ ] CI green (ruff + import-linter + pytest + frontend if applicable)

## 5. Granularity

- Too large (> size L, touches 3+ Areas) → **split** into several linked issues.
- A parent issue can reference sub-issues (`- [ ] #N`) to track progress.

## 6. Security

**No secrets** in an issue: no key, password, `DATABASE_URL`, `.env` contents, or app log
that would print one. Redact before pasting. Secrets live in AWS SSM.

## 7. Lifecycle after creation

`📥 Backlog` → fill fields → `🟢 Ready` → coding can start (see [`WORKFLOW.md`](WORKFLOW.md)).