/**
 * VOLDESK mock data barrel. Views import { DATA, DATA2, fmt } from "../data".
 * When a view is wired to the backend, swap its imports here for the typed
 * OpenAPI client / WS hooks — the views stay agnostic of the source.
 */
export { DATA, fmt, genCandles, mulberry32, smileFor, equityCurve } from "./core";
export { DATA2, scenarioSeries } from "./extended";
export type * from "./core";
export type * from "./extended";
