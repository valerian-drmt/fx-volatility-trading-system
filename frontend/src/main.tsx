import React, { Suspense, lazy } from "react";
import ReactDOM from "react-dom/client";
import VoldeskApp from "./voldesk/VoldeskApp";
import { DataProvider } from "./voldesk/data/provider";
import { ErrorBoundary } from "./components/ErrorBoundary";
import "./theme.css";

// Dev console is operator-only — lazy so its ~7 pages (tables, logs, redis,
// hardware…) never ship in the public desk bundle.
const DevLayout = lazy(() =>
  import("./pages/DevLayout").then((m) => ({ default: m.DevLayout })),
);

const container = document.getElementById("root");
if (!container) throw new Error("missing #root mount point");

// Path-based routing (no react-router yet). Base-aware so it survives the
// deploy subpath (import.meta.env.BASE_URL, e.g. "/fx-volatility-trading-system/").
//   /        → VoldeskApp (user-facing desk)
//   /dev/*   → DevLayout   (validation / diagnostic tabs)
const base = import.meta.env.BASE_URL.replace(/\/$/, "");
const rawPath = typeof window !== "undefined" ? window.location.pathname : "/";
const path = base && rawPath.startsWith(base) ? rawPath.slice(base.length) || "/" : rawPath;

// Only the desk consumes live desk-data; /dev stays provider-free so it
// doesn't open feeds it never reads.
// Both roots are wrapped: a render throw must degrade to a visible message, not
// unmount everything and leave an empty #root over the near-black --bg.
const tree =
  path.startsWith("/dev") ? (
    <ErrorBoundary label="dev console">
      <Suspense fallback={<div className="mono small dim" style={{ padding: 16 }}>loading dev console…</div>}>
        <DevLayout />
      </Suspense>
    </ErrorBoundary>
  ) : (
    <ErrorBoundary label="desk">
      <DataProvider>
        <VoldeskApp />
      </DataProvider>
    </ErrorBoundary>
  );

ReactDOM.createRoot(container).render(
  <React.StrictMode>{tree}</React.StrictMode>,
);
