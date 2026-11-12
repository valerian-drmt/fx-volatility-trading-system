"""Read-only SQL against the prod database, through the SSM tunnel.

Prerequisites (docs/ops/runbooks.md section 5):
  1. ``.\\scripts\\aws\\ec2.ps1 db-tunnel`` running in another window
     (localhost:15433 -> prod Postgres).
  2. ``DB_READONLY_PASSWORD`` in the environment (``load_secrets.ps1``).

The role is ``fxvol_reader``: SELECT only, every transaction read-only, 30 s
statement timeout, 3 connections. The password is read from the environment
and never printed.

    python scripts/local/prod_db.py --tables
    python scripts/local/prod_db.py "SELECT status, count(*) FROM position GROUP BY 1"
    python scripts/local/prod_db.py -f query.sql --csv > out.csv
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys

HOST, PORT, USER, DATABASE = "127.0.0.1", 15433, "fxvol_reader", "fxvol"

TABLES_SQL = """
SELECT c.relname AS table, c.reltuples::bigint AS est_rows,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS size
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r'
ORDER BY pg_total_relation_size(c.oid) DESC
"""


def _cell(value: object) -> str:
    return "" if value is None else str(value)


async def run(sql: str, limit: int, as_csv: bool) -> int:
    import asyncpg

    password = os.environ.get("DB_READONLY_PASSWORD")
    if not password:
        print("DB_READONLY_PASSWORD is not set in this environment (run load_secrets.ps1 first).", file=sys.stderr)
        return 2
    try:
        conn = await asyncpg.connect(
            host=HOST, port=PORT, user=USER, password=password, database=DATABASE,
            timeout=10, server_settings={"application_name": "prod_db.py"},
        )
    except (TimeoutError, OSError):
        print(f"No tunnel on {HOST}:{PORT}: run `.\\scripts\\aws\\ec2.ps1 db-tunnel` in another window.", file=sys.stderr)
        return 3
    except asyncpg.PostgresError as exc:
        print(f"Connection refused by Postgres: {type(exc).__name__} ({exc.sqlstate})", file=sys.stderr)
        return 3
    try:
        async with conn.transaction(readonly=True):
            cursor = await conn.cursor(sql)
            rows = await cursor.fetch(limit + 1)
    except asyncpg.PostgresError as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        await conn.close()

    truncated = len(rows) > limit
    rows = rows[:limit]
    if not rows:
        print("(0 rows)")
        return 0
    headers = list(rows[0].keys())
    table = [[_cell(v) for v in row.values()] for row in rows]
    if as_csv:
        writer = csv.writer(sys.stdout, lineterminator="\n")
        writer.writerow(headers)
        writer.writerows(table)
    else:
        widths = [min(60, max(len(h), *(len(r[i]) for r in table))) for i, h in enumerate(headers)]
        print(" | ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True)))
        print("-+-".join("-" * w for w in widths))
        for r in table:
            print(" | ".join(c[:w].ljust(w) for c, w in zip(r, widths, strict=True)))
    note = f"({len(rows)} rows" + (f", truncated at --limit {limit})" if truncated else ")")
    print(note, file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only SQL against prod through the SSM tunnel.")
    parser.add_argument("sql", nargs="?", help="SQL text (or use -f / --tables)")
    parser.add_argument("-f", "--file", help="read the SQL from a file")
    parser.add_argument("--tables", action="store_true", help="list tables with estimated rows and size")
    parser.add_argument("--limit", type=int, default=200, help="max rows printed (default 200)")
    parser.add_argument("--csv", action="store_true", help="CSV on stdout")
    args = parser.parse_args()
    if args.tables:
        sql = TABLES_SQL
    elif args.file:
        with open(args.file, encoding="utf-8") as handle:
            sql = handle.read()
    elif args.sql:
        sql = args.sql
    else:
        parser.error("give SQL text, -f FILE or --tables")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    return asyncio.run(run(sql, args.limit, args.csv))


if __name__ == "__main__":
    sys.exit(main())
