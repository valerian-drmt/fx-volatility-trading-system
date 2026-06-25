import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// Dev proxy targets FastAPI locally (R4). Prod traffic is routed by Nginx (R5 PR #9).
const API_TARGET = process.env.VITE_API_TARGET ?? "http://localhost:8000";

export default defineConfig({
  // Deployed under valeriandarmente.dev/fx-volatility-trading-system/ (the apex
  // "/" serves the static CV from S3 via CloudFront). All asset URLs and the
  // API/WS calls are emitted under this prefix.
  base: "/fx-volatility-trading-system/",
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  server: {
    port: 5173,
    strictPort: true,
    // The app is served under the base, so the client calls <base>/api and
    // <base>/ws. Forward those to local FastAPI, stripping the base prefix
    // (FastAPI serves /api/v1 and /ws at the root).
    proxy: {
      "/fx-volatility-trading-system/api": {
        target: API_TARGET,
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/fx-volatility-trading-system/, ""),
      },
      "/fx-volatility-trading-system/ws": {
        target: API_TARGET,
        ws: true,
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/fx-volatility-trading-system/, ""),
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
    target: "es2022",
  },
});
