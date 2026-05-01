// Minimal MSW 2 WebSocket helper. The hooks PR (R5 #4) extends this with per-channel
// mocks (ticks/vol/risk) — here we only expose the raw link factory so tests can
// open a link, push frames, and close it without touching a real backend.
import { ws } from "msw";

export const tickStream = ws.link("ws://localhost:5173/ws/ticks");
export const volStream = ws.link("ws://localhost:5173/ws/vol");
export const riskStream = ws.link("ws://localhost:5173/ws/risk");
