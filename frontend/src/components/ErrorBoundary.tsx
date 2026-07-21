/**
 * Render-error containment.
 *
 * Without a boundary a single throw unmounts the whole React tree, leaving an
 * empty `#root` over the near-black page background (`--bg`) — a black screen
 * with nothing to diagnose from. Wrap anything that fetches, and a failure
 * degrades to an inline message instead of taking the app down.
 *
 * A 401 is called out explicitly: `/dev` and every write endpoint sit behind
 * `require_write`, so "logged out" is the single most common cause of a crash
 * here and deserves an actionable message rather than a stack trace.
 */
import { Component, type ErrorInfo, type ReactNode } from "react";
import { ApiError } from "../api/client";

interface Props {
  children: ReactNode;
  /** Shown in the fallback so a per-tab boundary says which tab died. */
  label?: string;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  override state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    // Keep the component stack in the console — the fallback deliberately
    // shows only the message, but a dev debugging this wants the trace.
    console.error(`[ErrorBoundary${this.props.label ? ` ${this.props.label}` : ""}]`, error, info.componentStack);
  }

  private readonly retry = (): void => this.setState({ error: null });

  override render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;

    const unauthorized = error instanceof ApiError && error.status === 401;

    return (
      <div className="mono small" style={boxStyle}>
        <div style={{ color: "var(--danger, #e5534b)", marginBottom: 8 }}>
          {unauthorized ? "401 — authentication required" : "Something broke while rendering"}
          {this.props.label ? ` · ${this.props.label}` : ""}
        </div>

        {unauthorized ? (
          <p className="dim" style={{ margin: "0 0 10px" }}>
            The dev console and every write endpoint sit behind the auth cookie. Log in on the
            desk, then come back — the cookie is shared across both roots.
          </p>
        ) : (
          <pre style={preStyle}>{error.message}</pre>
        )}

        <button type="button" onClick={this.retry} style={btnStyle}>
          Retry
        </button>
      </div>
    );
  }
}

const boxStyle = {
  padding: 16,
  margin: 12,
  border: "1px solid var(--border, #2a2f3a)",
  borderRadius: 6,
  background: "var(--surface, #161a22)",
} as const;

const preStyle = {
  margin: "0 0 10px",
  padding: 8,
  overflow: "auto",
  maxHeight: 180,
  background: "var(--bg, #0f1115)",
  border: "1px solid var(--border, #2a2f3a)",
  borderRadius: 4,
  whiteSpace: "pre-wrap",
} as const;

const btnStyle = {
  background: "var(--accent, #3b82f6)",
  color: "#fff",
  border: 0,
  borderRadius: 3,
  padding: "4px 12px",
  cursor: "pointer",
} as const;