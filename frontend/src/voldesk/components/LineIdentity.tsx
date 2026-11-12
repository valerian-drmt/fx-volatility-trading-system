/**
 * VOLDESK — the identity columns of a position line, shared by the three
 * position panels (Open positions, Position breakdown, P&L attribution).
 *
 * The panels are three perspectives on the same portfolio: the same trades,
 * the same lines, the same FIRST columns. Only the columns after these differ
 * (prices and a Close button, greeks, P&L terms). The server builds one
 * identity per line (`api.orchestration.position_lines.identity`) and these
 * components are the only code that draws it, so one line cannot read
 * differently from one panel to the next.
 *
 *   Trade · Contract · Product · Structure · Side · Qty · Tenor
 *
 * One grouping rule for all three: a trade with several open lines is a
 * collapsible header with its legs underneath; a trade with one open line is
 * a plain row that still says what the trade is and how many of its legs are
 * left ("Strangle 10Δ · 1/2 legs").
 */
import type { JSX, ReactNode } from "react";
import { LineStatusTag } from "./LineStatus";
import { CLOSE_PHASE_LABEL, type LineClosing, type LineIdent } from "./lineIdent";

/** "closing · waiting at IB": the position has a close order in flight. */
function ClosingTag({ closing }: { closing?: LineClosing | undefined }): JSX.Element | null {
  if (!closing) return null;
  const price = closing.limit != null ? ` @ ${closing.limit}` : " at market";
  const dup = closing.orders > 1 ? ` · ${closing.orders} orders` : "";
  return (
    <span
      className={"tag " + (closing.orders > 1 ? "tag-warn" : "tag-info")}
      data-closing={closing.phase}
      style={{ marginLeft: 6 }}
      title={
        `close of ${closing.qty}${price}` +
        (closing.phase === "waiting" ? " — IB holds the order, it waits for a counterparty"
          : closing.phase === "partial" ? " — partly filled, the rest waits at IB"
            : closing.phase === "sent" ? " — placed, waiting for IB to confirm it"
              : " — being sent to IB") +
        (closing.orders > 1 ? `. ${closing.orders} close orders are working on this position: cancel the extra one.` : "")
      }
    >
      {CLOSE_PHASE_LABEL[closing.phase]}{dup}
    </span>
  );
}

const fmtQty = (q: number): string => (Number.isInteger(q) ? String(q) : q.toFixed(2));

/** The seven header cells. `cls` is the panel's own cell class. */
export function IdentityHead({ cls = "" }: { cls?: string }): JSX.Element {
  return (
    <>
      <th className={"l " + cls}>Trade</th>
      <th className={"l " + cls}>Contract</th>
      <th className={"l " + cls}>Product</th>
      <th className={"l " + cls}>Structure</th>
      <th className={"l " + cls}>Side</th>
      <th className={"r " + cls}>Qty</th>
      <th className={"r " + cls}>Tenor</th>
    </>
  );
}

/** The seven cells of one line. `main` = a plain row (the trade's only open
 * line, or a line without a trade); false = a leg under a group header. */
export function IdentityCells({
  ident, main, status, cls = "", extra,
}: {
  ident: LineIdent;
  main: boolean;
  status?: string | undefined;
  cls?: string;
  extra?: ReactNode;
}): JSX.Element {
  const partial = main && ident.legsTotal > 1;
  return (
    <>
      <td className={"l mono dim " + cls}>{main ? (ident.tradeId != null ? "#" + ident.tradeId : "—") : ""}</td>
      <td className={"l mono dim " + cls}>{ident.contractId ?? "—"}</td>
      <td className={"l " + cls}>
        <span className="sym">{main ? ident.tradeName : "↳ " + ident.legName}</span>
        {partial && (
          <span className="substruct" title="legs of this trade still open / legs it was opened with">
            {ident.legsOpen}/{ident.legsTotal} legs
          </span>
        )}
        {extra}
      </td>
      <td className={"l " + cls}>
        <span className="sym">{ident.symbol}</span>
        <LineStatusTag status={status} />
        <ClosingTag closing={ident.closing} />
      </td>
      <td className={"l " + cls}>
        <span className={"side-pill " + (ident.side === "BUY" ? "long" : "short")}>{ident.side}</span>
      </td>
      <td className={"r mono " + cls}>{fmtQty(ident.qty)}</td>
      <td className={"r mono dim " + cls}>{ident.tenor}</td>
    </>
  );
}

/** The seven cells of a group header (a trade with several open lines). */
export function GroupIdentityCells({
  idents, isOpen, onToggle, cls = "", extra,
}: {
  idents: LineIdent[];
  isOpen: boolean;
  onToggle: () => void;
  cls?: string;
  extra?: ReactNode;
}): JSX.Element {
  const first = idents[0]!;
  const buys = idents.filter((i) => i.side === "BUY").length;
  const side = buys * 2 > idents.length ? "BUY" : buys * 2 < idents.length ? "SELL" : first.side;
  const tenors = new Set(idents.map((i) => i.tenor));
  const qty = Math.min(...idents.map((i) => i.qty));
  return (
    <>
      <td className={"l mono dim " + cls}>
        <button
          className="pos-caret"
          onClick={(e) => { e.stopPropagation(); onToggle(); }}
          aria-label={isOpen ? "collapse legs" : "expand legs"}
          aria-expanded={isOpen}
        >
          {isOpen ? "▾" : "▸"}
        </button>
        {first.tradeId != null ? "#" + first.tradeId : "—"}
      </td>
      <td className={"l mono dim " + cls}>{idents.length}/{first.legsTotal} legs</td>
      <td className={"l " + cls}>
        <span className="sym">{first.tradeName}</span>
        {extra}
      </td>
      <td className={"l mono dim " + cls}>
        —
        <ClosingTag closing={idents.find((i) => i.closing)?.closing} />
      </td>
      <td className={"l " + cls}>
        <span className={"side-pill " + (side === "BUY" ? "long" : "short")}>{side}</span>
      </td>
      <td className={"r mono " + cls}>{fmtQty(qty)}</td>
      <td className={"r mono dim " + cls}>{tenors.size === 1 ? first.tenor : "—"}</td>
    </>
  );
}
