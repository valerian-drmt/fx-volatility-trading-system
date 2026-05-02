import { lazy, Suspense } from "react";
import { AppShell } from "./components/layout/AppShell";
import { StatusPanel } from "./components/panels/StatusPanel";
import { PortfolioPanel } from "./components/panels/PortfolioPanel";
import { LogsPanel } from "./components/panels/LogsPanel";

// Plotly bundle is ~800 kB gzip — lazy-load so the initial paint is fast.
const ChartPanel = lazy(() =>
  import("./components/panels/ChartPanel").then((m) => ({ default: m.ChartPanel })),
);
const TermStructurePanel = lazy(() =>
  import("./components/panels/TermStructurePanel").then((m) => ({ default: m.TermStructurePanel })),
);
const SmileChartPanel = lazy(() =>
  import("./components/panels/SmileChartPanel").then((m) => ({ default: m.SmileChartPanel })),
);
const VolScannerPanel = lazy(() =>
  import("./components/panels/VolScannerPanel").then((m) => ({ default: m.VolScannerPanel })),
);

const PLACEHOLDER = (label: string): JSX.Element => (
  <div className="panel panel-placeholder">
    <header className="panel-header">
      <h2>{label}</h2>
    </header>
    <div className="panel-body">landing in a later R5 PR</div>
  </div>
);

const LOADING = <div className="panel panel-placeholder"><div className="panel-body">loading charts…</div></div>;

export default function App(): JSX.Element {
  return (
    <AppShell
      left={
        <>
          <StatusPanel />
          <PortfolioPanel />
          <LogsPanel />
        </>
      }
      center={
        <Suspense fallback={LOADING}>
          <div className="chart-row">
            <ChartPanel />
            <TermStructurePanel />
            <SmileChartPanel />
          </div>
          <VolScannerPanel />
        </Suspense>
      }
      right={PLACEHOLDER("Order Ticket / Book")}
    />
  );
}
