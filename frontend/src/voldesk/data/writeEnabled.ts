/**
 * Write-feature gate (R11). All mutations — config commit/revert (Settings),
 * order submit/close (Trade) — are disabled until auth lands in Phase 2.
 * `VITE_WRITE_ENABLED=true` flips it on; default false = read-only desk.
 */
export const WRITE_ENABLED = (import.meta.env["VITE_WRITE_ENABLED"] ?? "false") === "true";
