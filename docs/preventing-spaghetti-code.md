# Preventing Spaghetti Code — A Production Engineering Reference

> Generic, project-agnostic notes on what causes spaghetti code, the principles
> that prevent it, and the specific guard rails needed when an AI coding agent
> (Claude Code, Cursor, Copilot, etc.) is part of the workflow.
>
> This document is **theory and policy**, not a walk-through of the current
> codebase. It is meant to be selectively implemented later (linters, hooks,
> CI gates, agent rules).

---

## 1. What "spaghetti code" actually means

Spaghetti code is not a single defect — it is the steady-state outcome of many
small, locally-rational decisions that nobody zooms out from. The defining
symptoms :

| Symptom | Concrete sign |
|---|---|
| **Tangled control flow** | jumps between modules without an obvious entry point ; long functions with deep nesting ; callbacks that mutate shared state |
| **Implicit coupling** | module A breaks because module Z changed ; tests fail in unrelated files ; "you can't change X without changing Y" |
| **No layering** | DB calls in UI components, business rules in routers, formatting code in domain models |
| **God modules** | one file > 1000 lines doing everything ; one class with > 20 public methods covering unrelated concerns |
| **Hidden state** | results depend on call order ; module-level globals mutated from anywhere ; singletons that aren't called singletons |
| **No clear ownership** | multiple call sites duplicating the same logic with subtle drift ; "which version is canonical ?" cannot be answered |
| **Defensive code everywhere** | nullchecks and try/except wrapped around code that can never fail at runtime, because nobody trusts the surrounding system |

The cost is non-linear : refactoring a 100k-line spaghetti codebase is
~10× more expensive than refactoring 10× a 10k-line one, because every change
creates ripple effects.

## 2. Why teams end up there

Root causes, ranked by frequency :

1. **No deliberate architecture at the start.** Every feature is bolted on
   beside the previous one. There is no place where "this kind of code goes".
2. **Speed pressure without refactoring budget.** Shipping a feature in a
   suboptimal place is fine — *not paying back the debt is not*. Teams that
   never schedule cleanup land in spaghetti within 18 months.
3. **Premature abstraction.** Generic frameworks built before there are
   3 concrete callers always model the wrong thing.
4. **Copy-paste reuse.** Same logic in 7 places that diverge over years.
5. **Fear of touching legacy code.** Once a module is "scary", new code goes
   *around* it instead of through it, growing the spaghetti perimeter.
6. **No tests at the seams.** Without tests, refactoring is reckless, so it
   doesn't happen.
7. **Reviews that only check correctness, never structure.** PRs land on the
   "does it work ?" bar, never on the "does it belong here ?" bar.
8. **Tooling absent or ignored.** No linter, no formatter, no type checker,
   no complexity gate, no dependency graph — every author improvises.

## 3. First principles that prevent it

### 3.1 Separation of concerns

Each module answers exactly one question. If you cannot summarize a module's
purpose in one sentence without using the word "and", split it.

A useful default decomposition for backend systems :

```
┌─────────────────────────────┐
│ presentation / transport    │  HTTP, CLI, WebSocket, queue consumers
├─────────────────────────────┤  ↓ depends on ↓
│ application / use-case      │  orchestration of domain operations
├─────────────────────────────┤  ↓
│ domain (pure)               │  business rules, no I/O, no framework
├─────────────────────────────┤  ↑ depends on ↑
│ infrastructure / adapters   │  DB, cache, external APIs, file system
└─────────────────────────────┘
```

The arrows point **inward** : domain code never imports from infrastructure or
presentation. This is the same dependency rule as Hexagonal Architecture and
Clean Architecture — the labels matter less than the rule.

### 3.2 Module boundaries are public contracts

Inside a module, anything goes. *Across* modules, only the published interface.
Three concrete habits :

- One `__init__.py` (or `index.ts`) per package, re-exporting the public API ;
  internal files are not imported from outside.
