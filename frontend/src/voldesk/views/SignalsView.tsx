/**
 * VOLDESK — Signal (surface): IV surface z-score field + PCA surface modes
 * (conviction hierarchy), mode stability, and the fair-vol level gate. Ported
 * 1:1 from the prototype's `js/views_signals.jsx` (global-window pattern) into
 * typed ES modules. Mock data for now; wires to the backend in a later lot.
 *
 * The prototype's `activeSignal` / `PcCard` were exposed on `window` for other
 * tabs to consume; they have no in-view consumer, so they are not ported here
 * (this module exports only the `SignalsView` component).
 */
import { Fragment, memo, useMemo, useState } from "react";
import { Heatmap } from "../components/charts";
import { Panel, Tag } from "../components/common";
import { FreshBadge } from "../components/FreshBadge";
import { type Tone } from "../components/format";
import { DATA, fmt } from "../data";
import type { Pc, TermPoint } from "../data";
import type { PcaCard, SurfaceData } from "../data/deskData";
import { useDeskData } from "../data/deskData";

const FAIR_COL = "#46b3d6"; // distinct cool color for the σ_fair curve (vs orange accent for ATM)

// IV surface — Grid + z-score (the recommended workhorse): IV number printed per cell,
// background colored by the PCA rich/cheap z-score (cheap → blue, fair → neutral, rich → red).
// Couples the display to the actual signal and serves "read exact value" + "locate dislocation" at once.
const SIG_ZMAX = 2.5;
function sigDivZ(z: number): string {
  const t = Math.max(0, Math.min(1, (z + SIG_ZMAX) / (2 * SIG_ZMAX)));
  const A: [number, number, number] = [56, 118, 209];
  const M: [number, number, number] = [62, 68, 82];
  const B: [number, number, number] = [201, 68, 64];
  const mix = (c0: [number, number, number], c1: [number, number, number], f: number): string =>
    `rgb(${c0.map((v, i) => Math.round(v + (c1[i]! - v) * f)).join(",")})`;
  return t < 0.5 ? mix(A, M, t / 0.5) : mix(M, B, (t - 0.5) / 0.5);
}
// memo: the IV surface only changes on a vol cycle (~3 min), but the panel sits
// in a tree that re-renders far more often — skip the 30-cell colour/title work
// when `data` (a memoized surface object) is unchanged.
const IVSurfaceZ = memo(function IVSurfaceZ({ data }: { data: SurfaceData | null }): JSX.Element {
  // 30 cells × {colour mix, NaN check, title string} — derived once per surface.
  const cells = useMemo(() => {
    if (!data) return null;
    const deltas = data.deltas;
    const C = deltas.length;
    return data.ivSurface.map((row, i) =>
      row.map((v, j) => {
        const zz = data.ivZ[i]![j]!;
        const missing = Number.isNaN(v);
        const wing = j === 0 || j === C - 1;
        return {
          className: "gz-cell" + (wing ? " wing" : "") + (missing ? " gz-missing" : ""),
          style: missing ? undefined : { background: sigDivZ(zz) },
          title: missing
            ? `${data.tenors[i]} ${deltas[j]} · no data`
            : `${data.tenors[i]} ${deltas[j]} · IV ${v.toFixed(1)} · ${zz > 0 ? "+" : ""}${zz.toFixed(1)}σ`,
          missing,
          label: missing ? "—" : v.toFixed(1),
        };
      }),
    );
  }, [data]);
  if (!data || !cells) {
    return <div className="dim small mono ivz-empty">surface unavailable (market closed / no vol cycle)</div>;
  }
  const surf = data.ivSurface,
    deltas = data.deltas,
    tenors = data.tenors,
    sources = data.sources ?? [];
  const C = deltas.length;
  const hasInterp = sources.includes("interp");
  return (
    <div className="ivz">
      <div className="gz ivz-grid" style={{ gridTemplateColumns: `42px repeat(${C}, 1fr)` }}>
        <div className="gz-corner mono">Tenor\Δ</div>
        {deltas.map((d, j) => (
          <div key={d} className={"gz-colh mono" + (j === 0 || j === C - 1 ? " wing" : "")}>
            {d}
          </div>
        ))}
        {surf.map((row, i) => (
          <Fragment key={i}>
            <div className={"gz-rowh mono" + (sources[i] === "interp" ? " gz-rowh-interp" : "")}>
              {tenors[i]}
              {sources[i] === "interp" && (
                <span className="interp-mark" title="interpolated — no listed contract at this tenor">~</span>
              )}
            </div>
            {row.map((_v, j) => {
              const cell = cells[i]![j]!;
              return (
                <div key={j} className={cell.className} style={cell.style} title={cell.title}>
                  <span className={"gz-iv mono" + (cell.missing ? " dim" : "")}>{cell.label}</span>
                </div>
              );
            })}
          </Fragment>
        ))}
      </div>
      <div className="ivz-foot">
        <div className="ivz-leg">
          <span className="mono small dim">cheap</span>
          <i style={{ background: `linear-gradient(90deg,${sigDivZ(-SIG_ZMAX)},${sigDivZ(0)},${sigDivZ(SIG_ZMAX)})` }} />
          <span className="mono small dim">rich</span>
        </div>
      </div>
      {hasInterp && (
        <div className="ivz-interp-note small">
          <span className="interp-mark">~</span> interpolated tenor — no listed CME contract at that maturity;
          IV is modelled from the listed expiries. A read-only estimate, not a market quote.
        </div>
      )}
    </div>
  );
});

