import type { Data } from "plotly.js";
import type { SmilePoint } from "./SmileChart";
import type { TermPoint } from "./TermStructureChart";

export function smileTrace(points: SmilePoint[]): Data {
  return {
    type: "scatter",
    mode: "lines+markers",
    x: points.map((p) => p.strike),
    y: points.map((p) => p.vol),
    line: { color: "#4f9dff" },
    marker: { color: "#4f9dff", size: 6 },
  };
}

export function termTrace(points: TermPoint[]): Data {
  return {
    name: "σ mid",
    type: "scatter",
    mode: "lines+markers",
    x: points.map((p) => p.tenor),
    y: points.map((p) => p.atmVol),
    line: { color: "#3fb950", shape: "spline" },
    marker: { color: "#3fb950", size: 6 },
  };
}

export function termFairTrace(points: TermPoint[]): Data {
  return {
    name: "σ fair (GARCH)",
    type: "scatter",
    mode: "lines+markers",
    x: points.map((p) => p.tenor),
    y: points.map((p) => p.fairVol ?? null),
    line: { color: "#f59e0b", shape: "spline", dash: "dash" },
    marker: { color: "#f59e0b", size: 5 },
    connectgaps: false,
  };
}

export function termRvTrace(points: TermPoint[]): Data {
  return {
    name: "RV (Yang-Zhang)",
    type: "scatter",
    mode: "lines",
    x: points.map((p) => p.tenor),
    y: points.map((p) => p.rv ?? null),
    line: { color: "#94a3b8", shape: "linear", dash: "dot" },
    connectgaps: false,
  };
}