- Function signatures use **domain types**, not transport types. A pricing
  function takes `Price` and `Volatility`, not `dict[str, Any]` from a JSON
  body.
- Side effects are explicit at the boundary : functions that hit a DB or a
  network are easily greppable (suffix `_async`, name contains `fetch_` /
  `publish_` / `persist_`, etc.).

### 3.3 Direction of dependencies

Two rules eliminate most cycles :

1. **Stable depends on volatile, never the reverse.** Domain (rarely changes)
   is depended on by routers (change weekly). Routers do not depend on each
   other.
2. **Depend on abstractions, not implementations.** A vol engine talks to
   "a publisher" interface ; the Redis-backed publisher implements it. The
   engine has no `import redis` statement.

A static dependency graph (e.g. `pydeps`, `madge`, `dependency-cruiser`) drawn
in CI catches cycles within seconds. **A cycle in the dependency graph is the
single strongest predictor of future spaghetti** ; treat it as a build break.

### 3.4 Cohesion over reuse

It is better to have two similar 30-line functions in two modules than one
shared 50-line function with a `mode` flag. Reuse is only valuable when the
code is **conceptually the same** — not when it merely looks similar today.
The "rule of three" says : extract a helper after the third concrete duplicate,
not before.

### 3.5 Pure core, imperative shell

The deeper into the codebase, the more functional the code should be. Pure
functions (no I/O, no mutation of shared state, deterministic) are testable,
composable, and immune to ordering bugs. Push side effects to the edges
(transport layer, repositories). Tests for pure code do not need fixtures,
mocks, or containers — write 100× more of them.

### 3.6 Make state explicit

- No module-level mutable globals. If something is global, it is configuration,
  loaded once at startup and treated as immutable thereafter.
- Pass dependencies as arguments (constructor / function parameter), do not
  reach out via imports. This is dependency injection — the *technique*, not
  the framework.
- Database transactions, caches, and message buses are passed in, not
  constructed inside business logic. This makes tests trivial (substitute
  in-memory equivalents) and ownership obvious (the layer that constructs
  also closes).

### 3.7 SOLID, briefly

Worth memorising because they each map to a spaghetti symptom :

| Letter | Rule | Spaghetti it prevents |
|---|---|---|
| **S** | Single responsibility | god classes |
| **O** | Open/closed | shotgun surgery on every new variant |
| **L** | Liskov substitution | base classes whose subclasses break callers |
| **I** | Interface segregation | clients depend on methods they don't use |
| **D** | Dependency inversion | high-level modules importing low-level modules |

You don't need to enforce SOLID dogmatically — just recognize the smell when
one is violated, and decide *consciously* whether the violation is worth it.

### 3.8 YAGNI / KISS / "do the simplest thing that could possibly work"

Most spaghetti is **excess code that pretended a future requirement would
arrive**. It rarely arrived ; the abstraction stayed. The bar for adding
generality is one of :

- Three concrete current users, OR
- A specific, dated requirement to add a fourth, AND
- A reviewer who explicitly approves the abstraction

Otherwise : write the inline version. Refactor when the third caller appears.

### 3.9 Tests as design pressure

Tests are not only correctness checks — they are the **first user of your
code**. If a function is hard to test, it is hard to use. Specifically :

- A function that needs 12 mocks to test has too many dependencies.
- A function that requires a DB and a Redis to test should be split into
  a pure core (most of the logic) and a thin adapter (the I/O).
- A function whose tests need a `time.sleep` has a race condition you have
  not isolated.

A working test suite enables refactoring ; without one, refactoring is
russian roulette and therefore does not happen.

## 4. Anti-patterns and how to recognize them

