/**
 * Phase P6.5 placeholder — delegates to the existing SmileChartPanel.
 *
 * The full diagnostic (Live Smile / Parameter Dynamics / Surface
 * Heatmap / No-arb Health tabs) needs ≥30 svi_params snapshots per
 * tenor to draw the Parameter Dynamics time series. Until the
 * sandbox has accumulated enough history, the existing Smile panel
 * with SVI fit + fair/RV reference lines covers "live smile" — the
 * only tab that renders anything meaningful today.
 */
import { SmileChartPanel } from "./SmileChartPanel";

export function SurfaceDiagnosticPanel(): JSX.Element {
  return <SmileChartPanel />;
}