// ATM term curve with σ_fair overlay (the level / gate visual)
function ATMTermChart({ ts }: { ts: TermPoint[] }): JSX.Element {
  const w = 560,
    h = 140,
    pl = 38,
    pr = 16,
    pt = 14,
    pb = 26;
  // SVG scale + path strings derived once per term-structure (the chart sits in
  // a tree that re-renders far more often than the vol cycle that moves `ts`).
  const geo = useMemo(() => {
    // RV (realized) is horizon-matched per tenor (Yang-Zhang over a trailing
    // window ≈ each tenor) → a realized-vol curve aligned with IV / σ_fair.
    const hasRv = ts.some((t) => t.rv > 0);
    const all = ts.flatMap((t) => (hasRv ? [t.atm, t.fair, t.rv] : [t.atm, t.fair]));
    const lo = Math.min(...all) - 0.15,
      hi = Math.max(...all) + 0.15,
      rng = hi - lo || 1;
    const X = (i: number): number => pl + (i / (ts.length - 1)) * (w - pl - pr);
    const Y = (v: number): number => pt + (1 - (v - lo) / rng) * (h - pt - pb);
    const line = (key: "atm" | "fair" | "rv"): string =>
      ts.map((t, i) => (i ? "L" : "M") + X(i).toFixed(1) + " " + Y(t[key]).toFixed(1)).join(" ");
    const ticks: number[] = [];
    for (let v = Math.ceil(lo); v <= hi; v += 0.5) ticks.push(v);
    return {
      hasRv,
      atm: line("atm"),
      fair: line("fair"),
      rv: line("rv"),
      ticks: ticks.map((v) => ({ v, y: Y(v) })),
      pts: ts.map((t, i) => ({ tenor: t.tenor, x: X(i), y: Y(t.atm) })),
    };
  }, [ts]);
  const { hasRv } = geo;
  return (
    <div>
      <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} style={{ display: "block" }}>
        {/* axes — X baseline + Y axis (made prominent) */}
        <line x1={pl} x2={pl} y1={pt} y2={h - pb} stroke="var(--border)" strokeWidth="1.3" />
        <line x1={pl} x2={w - pr} y1={h - pb} y2={h - pb} stroke="var(--border)" strokeWidth="1.3" />
        {geo.ticks.map((tk, i) => (
          <g key={i}>
            <line x1={pl} x2={w - pr} y1={tk.y} y2={tk.y} stroke="var(--line)" opacity="0.6" />
            <text x={6} y={tk.y + 3} fill="var(--text-dim)" fontSize="10" fontWeight={600} fontFamily="var(--mono)">
              {tk.v.toFixed(1)}
            </text>
          </g>
        ))}
        {hasRv && <path d={geo.rv} stroke="var(--muted)" strokeDasharray="2 2" fill="none" strokeWidth="1.4" />}
        <path d={geo.fair} stroke={FAIR_COL} strokeDasharray="5 3" fill="none" strokeWidth="1.8" />
        <path d={geo.atm} stroke="var(--accent)" fill="none" strokeWidth="2.2" />
        {geo.pts.map((p, i) => (
          <circle key={i} cx={p.x} cy={p.y} r="2.6" fill="var(--accent)" stroke="var(--bg)" strokeWidth="1.2" />
        ))}
        {geo.pts.map((p, i) => (
          <text key={"l" + i} x={p.x} y={h - 6} fill="var(--fg)" fontSize="11" fontWeight={600} fontFamily="var(--mono)" textAnchor="middle">
            {p.tenor}
          </text>
        ))}
      </svg>
      <div className="ts-legend">
        <span>
          <i className="lg-line atm" />
          ATM IV
        </span>
        <span>
          <i className="lg-line fair" style={{ borderColor: FAIR_COL }} />
          σ_fair
        </span>
        {hasRv && (
          <span>
            <i className="lg-line" style={{ borderColor: "var(--muted)", borderStyle: "dotted" }} />
            RV (realized)
          </span>
        )}
      </div>
    </div>
  );
}

