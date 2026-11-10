import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// Dev proxy targets FastAPI locally (R4). Prod traffic is routed by Nginx (R5 PR #9).
const API_TARGET = process.env.VITE_API_TARGET ?? "http://localhost:8000";

export default defineConfig({
  // Deployed under valeriandarmente.dev/fx-volatility-trading-system/ (CloudFront
  // forwards this prefix to the EC2 origin; the apex "/" is the static CV on S3).
  // main.tsx is BASE_URL-aware so client routing survives the prefix. API/WS calls
  // stay root-absolute (/api, /ws) — the backend is not under the prefix.
  base: "/fx-volatility-trading-system/",
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(import.meta.dirname, "src") },
  },
  server: {
    port: 5173,
    strictPort: true,
    // The app is served under /fx-volatility-trading-system/, so the client
    // calls <base>/api and <base>/ws. Forward those to local FastAPI, stripping
    // the base prefix (FastAPI serves /api/v1 and /ws at the root).
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
    // Maps are built (CI keeps them in the build artifact for debugging) but
    // not referenced from the bundle, not copied into the image and refused
    // by nginx (#349, audit 09#9): the public desk ships no source.
    sourcemap: "hidden",
    target: "es2022",
  },
});
