import Plotly from "plotly.js-basic-dist-min";
import createPlotlyComponent from "react-plotly.js/factory";
import type { Data, Layout, Config } from "plotly.js";

// Wire react-plotly.js against the basic (smaller) plotly bundle.
const Plot = createPlotlyComponent(Plotly);

const DARK_LAYOUT: Partial<Layout> = {
  paper_bgcolor: "#181b22",
  plot_bgcolor: "#0f1115",
  font: { color: "#e6e8ee", size: 11 },
  margin: { t: 20, r: 10, b: 30, l: 40 },
  showlegend: false,
  xaxis: { gridcolor: "#262a33", zeroline: false },
  yaxis: { gridcolor: "#262a33", zeroline: false },
};

const DEFAULT_CONFIG: Partial<Config> = {
  displayModeBar: false,
  responsive: true,
};

// Interactive: drag-to-zoom box + wheel zoom + pan/reset via the modebar. Used
// for time-series charts you want to inspect closely (pair with a stable
// `layout.uirevision` so the zoom survives data refreshes).
const INTERACTIVE_CONFIG: Partial<Config> = {
  displayModeBar: true,
  displaylogo: false,
  responsive: true,
  scrollZoom: true,
  modeBarButtonsToRemove: ["select2d", "lasso2d"],
};

export interface PlotlyChartProps {
  data: Data[];
  layout?: Partial<Layout>;
  height?: number;
  interactive?: boolean;
}

export function PlotlyChart({ data, layout, height = 260, interactive = false }: PlotlyChartProps): JSX.Element {
  return (
    <Plot
      data={data}
      layout={{ ...DARK_LAYOUT, ...layout, autosize: true, height }}
      config={interactive ? INTERACTIVE_CONFIG : DEFAULT_CONFIG}
      style={{ width: "100%", height }}
      useResizeHandler
    />
  );
}