// z-score over the display window — POSITION (vs per-mode thresholds) + TRAJECTORY (extending vs reverting).
// `series` = the real backend z-history (oldest→newest, /signals/pca/history).
// Empty state until ≥2 points are persisted — no synthetic fallback.
function ZSeriesChart({ pc, view, series }: { pc: Pc; view: string; series?: number[] }): JSX.Element {
  // Single daily z-history → the two views are just different daily windows
  // (1M ≈ 22 pts vs 3M ≈ 65 pts). No hourly series exists, so no "hourly" view.
  const data = series && series.length >= 2 ? series.slice(view === "1M" ? -22 : -65) : [];
  const w = 300,
    h = 110,
    pl = 4,
    pr = 34,
    pt = 10,
    pb = 16;
  if (data.length < 2) {
    return (
      <svg width="100%" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ display: "block" }}>
        <text x={w / 2} y={h / 2} textAnchor="middle" fill="var(--text-faint)" fontSize="9" fontFamily="var(--mono)">
          z-history accumulating…
        </text>
      </svg>
    );
  }
  const n = data.length;
  const ymin = -3,
    ymax = 3;
  const X = (i: number): number => pl + (i / (n - 1)) * (w - pl - pr);
  const Y = (z: number): number => pt + (1 - (Math.max(ymin, Math.min(ymax, z)) - ymin) / (ymax - ymin)) * (h - pt - pb);
  const thr = pc.thr;
  const path = data.map((z, i) => (i ? "L" : "M") + X(i).toFixed(1) + " " + Y(z).toFixed(1)).join(" ");
  const tone =
    pc.label === "CHEAP" ? "var(--pos)" : pc.label === "EXPENSIVE" || pc.label === "RICH" ? "var(--neg)" : "var(--fg)";
  const fs = 8.5;
  return (
    <svg width="100%" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ display: "block" }}>
      {/* rich zone (above +thr) and cheap zone (below −thr) */}
      <rect x={pl} y={Y(ymax)} width={w - pl - pr} height={Y(thr) - Y(ymax)} fill="var(--neg)" opacity="0.1" />
      <rect x={pl} y={Y(-thr)} width={w - pl - pr} height={Y(ymin) - Y(-thr)} fill="var(--pos)" opacity="0.1" />
      {/* threshold + mean reference lines */}
      <line x1={pl} x2={w - pr} y1={Y(thr)} y2={Y(thr)} stroke="var(--neg)" strokeWidth="1" strokeDasharray="3 2" vectorEffect="non-scaling-stroke" />
      <line x1={pl} x2={w - pr} y1={Y(-thr)} y2={Y(-thr)} stroke="var(--pos)" strokeWidth="1" strokeDasharray="3 2" vectorEffect="non-scaling-stroke" />
      <line x1={pl} x2={w - pr} y1={Y(0)} y2={Y(0)} stroke="var(--fg)" strokeWidth="1" strokeDasharray="2 3" opacity="0.22" vectorEffect="non-scaling-stroke" />
      <text x={w - pr + 3} y={Y(thr) + 3} fill="var(--neg)" fontSize={fs} fontFamily="var(--mono)">
        +{thr.toFixed(1)}
      </text>
      <text x={w - pr + 3} y={Y(-thr) + 3} fill="var(--pos)" fontSize={fs} fontFamily="var(--mono)">
        −{thr.toFixed(1)}
      </text>
      <text x={w - pr + 3} y={Y(0) + 3} fill="var(--text-faint)" fontSize={fs} fontFamily="var(--mono)">
        µ
      </text>
      {/* z path + current point */}
      <path d={path} fill="none" stroke={tone} strokeWidth="1.6" vectorEffect="non-scaling-stroke" />
      <circle cx={X(n - 1)} cy={Y(data[n - 1]!)} r="3" fill={tone} stroke="var(--surface)" strokeWidth="1.2" vectorEffect="non-scaling-stroke" />
      <text x={pl} y={h - 4} fill="var(--text-faint)" fontSize={fs} fontFamily="var(--mono)">
        {view === "1M" ? "1M · daily →" : "3M · daily →"}
      </text>
    </svg>
  );
}

