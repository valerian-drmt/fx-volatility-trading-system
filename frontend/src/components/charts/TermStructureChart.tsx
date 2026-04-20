import { PlotlyChart } from "./PlotlyChart";
import { termTrace } from "./traces";

export interface TermPoint {
  tenor: string;
  atmVol: number;
}

export function TermStructureChart({ points }: { points: TermPoint[] }): JSX.Element {
  if (points.length === 0) return <div className="chart-empty">no term structure</div>;
  return (
    <PlotlyChart
      data={[termTrace(points)]}
      layout={{ xaxis: { title: { text: "Tenor" }, type: "category" }, yaxis: { title: { text: "ATM σ" } } }}
    />
  );
}
