export interface Column<R> {
  key: keyof R & string;
  label: string;
  render?: (row: R) => React.ReactNode;
}

export interface DataTableProps<R> {
  columns: Column<R>[];
  rows: R[];
  empty?: string;
  rowKey?: (row: R, index: number) => string | number;
}

export function DataTable<R>({
  columns,
  rows,
  empty = "no data",
  rowKey,
}: DataTableProps<R>): JSX.Element {
  if (rows.length === 0) {
    return <div className="data-table-empty">{empty}</div>;
  }
  return (
    <table className="data-table">
      <thead>
        <tr>
          {columns.map((c) => (
            <th key={c.key}>{c.label}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, i) => (
          <tr key={rowKey ? rowKey(row, i) : i}>
            {columns.map((c) => (
              <td key={c.key}>{c.render ? c.render(row) : String(row[c.key] ?? "")}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
