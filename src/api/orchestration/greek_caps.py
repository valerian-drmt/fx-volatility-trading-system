"""Live greek caps: NAV base, spot, regime multiplier -> ``GreekCaps`` (#315).

Extracted from ``GET /portfolio/greek-limits`` so the pre-trade gate checks
the SAME caps the panel displays (audit 03#7: ``compute_caps`` was called by
the panel endpoint only, the caps were never checked).
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.risk import greek_limits as gl
from persistence.models import AppConfigScalar, RegimeSnapshot, VolSurface
from persistence.rollups import read_nav_series

# The daily net-liq series comes from the materialised ``portfolio_daily``
# rollup (#368): the pre-trade gate runs this on every submit, so it must
# never pay for a DISTINCT ON over the whole account history.
_NAV_DAYS = 504


@dataclass(frozen=True)
class LiveCaps:
    caps: gl.GreekCaps
    nav_series: list[float]
    params: dict[str, float]


async def compute_live_caps(db: AsyncSession) -> LiveCaps:
    nav_pairs, _ = await read_nav_series(db, days=_NAV_DAYS)
    nav_series = [v for _, v in nav_pairs]
    spot = (await db.execute(
        select(VolSurface.spot).where(VolSurface.underlying == "EURUSD")
        .order_by(desc(VolSurface.timestamp)).limit(1)
    )).scalar_one_or_none()
    rows = (await db.execute(
        select(AppConfigScalar.name, AppConfigScalar.value)
        .where(AppConfigScalar.namespace == "greek_limits")
    )).all()
    params = {name: float(val) for name, val in rows if name in gl.CONFIG_DEFAULTS}

    nav_b = gl.nav_base(
        nav_series,
        hwm_floor=params.get("nav_hwm_floor", gl.CONFIG_DEFAULTS["nav_hwm_floor"]),
        halflife=params.get("nav_halflife_days", gl.CONFIG_DEFAULTS["nav_halflife_days"]),
    ) or 0.0
    spot_f = float(spot) if spot else 0.0

    vol_levels = [
        float(r[0]) for r in (await db.execute(
            select(RegimeSnapshot.vol_level_pct)
            .where(
                RegimeSnapshot.vol_level_pct.is_not(None),
                RegimeSnapshot.timestamp >= datetime.now(UTC) - timedelta(days=90),
            )
            .order_by(RegimeSnapshot.timestamp)
        )).all()
    ]
    regime = 1.0
    if len(vol_levels) >= 10:
        baseline = statistics.median(vol_levels)
        if baseline > 0:
            regime = max(1.0, min(3.0, vol_levels[-1] / baseline))
    return LiveCaps(caps=gl.compute_caps(nav_b, spot_f, regime, params=params),
                    nav_series=nav_series, params=params)
