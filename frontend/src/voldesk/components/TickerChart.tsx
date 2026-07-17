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
import type { MacroEvent } from "../data/core";
import type { TradeEvent } from "../data/live/portfolio";

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

export function TickerChart({
  spot,
  markers = [],
  events = [],
}: {
  spot: number;
  markers?: TradeEvent[];
  events?: MacroEvent[];
}): JSX.Element {
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
  // TradingView-style future zone: when macro events are drawn, the candles are
  // compressed into the left 75% of the plot and the time axis keeps running
  // into the blank right 25%, so upcoming events sit at their TRUE time.
  const futFrac = events.length ? 0.25 : 0;
  const colW = (plotW * (1 - futFrac)) / N;
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

  // Trade markers: map an event timestamp to a fractional candle x (null if it
  // falls outside the visible range), anchored to entry_spot or the candle close.
  const t0 = candles[0]!.t,
    tN = candles[N - 1]!.t;
  const interval = N > 1 ? (tN - t0) / (N - 1) : 3_600_000;
  const markerX = (t: number): number | null =>
    t < t0 - interval / 2 || t > tN + interval / 2 ? null : X((t - t0) / interval);
  const priceAt = (t: number): number =>
    candles[Math.max(0, Math.min(N - 1, Math.round((t - t0) / interval)))]!.c;
  const fmtTs = (t: number): string => {
    const d = new Date(t);
    return `${pad2(d.getUTCDate())}/${pad2(d.getUTCMonth() + 1)} ${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}`;
  };
  const visibleMarkers = markers
    .map((m) => ({ m, x: markerX(m.t) }))
    .filter((e): e is { m: TradeEvent; x: number } => e.x != null);

  // Macro-event dots along the bottom of the price area, at their TRUE time on
  // the axis (the same linear time→x mapping the candles use, extended into the
  // future zone). Filled dot = past/current, hollow ring = upcoming. Events
  // beyond the visible window simply don't show — switch to a wider range
  // (1W/1M) to see further out, like TradingView.
  const evtY = priceBot - 7;
  const evtCol = (impact: string): string =>
    /high/i.test(impact) ? "var(--neg)" : /med/i.test(impact) ? "var(--warn)" : "var(--text-dim)";
  const evtDots = events
    .map((e) => ({ e, t: Date.parse(e.date) }))
    .filter((d) => !Number.isNaN(d.t) && d.t >= t0 - interval / 2)
    .sort((a, b) => a.t - b.t)
    .map((d) => ({ ...d, x: X((d.t - t0) / interval), future: d.t > tN + interval / 2 }))
    .filter((d) => d.x > pl + 4 && d.x < W - pr - 3);

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
        {/* trade markers — ▲ open (entry) / ● close (coloured by realized P&L), hover = tooltip */}
        {visibleMarkers.map(({ m, x }, i) => {
          const y = m.spot != null ? Y(m.spot) : Y(priceAt(m.t));
          const col =
            m.kind === "open" ? "var(--accent)" : m.pnl == null ? "var(--muted)" : m.pnl >= 0 ? "var(--pos)" : "var(--neg)";
          const tip =
            m.kind === "open"
              ? `Opened #${m.id} ${m.type}${m.spot != null ? " @ " + m.spot.toFixed(4) : ""} · ${fmtTs(m.t)}`
              : `Closed #${m.id} ${m.type}${m.pnl != null ? " " + (m.pnl >= 0 ? "+" : "−") + "$" + Math.abs(m.pnl / 1000).toFixed(1) + "k" : ""} · ${fmtTs(m.t)}`;
          return (
            <g key={"m" + i} style={{ cursor: "pointer" }}>
              <title>{tip}</title>
              {m.kind === "open" ? (
                <path d={`M${x} ${y - 5.5} L${x + 4.5} ${y + 3} L${x - 4.5} ${y + 3} Z`} fill="none" stroke={col} strokeWidth="1.7" />
              ) : (
                <circle cx={x} cy={y} r="3.7" fill={col} stroke="var(--bg)" strokeWidth="1" />
              )}
            </g>
          );
        })}
        {/* "now" divider between the candles and the future zone */}
        {futFrac > 0 && (
          <line x1={pl + N * colW} x2={pl + N * colW} y1={pt} y2={priceBot} stroke="var(--line)" strokeDasharray="2 3" />
        )}
        {/* macro-event dots — bottom of the price area, at their scheduled time;
            hollow ring = upcoming */}
        {evtDots.map((d, i) => (
          <g key={"e" + i} style={{ cursor: "pointer" }}>
            <title>
              {`${d.e.code} · ${d.e.content} · ${fmtTs(d.t)}${d.future && d.e.in ? " · in " + d.e.in : ""}`}
            </title>
            <circle
              cx={d.x}
              cy={evtY}
              r="3.2"
              fill={d.future ? "var(--bg)" : evtCol(d.e.impact)}
              stroke={evtCol(d.e.impact)}
              strokeWidth="1.4"
            />
          </g>
        ))}
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
