"""Pure domain logic shared across ``api/``, ``src/services/`` and the
future ``services/<name>/`` containers introduced in R7.

Every module under ``core/`` MUST be :
- side-effect free (no Redis, no DB, no IB, no filesystem)
- deterministic for identical inputs
- typed explicitly
- covered by a snapshot-equivalence test that locks numerical output
"""
