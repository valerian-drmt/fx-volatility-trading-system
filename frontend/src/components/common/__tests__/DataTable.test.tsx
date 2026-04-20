import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DataTable, type Column } from "../DataTable";

interface Row {
  id: number;
  name: string;
}
const COLS: Column<Row>[] = [
  { key: "id", label: "ID" },
  { key: "name", label: "Name" },
];

describe("DataTable", () => {
  it("renders header labels and rows", () => {
    render(<DataTable columns={COLS} rows={[{ id: 1, name: "alpha" }]} />);
    expect(screen.getByText("ID")).toBeInTheDocument();
    expect(screen.getByText("alpha")).toBeInTheDocument();
  });

  it("shows the empty placeholder when rows is empty", () => {
    render(<DataTable columns={COLS} rows={[]} empty="nothing here" />);
    expect(screen.getByText("nothing here")).toBeInTheDocument();
    expect(screen.queryByText("ID")).not.toBeInTheDocument();
  });

  it("uses custom render when provided", () => {
    render(
      <DataTable
        columns={[{ key: "name", label: "Upper", render: (r) => r.name.toUpperCase() }]}
        rows={[{ id: 1, name: "beta" }]}
      />,
    );
    expect(screen.getByText("BETA")).toBeInTheDocument();
  });
});