| Smell | What you see | Fix direction |
|---|---|---|
| **God class** | one class > 500 lines or > 15 public methods | extract by responsibility (parsing / persistence / formatting) |
| **Feature envy** | method on class A spends most of its time reading class B's fields | move the method to B |
| **Shotgun surgery** | adding a feature requires editing 7 files in 7 modules | the concept has no home — create one |
| **Long parameter list** | 6+ positional args | parameters cluster into an object ; pass that |
| **Primitive obsession** | passing `str` / `float` everywhere for things that are domain concepts | introduce small value types (`Strike`, `Tenor`, `Currency`) |
| **Deep nesting** | indentation levels > 3 | early returns, guard clauses, extract function |
| **Comment as deodorant** | "// this is hacky but" | the comment is correct ; address the hack instead of describing it |
| **Boolean parameters** | `do_thing(payload, True, False, True)` | split into two named functions or use enums |
| **Output arguments** | functions that mutate their inputs *and* return | pick one ; prefer pure return |
| **Stringly typed** | `event_type == "TRADE_OPEN"` everywhere | enum / sealed class |
| **Catch-all exception** | `except Exception: pass` | name the failure modes you actually expect |

## 5. Process and tooling that keep code clean

### 5.1 Mechanical guards (no human judgment required)

These should be CI-blocking :

- **Linter** with the strictest reasonable ruleset : `ruff` (Python),
  `eslint --max-warnings=0` + `typescript-eslint` (TS), `golangci-lint` (Go).
- **Formatter** with no configurable options : `black`/`ruff format`,
  `prettier`, `gofmt`. Disagreement about style stops happening.
- **Type checker** : `mypy --strict` or `pyright` (Python), `tsc --strict` (TS).
- **Dead code detector** : `vulture`, `ts-unused-exports`, `deadcode`.
- **Cyclomatic complexity gate** : reject functions over a threshold (e.g.
  CC > 10) — `radon` (Python), `eslint-plugin-complexity`.
- **Import cycle gate** : `import-linter` (Python), `dependency-cruiser` (TS).
- **Test coverage floor** with a sane number (60-80%) — measured on lines and
  branches, not just lines. Lower numbers are better than dishonest 95%
  coverage of trivial getters.
- **Secret scanning** : `gitleaks`, `trufflehog` in pre-commit + CI.

A pre-commit hook that runs the fast subset (format, lint, type check on
changed files) catches > 80% of issues before they reach review.

### 5.2 Architectural guards

- A **diagram of module boundaries** committed to the repo (kept up to date,
  ideally generated). Reviewers can answer "does this PR cross a boundary that
  it shouldn't" by *looking*.
- An `ARCHITECTURE.md` that names each layer, the responsibility of each, and
  the dependency rules between them. New PRs are reviewed against it.
- `CODEOWNERS` mapping directories to humans. The reviewer of a directory
  enforces its norms ; the rest of the team learns by reading reviews.

### 5.3 Reviews that catch structure, not just bugs

Reviewers should ask, in order :

1. **Does this belong in this module ?** If no, stop ; reject with the right
   destination.
2. **Is the public interface minimal ?** Newly exported names default to
   private.
3. **Are dependencies still pointing inward ?** No new imports from
   domain → infrastructure.
4. **Is there a simpler version ?** "Could this be inline ? Could this be
   removed ?"
5. **Are the tests testing behavior, not implementation ?** Tests that mock
   internals will block refactoring.
6. **Then** : does the code work ?

A 30-line PR reviewed on these criteria is more valuable than a 600-line PR
where the reviewer only had time to skim for typos.

### 5.4 Refactoring as a continuous activity

Two practical rules :

- **Boy Scout rule** : leave the file you touched a little better than you
  found it. Rename a misleading variable, extract a 3-line block, drop a dead
  import. Small ambient improvements compound.
- **Time-boxed refactoring slots** : 10–20% of each sprint is paying down
  debt, not features. Without a budget line, debt is never paid.

### 5.5 PR size as a hard limit

