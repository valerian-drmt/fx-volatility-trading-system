// @ts-expect-error — plotly.js-gl3d-dist-min ships no .d.ts.
import Plotly from "plotly.js-gl3d-dist-min";
import { useMemo } from "react";
import createPlotlyComponent from "react-plotly.js/factory";
import type { Config, Data, Layout } from "plotly.js";

const Plot = createPlotlyComponent(Plotly);

const DARK_3D_LAYOUT: Partial<Layout> = {
  paper_bgcolor: "#181b22",
  plot_bgcolor: "#0f1115",
  font: { color: "#e6e8ee", size: 11 },
  margin: { t: 16, r: 16, b: 16, l: 16 },
  showlegend: false,
  scene: {
    xaxis: {
      title: { text: "tenor" }, gridcolor: "#262a33",
      zerolinecolor: "#262a33", color: "#aaa",
      tickfont: { color: "#aaa" }, showbackground: true, backgroundcolor: "#10121a",
    },
    yaxis: {
      title: { text: "delta" }, gridcolor: "#262a33",
      zerolinecolor: "#262a33", color: "#aaa",
      tickfont: { color: "#aaa" }, showbackground: true, backgroundcolor: "#10121a",
    },
    zaxis: {
      title: { text: "iv (%)" }, gridcolor: "#262a33",
      zerolinecolor: "#262a33", color: "#aaa",
      tickfont: { color: "#aaa" }, showbackground: true, backgroundcolor: "#0c0e14",
    },
    bgcolor: "#0f1115",
    camera: { eye: { x: 1.7, y: 1.7, z: 1.0 } },
    aspectmode: "manual" as const,
    aspectratio: { x: 1.4, y: 1.0, z: 0.7 },
  },
};

// Perceptually uniform-ish diverging colorscale (cool→warm) — better than the
// default Plotly viridis for spotting smile asymmetry across the strike axis.
const COLORSCALE: Array<[number, string]> = [
  [0.00, "#1b1f3a"],
  [0.15, "#2a4d7a"],
  [0.35, "#3e8eab"],
  [0.55, "#7fc6a4"],
  [0.75, "#f0c674"],
  [0.90, "#e07b39"],
  [1.00, "#a02323"],
];

export interface Surface3DProps {
  /** Tenor labels along x-axis (e.g. ["1M","2M","3M","4M","5M","6M"]). */
  xLabels: string[];
  /** Delta labels along y-axis (e.g. ["10dp","25dp","atm","25dc","10dc"]). */
  yLabels: string[];
  /** ``z[yIdx][xIdx]`` = iv in % at (yLabels[yIdx], xLabels[xIdx]). */
  z: number[][];
  height?: number;
  /** Sub-cells per source cell along each axis (bilinear refine). 4 = 16× density. */
  refine?: number;
}

/** Bilinear refinement of a 2D grid — densifies the 6×5 input to ~24×20 so
 *  Plotly's surface shader has more vertices and the gradient stays smooth.
 *  NaN cells in the input propagate (no extrapolation). */
function bilinearRefine(z: number[][], factor: number): number[][] {
  if (factor <= 1 || z.length < 2) return z;
  const row0 = z[0];
  if (!row0 || row0.length < 2) return z;
  const ny = z.length;
  const nx = row0.length;
  const NY = (ny - 1) * factor + 1;
  const NX = (nx - 1) * factor + 1;
  const out: number[][] = Array.from({ length: NY }, () => new Array<number>(NX).fill(Number.NaN));
  for (let Y = 0; Y < NY; Y++) {
    const fy = Y / factor;
    const y0 = Math.min(Math.floor(fy), ny - 1);
    const y1 = Math.min(y0 + 1, ny - 1);
    const dy = fy - y0;
    const rowY0 = z[y0] as number[];
    const rowY1 = z[y1] as number[];
    const outRow = out[Y] as number[];
    for (let X = 0; X < NX; X++) {
      const fx = X / factor;
      const x0 = Math.min(Math.floor(fx), nx - 1);
      const x1 = Math.min(x0 + 1, nx - 1);
      const dx = fx - x0;
      const v00 = rowY0[x0] as number;
      const v10 = rowY0[x1] as number;
      const v01 = rowY1[x0] as number;
      const v11 = rowY1[x1] as number;
      if (
        !Number.isFinite(v00) || !Number.isFinite(v10) ||
        !Number.isFinite(v01) || !Number.isFinite(v11)
      ) {
        outRow[X] = Number.NaN;
        continue;
      }
      const top = v00 * (1 - dx) + v10 * dx;
      const bot = v01 * (1 - dx) + v11 * dx;
      outRow[X] = top * (1 - dy) + bot * dy;
    }
  }
  return out;
}

