/**
 * VOLDESK — EUR/USD candlestick ticker (TradingView-style) with a market-session
 * overlay + range presets (Day / Week / Month).
 *
 * Each preset pairs a candle interval with a lookback range (like TradingView):
 *   1D → 15m candles over ~1 day · 1W → 1h over ~1 week · 1M → 4h over ~1 month.
 * Candle count differs per range, by design. Data is REAL OHLC: GET /bars serves
 * candles the market-data engine pulls from IB (reqHistoricalData, MIDPOINT) and
 * caches in Redis. Empty until the engine has populated the cache (needs IB
 * Gateway + the engines profile running).
 *
 * The session indicator is three NON-overlapping colour bands (London / New York
 * / Hong Kong), one row each, lit on the candles whose UTC hour falls inside that
 * market's trading window — so overlapping sessions (e.g. London∩NY) never stack.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { fetchBars, type Bar } from "../../api/endpoints";
import { useFetch } from "../../hooks/useFetch";

// UTC trading windows + one distinct colour per market (non-overlapping rows).
const SESSIONS = [
  { code: "LON", label: "London", open: 7, close: 16, color: "#4c8dff" },
  { code: "NY", label: "New York", open: 12, close: 21, color: "#3fb950" },
  { code: "HK", label: "Hong Kong", open: 1, close: 9, color: "#e3a008" },
];

// range presets → backend tf key + the candle interval each one uses (for the note)
const PRESETS = [
  { key: "1D", interval: "15m", intraday: true },
  { key: "1W", interval: "1h", intraday: false },
  { key: "1M", interval: "4h", intraday: false },
];
const LIMIT = 250; // upper bound on bars fetched (backend caps at ≤500)

const isOpen = (hour: number, open: number, close: number): boolean => hour >= open && hour < close;
const pad2 = (n: number): string => String(n).padStart(2, "0");

export function TickerChart({ spot }: { spot: number }): JSX.Element {
  const [tf, setTf] = useState("1D");
  const preset = PRESETS.find((p) => p.key === tf)!;
  // real OHLC candles from the market-data engine's IB cache (poll every 60s)
  const barsFetch = useFetch<Bar[]>(() => fetchBars("EURUSD", tf, LIMIT), 180_000, true, 60_000);
  // useFetch only re-runs on its poll tick, not when the fetcher closure (tf)
  // changes — so refetch immediately whenever the preset button changes.
  const { reload } = barsFetch;
  const firstTf = useRef(true);
  useEffect(() => {
    if (firstTf.current) {
      firstTf.current = false;
      return; // mount already fetched via useFetch
    }
    reload();
  }, [tf, reload]);
  const candles = useMemo(() => barsFetch.data ?? [], [barsFetch.data]);
  const N = candles.length;

  const W = 520,
    H = 300,
    pl = 46,
    pr = 10,
    pt = 8;
  const bandH = 15,
    bandGap = 3,
    axisH = 16;
  const bandsTop = H - axisH - SESSIONS.length * (bandH + bandGap);
  const priceBot = bandsTop - 10;

  const tfButtons = (
    <div className="ticker-tf">
      {PRESETS.map((p) => (
        <button key={p.key} type="button" className={"ticker-tf-btn " + (tf === p.key ? "on" : "")} onClick={() => setTf(p.key)}>
          {p.key}
        </button>
      ))}
    </div>
  );

  if (N < 2) {
    return (
      <div className="ticker">
        {tfButtons}
        <div className="ticker-empty dim small mono">
          {barsFetch.status === "missing" ? "bars unavailable — needs market-data engine + IB up" : "loading bars…"}
        </div>
      </div>
    );
  }

  const plotW = W - pl - pr;
  const colW = plotW / N;
  const bodyW = Math.max(1, colW * 0.64);

  const lo = Math.min(...candles.map((c) => c.l), spot || Infinity);
  const hi = Math.max(...candles.map((c) => c.h), spot || -Infinity);
  const pad = (hi - lo) * 0.08 || 0.001;
  const yLo = lo - pad,
    yHi = hi + pad;
  const X = (i: number): number => pl + (i + 0.5) * colW;
  const Y = (p: number): number => pt + (1 - (p - yLo) / (yHi - yLo)) * (priceBot - pt);
  const hourOf = (c: Bar): number => new Date(c.t).getUTCHours();
  // intraday → "HHh"; multi-day ranges → "DD/MM"
  const axisLabel = (c: Bar): string => {
    const d = new Date(c.t);
    return preset.intraday ? pad2(d.getUTCHours()) + "h" : pad2(d.getUTCDate()) + "/" + pad2(d.getUTCMonth() + 1);
  };

  const last = spot || candles[N - 1]!.c;
  const ticks: number[] = [];
  for (let i = 0; i <= 4; i++) ticks.push(yLo + ((yHi - yLo) / 4) * i);
  const labelEvery = Math.ceil(N / 8);

  return (
    <div className="ticker">
      {tfButtons}
      <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{ display: "block" }}>
        <rect x={pl} y={pt} width={plotW} height={priceBot - pt} fill="var(--bg-3)" opacity="0.4" />
        {/* price grid + axis */}
        {ticks.map((p, i) => (
          <g key={"t" + i}>
            <line x1={pl} x2={W - pr} y1={Y(p)} y2={Y(p)} stroke="var(--line)" opacity="0.5" />
            <text x={pl - 4} y={Y(p) + 3} textAnchor="end" fontSize="9" fontFamily="var(--mono)" fill="var(--text-faint)">
              {p.toFixed(4)}
            </text>
          </g>
        ))}
        {/* candles */}
        {candles.map((c, i) => {
          const up = c.c >= c.o;
          const col = up ? "var(--pos)" : "var(--neg)";
          const x = X(i);
          const top = Math.min(Y(c.o), Y(c.c));
          const bh = Math.max(1, Math.abs(Y(c.c) - Y(c.o)));
          return (
            <g key={"c" + i}>
              <line x1={x} x2={x} y1={Y(c.h)} y2={Y(c.l)} stroke={col} strokeWidth="1" />
              <rect x={x - bodyW / 2} y={top} width={bodyW} height={bh} fill={col} />
            </g>
          );
        })}
        {/* live last price */}
        <line x1={pl} x2={W - pr} y1={Y(last)} y2={Y(last)} stroke="var(--accent)" strokeWidth="1" strokeDasharray="3 2" />
        <text x={W - pr} y={Y(last) - 3} textAnchor="end" fontSize="9.5" fontWeight={700} fontFamily="var(--mono)" fill="var(--accent)">
          {last.toFixed(5)}
        </text>
        {/* session bands — one non-overlapping row per market */}
        {SESSIONS.map((s, si) => {
          const y = bandsTop + si * (bandH + bandGap);
          return (
            <g key={s.code}>
              {candles.map((c, i) => (
                <rect key={i} x={pl + i * colW} y={y} width={colW} height={bandH} fill={s.color} opacity={isOpen(hourOf(c), s.open, s.close) ? 0.85 : 0.07} />
              ))}
              <text x={pl - 5} y={y + bandH / 2} dominantBaseline="central" textAnchor="end" fontSize="12.5" fontWeight={800} fontFamily="var(--mono)" fill={s.color}>
                {s.code}
              </text>
            </g>
          );
        })}
        {/* time axis (UTC) — hour intraday, date for week/month */}
        {candles.map((c, i) => (i % labelEvery === 0 ? (
          <text key={"h" + i} x={X(i)} y={H - 3} textAnchor="middle" fontSize="8" fontFamily="var(--mono)" fill="var(--text-faint)">
            {axisLabel(c)}
          </text>
        ) : null))}
      </svg>
      <div className="ticker-legend">
        {SESSIONS.map((s) => (
          <span key={s.code}>
            <i style={{ background: s.color }} />
            {s.label}
          </span>
        ))}
      </div>
    </div>
  );
}
