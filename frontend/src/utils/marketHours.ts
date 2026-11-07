/**
 * FX cash market hours + the feed-state label the desk shows next to the
 * ticker. Mirrors `src/shared/market_hours.py` (Sun 22:00 UTC -> Fri 22:00 UTC).
 *
 * Before this the topbar read "no feed" every weekend: the ticks stream is
 * empty because the market is closed, not because anything is down. The
 * label now says which it is; "no feed" is reserved for a quiet stream while
 * the market is open, which IS a problem.
 */
import type { FreshStatus } from "../voldesk/data/freshness";

export function isFxMarketOpen(now: Date = new Date()): boolean {
  const dow = now.getUTCDay(); // 0 = Sun, 5 = Fri, 6 = Sat
  const h = now.getUTCHours();
  if (dow === 6) return false;
  if (dow === 0) return h >= 22;
  if (dow === 5) return h < 22;
  return true;
}

export type FeedTone = "open" | "closed" | "warn" | "down";

export interface FeedState {
  label: string;
  tone: FeedTone;
}

/** Ticks freshness + market calendar -> what the badge says. */
export function feedState(status: FreshStatus, marketOpen: boolean): FeedState {
  if (status === "live") return { label: "Market open", tone: "open" };
  if (!marketOpen) return { label: "Market closed", tone: "closed" };
  if (status === "stale") return { label: "feed stale", tone: "warn" };
  return { label: "no feed", tone: "down" };
}

/** Desk-wide banner while the market is closed (#347): says the grids on
 * screen are the last complete ones and from when, instead of letting a
 * Friday surface pass for a live one. `null` while the market is open. */
export function closedBanner(
  marketOpen: boolean,
  gridAsOf: number | null | undefined,
  source: string | null | undefined,
): string | null {
  if (marketOpen) return null;
  if (gridAsOf == null) return "Markets closed · no complete grid yet";
  const when = new Date(gridAsOf).toLocaleString("en-GB", {
    day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "UTC",
  });
  const what = source === "last-complete" ? "last complete grid" : "grid";
  return `Markets closed · ${what} from ${when} UTC`;
}

export const FEED_TONE_COLOR: Record<FeedTone, string> = {
  open: "var(--pos)",
  closed: "var(--neg)", // red like the dev status badge: closed is a state to notice
  warn: "var(--warn)",
  down: "var(--neg)",
};
