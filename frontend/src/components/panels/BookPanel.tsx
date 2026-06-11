import { useEffect, useState } from "react";
import { fetchPositions, type Positions } from "../../api/endpoints";
import { DataTable, type Column } from "../common/DataTable";

type Row = Positions[number];

const COLUMNS: Column<Row>[] = [
  { key: "id", label: "ID" },
  { key: "structure", label: "Structure" },
  {
    key: "side",
    label: "Side",
    render: (r) => <span data-sign={r.side === "BUY" ? "pos" : "neg"}>{r.side}</span>,
  },
  { key: "quantity", label: "Qty" },
  { key: "tenor", label: "Tenor", render: (r) => r.tenor ?? "—" },
  { key: "expiry", label: "Expiry", render: (r) => r.expiry ?? "—" },
  {
    key: "contract_price_entry",
    label: "Entry",
    render: (r) =>
      r.contract_price_entry != null ? Number(r.contract_price_entry).toFixed(4) : "—",
  },
  {
    key: "market_price",
    label: "Mark",
    render: (r) => (r.market_price != null ? Number(r.market_price).toFixed(4) : "—"),
  },
  {
    key: "current_pnl_usd",
    label: "PnL",
    render: (r) => (r.current_pnl_usd != null ? Number(r.current_pnl_usd).toFixed(2) : "—"),
  },
];

export function BookPanel(): JSX.Element {
  const [rows, setRows] = useState<Positions>([]);

  useEffect(() => {
    fetchPositions({ limit: 200 })
      .then(setRows)
      .catch(() => setRows([]));
  }, []);

  // Since migration 028 the ``positions`` table holds OPEN positions only
  // (closed ones are DELETEd at sync time), so the book is a single table.
  return (
    <section className="panel book-panel" data-testid="book-panel">
      <header className="panel-header">
        <h2>Book</h2>
        <span className="panel-count">{rows.length} open</span>
      </header>
      <div className="panel-body">
        <DataTable columns={COLUMNS} rows={rows} empty="no open positions" rowKey={(r) => r.id} />
      </div>
    </section>
  );
}
