import { PlotlyChart } from "./PlotlyChart";
import { termFairTrace, termRvTrace, termTrace } from "./traces";

export interface TermPoint {
  tenor: string;
  atmVol: number;
  fairVol?: number | null;
  rv?: number | null;
}

export function TermStructureChart({ points }: { points: TermPoint[] }): JSX.Element {
  if (points.length === 0) return <div className="chart-empty">no term structure</div>;
  const traces = [termTrace(points)];
  if (points.some((p) => p.fairVol != null)) traces.push(termFairTrace(points));
  if (points.some((p) => p.rv != null)) traces.push(termRvTrace(points));
  return (
    <PlotlyChart
      data={traces}
      layout={{
        xaxis: { title: { text: "Tenor" }, type: "category" },
        yaxis: { title: { text: "σ (%)" } },
        showlegend: true,
        legend: { orientation: "h", y: -0.2 },
      }}
    />
  );
}
