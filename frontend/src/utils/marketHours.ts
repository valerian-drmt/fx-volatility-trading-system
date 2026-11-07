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

export const FEED_TONE_COLOR: Record<FeedTone, string> = {
  open: "var(--pos)",
  closed: "var(--text-dim)",
  warn: "var(--warn)",
  down: "var(--neg)",
};
