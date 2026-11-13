"""How often the vol surface is published, and how old it may be (#480).

The vol engine publishes one surface per cycle. The freshness limit used to
be a free number in the config (120 s) below the cycle itself (180 s): for a
third of every cycle the latest surface was "stale" by the system's own rule,
and half of the refused submits on record were that.

The limit is therefore derived from the cadence: a surface is fresh while no
publication has been missed. The configured value can only make the limit
looser than that floor, never tighter than the engine can deliver.
"""
from __future__ import annotations

from typing import Final

# One surface per cycle (work, then sleep to the deadline).
SURFACE_PUBLISH_INTERVAL_S: Final = 180.0
# A cycle plus half of one: tolerates the cycle's own computing time and
# jitter, and trips when a publication is missed.
SURFACE_MISSED_FACTOR: Final = 1.5


def surface_age_floor_s() -> float:
    """The tightest freshness limit the publishing cadence allows."""
    return SURFACE_PUBLISH_INTERVAL_S * SURFACE_MISSED_FACTOR


def surface_age_limit_s(configured: float | None) -> float:
    """The limit to enforce: the configured one, never below the floor."""
    floor = surface_age_floor_s()
    try:
        value = float(configured) if configured is not None else floor
    except (TypeError, ValueError):
        value = floor
    return max(value, floor)


def is_surface_fresh(age_s: float | None, configured: float | None) -> bool:
    """False for an unknown age: not knowing is not fresh."""
    return age_s is not None and age_s <= surface_age_limit_s(configured)
