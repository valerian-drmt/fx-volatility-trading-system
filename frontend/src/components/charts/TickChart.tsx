import type { Data } from "plotly.js";
import { PlotlyChart } from "./PlotlyChart";
import type { Tick } from "../../hooks/useTicks";

export function TickChart({ history }: { history: Tick[] }): JSX.Element {
  if (history.length === 0) return <div className="chart-empty">waiting for ticks…</div>;
  const x = history.map((_, i) => i);
  const mid: Data = {
    type: "scatter",
    mode: "lines",
    x,
    y: history.map((t) => t.mid),
    line: { color: "#4f9dff" },
    name: "mid",
  };
  return (
    <PlotlyChart
      data={[mid]}
      layout={{ xaxis: { title: { text: "tick index" } }, yaxis: { title: { text: "price" } } }}
    />
  );
}
