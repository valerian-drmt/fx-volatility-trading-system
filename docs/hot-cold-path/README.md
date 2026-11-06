# Hot / cold path programme

Goal: the desk feels instant where money moves (orders, fills, positions,
account, spot) and may be slow where it thinks (vol surface, PCA, regime,
rollups, attribution). The six engines are already separate processes; this
programme adds the rules that keep the cold work from standing in the hot
work's way, and the measurements that prove it.

| File | What it holds |
|---|---|
| [01-problems.md](01-problems.md) | The problem register: ten problems (P1 to P10), each with symptom, mechanism, evidence, impact and the incident that revealed it. |
| [02-tasks.md](02-tasks.md) | The task plan: ten tasks (T1 to T10) with scope, acceptance, dependencies, estimate, and the problems each one closes. |
| [../architecture/hot-cold-paths.md](../architecture/hot-cold-paths.md) | The architecture note the programme is built on: topology, the two lanes, the target. |

Tracking: GitHub epic #375 and one issue per task (#365 to #374), labelled
`hot-cold-path`, on project board #5. The problem register is updated when a task closes a problem
(status column in `01-problems.md`); the tickets carry the discussion.

## Reading order for a newcomer

1. The topology diagram in the architecture note (which engine is on which path).
2. `01-problems.md` sections P1, P3 and P5: the three collisions that explain
   almost every latency complaint.
3. `02-tasks.md` dependency diagram: which task unblocks which.

## Principles (used as acceptance language in every task)

- A hot read never waits behind a cold query: no cold scan inside a request.
- A hot update is pushed, not polled: polling remains only as reconciliation.
- A panel never blanks and never substitutes a mock: last good value plus a
  freshness badge that ages on a clock.
- Priority is enforced by the kernel and the scheduler, not by hoping the
  cold work is quick.
- Every promise is measured: click to acknowledgement, fill to screen.
