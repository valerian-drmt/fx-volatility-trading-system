import { useEffect, useMemo, useState } from "react";
import { fetchPositions, type Positions } from "../../api/endpoints";
import { DataTable, type Column } from "../common/DataTable";

type Row = Positions[number];

const COLUMNS: Column<Row>[] = [
  { key: "id", label: "ID" },
  { key: "symbol", label: "Symbol" },
  {
    key: "side",
    label: "Side",
    render: (r) => <span data-sign={r.side === "BUY" ? "pos" : "neg"}>{r.side}</span>,
  },
  { key: "quantity", label: "Qty" },
  { key: "option_type", label: "OptType", render: (r) => r.option_type ?? "—" },
  { key: "strike", label: "Strike", render: (r) => r.strike ?? "—" },
  { key: "maturity", label: "Maturity", render: (r) => r.maturity ?? "—" },
  {
    key: "entry_price",
    label: "Entry",
    render: (r) => Number(r.entry_price).toFixed(4),
  },
];

export function BookPanel(): JSX.Element {
  const [rows, setRows] = useState<Positions>([]);

  useEffect(() => {
    fetchPositions({ limit: 200 })
      .then(setRows)
      .catch(() => setRows([]));
  }, []);

  const { open, closed } = useMemo(() => {
    const o: Positions = [];
    const c: Positions = [];
    for (const r of rows) (r.status === "OPEN" ? o : c).push(r);
    return { open: o, closed: c };
  }, [rows]);

  return (
    <section className="panel book-panel" data-testid="book-panel">
      <header className="panel-header">
        <h2>Book</h2>
        <span className="panel-count">
          {open.length} open · {closed.length} closed
        </span>
      </header>
      <div className="panel-body">
        <h3 className="book-subhead">Open</h3>
        <DataTable columns={COLUMNS} rows={open} empty="no open positions" rowKey={(r) => r.id} />
        <h3 className="book-subhead">Closed</h3>
        <DataTable
          columns={COLUMNS}
          rows={closed}
          empty="no closed positions"
          rowKey={(r) => r.id}
        />
      </div>
    </section>
  );
}
