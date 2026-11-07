/**
 * Desk latency beacon (#370, hot/cold T6). One push in ten, the provider
 * reports how long the row took from the server's publish stamp to the
 * render, and from the WebSocket receive to the render. The api folds them
 * into `desk_publish_to_screen_seconds` for the "Desk fluidity" panel.
 *
 * `fetch` with `keepalive` (not `sendBeacon`) so the JSON content type is
 * honoured by the api and a tab closing mid-flight still delivers.
 */
export type BeaconChannel = "positions" | "account" | "spot_orders" | "orders" | "ticks";

export interface BeaconPayload {
  channel: BeaconChannel;
  publish_to_screen_ms: number | null;
  receive_to_screen_ms: number | null;
}

export const SAMPLE_RATE = 0.1;
const MAX_MS = 60_000;

/** Pure: should this push be reported? `rng` in [0, 1). */
export function shouldSample(rng: number, rate = SAMPLE_RATE): boolean {
  return rng < rate;
}

/** Pure: timings from the message stamp, the receive time and the render time.
 * Values outside [0, 60 s] (clock skew, a stale tab) are dropped, not sent. */
export function beaconPayload(
  channel: BeaconChannel,
  publishedAtMs: number | null,
  receivedAtMs: number | null,
  renderedAtMs: number,
): BeaconPayload | null {
  const clamp = (v: number | null): number | null =>
    v === null || !Number.isFinite(v) || v < 0 || v > MAX_MS ? null : Math.round(v);
  const publish = publishedAtMs === null ? null : clamp(renderedAtMs - publishedAtMs);
  const receive = receivedAtMs === null ? null : clamp(renderedAtMs - receivedAtMs);
  if (publish === null && receive === null) return null;
  return { channel, publish_to_screen_ms: publish, receive_to_screen_ms: receive };
}

const BASE_URL =
  import.meta.env["VITE_API_BASE_URL"] ?? import.meta.env.BASE_URL.replace(/\/$/, "");

/** Fire and forget; never throws, never awaited by the caller. */
export function postBeacon(payload: BeaconPayload): void {
  try {
    void fetch(`${BASE_URL}/api/v1/metrics/beacon`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      keepalive: true,
    }).catch(() => undefined);
  } catch {
    /* a beacon must never break the desk */
  }
}
