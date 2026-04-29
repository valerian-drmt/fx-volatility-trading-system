import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { DevLayout } from "./pages/DevLayout";
import "./theme.css";

const container = document.getElementById("root");
if (!container) throw new Error("missing #root mount point");

// R9 sandbox : path-based routing (no react-router yet, cf. routes.tsx).
// /dev/* → DevLayout (validation tabs), default → App (live dashboard).
const path = typeof window !== "undefined" ? window.location.pathname : "/";
const Root = path.startsWith("/dev") ? DevLayout : App;

ReactDOM.createRoot(container).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>,
);
