import { lazy, Suspense } from "react";
import { AppShell } from "./components/layout/AppShell";
import { StatusPanel } from "./components/panels/StatusPanel";
import { PortfolioPanel } from "./components/panels/PortfolioPanel";
import { LogsPanel } from "./components/panels/LogsPanel";
import { OrderTicketPanel } from "./components/panels/OrderTicketPanel";
import { BookPanel } from "./components/panels/BookPanel";

// Plotly bundle is ~380 kB gzip — lazy-load so the initial paint is fast.
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
      right={
        <>
          <OrderTicketPanel />
          <BookPanel />
        </>
      }
    />
  );
}
