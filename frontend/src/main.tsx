import React from "react";
import ReactDOM from "react-dom/client";

import VoldeskApp from "./voldesk/VoldeskApp";
import { DataProvider } from "./voldesk/data/provider";
import "./theme.css";

const container = document.getElementById("root");
if (!container) throw new Error("missing #root mount point");

// R11 pivot: the app now boots the user-facing voldesk, wrapped in the live
// DataProvider (REST + WS). The legacy dashboard is retired in the drop-legacy
// PR (A5); the /dev console + /config editor land with their own PRs (they're
// not on main yet — they live on sandbox).
ReactDOM.createRoot(container).render(
  <React.StrictMode>
    <DataProvider>
      <VoldeskApp />
    </DataProvider>
  </React.StrictMode>,
);
