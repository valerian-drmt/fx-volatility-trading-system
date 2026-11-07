/**
 * WebSocket connection chip (hot/cold T3, #367). Sits next to a panel title
 * and says whether that panel is being pushed to or living on its 60 s
 * reconciliation poll:
 *   open        -> "push live"            (green)
 *   connecting  -> "push reconnecting"    (amber)
 *   closed      -> "push down · 60 s poll" (red)
 * The poll keeps the panel alive either way; the chip only changes colour.
 */
import type { WsStatus } from "../../hooks/useWebSocket";

export function WsChip({ status, label = "push" }: { status: WsStatus; label?: string }): JSX.Element {
  const cls = status === "open" ? "pos" : status === "connecting" ? "warn" : "neg";
  const word = status === "open" ? "live" : status === "connecting" ? "reconnecting" : "down · 60 s poll";
  const title = status === "open"
    ? "WebSocket connected: changes arrive as they happen"
    : status === "connecting"
      ? "WebSocket reconnecting; the 60 s poll keeps this panel current"
      : "WebSocket down; the 60 s poll keeps this panel current";
  return (
    <span className="dim mono small fresh-badge ws-chip" title={title} data-ws={status}>
      {label} · <span className={cls}><i className={"fresh-dot " + cls} aria-hidden="true" /> {word}</span>
    </span>
  );
}
