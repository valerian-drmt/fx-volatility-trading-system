import React from "react";
import ReactDOM from "react-dom/client";
import VoldeskApp from "./voldesk/VoldeskApp";
import { DevLayout } from "./pages/DevLayout";
import { VolEngineConfigPage } from "./pages/VolEngineConfigPage";
import "./theme.css";

const container = document.getElementById("root");
if (!container) throw new Error("missing #root mount point");

// Path-based routing (no react-router yet). Base-aware so it survives the
// deploy subpath (import.meta.env.BASE_URL, e.g. "/fx-volatility-trading-system/").
//   /        → VoldeskApp (user-facing desk)
//   /dev/*   → DevLayout   (validation / diagnostic tabs)
//   /config  → VolEngineConfigPage (engine config editor)
const base = import.meta.env.BASE_URL.replace(/\/$/, "");
const rawPath = typeof window !== "undefined" ? window.location.pathname : "/";
const path = base && rawPath.startsWith(base) ? rawPath.slice(base.length) || "/" : rawPath;

const Root =
  path.startsWith("/config") ? VolEngineConfigPage :
  path.startsWith("/dev")    ? DevLayout :
  VoldeskApp;

ReactDOM.createRoot(container).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>,
);
