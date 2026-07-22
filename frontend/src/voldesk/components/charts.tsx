/**
 * VOLDESK shared chart primitives (SVG, no chart lib). Ported from the
 * prototype's `js/charts.jsx`. Only the components actually used by the
 * shipped views are ported here: Heatmap (Signal) and Donut (Trade). The
 * prototype's CandleChart / GaussCurve / Sparkline / ZGauge were only used by
 * the parking-bench TestView (not in the nav) and are intentionally dropped.
 */
import { memo, useMemo } from "react";
import { fmt } from "../data";

// loadings heat colour: green (+) ↔ red (−), opacity scaled by |v|/max
function loadColor(v: number, max: number): string {
  const t = Math.max(-1, Math.min(1, v / max));
  if (t >= 0) return `oklch(0.62 ${0.02 + 0.14 * t} 155 / ${0.18 + 0.62 * t})`;
  return `oklch(0.60 ${0.02 + 0.16 * -t} 25 / ${0.18 + 0.62 * -t})`;
}

interface HeatmapProps {
  rows: string[];
  cols: string[];
  matrix: number[][];
  decimals?: number;
}

// memo: a Signal mode-card re-renders on every desk tick once mounted; the
// loadings heatmap (30 cells × colour compute) is pure in its props, so skip it
// when rows/cols/matrix are unchanged.
export const Heatmap = memo(function Heatmap({ rows, cols, matrix, decimals = 2 }: HeatmapProps): JSX.Element {
  // Per-cell background colours derived once per matrix (max scan + 30 colour mixes).
  const max = useMemo(() => Math.max(0.001, ...matrix.flat().map((v) => Math.abs(v))), [matrix]);
  const bg = useMemo(() => matrix.map((row) => row.map((v) => loadColor(v, max))), [matrix, max]);
  return (
    <table className="heatmap">
      <thead>
        <tr>
          <th></th>
          {cols.map((c) => (
            <th key={c}>{c}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {matrix.map((row, ri) => (
          <tr key={ri}>
            <th>{rows[ri]}</th>
            {row.map((v, ci) => (
              <td key={ci} style={{ background: bg[ri]![ci] }}>
                {fmt.sgn(v, decimals)}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
});