// PCA surface-mode card — the RELATIVE signal only (z vs history, loadings). The level gate lives in its own Fair vol panel.
// memo: one card per PC; its props (pc, view) only change on a vol cycle or a
// timeframe toggle, so skip the SVG z-series + loadings heatmap re-render on
// unrelated desk ticks. Exported for the Dashboard Signal card (compact form,
// showLoadings={false} drops the tenor×delta heatmap).
export const ModeCard = memo(function ModeCard({
  pc,
  view,
  showLoadings = true,
}: {
  pc: PcaCard;
  view: string;
  showLoadings?: boolean;
}): JSX.Element {
  const tone: Tone =
    pc.label === "CHEAP" ? "good" : pc.label === "EXPENSIVE" || pc.label === "RICH" ? "danger" : "neutral";
  return (
    <div className={"modecard tone-" + tone}>
      <div className="mc-head">
        <span className="mc-id">{pc.id}</span>
        <span className="mc-name">{pc.name}</span>
        <span className="mc-desc dim">{pc.desc}</span>
        <span className={"mc-tier mono tier-" + pc.tier}>
          {pc.tier === 1 ? "CORE" : pc.tier === 2 ? "SECONDARY" : "TERTIARY"} · {pc.variance.toFixed(2)}% var
        </span>
      </div>
      <div className="mc-zrow">
        {/* z coloured by rich/cheap : green = CHEAP, red = EXPENSIVE, white = FAIR */}
        <span className={"mc-z mono " + (tone === "good" ? "pos" : tone === "danger" ? "neg" : "")}>{fmt.sgn(pc.z, 2)}</span>
        <div className="mc-zmeta">
          <Tag tone={tone}>{pc.label}</Tag>
          <span className="dim small mono">percentile {pc.pctile.toFixed(2)}%</span>
        </div>
      </div>
      <ZSeriesChart pc={pc} view={view} series={pc.zHistory} />
      {showLoadings && (
        <>
          <div className="mc-load-lbl dim small mono">loadings · tenor × delta</div>
          <Heatmap rows={DATA.tenors} cols={DATA.deltas} matrix={pc.load} />
        </>
      )}
    </div>
  );
});

