import { PlotlyChart } from "./PlotlyChart";
import { smileTrace } from "./traces";

export interface SmilePoint {
  strike: number;
  vol: number;
}

export interface SmileChartProps {
  points: SmilePoint[];
  tenor: string;
}

export function SmileChart({ points, tenor }: SmileChartProps): JSX.Element {
  if (points.length === 0) {
    return <div className="chart-empty">no smile data for {tenor}</div>;
  }
  return (
    <PlotlyChart
      data={[smileTrace(points)]}
      layout={{ xaxis: { title: { text: "Strike" } }, yaxis: { title: { text: `σ(${tenor})` } } }}
    />
  );
}