Above ~400 lines of net diff, reviewers stop reading carefully. A useful cap is
**200 lines, 1–3 files, one concept per PR.** Big changes are split into a
sequence of small PRs that each compile, test, and ship independently. The
entire stack of "feature/branch-N depends on feature/branch-N-1" workflow is
designed for this.

## 6. Specific guard rails for AI coding agents (Claude Code & co.)

AI agents are productivity multipliers — and *spaghetti multipliers* if not
constrained. The patterns below are not about distrusting the agent ; they
are about giving it the same constraints a senior engineer operates under.

### 6.1 Why agents drift toward spaghetti

| Drift | Cause |
|---|---|
| **Eagerness to "help"** | adds error handling, fallbacks, helpers nobody asked for |
| **No global view** | does not know the existing utility 3 directories away ; reinvents it |
| **Copy-paste from training** | reproduces patterns from random GitHub code, including bad ones |
| **Scope creep** | a "fix this null bug" turns into a 12-file refactor |
| **Imaginary requirements** | adds branches for cases the spec did not mention |
| **Premature abstraction** | introduces a `BaseFooHandler` for one concrete handler |
| **No memory of past decisions** | reverts conventions established in previous sessions |
| **Defensive programming** | wraps internal trusted calls in try/except "just in case" |
| **Yes-and dialogue** | every reply expands rather than narrows scope |

### 6.2 Constraints to put in `CLAUDE.md` / `.cursorrules` / equivalent

These should be load-bearing rules the agent sees on every turn :

- **Edit existing files before creating new ones.** A new file requires a
  one-line justification. Especially : no new docs, READMEs, or
  helpers unless explicitly requested.
- **PR size cap : ≤ 200 lines net, ≤ 3 files, one concept.** Anything larger
  is split before coding starts.
- **Minimal-change principle.** A bug fix changes only what's needed to fix
  the bug. No "while I was there" cleanup, no opportunistic refactor.
- **Read before write.** Before modifying any file, the agent must have read
  it (and the relevant callers) in the current session.
- **No defensive code at internal boundaries.** Validate inputs at system
  boundaries (HTTP, CLI, queue) — not on every internal function.
- **No silent fallbacks.** Returning `None` / `[]` / a default on failure
  hides bugs ; raise with a clear message instead, unless the spec explicitly
  asks for graceful degradation.
- **No new abstractions without three concrete callers.** Defer the
  generic / configurable / pluggable version until pressure exists.
- **No comments restating code.** Comments answer "why is this surprising ?",
  never "what does this line do ?".
- **Type hints / type annotations are mandatory** on new public functions —
  they are the cheapest documentation and they force the agent to think
  about the contract.
- **Tests are required for new behavior** — pure unit tests, not integration
  tests requiring a stack.
- **Stay in scope.** If the agent notices an unrelated bug, it surfaces it
  in a sentence ("noticed X is also broken") and does not fix it in the
  same diff.

### 6.3 Process discipline around the agent

- **Plan before code.** For any task longer than a one-line edit, the agent
  produces a plan first (files to touch, public signatures, tests). The
  human approves before code is written. This single step kills most
  scope creep.
- **Read-only research subagents.** Spawn an agent in read-only mode to
  *understand* a part of the codebase, then a separate writing-capable agent
  for the actual change. The research output stays out of the writer's
  context bloat.
- **Manual-test gate before commit.** The agent stops after coding and
  presents : files changed, what to verify, how to verify. Commits only
  happen after explicit human approval. This is also a comprehension gate
  for the human.
- **Diff review like a PR.** The human reads the agent's diff with the
  same checklist as a human PR (§5.3). Approve or reject — don't tweak.
  Tweaking trains the agent to expect rescue.
- **Reset the conversation regularly.** Long-running sessions accumulate
  outdated context, drifted conventions, and dead ends. Start a fresh
  session for unrelated work.
- **Commit messages and branch names are the agent's contract.** Atomic
  commits with Conventional Commit prefixes — one concept per commit. If
  the agent cannot name the commit cleanly, the change is too big.

