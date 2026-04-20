import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./theme.css";

const container = document.getElementById("root");
if (!container) throw new Error("missing #root mount point");

ReactDOM.createRoot(container).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
