# Documentation standard

How documentation is organised, written, and kept alive in this repository.
This file is the **spec**, not the documentation itself — it defines where a doc
belongs, what shape it takes, and when it gets deleted.

Read it before adding a `.md` file or a diagram.

---

## 1. Principles

1. **Docs-as-code.** Documentation lives in this repository, next to the code it
   describes, and is reviewed in the same PR as the change that affects it. A doc
   that cannot be reviewed alongside code will drift.
2. **Generate, don't maintain.** If a fact is already encoded in typed source
   (`pipelines.ts`, `models.py`, the OpenAPI schema), the doc renders *from* it.
   Hand-copying a fact creates a second source of truth that will disagree with
   the first.
3. **One reader, one question.** Each doc answers one question for one audience.
   Docs that answer three questions get read for none of them.
4. **Volume is not quality.** A reader evaluating this system gives it ten
   minutes. Thirty-eight diagrams read as noise; one context diagram plus six
   recorded decisions reads as judgement.
5. **Delete aggressively.** A stale doc is worse than a missing one — it is
   confidently wrong. See §7.

---

## 2. Where documentation lives

Four locations, each with a rule. Nothing goes anywhere else — in particular, no
`.md` at the repository root beyond the four listed below.

| Location | Holds | Audience |
|---|---|---|
| Root (`README.md`, `SECURITY.md`, `CONTRIBUTING` → `.github/`, `LICENSE`) | What the project is, in 30 seconds. Entry point only. | Anyone arriving cold |
| `docs/` | How the system works — architecture, decisions, runbooks, domain reference | Engineers working on it |
| `.github/` | How we work — issue/PR/branch protocol | Contributors |
| `infrastructure/<target>/` | How a specific deployment target is provisioned and operated | Whoever is on call |

**Work tracking is not documentation.** Issues, board state, and sprint status
live in GitHub Issues + Project #5 (see `.github/PROJECT_BOARD.md`) and are never
mirrored into `.md` files. A ticket describes what we are doing; `docs/` describes
how the system works. The former dies when the ticket closes.

### Target `docs/` layout

```
docs/
├── README.md                  index — routes the reader, owns no content
├── DOCUMENTATION_STANDARD.md  this file
├── adr/                       architecture decision records (see §5)
│   ├── README.md              index + template
│   └── NNNN-<slug>.md
├── architecture/              how the system is built
│   ├── backend.md
│   ├── order-pipeline/        (existing multi-part reference)
│   ├── panels.md              GENERATED from pipelines.ts — do not hand-edit
│   └── infrastructure.md      the three infra diagrams (§4.3)
├── runbooks/                  operator procedures, imperative mood
│   ├── run-local-stack.md
│   ├── docker-cheatsheet.md
│   ├── db-schema-drift.md
│   └── ib-order-ops.md
├── reference/                 domain + product reference
│   ├── strategy.md
│   ├── glossary.md            SVI, SSVI, VRP, RV, free-legs, pillar, …
│   ├── greek-limits.md
│   └── surface-tenor-pillars.md
├── observability/             (existing — conventions + runbooks)
└── assets/                    diagram sources and exports (§4)
```

---

## 3. Document types

Every doc is exactly one of these. If it is two, split it.

| Type | Answers | Mood | Lives in |
|---|---|---|---|
| **ADR** | Why did we choose X over Y? | Past tense, decided | `docs/adr/` |
| **Architecture** | How is this built and why does data flow this way? | Descriptive | `docs/architecture/` |
| **Runbook** | How do I perform this operation? | Imperative, numbered steps | `docs/runbooks/`, `infrastructure/*/` |
| **Reference** | What does this term/limit/parameter mean? | Declarative, lookup-shaped | `docs/reference/` |
| **Index** | Where do I find X? | Table of links, no content | `README.md` per folder |

Explicitly **not** document types, and never committed:

- Session plans, TODO lists, "audit of X" write-ups, remediation trackers — these
  are work artefacts. They belong in an issue, or nowhere.
- Anything with a date in the title describing a one-off effort.

---

## 4. Diagrams

### 4.1 Format decision