### 6.4 Anti-patterns specific to agents

| Smell | What it looks like | Counter-instruction |
|---|---|---|
| **Helper-module sprawl** | new `utils.py` / `helpers.py` / `_internal.py` per task | "Helpers go next to the only caller until a third caller appears." |
| **Fallback addiction** | `try: real_call() except: return safe_default()` | "Crash loud at internal boundaries ; only catch where the spec demands graceful behavior." |
| **Re-implementation** | recreates a util that exists | "Search before you write. Show me grep output for the concept first." |
| **Doc inflation** | adds 400-line docstring + new MD file for a 5-line fix | "Update existing docs only if behavior visible to users changed." |
| **Configuration creep** | new env var / setting / feature flag per change | "New configuration requires a written justification." |
| **Style drift** | imports reordered, unrelated reformatting | "Format only files you semantically modified ; never reformat the rest of a file." |
| **Imaginary tests** | tests that assert the implementation, not behavior | "A test should be valid after a refactor that preserves behavior. If yours wouldn't, rewrite it." |
| **Plan-then-ignore** | proposes a 3-step plan, then writes 7 unrelated changes | "If the plan changes mid-flight, stop and re-confirm before continuing." |
| **Phantom imports** | imports modules that don't exist or that won't exist after another in-flight change | "Verify each import resolves before claiming the change works." |

### 6.5 Mechanical enforcement (preferred over reminders)

Rules in a markdown file are advisory ; rules in CI are mandatory. Implement
as much of the policy as possible as machine-checkable gates :

- Pre-commit hook : lint + format + type check on changed files only.
- Diff-size CI check : fail PRs over a configured threshold (e.g. 400 lines)
  unless the title contains an explicit `[large-pr]` opt-in.
- Architecture import linter : declared rules ("domain may not import from
  infrastructure"), enforced in CI.
- Cyclomatic complexity gate : fail if any new/modified function exceeds
  a threshold.
- "No new files in `utils/` / `helpers/` / `lib/` without prior approval"
  enforced by a CODEOWNERS or a custom check.
- Coverage delta : fail if a PR drops coverage in touched files below the
  pre-PR level.
- Secret scanning + dependency vulnerability scan, blocking on high severity.

Once these are in place, agent or human, the ceiling on damage per PR is
low — and that is the entire point.

### 6.6 A short reviewer prompt for the agent's diff

When reviewing what an agent produced, run this checklist explicitly :

1. Does the diff do **only** what was asked ?
2. Are all new files justified — could the change have lived in an existing
   one ?
3. Is anything repeated that already exists elsewhere in the repo ?
4. Are there try/except blocks, fallbacks, or default values without a
   stated reason ?
5. Are there new abstractions with only one caller ?
6. Do the tests assert behavior, or implementation ?
7. Would I be comfortable maintaining this code in a year ?

If any answer is "no", do not negotiate fixes — reject and re-prompt with a
narrower instruction. Iterating on a confused diff produces worse results
than starting over with a clearer brief.

---

## 7. Summary cheat sheet

**Architecture** — one sentence per module, dependencies inward, pure core,
imperative shell, no cycles.

**Code** — small functions, named types over primitives, explicit dependencies,
no module-level mutable state, comments only for "why", crash early at
internal boundaries.

**Process** — small PRs (≤ 200 LOC, 1 concept), reviewers gate structure first
correctness second, refactor continuously not at the end, mechanical lint /
type / complexity gates in CI.

**Agent** — plan before code, edit before create, read before write, manual
test gate before commit, no defensive code, no helpers without three callers,
no scope creep, mechanical CI guards beat verbal reminders every time.

The goal is not perfection. The goal is that on any given Tuesday, the next
change is **easy** — that the system has not silently grown into something
that resists improvement. Spaghetti is the absence of that property ;
preventing it is the daily, dull discipline above.
