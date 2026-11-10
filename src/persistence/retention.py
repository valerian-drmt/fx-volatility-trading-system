"""History retention: one bounded DELETE per table, each in its own
transaction, counted (#330, audit DI-35 / DI-21).

Both prune jobs used to run several deletes in one session with one commit
and swallow any error: one old ``pca_signal_history`` row still referenced
by a ``trade_structure.pca_signal_id`` made the shared transaction roll back,
so NOTHING was pruned, permanently and silently, and the largest tables grew
without bound. Here every table is pruned on its own, a failure is logged
with the table name and counted in ``history_prune_failures_total``, and the
rows removed land in ``history_prune_rows_total``. Referenced PCA signals are
excluded from the delete instead of blocking it.

Table names are static (a whitelist below); only the cutoff is a parameter.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PruneSpec:
    table: str
    ts_column: str = "timestamp"
    # Extra predicate appended to the DELETE (static SQL, no user input).
    extra_where: str | None = None


# A signal that a trade or an armed preview points at is part of that trade's
# audit trail: keep it however old (the row count involved is tiny).
_PCA_SIGNAL_KEEP_REFERENCED = (
    "AND id NOT IN (SELECT pca_signal_id FROM trade_preview WHERE pca_signal_id IS NOT NULL) "
    "AND id NOT IN (SELECT pca_signal_id FROM trade_structure WHERE pca_signal_id IS NOT NULL)"
)

RISK_ENGINE_TABLES: tuple[PruneSpec, ...] = (
    PruneSpec("open_position_history"),
    PruneSpec("account_history"),
)
VOL_ENGINE_TABLES: tuple[PruneSpec, ...] = (
    PruneSpec("vol_surface_history"),
    PruneSpec("pca_signal_history", extra_where=_PCA_SIGNAL_KEEP_REFERENCED),
)

_ALLOWED_TABLES = frozenset(s.table for s in (*RISK_ENGINE_TABLES, *VOL_ENGINE_TABLES))

# Off-peak schedule and batch size (#373, hot/cold T9): one run a night at
# 02:30 UTC (after the IB Gateway's 23:59 UTC restart, before the London
# open), in slices small enough that no hot read waits behind the delete.
RETENTION_AT_UTC: tuple[int, int] = (2, 30)
BATCH_ROWS: int = 50_000


def next_retention_run(now: datetime | None = None) -> datetime:
    """The next ``RETENTION_AT_UTC`` strictly after ``now``."""
    now = now or datetime.now(UTC)
    hour, minute = RETENTION_AT_UTC
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def _record(engine: str, table: str, *, rows: int = 0, failed: bool = False) -> None:
    try:
        from shared.observability import history_prune_failures_total, history_prune_rows_total

        if failed:
            history_prune_failures_total.labels(engine=engine, table=table).inc()
        else:
            history_prune_rows_total.labels(engine=engine, table=table).inc(rows)
    except Exception:  # metrics are never load-bearing
        pass


async def prune_table(
    db: AsyncSession, spec: PruneSpec, cutoff: datetime,
) -> int:
    """DELETE rows of ``spec.table`` older than ``cutoff``; returns the row
    count. The caller owns the transaction."""
    if spec.table not in _ALLOWED_TABLES:
        raise ValueError(f"table {spec.table!r} is not in the retention whitelist")
    sql = f"DELETE FROM {spec.table} WHERE {spec.ts_column} < :cutoff"
    if spec.extra_where:
        sql += " " + spec.extra_where
    result = await db.execute(text(sql), {"cutoff": cutoff})
    return int(getattr(result, "rowcount", 0) or 0)


async def prune_table_batched(
    sessionmaker: async_sessionmaker[AsyncSession], spec: PruneSpec, cutoff: datetime,
    *, batch_rows: int = BATCH_ROWS,
) -> int:
    """DELETE in slices of ``batch_rows`` ids, one short transaction each, until
    a slice comes back short. Every history table has an integer ``id`` key."""
    if spec.table not in _ALLOWED_TABLES:
        raise ValueError(f"table {spec.table!r} is not in the retention whitelist")
    inner = f"SELECT id FROM {spec.table} WHERE {spec.ts_column} < :cutoff"
    if spec.extra_where:
        inner += " " + spec.extra_where
    sql = f"DELETE FROM {spec.table} WHERE id IN ({inner} LIMIT :n)"
    total = 0
    while True:
        async with sessionmaker() as db:
            result = await db.execute(text(sql), {"cutoff": cutoff, "n": int(batch_rows)})
            await db.commit()
        n = int(getattr(result, "rowcount", 0) or 0)
        total += n
        if n < batch_rows:
            return total


async def run_retention(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    engine: str,
    cutoff: datetime,
    tables: tuple[PruneSpec, ...] | list[PruneSpec],
    batch_rows: int = BATCH_ROWS,
) -> dict[str, Any]:
    """Prune every table on its own, in batches. Never raises: the summary
    maps each table to the rows removed or to ``"failed"``, and the counters
    carry the same information for the alert."""
    summary: dict[str, Any] = {}
    for spec in tables:
        try:
            n = await prune_table_batched(sessionmaker, spec, cutoff, batch_rows=batch_rows)
            summary[spec.table] = n
            _record(engine, spec.table, rows=n)
            logger.info("history_pruned", extra={"engine": engine, "table": spec.table, "rows": n,
                                                 "cutoff": cutoff.isoformat()})
        except Exception:
            summary[spec.table] = "failed"
            _record(engine, spec.table, failed=True)
            logger.exception("history_prune_failed", extra={"engine": engine, "table": spec.table})
    return summary
