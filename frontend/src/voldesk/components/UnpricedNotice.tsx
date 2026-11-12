/**
 * VOLDESK — "the net greeks are incomplete" notice (#459).
 *
 * A held line with no delta is not a line with zero delta: nothing priced it
 * (a leg only the book holds, an instrument the risk engine cannot read). The
 * totals above the table are then short of it, and the pre-trade check
 * refuses new trades for the same reason. This says so, and names the lines.
 */
import type { JSX } from "react";
import type { Position } from "../data";

export function UnpricedNotice({ positions }: { positions: Position[] }): JSX.Element | null {
  const missing = positions.filter((p) => p.unpriced);
  if (missing.length === 0) return null;
  const names = missing.map((p) => p.structure).join(", ");
  return (
    <div
      data-testid="unpriced-notice"
      className="small mono"
      role="status"
      style={{ padding: "8px 10px", color: "var(--warn)", borderBottom: "1px solid var(--bg-3)" }}
    >
      Net greeks incomplete: {missing.length} line{missing.length > 1 ? "s" : ""} not priced ({names}).
      New trades are refused until every held line has a delta.
    </div>
  );
}
