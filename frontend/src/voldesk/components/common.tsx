/**
 * VOLDESK shared UI primitives — ported from the standalone prototype's
 * `js/common.jsx` (global-window pattern) into typed ES modules.
 *
 * These mirror the prototype 1:1 (same classNames → same `voldesk.css`).
 */
import type { ReactNode } from "react";
import { pnlCls, type Status, type Tone } from "./format";

interface PanelProps {
  title?: ReactNode;
  right?: ReactNode;
  children?: ReactNode;
  pad?: boolean;
  className?: string;
  scroll?: boolean;
  /** Stable id for the dev Pipeline tab to isolate this panel (data-pp). */
  dataPp?: string;
}

export function Panel({
  title,
  right,
  children,
  pad = true,
  className = "",
  scroll = false,
  dataPp,
}: PanelProps): JSX.Element {
  return (
    <section className={"panel " + className} data-pp={dataPp}>
      {(title || right) && (
        <header className="panel-head">
          <h3>{title}</h3>
          <div className="panel-head-right">{right}</div>
        </header>
      )}
      <div className={(pad ? "panel-body" : "panel-body nopad") + (scroll ? " scroll" : "")}>
        {children}
      </div>
    </section>
  );
}

export function Tag({ children, tone = "neutral" }: { children: ReactNode; tone?: Tone }): JSX.Element {
  return <span className={"tag tag-" + tone}>{children}</span>;
}

export function Delta({
  v,
  suffix = "%",
  d = 2,
}: {
  v: number | null | undefined;
  suffix?: string;
  d?: number;
}): JSX.Element | null {
  if (v == null) return null;
  return (
    <span className={"delta " + pnlCls(v)}>
      {v >= 0 ? "▲" : "▼"} {Math.abs(v).toFixed(d)}
      {suffix}
    </span>
  );
}

export function StatusDot({ status }: { status: Status }): JSX.Element {
  const map: Record<Status, string> = { up: "var(--pos)", warn: "var(--warn)", down: "var(--neg)" };
  return <span className="status-dot" style={{ background: map[status] || "var(--muted)" }} />;
}

interface BarProps {
  pct: number;
  tone?: string;
  label?: ReactNode;
  value?: ReactNode;
  used?: ReactNode;
  limit?: ReactNode;
}

// horizontal bar (utilization / weights), colored by threshold
export function Bar({ pct, tone, label, value, used, limit }: BarProps): JSX.Element {
  const col =
    tone === "auto"
      ? pct > 80
        ? "var(--neg)"
        : pct > 60
          ? "var(--warn)"
          : "var(--pos)"
      : tone || "var(--accent)";
  return (
    <div className="ubar-row">
      <div className="ubar-head">
        {label && <span className="ubar-label">{label}</span>}
        {(used || limit) && (
          <span className="ubar-ctx mono">
            {used}
            <span className="dim"> / {limit}</span>
          </span>
        )}
        <span className="ubar-value mono" style={{ color: col }}>
          {value != null ? value : pct.toFixed(0) + "%"}
        </span>
      </div>
      <div className="ubar-track">
        <div className="ubar-fill" style={{ width: Math.min(100, pct) + "%", background: col }} />
      </div>
    </div>
  );
}