**Mermaid is the default.** Fenced ` ```mermaid ` blocks inside the `.md`.

Rationale: renders natively on GitHub, diffs as text in PRs, requires no external
tool, and cannot fall out of sync with the file it documents by being forgotten in
an export step.

**draw.io is reserved for hero diagrams** — the two or three where nesting and
spatial layout carry meaning that Mermaid renders badly (topology, boxes inside
boxes, network boundaries).

When using draw.io, save as **`.drawio.svg`**, never bare `.drawio`. The dual
format is editable in draw.io *and* renders inline on GitHub; a bare `.drawio`
displays as unreadable XML.

**Generated diagrams** for anything already modelled in typed source. See §4.4.

> Rule of thumb: if it fits in Mermaid, it goes in Mermaid. If it is already in
> code, it gets generated. draw.io is the exception, not the tool of choice.

### 4.2 Asset conventions

- Sources and exports live in `docs/assets/`, named `<topic>.drawio.svg`.
- Inline Mermaid stays inside the `.md` that uses it — never extracted to `.mmd`.
- No PNG/JPEG for diagrams. Raster diagrams cannot be diffed or edited.
- Screenshots (UI only) go in `docs/assets/screenshots/`, PNG, and are captioned
  with the date they were taken.

### 4.3 The three required infrastructure diagrams

The AWS/deployment story is the least self-evident part of this system and the
part least visible from reading source. These three are deliverables, not
suggestions.

| # | Diagram | Format | Shows | Lives in |
|---|---|---|---|---|
| 1 | **CI/CD trust chain** | Mermaid `flowchart LR` | `push → CI green → workflow_run → deploy job (environment: production) → OIDC token → sts:AssumeRole → SSM SendCommand → EC2 pulls GHCR → compose up → alembic → smoke` | `docs/architecture/infrastructure.md` **+ inlined in root `README.md`** |
| 2 | **Secrets lifecycle** | Mermaid `flowchart` | KMS CMK (annual rotation) → SSM `/fxvol/prod/*` (SecureString) → two consumers: laptop via `load_secrets.ps1` (RAM only) and EC2 via instance role (tmpfs). Write path is console-only. | `docs/architecture/infrastructure.md` |
| 3 | **Deployment topology** | `.drawio.svg` | VPC / EC2 / the three docker networks (`fxvol-public`, `fxvol-internal`, `fxvol-external`) / nginx TLS termination / IB outbound / EBS + backups | `docs/assets/deployment-topology.drawio.svg` |

**Label the trade-offs, not just the boxes.** These diagrams exist to make design
decisions legible. Annotate the *absences* — "no static AWS credentials", "no
inbound SSH", "instance role: read-only, no `PutParameter`". A box-and-arrow
diagram anyone can draw; an annotated one shows why it is shaped that way.

Diagram 1 is the one a reader evaluating this project will actually see. It goes
in the root README under an **Infrastructure & Deployment** heading.

### 4.4 Generated diagrams — panels

`frontend/src/pages/dev/pipelines.ts` already models 38 production panels as typed
data (nodes, edges, per-node health keys, 26 with a full DAG), rendered live with
health colouring by `PipelineViz.tsx`.

**Do not hand-draw panel diagrams.** They would duplicate that model, rot within a
month, and be strictly worse than the live version, which shows real health state.

Instead: a script emits Mermaid from `pipelines.ts` into
`docs/architecture/panels.md`, with a CI drift check — the same pattern as
`npm run gen:api:check` for OpenAPI → `schema.d.ts`. The generated file carries a
`<!-- GENERATED — do not edit -->` header.

The in-app dev Pipeline tab remains the primary artefact. The generated file
exists so the wiring is legible to someone reading GitHub without booting the
stack.

---

## 5. ADRs

An **Architecture Decision Record** captures one decision, the alternatives
rejected, and the trade-off accepted. This is the highest-value documentation in
the repository and currently the largest gap: real decisions are presently buried
inside prose (e.g. "Final decision (2026-06-25, revised)" inside
`surface-tenor-pillars.md`).

### Rules

- One decision per file, `docs/adr/NNNN-<kebab-slug>.md`, numbered sequentially.
- **Immutable once merged.** A reversed decision gets a *new* ADR that supersedes
  the old one; the old one is marked `Superseded by ADR-NNNN` and kept. The record
  of having changed our mind is the point.
- Written when the decision is made, not reconstructed later.
- Short. One page. If it needs more, the extra belongs in `docs/architecture/`.

### Template

```markdown
# ADR-NNNN — <decision in one line>

- **Status**: Accepted | Superseded by ADR-NNNN | Deprecated
- **Date**: YYYY-MM-DD

## Context
What forced a choice. Constraints, and what we knew at the time.

## Options considered
| Option | Pro | Con |
|---|---|---|
| A (chosen) | | |
| B | | |

## Decision
What we chose, stated plainly.

## Consequences
What this makes easy, what it makes hard, and what we accept as the cost.
What would make us revisit this.
```

### Starter set — decisions worth recording

These are already made; they are undocumented as decisions:

1. Redis pub/sub as the internal bus (vs Kafka/NATS) for a single-node deployment
2. Four separate IB client IDs, one per engine (vs a shared connection)
3. SVI/SSVI parameterisation for the surface (vs SABR)
4. `src/`-layout with import-linter contracts (vs a flat package)
5. OIDC federation for deployment (vs long-lived IAM keys in GitHub secrets)
6. SSM Parameter Store + KMS CMK (vs Secrets Manager, vs `.env` on the instance)
7. Single-file `models.py` for 28 ORM classes (vs per-domain modules)
8. Docker Compose on one EC2 instance (vs ECS/EKS)
9. Chosen tenor pillar set 1M–6M (existing content in `surface-tenor-pillars.md`)

---

## 6. Writing conventions

- **English only**, across every doc, comment, commit, issue, and label.
- **Filenames**: `kebab-case.md`. Exceptions: `README.md`, `SECURITY.md`,
  `LICENSE`, and `docs/adr/NNNN-*.md`. No `SCREAMING_CASE.md`.
- **Every doc opens with a one-paragraph statement of what it answers and for
  whom**, before any heading.
- **Link, don't duplicate.** A fact has one home; everywhere else links to it.
- **Cite real paths** — `src/engines/vol/engine.py:42`, not "the vol engine".
- Runbook steps are numbered, imperative, and state the expected output of each
  command so a reader knows whether it worked.
- No French, no bot co-authorship attribution, no AI-assistant references. This
  repository is public.

---

## 7. Maintenance

### A doc is stale when

- It references a path, table, container, or command that no longer exists.
- It describes a plan rather than the system as it now stands.
- Its "last verified" date is older than the subsystem's last significant change.

### Rules

- **Changing behaviour means updating its doc in the same PR.** A PR that renames
  a table and leaves the schema doc untouched is incomplete.
- Architecture and infrastructure docs carry a footer:
  `_Last verified against the code: YYYY-MM-DD._`
- Docs describing completed one-off work are **deleted**, not archived. Git holds
  the history.
- Index files (`README.md` per folder) are updated in the same PR as any file
  they list.

### Known stale references to fix during migration

- `docs/README.md:30` and `docs/branch-protection.md:40` both cite
  `releases/git_management/WORKFLOW.md` as the single source of truth for the git
  workflow. That path is gitignored; the live file is `.github/WORKFLOW.md`.
- `docs/README.md:31` states "17 containers"; the root `README.md` states 11.

---

## 8. Public-repository constraint

This repository is public. Before adding infrastructure documentation:

- Never commit secret **values**. Parameter *names* (`/fxvol/prod/DB_PASSWORD`)
  are fine; values, hashes, and tokens are not. See the secrets rule in
  `CLAUDE.md`.
- **Account identifiers are reconnaissance material.** AWS account IDs, ARNs,
  instance IDs, security-group IDs, bucket names, and DNS records are not secrets
  in the strict sense, but publishing them is conventionally treated as a mistake.
- `infrastructure/aws/STATE.md` is tracked and public, and contains sections for
  account, IAM, KMS, network, DNS, and cost. **It should be split before the AWS
  story is made more prominent**: a public `infrastructure/aws/architecture.md`
  describing the shape, and a gitignored `STATE.local.md` holding identifiers.
  Making the infrastructure prominent while it leaks identifiers inverts the
  signal the documentation is meant to send.

---

## 9. Migration plan

Current state, and where each file lands. No content rewrite implied unless noted.

| Current | Target | Action |
|---|---|---|
| `docs/BACKEND_ARCHITECTURE.md` | `docs/architecture/backend.md` | move + rename |
| `docs/ORDER_PIPELINE.md` | — | **merge** into `docs/architecture/order-pipeline/`, which already covers it; delete the duplicate |
| `docs/order-pipeline/**` | `docs/architecture/order-pipeline/**` | move |
| `docs/IB_ORDER_OPS.md` | `docs/runbooks/ib-order-ops.md` | move + rename |
| `docs/run-local-stack.md` | `docs/runbooks/run-local-stack.md` | move |
| `docs/docker-cheatsheet.md` | `docs/runbooks/docker-cheatsheet.md` | move |
| `docs/db_schema_drift_workflow.md` | `docs/runbooks/db-schema-drift.md` | move + rename |
| `docs/branch-protection.md` | `.github/branch-protection.md` | move — it is protocol, not system; fix stale ref |
| `docs/strategy.md` | `docs/reference/strategy.md` | move |
| `docs/surface_tenor_pillars.md` | `docs/reference/surface-tenor-pillars.md` + **ADR-0009** | move + extract the decision |
| `greek-limits-spec.md` (root) | `docs/reference/greek-limits.md` | move — no orphan docs at root |
| `docs/vol_trading_pca/events_pipeline_spec.md` | `docs/architecture/events-pipeline.md` | move + rename |
| `docs/vol_trading_pca/notebooks/` | keep in place | notebook, not a doc |
| `docs/observability/**` | keep in place | already correct |
| `infrastructure/**` | keep in place | already correct; apply §8 to `STATE.md` |
| — | `docs/reference/glossary.md` | **new** |
| — | `docs/adr/` + starter ADRs | **new** |
| — | `docs/architecture/infrastructure.md` + 3 diagrams | **new** (§4.3) |
| — | `docs/architecture/panels.md` | **new**, generated (§4.4) |
| `docs/README.md` | rewritten | route to the new tree; fix both stale refs |

### Order of execution

1. Split `STATE.md` per §8 — before anything makes the infra more visible.
2. Create the three infrastructure diagrams; inline diagram 1 in the root README.
3. Create `docs/adr/` with the template and the starter set.
4. Move and rename existing files; fix stale cross-references.
5. Add the glossary.
6. Build the `pipelines.ts` → `panels.md` generator and its CI drift check.

Steps 1–3 carry nearly all the value. Steps 4–6 are tidying.

---

_Last verified against the code: 2026-07-19._