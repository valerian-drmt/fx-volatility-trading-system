import { useEffect, useRef } from "react";
import { useConnectionStore } from "../store/connectionStore";

export const MIN_BACKOFF_MS = 1_000;
export const MAX_BACKOFF_MS = 60_000;

/** Exponential backoff : 1s, 2s, 4s, 8s, 16s, 32s, 60s (cap). Exposed for testing. */
export function computeBackoff(attempt: number): number {
  const delay = MIN_BACKOFF_MS * 2 ** Math.max(0, attempt);
  return Math.min(delay, MAX_BACKOFF_MS);
}

export interface UseWebSocketOptions<T> {
  /** Parser for incoming frames. Throws → frame dropped, no state change. */
  parse?: (raw: string) => T;
  /** Fired for every successfully parsed frame. */
  onMessage: (payload: T) => void;
}

/**
 * Subscribe to a backend WebSocket channel with automatic exponential reconnect.
 * The caller only sees parsed frames via `onMessage` — connection state is
 * reflected globally in `useConnectionStore`.
 */
export function useWebSocket<T>(url: string, opts: UseWebSocketOptions<T>): void {
  const { parse, onMessage } = opts;
  // Stable refs so the effect below never needs to re-run because a parent
  // re-rendered — that would churn the socket uselessly.
  const onMessageRef = useRef(onMessage);
  const parseRef = useRef(parse);
  onMessageRef.current = onMessage;
  parseRef.current = parse;

  useEffect(() => {
    const conn = useConnectionStore.getState();
    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;
    let cancelled = false;

    const open = (): void => {
      if (cancelled) return;
      conn.setStatus("connecting");
      ws = new WebSocket(url);
      ws.onopen = () => {
        attempt = 0;
        useConnectionStore.getState().setStatus("open");
      };
      ws.onmessage = (evt) => {
        try {
          const raw = typeof evt.data === "string" ? evt.data : String(evt.data);
          const payload = parseRef.current ? parseRef.current(raw) : (raw as unknown as T);
          onMessageRef.current(payload);
        } catch {
          /* dropped malformed frame */
        }
      };
      ws.onerror = () => {
        useConnectionStore.getState().noteRetry("ws error");
      };
      ws.onclose = () => {
        if (cancelled) return;
        const delay = computeBackoff(attempt);
        attempt += 1;
        useConnectionStore.getState().noteRetry(`closed, retry in ${delay}ms`);
        reconnectTimer = setTimeout(open, delay);
      };
    };

    open();

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (ws && ws.readyState <= WebSocket.OPEN) ws.close();
      useConnectionStore.getState().setStatus("closed");
    };
  }, [url]);
}
