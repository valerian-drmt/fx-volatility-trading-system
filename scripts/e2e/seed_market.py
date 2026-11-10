"""Seed a market into a running stack for the unmocked e2e round trip (#341).

Runs inside the api container (`docker compose exec api ...`), so it writes
through the same bus code the engines use: `bus.publisher.publish_tick` for
ticks and the `latest_vol_surface:<symbol>` key the vol engine sets.

    # the vol engine's canonical payload (tests/fixtures/payloads/vol_update.json) on stdin
    docker compose exec -T api python scripts/e2e/seed_market.py surface < tests/fixtures/payloads/vol_update.json
    # ticks every 500 ms for N seconds (run detached)
    docker compose exec -d api python scripts/e2e/seed_market.py ticks --bid 1.12345 --ask 1.12347 --seconds 900

The surface is re-stamped "now" so the api serves it as live. Nothing here
talks to IB; it is the IB-free half of the pipeline (bus -> api -> nginx ->
browser) that the e2e spec asserts on.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime

from bus import keys
from bus.client import get_async_redis
from bus.publisher import publish_tick


async def seed_surface(symbol: str) -> None:
    payload = json.load(sys.stdin)
    payload["symbol"] = symbol
    payload["timestamp"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    redis = get_async_redis()
    await redis.set(keys.LATEST_VOL_SURFACE.format(symbol=symbol), json.dumps(payload), ex=keys.TTL_VOL_SURFACE)
    print(f"seed_market: surface for {symbol} with {len([k for k in payload['surface'] if not k.startswith('_')])} tenors")


async def seed_ticks(symbol: str, bid: float, ask: float, seconds: float, every: float) -> None:
    redis = get_async_redis()
    loop = asyncio.get_running_loop()
    end = loop.time() + seconds
    n = 0
    while loop.time() < end:
        await publish_tick(redis, symbol, bid, ask, round((bid + ask) / 2, 6), timestamp=datetime.now(UTC))
        n += 1
        await asyncio.sleep(every)
    print(f"seed_market: {n} ticks for {symbol}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("surface")
    s.add_argument("--symbol", default="EURUSD")
    t = sub.add_parser("ticks")
    t.add_argument("--symbol", default="EURUSD")
    t.add_argument("--bid", type=float, required=True)
    t.add_argument("--ask", type=float, required=True)
    t.add_argument("--seconds", type=float, default=900.0)
    t.add_argument("--every", type=float, default=0.5)
    args = parser.parse_args()
    if args.cmd == "surface":
        asyncio.run(seed_surface(args.symbol))
    else:
        asyncio.run(seed_ticks(args.symbol, args.bid, args.ask, args.seconds, args.every))
    return 0


if __name__ == "__main__":
    sys.exit(main())
