# DB schema drift workflow

When the **DB Schema** dev tab in **DIFF** mode surfaces drifts between
`models.py` (ORM) and the live PostgreSQL database, use the two
commands below to bring the DB back in sync via alembic — the proper
tracked-in-git path (vs. running raw `ALTER` in psql which leaves
`alembic_version` lying).

## The two commands

```bash
# 1. Auto-generate a migration that compares Base.metadata to the live DB
docker compose exec api alembic -c src/persistence/alembic.ini revision --autogenerate -m "sync_drift"

# 2. Inspect the generated file in src/persistence/migrations/versions/
#    (open it, read every op.alter_column / op.execute, sanity-check the
#    downgrade is the exact reverse), then apply :
docker compose exec api alembic -c src/persistence/alembic.ini upgrade head
```

After the upgrade, refresh the DB Schema dev tab in DIFF mode — the
counter should flip from `⚠ N drift` to `✓ in sync`.

## Cases autogenerate can miss

Alembic's `--autogenerate` reads what it understands. It misses :

- **JSON ↔ JSONB type changes** — alembic emits `op.alter_column(..., type_=JSONB())`
  but PG requires the `USING` clause for the cast. You'll need to swap
  it for `op.execute("ALTER TABLE x ALTER COLUMN y TYPE JSONB USING y::JSONB;")`.
- **CHECK constraints with anonymous names** — alembic can't always
  diff them ; verify visually in the DIFF tab footer.
- **Table comments and column comments** — only surfaced if you wired
  `comment="..."` on the ORM model.
- **Single-column UNIQUE = `unique=True`** when the DB stores it as a
  named UniqueConstraint with a different name — alembic generates a
  drop+add that's mostly noise.

In those cases, edit the generated `.py` to use raw `op.execute("...")`
with the SQL the dev tab's drift badge (⧉ icon) copies to clipboard.

## When NOT to use this

- **In production without a release window** — every `ALTER COLUMN ...
  SET NOT NULL` rewrites the column on PG ≤ 11 and can hold an
  ACCESS EXCLUSIVE lock. Use a multi-step migration (add column nullable,
  backfill, set not null) for big tables.
- **For `DROP COLUMN` on a column the app still reads** — feature-flag
  the read path off first, deploy, *then* drop in a follow-up migration.
