/**
 * VOLDESK shared chart primitives (SVG, no chart lib). Ported from the
 * prototype's `js/charts.jsx`. Only the components actually used by the
 * shipped views are ported here: Heatmap (Signal) and Donut (Trade). The
 * prototype's CandleChart / GaussCurve / Sparkline / ZGauge were only used by
 * the parking-bench TestView (not in the nav) and are intentionally dropped.
 */
import type { ReactNode } from "react";
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

export function Heatmap({ rows, cols, matrix, decimals = 2 }: HeatmapProps): JSX.Element {
  const flat = matrix.flat();
  const max = Math.max(0.001, ...flat.map((v) => Math.abs(v)));
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
              <td key={ci} style={{ background: loadColor(v, max) }}>
                {fmt.sgn(v, decimals)}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

interface DonutSegment {
  value: number;
  color: string;
  label?: string;
}
interface DonutProps {
  segments: DonutSegment[];
  center?: ReactNode;
  size?: number;
  thickness?: number;
}

export function Donut({ segments, center, size = 84, thickness = 11 }: DonutProps): JSX.Element {
  const total = segments.reduce((s, x) => s + x.value, 0) || 1;
  const r = (size - thickness) / 2;
  const circ = 2 * Math.PI * r;
  const gap = 1.5;
  let offset = 0;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{ display: "block" }}>
      <g transform={`rotate(-90 ${size / 2} ${size / 2})`}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--bg-3)" strokeWidth={thickness} />
        {segments.map((s, i) => {
          const len = (s.value / total) * circ;
          const el = (
            <circle
              key={i}
              cx={size / 2}
              cy={size / 2}
              r={r}
              fill="none"
              stroke={s.color}
              strokeWidth={thickness}
              strokeDasharray={`${Math.max(0, len - gap)} ${circ - Math.max(0, len - gap)}`}
              strokeDashoffset={-offset}
              strokeLinecap="butt"
            />
          );
          offset += len;
          return el;
        })}
      </g>
      {center && (
        <text
          x={size / 2}
          y={size / 2}
          textAnchor="middle"
          dominantBaseline="central"
          fontSize="13"
          fontWeight="700"
          fill="var(--fg)"
          fontFamily="var(--mono)"
        >
          {center}
        </text>
      )}
    </svg>
  );
}
