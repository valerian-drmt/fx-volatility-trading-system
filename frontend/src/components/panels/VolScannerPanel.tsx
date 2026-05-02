import { useEffect, useState } from "react";
import { fetchSignals, type Signals } from "../../api/endpoints";
import { DataTable, type Column } from "../common/DataTable";

type Row = Signals[number];

const COLUMNS: Column<Row>[] = [
  { key: "timestamp", label: "Time" },
  { key: "underlying", label: "Symbol" },
  { key: "tenor", label: "Tenor" },
  {
    key: "signal_type",
    label: "Signal",
    render: (r) => <span data-severity={r.signal_type === "CHEAP" ? "INFO" : "WARN"}>{r.signal_type}</span>,
  },
  { key: "ecart", label: "Δ" },
];

export function VolScannerPanel(): JSX.Element {
  const [rows, setRows] = useState<Signals>([]);
  useEffect(() => {
    fetchSignals({ limit: 30 })
      .then(setRows)
      .catch(() => setRows([]));
  }, []);
  return (
    <section className="panel scanner-panel" data-testid="scanner-panel">
      <header className="panel-header">
        <h2>Vol Scanner</h2>
        <span className="panel-count">{rows.length}</span>
      </header>
      <div className="panel-body">
        <DataTable columns={COLUMNS} rows={rows} empty="no recent signals" />
      </div>
    </section>
  );
}
