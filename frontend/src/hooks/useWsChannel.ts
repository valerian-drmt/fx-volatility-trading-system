/**
 * Base WebSocket hook (R11 PR F) — auto-reconnect with exponential backoff,
 * status + receive-time tracking. Used by the voldesk live streams
 * (ticks / vol / risk / positions / orders / exit_alerts).
 *
 * Base URL: same-origin `/ws` in prod (Nginx) and dev (Vite proxy); override
 * via VITE_WS_BASE_URL. `path` = null disables the connection (mock mode).
 */
import { useEffect, useRef, useState } from "react";

function wsBase(): string {
  const override = import.meta.env["VITE_WS_BASE_URL"];
  if (typeof override === "string" && override) return override;
  if (typeof location === "undefined") return "";
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}`;
}

export type WsStatus = "connecting" | "open" | "closed";

export interface WsState<T> {
  /** last successfully-parsed message, or null before the first */
  last: T | null;
  status: WsStatus;
  /** epoch ms of the last message received */
  asOf: number | null;
}

const MAX_BACKOFF_MS = 15_000;

export function useWebSocket<T>(
  path: string | null,
  parse: (raw: string) => T | null = defaultParse,
  /** >0 : coalesce messages, emit only the latest once per `throttleMs`
   * (e.g. a 5/s tick feed displayed on a steady 1s beat). 0 = emit each message. */
  throttleMs = 0,
): WsState<T> {
  const [state, setState] = useState<WsState<T>>({
    last: null,
    status: path ? "connecting" : "closed",
    asOf: null,
  });
  const parseRef = useRef(parse);
  parseRef.current = parse;

  useEffect(() => {
    if (!path) {
      setState({ last: null, status: "closed", asOf: null });
      return;
    }
    let stopped = false;
    let socket: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let attempt = 0;
    // Throttle buffer : keep only the latest message, flush on an interval so
    // setState (and re-render) fires at most once per `throttleMs`.
    let pending: WsState<T> | null = null;
    let flush: ReturnType<typeof setInterval> | undefined;
    const emit = (s: WsState<T>): void => {
      if (throttleMs > 0) pending = s;
      else setState(s);
    };
    if (throttleMs > 0) {
      flush = setInterval(() => {
        if (pending) {
          setState(pending);
          pending = null;
        }
      }, throttleMs);
    }

    const connect = (): void => {
      if (stopped) return;
      setState((s) => ({ ...s, status: "connecting" }));
      socket = new WebSocket(`${wsBase()}${path}`);
      socket.onopen = () => {
        attempt = 0;
        setState((s) => ({ ...s, status: "open" }));
      };
      socket.onmessage = (ev: MessageEvent) => {
        const raw = typeof ev.data === "string" ? ev.data : "";
        const parsed = parseRef.current(raw);
        if (parsed !== null) {
          emit({ last: parsed, status: "open", asOf: Date.now() });
        }
      };
      socket.onclose = () => {
        if (stopped) return;
        setState((s) => ({ ...s, status: "closed" }));
        const backoff = Math.min(1000 * 2 ** attempt, MAX_BACKOFF_MS);
        attempt += 1;
        timer = setTimeout(connect, backoff);
      };
      socket.onerror = () => socket?.close();
    };

    connect();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      if (flush) clearInterval(flush);
      socket?.close();
    };
  }, [path, throttleMs]);

  return state;
}

function defaultParse<T>(raw: string): T | null {
  if (!raw) return null;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}
