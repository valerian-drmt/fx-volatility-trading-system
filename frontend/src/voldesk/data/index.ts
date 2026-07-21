/**
 * VOLDESK data barrel. `DATA` now carries only preview/axis constants
 * (OrderBuilder smile preview, wing bucketing, heatmap axes) — all book data
 * comes from the live provider (`useDeskData()`); empty states are in
 * `neutral.ts`.
 */
export { DATA, fmt, smileFor } from "./core";
export { EMPTY_ACCOUNT, EMPTY_GREEKS } from "./neutral";
export type * from "./core";
export type * from "./extended";
