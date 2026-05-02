import { useSystemAlerts, type SystemAlert } from "../../hooks/useSystemAlerts";
import { DataTable, type Column } from "../common/DataTable";

const COLUMNS: Column<SystemAlert>[] = [
  { key: "ts", label: "Time", render: (a) => a.ts ?? "—" },
  {
    key: "severity",
    label: "Lvl",
    render: (a) => <span data-severity={a.severity}>{a.severity}</span>,
  },
  { key: "message", label: "Message" },
];

export function LogsPanel(): JSX.Element {
  const alerts = useSystemAlerts();
  return (
    <section className="panel logs-panel" data-testid="logs-panel">
      <header className="panel-header">
        <h2>Logs</h2>
        <span className="panel-count">{alerts.length}</span>
      </header>
      <div className="panel-body">
        <DataTable columns={COLUMNS} rows={alerts.slice().reverse()} empty="no alerts yet" />
      </div>
    </section>
  );
}