export function Plot3DSurface({
  xLabels, yLabels, z, height = 360, refine = 6,
}: Surface3DProps): JSX.Element {
  // Densified z + matching tick coordinates. Original integer ticks for the
  // labels still anchor at the original cell positions.
  const zDense = useMemo(() => bilinearRefine(z, refine), [z, refine]);
  const xDenseLen = zDense[0]?.length ?? 0;
  const xDenseTicks = useMemo(
    () => Array.from({ length: xDenseLen }, (_, i) => i / refine),
    [xDenseLen, refine],
  );
  const yDenseTicks = useMemo(
    () => Array.from({ length: zDense.length }, (_, i) => i / refine),
    [zDense, refine],
  );
  const xTickvals = useMemo(() => xLabels.map((_, i) => i), [xLabels]);
  const yTickvals = useMemo(() => yLabels.map((_, i) => i), [yLabels]);

  // Auto z-range so contour spacing adapts to current vol level (1M ATM ~7%
  // looks crushed if we fix the range to [0, 50]).
  const finiteZ = zDense.flat().filter((v) => Number.isFinite(v));
  const zMin = finiteZ.length ? Math.min(...finiteZ) : 0;
  const zMax = finiteZ.length ? Math.max(...finiteZ) : 1;
  const zPad = (zMax - zMin) * 0.10 || 0.5;

  const layout: Partial<Layout> = {
    ...DARK_3D_LAYOUT,
    scene: {
      ...DARK_3D_LAYOUT.scene,
      xaxis: {
        ...DARK_3D_LAYOUT.scene?.xaxis,
        tickvals: xTickvals, ticktext: xLabels,
      },
      yaxis: {
        ...DARK_3D_LAYOUT.scene?.yaxis,
        tickvals: yTickvals, ticktext: yLabels,
      },
      zaxis: {
        ...DARK_3D_LAYOUT.scene?.zaxis,
        range: [zMin - zPad, zMax + zPad],
      },
    },
    autosize: true, height,
  };
  // ``contours`` per-axis is supported by Plotly but the @types/plotly.js
  // declaration is too narrow ; cast to relax the literal-properties check.
  const surfaceTrace = {
    type: "surface",
    x: xDenseTicks, y: yDenseTicks, z: zDense,
    colorscale: COLORSCALE,
    showscale: true,
    colorbar: {
      thickness: 10, len: 0.65, x: 1.02,
      tickfont: { color: "#aaa", size: 9 },
      title: { text: "iv %", font: { color: "#aaa", size: 10 }, side: "right" },
    },
    // Soft, slightly metallic look — ambient kept high so dark theme stays
    // readable even when the mesh tilts steeply.
    lighting: {
      ambient: 0.55, diffuse: 0.85, specular: 0.25,
      roughness: 0.65, fresnel: 0.20,
    },
    lightposition: { x: 100, y: 200, z: 80 },
    contours: {
      x: { show: true, color: "rgba(255,255,255,0.08)", width: 1 },
      y: { show: true, color: "rgba(255,255,255,0.08)", width: 1 },
      z: { show: true, usecolormap: true, highlightcolor: "#ffffff",
           project: { z: true } },
    },
    opacity: 0.97,
    hoverinfo: "x+y+z",
    hovertemplate:
      "tenor=%{x:.2f}<br>delta=%{y:.2f}<br>iv=%{z:.2f}%<extra></extra>",
  };
  const data: Data[] = [surfaceTrace as unknown as Data];
  const config: Partial<Config> = {
    displayModeBar: true,
    modeBarButtonsToRemove: ["toImage"],
    responsive: true,
  };
  return (
    <Plot
      data={data}
      layout={layout}
      config={config}
      style={{ width: "100%", height }}
      useResizeHandler
    />
  );
}
