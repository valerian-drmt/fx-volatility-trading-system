"""Read-only SQL against the prod database: one command, nothing to prepare.

Each call (docs/ops/runbooks.md section 5):
  1. reads the read-only role's password from SSM into memory (profile
     ``fxvol-dev``), unless ``DB_READONLY_PASSWORD`` is already in the environment;
  2. opens an SSM port-forwarding session to the host (profile ``admin``),
     localhost:15433 -> prod Postgres on the host loopback, unless a tunnel
     (``ec2.ps1 db-tunnel``) is already up;
  3. refuses to go further unless the IB account is a PAPER account;
  4. runs the SQL in one read-only transaction and closes the tunnel it opened.

The role is ``fxvol_reader``: SELECT only, every transaction read-only, 30 s
statement timeout, 3 connections. The password stays in this process's memory:
never printed, never written, never passed on a command line.

Paper only, three times over: the deploy grants the login only for a paper
account, the execution engine revokes it on the first heartbeat of a live
account, and this script checks ``runtime_ib_session.account_type`` itself.

    python scripts/local/prod_db.py --tables
    python scripts/local/prod_db.py "SELECT status, count(*) FROM position GROUP BY 1"
    python scripts/local/prod_db.py -f query.sql --csv > out.csv
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import socket
import subprocess
import sys
import time

HOST, PORT, USER, DATABASE = "127.0.0.1", 15433, "fxvol_reader", "fxvol"
HOST_PORT = 15432  # Postgres on the prod host's loopback (docker-compose.yml)
REGION = os.environ.get("AWS_REGION", "eu-west-1")
SECRET_PROFILE = os.environ.get("FXVOL_SECRET_PROFILE", "fxvol-dev")
TUNNEL_PROFILE = os.environ.get("FXVOL_TUNNEL_PROFILE", "admin")
PARAM = "/fxvol/prod/DB_READONLY_PASSWORD"
# Git Bash rewrites a leading "/" in an argument into a Windows path.
AWS_ENV = {**os.environ, "MSYS_NO_PATHCONV": "1", "MSYS2_ARG_CONV_EXCL": "*"}

ACCOUNT_TYPE_SQL = "SELECT account_type FROM runtime_ib_session WHERE broker = 'IB' LIMIT 1"
TABLES_SQL = """
SELECT c.relname AS table, c.reltuples::bigint AS est_rows,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS size
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r'
ORDER BY pg_total_relation_size(c.oid) DESC
"""


def _cell(value: object) -> str:
    return "" if value is None else str(value)


def _aws(*args: str, profile: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["aws", *args, "--region", REGION, "--profile", profile],
        capture_output=True, text=True, env=AWS_ENV, check=False, timeout=60,
    )


def read_password() -> str | None:
    """The reader password, from the environment or SSM. Held in memory only."""
    value = os.environ.get("DB_READONLY_PASSWORD")
    if value:
        return value
    out = _aws(
        "ssm", "get-parameter", "--name", PARAM, "--with-decryption",
        "--query", "Parameter.Value", "--output", "text", profile=SECRET_PROFILE,
    )
    if out.returncode != 0:
        # Fixed messages only: nothing that came back from the call is echoed.
        if "ParameterNotFound" in out.stderr:
            print("The reader parameter does not exist in SSM: the access is closed.", file=sys.stderr)
        elif "AccessDenied" in out.stderr:
            print("The AWS profile for secrets is not allowed to read the reader parameter.", file=sys.stderr)
        else:
            print("Could not read the reader parameter from SSM (AWS CLI error: login expired?).", file=sys.stderr)
        return None
    return out.stdout.strip() or None


def _port_open() -> bool:
    with socket.socket() as probe:
        probe.settimeout(1)
        return probe.connect_ex((HOST, PORT)) == 0


def open_tunnel() -> subprocess.Popen[bytes] | None:
    """Start an SSM port-forwarding session; None when one is already listening."""
    if _port_open():
        return None
    found = _aws(
        "ec2", "describe-instances",
        "--filters", "Name=tag:Name,Values=fxvol-prod", "Name=instance-state-name,Values=running",
        "--query", "Reservations[].Instances[].InstanceId | [0]", "--output", "text",
        profile=TUNNEL_PROFILE,
    )
    instance = found.stdout.strip()
    if found.returncode != 0 or not instance or instance == "None":
        raise RuntimeError(f"no running fxvol-prod instance ({found.stderr.strip()[-200:] or 'none found'})")
    params = json.dumps({"portNumber": [str(HOST_PORT)], "localPortNumber": [str(PORT)]})
    proc = subprocess.Popen(
        ["aws", "ssm", "start-session", "--target", instance,
         "--document-name", "AWS-StartPortForwardingSession", "--parameters", params,
         "--region", REGION, "--profile", TUNNEL_PROFILE],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=AWS_ENV,
    )
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if _port_open():
            return proc
        if proc.poll() is not None:
            err = (proc.stderr.read() if proc.stderr else b"").decode("utf-8", "replace").strip()
            raise RuntimeError(f"SSM tunnel exited: {err[-300:] or proc.returncode}")
        time.sleep(0.5)
    close_tunnel(proc)
    raise RuntimeError("SSM tunnel did not come up within 25 s")


def close_tunnel(proc: subprocess.Popen[bytes] | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    if os.name == "nt":  # aws.exe spawns session-manager-plugin: kill the tree
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def paper_only(account_type: object) -> str | None:
    """None when the account is paper; otherwise the refusal to print."""
    if account_type == "paper":
        return None
    seen = "unknown" if account_type is None else str(account_type)
    return f"Refused: this access is for a PAPER account only, and prod reports account_type={seen}."


async def run(sql: str, limit: int, as_csv: bool) -> int:
    import asyncpg

    password = read_password()
    if not password:
        return 2
    try:
        conn = await asyncpg.connect(
            host=HOST, port=PORT, user=USER, password=password, database=DATABASE,
            timeout=10, server_settings={"application_name": "prod_db.py"},
        )
    except (TimeoutError, OSError):
        print(f"Nothing answers on {HOST}:{PORT} (is the Postgres port published on the host yet?).", file=sys.stderr)
        return 3
    except asyncpg.PostgresError as exc:
        print(f"Connection refused by Postgres: {type(exc).__name__} ({exc.sqlstate})", file=sys.stderr)
        return 3
    try:
        refusal = paper_only(await conn.fetchval(ACCOUNT_TYPE_SQL))
        if refusal:
            print(refusal, file=sys.stderr)
            return 4
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
    parser = argparse.ArgumentParser(description="Read-only SQL against prod through an SSM tunnel (paper account only).")
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
    try:
        tunnel = open_tunnel()
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"Tunnel: {exc}", file=sys.stderr)
        return 3
    try:
        return asyncio.run(run(sql, args.limit, args.csv))
    finally:
        close_tunnel(tunnel)


if __name__ == "__main__":
    sys.exit(main())
