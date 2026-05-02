import { useTicks } from "../../hooks/useTicks";
import { ConnectionIndicator } from "../common/ConnectionIndicator";
import { MetricTile } from "../common/MetricTile";

export function StatusPanel(): JSX.Element {
  const { last, count } = useTicks();
  return (
    <section className="panel status-panel" data-testid="status-panel">
      <header className="panel-header">
        <h2>Status</h2>
        <ConnectionIndicator />
      </header>
      <div className="panel-body status-metrics">
        <MetricTile label="Ticks" value={count} hint="since session start" />
        <MetricTile label="Bid" value={last?.bid ?? "—"} />
        <MetricTile label="Ask" value={last?.ask ?? "—"} />
        <MetricTile label="Mid" value={last?.mid ?? "—"} />
      </div>
    </section>
  );
}
