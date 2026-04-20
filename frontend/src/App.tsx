import { AppShell } from "./components/layout/AppShell";
import { StatusPanel } from "./components/panels/StatusPanel";
import { PortfolioPanel } from "./components/panels/PortfolioPanel";
import { LogsPanel } from "./components/panels/LogsPanel";

const PLACEHOLDER = (label: string): JSX.Element => (
  <div className="panel panel-placeholder">
    <header className="panel-header">
      <h2>{label}</h2>
    </header>
    <div className="panel-body">landing in a later R5 PR</div>
  </div>
);

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
        <>
          {PLACEHOLDER("Chart / Term Structure / Smile")}
          {PLACEHOLDER("Vol Scanner")}
        </>
      }
      right={PLACEHOLDER("Order Ticket / Book")}
    />
  );
}
