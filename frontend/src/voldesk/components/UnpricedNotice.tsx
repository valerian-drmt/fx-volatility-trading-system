/**
 * VOLDESK — "the net greeks are incomplete" notice (#459).
 *
 * A position the broker HOLDS with no delta is not a position with zero
 * delta: the risk engine could not price it. The net greeks are then short of
 * it and the pre-trade check refuses new trades (it counts the broker's rows
 * with no delta). This says so and names the contracts.
 *
 * A "book only" line is not concerned: the broker does not hold it, so it
 * carries no risk and blocks nothing. The first version of this notice
 * counted those lines too and announced refusals that were not happening.
 */
import type { JSX } from "react";
import type { Position } from "../data";

export function UnpricedNotice({ positions }: { positions: Position[] }): JSX.Element | null {
  const missing = positions.filter((p) => p.unpriced && !p.netted);
  if (missing.length === 0) return null;
  const names = missing.map((p) => p.structure).join(", ");
  return (
    <div
      data-testid="unpriced-notice"
      className="small mono"
      role="status"
      style={{ padding: "8px 10px", color: "var(--warn)", borderBottom: "1px solid var(--bg-3)" }}
    >
      Net greeks incomplete: {missing.length} held position{missing.length > 1 ? "s" : ""} not priced ({names}).
      New trades are refused until each has a delta.
    </div>
  );
}
