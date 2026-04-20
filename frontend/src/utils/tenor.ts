/** Convert a tenor label (e.g. "1W", "1M", "3M", "1Y") to approximate calendar days. */
const UNIT: Record<string, number> = { D: 1, W: 7, M: 30, Y: 365 };

export function tenorToDays(tenor: string): number | null {
  const m = /^(\d+)([DWMY])$/.exec(tenor.toUpperCase());
  if (!m) return null;
  const [, n, u] = m;
  const unitDays = UNIT[u ?? ""];
  if (!unitDays) return null;
  return Number(n) * unitDays;
}