// (Mode stability panel removed — eigengap diagnostics dropped from the desk.)
// (Expressions moved to the Order builder as an exposure reference — see order_builder.jsx)

// memo: the fair-vol gate (term chart + skew table) only moves on a vol cycle.
const FairVolGate = memo(function FairVolGate({ ts }: { ts: TermPoint[] | null }): JSX.Element {
  if (!ts) {
    return <div className="dim small mono ivz-empty">term structure unavailable (market closed / no vol cycle)</div>;
  }
  return (
    <div>
      <div className="fv-chart">
        <div className="surf-curve-lbl dim small mono">IV vs fair</div>
        <ATMTermChart ts={ts} />
      </div>
      <div className="table-scroll fv-table-wrap">
        <table className="dt fv-table">
          <thead>
            <tr>
              <th className="l">Tenor</th>
              <th className="r">ATM</th>
              <th className="r">RV</th>
              <th className="r">Fair</th>
              <th className="r">Spread</th>
              <th className="r fv-skew col-grp">25Δ BF</th>
              <th className="r fv-skew">10Δ BF</th>
              <th className="r fv-skew">25Δ RR</th>
              <th className="r fv-skew col-grp-end">10Δ RR</th>
            </tr>
          </thead>
          <tbody>
            {ts.map((t) => {
              const spread = t.atm - t.fair,
                rich = spread > 0;
              const fly = (v: number): string => parseFloat(v.toFixed(3)).toString();
              const rr = (v: number): string => (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(2);
              return (
                <tr key={t.tenor}>
                  <td className="l mono">{t.tenor}</td>
                  <td className="r mono">{t.atm.toFixed(2)}</td>
                  <td className="r mono">{t.rv.toFixed(2)}</td>
                  <td className="r mono">{t.fair.toFixed(2)}</td>
                  <td className={"r mono " + (rich ? "pos" : "neg")}>{fmt.sgn(spread, 2)}</td>
                  <td className="r mono fv-skew col-grp">{fly(t.bf25)}</td>
                  <td className="r mono fv-skew">{fly(t.bf10)}</td>
                  <td className="r mono fv-skew">{rr(t.rr25)}</td>
                  <td className="r mono fv-skew col-grp-end">{rr(t.rr10)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
});

export function SignalsView(): JSX.Element {
  const [view, setView] = useState<string>("3M");
  const { surface, termStructure, pca } = useDeskData();
  const pcsList = pca.data?.pcs ?? [];
  return (
    <div className="ts-grid">
      <div className="sig-cluster">
        <div className="sig-left">
          <Panel title="IV surface" dataPp="iv-surface" right={<FreshBadge fresh={surface} label="" />} className="ts-curve-panel">
            <IVSurfaceZ data={surface.data} />
          </Panel>
        </div>
        <Panel title="Fair vol" dataPp="fair-vol" right={<FreshBadge fresh={termStructure} label="RV / GARCH" />} className="ts-fv-panel sig-fv" pad>
          <FairVolGate ts={termStructure.data} />
        </Panel>
      </div>

      <Panel
        title="PCA engine — surface modes"
        dataPp="pca-modes"
        right={
          <div className="pca-head-right">
            <div className="tf-group">
              {["3M", "1M"].map((v) => (
                <button key={v} className={"chip " + (view === v ? "on" : "")} onClick={() => setView(v)}>
                  {v === "3M" ? "3M daily" : "1M daily"}
                </button>
              ))}
            </div>
            <FreshBadge fresh={pca} />
          </div>
        }
        className="ts-pca-panel"
      >
        {pcsList.length ? (
          <div className="mode-grid">
            {pcsList.map((pc) => (
              <ModeCard key={pc.id} pc={pc} view={view} />
            ))}
          </div>
        ) : (
          <div className="dim small mono ivz-empty">PCA model unavailable (no fit / market closed)</div>
        )}
      </Panel>
    </div>
  );
}
