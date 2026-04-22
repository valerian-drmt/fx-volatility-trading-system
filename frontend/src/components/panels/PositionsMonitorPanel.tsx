/**
 * Phase P6.4 placeholder — delegates to the existing BookPanel for now.
 *
 * A dedicated structure-centric monitor (grouping legs into vol
 * structures, exit-alert column, delta-hedge sub-panel) requires a
 * structure-id column on the positions table and the execution
 * adapter that submits structures as a unit. Those land with Phase 5
 * IB integration ; until then the BookPanel view of raw legs is still
 * the single source of truth.
 */
import { BookPanel } from "./BookPanel";

export function PositionsMonitorPanel(): JSX.Element {
  return <BookPanel />;
}
