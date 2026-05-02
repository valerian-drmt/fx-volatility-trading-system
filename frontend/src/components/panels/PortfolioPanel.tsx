import { useEffect, useState } from "react";
import { fetchPositions, type Positions, ApiError } from "../../api/endpoints";
import { DataTable, type Column } from "../common/DataTable";

type Row = Positions[number];

const COLUMNS: Column<Row>[] = [
  { key: "id", label: "ID" },
  { key: "symbol", label: "Symbol" },
  { key: "instrument_type", label: "Type" },
  { key: "side", label: "Side" },
  { key: "quantity", label: "Qty" },
  {
    key: "entry_price",
    label: "Entry",
    render: (r) => Number(r.entry_price).toFixed(4),
  },
  { key: "status", label: "Status" },
];

export function PortfolioPanel(): JSX.Element {
  const [rows, setRows] = useState<Positions>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchPositions({ limit: 50 })
      .then(setRows)
      .catch((e: unknown) => {
        setError(e instanceof ApiError ? `API ${e.status}` : "unknown error");
      });
  }, []);

  return (
    <section className="panel portfolio-panel" data-testid="portfolio-panel">
      <header className="panel-header">
        <h2>Portfolio</h2>
        <span className="panel-count">{rows.length}</span>
      </header>
      <div className="panel-body">
        {error ? <div className="panel-error">{error}</div> : null}
        <DataTable columns={COLUMNS} rows={rows} empty="no positions" rowKey={(r) => r.id} />
      </div>
    </section>
  );
}
