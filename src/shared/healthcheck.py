"""Container healthcheck for the engines, one implementation (#344, OPS-13/14).

Used by every engine Dockerfile ``HEALTHCHECK`` and by the matching
``docker-compose.yml`` blocks, so the two can no longer drift: the image
checks used ``float(ts)`` on an ISO-8601 heartbeat and failed unconditionally
whenever a heartbeat existed; only the compose override worked.

    python -m shared.healthcheck heartbeat <engine> <max_age_s> [--tick SYMBOL MAX_AGE_S]

- ``heartbeat``: ``heartbeat:<engine>`` must exist and be younger than
  ``max_age_s`` (ISO-8601 as written by ``bus.publisher.set_heartbeat``; a
  bare epoch float is accepted too).
- ``--tick``: during FX market hours the engine must also have produced data:
  ``latest_spot:<SYMBOL>`` must be present (it carries a 30 s TTL, so presence
  is freshness) or, when ``MAX_AGE_S`` is given and the value cannot be aged,
  the key's remaining TTL must show it was written within ``MAX_AGE_S``.
  Outside market hours only the heartbeat counts (weekend-aware).

Exit 0 = healthy, 1 = unhealthy, with one line of reason on stdout so
``docker inspect`` shows why. Standard library + redis only.
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from typing import cast


def parse_heartbeat(raw: str | bytes | None) -> datetime | None:
    """ISO-8601 (``...Z`` or offset) or epoch seconds -> aware datetime; None if unusable."""
    if raw is None:
        return None
    text = raw.decode() if isinstance(raw, bytes) else str(raw)
    text = text.strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        pass
    try:
        return datetime.fromtimestamp(float(text), tz=UTC)
    except (ValueError, OverflowError, OSError):
        return None


def evaluate(
    *,
    heartbeat_raw: str | bytes | None,
    now: datetime,
    max_age_s: float,
    market_open: bool = False,
    tick_required: bool = False,
    tick_present: bool | None = None,
    tick_ttl_s: int | None = None,
    tick_key_ttl_s: int | None = None,
    tick_max_age_s: float | None = None,
) -> tuple[bool, str]:
    """Pure decision: (healthy, reason). Tested without Redis."""
    hb = parse_heartbeat(heartbeat_raw)
    if hb is None:
        return False, "no heartbeat"
    age = (now - hb).total_seconds()
    if age >= max_age_s:
        return False, f"heartbeat stale ({age:.0f}s >= {max_age_s:.0f}s)"
    if tick_required and market_open:
        if not tick_present:
            return False, f"heartbeat ok ({age:.0f}s) but no tick during market hours"
        # The tick key has a TTL; if we know both the configured TTL and the
        # remaining TTL, the write age is their difference.
        if (
            tick_max_age_s is not None
            and tick_ttl_s is not None
            and tick_key_ttl_s is not None
            and tick_key_ttl_s >= 0
        ):
            tick_age = tick_ttl_s - tick_key_ttl_s
            if tick_age > tick_max_age_s:
                return False, f"tick stale ({tick_age}s > {tick_max_age_s:.0f}s)"
        return True, f"heartbeat {age:.0f}s, tick fresh"
    return True, f"heartbeat {age:.0f}s"


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="shared.healthcheck")
    sub = parser.add_subparsers(dest="cmd", required=True)
    hb = sub.add_parser("heartbeat", help="heartbeat age check (+ optional tick presence)")
    hb.add_argument("engine")
    hb.add_argument("max_age_s", type=float)
    hb.add_argument("--tick", nargs=2, metavar=("SYMBOL", "MAX_AGE_S"), default=None)
    args = parser.parse_args(argv)

    import redis  # local import: keeps module import cheap for the pure helpers

    from bus import keys
    from shared.config import get_settings
    from shared.market_hours import is_fx_market_open

    client = redis.Redis.from_url(get_settings().REDIS_URL, socket_timeout=3)
    now = datetime.now(UTC)
    heartbeat_raw = client.get(keys.HEARTBEAT.format(engine_name=args.engine))

    tick_kwargs: dict[str, object] = {}
    if args.tick is not None:
        symbol, tick_max = args.tick
        tick_key = keys.LATEST_SPOT.format(symbol=symbol)
        tick_kwargs = {
            "tick_required": True,
            "market_open": is_fx_market_open(now),
            "tick_present": bool(client.exists(tick_key)),
            "tick_ttl_s": keys.TTL_SPOT,
            "tick_key_ttl_s": int(cast("int", client.ttl(tick_key))),
            "tick_max_age_s": float(tick_max),
        }

    healthy, reason = evaluate(
        heartbeat_raw=heartbeat_raw, now=now, max_age_s=args.max_age_s, **tick_kwargs,  # type: ignore[arg-type]
    )
    print(f"{args.engine}: {'healthy' if healthy else 'UNHEALTHY'} - {reason}")
    return 0 if healthy else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main(sys.argv[1:]))
