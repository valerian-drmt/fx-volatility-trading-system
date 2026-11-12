/**
 * VOLDESK — the status of a position line (#461), shown the same way on the
 * Trade, Risk and Portfolio panels. The three panels list the same lines; a
 * line on which our book and the broker disagree carries this badge on all
 * of them, instead of being a row on one panel only.
 */
import type { JSX } from "react";

export type LineStatus = "matched" | "qty_mismatch" | "book_only" | "broker_only" | "cash";

const BADGE: Record<string, { label: string; cls: string; title: string }> = {
  book_only: {
    label: "book only",
    cls: "tag-danger",
    title: "Our book holds this, the broker does not. Nothing can be priced or closed on it.",
  },
  broker_only: {
    label: "not booked",
    cls: "tag-warn",
    title: "The broker holds this and no trade in our book explains it.",
  },
  qty_mismatch: {
    label: "qty differs",
    cls: "tag-warn",
    title: "Our book and the broker hold different quantities of this.",
  },
  cash: {
    label: "cash",
    cls: "tag-neutral",
    title: "Spot FX balance: cash management, not a booked position.",
  },
};

/** The badge for a line status; nothing for a matched line. */
export function LineStatusTag({ status }: { status?: string | null | undefined }): JSX.Element | null {
  const b = status ? BADGE[status] : undefined;
  if (!b) return null;
  return (
    <span className={"tag " + b.cls} title={b.title} data-line-status={status} style={{ marginLeft: 6 }}>
      {b.label}
    </span>
  );
}
