# fx-options-frontend

React 18 + TypeScript (strict) + Vite 5 frontend for the FX options desk. Consumes the FastAPI backend delivered in R4 (REST + WebSocket).

## Prerequisites

- **Node 20 LTS** (CI pins this version; local 18+/24 works for dev)
- FastAPI backend running on `http://localhost:8000` (see project root `README.md`)

## Quick start

```bash
cd frontend
npm ci
npm run dev
```

Vite dev server listens on <http://localhost:5173> and proxies `/api` and `/ws` to FastAPI.

## Scripts

| Script | Purpose |
|---|---|
| `npm run dev` | Vite dev server with HMR and backend proxy |
| `npm run build` | Type-check (`tsc -b`) then emit production bundle in `dist/` |
| `npm run preview` | Serve the built bundle on port 4173 |
| `npm run typecheck` | `tsc --noEmit`, strict mode |
| `npm run lint` | ESLint with `--max-warnings 0` |
| `npm run lint:fix` | ESLint auto-fix |
| `npm run format` | Prettier write |

## Roadmap (R5)

This PR ships the **scaffold only**: Vite config, TS strict, ESLint, Prettier, empty App shell. Subsequent R5 PRs layer OpenAPI typegen, Zustand stores, WebSocket hooks, panels, charts and Playwright e2e.
