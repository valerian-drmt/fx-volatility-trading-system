/**
 * Base WebSocket hook (R11 PR F) — auto-reconnect with exponential backoff,
 * status + receive-time tracking. Used by the voldesk live streams
 * (ticks / vol / risk / positions / account / spot_orders / orders).
 *
 * Base URL: same-origin under the deploy base in prod (Nginx) and dev (Vite
 * proxy), i.e. wss://host<base>/ws/... where <base> = import.meta.env.BASE_URL
 * (e.g. "/fx-volatility-trading-system/"). Override via VITE_WS_BASE_URL.
 * `path` = null disables the connection (mock mode).
 *
 * `useMultiWebSocket` (#367) keeps one socket per path for the per-structure
 * `orders:<id>` channel; the set of paths can change between renders and only
 * the added / removed sockets are opened / closed.
 */
import { useEffect, useRef, useState } from "react";

function wsBase(): string {
  const override = import.meta.env["VITE_WS_BASE_URL"];
  if (typeof override === "string" && override) return override;
  if (typeof location === "undefined") return "";
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const prefix = import.meta.env.BASE_URL.replace(/\/$/, "");
  return `${proto}//${location.host}${prefix}`;
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

interface ManagedSocket {
  close: () => void;
}

/** One reconnecting socket. `onMessage` gets the raw text; `onStatus` the
 * lifecycle. Returns a handle whose `close()` stops the reconnect loop. */
export function openManagedSocket(
  path: string,
  onMessage: (raw: string) => void,
  onStatus: (status: WsStatus) => void,
): ManagedSocket {
  let stopped = false;
  let socket: WebSocket | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let attempt = 0;
  const connect = (): void => {
    if (stopped) return;
    onStatus("connecting");
    try {
      socket = new WebSocket(`${wsBase()}${path}`);
    } catch {
      // No WebSocket constructor (SSR / a bare test runtime): stay closed.
      onStatus("closed");
      return;
    }
    socket.onopen = () => {
      attempt = 0;
      onStatus("open");
    };
    socket.onmessage = (ev: MessageEvent) => {
      onMessage(typeof ev.data === "string" ? ev.data : "");
    };
    socket.onclose = () => {
      if (stopped) return;
      onStatus("closed");
      const backoff = Math.min(1000 * 2 ** attempt, MAX_BACKOFF_MS);
      attempt += 1;
      timer = setTimeout(connect, backoff);
    };
    socket.onerror = () => socket?.close();
  };
  connect();
  return {
    close: () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      socket?.close();
    },
  };
}

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
    const handle = openManagedSocket(
      path,
      (raw) => {
        const parsed = parseRef.current(raw);
        if (parsed !== null) emit({ last: parsed, status: "open", asOf: Date.now() });
      },
      (status) => setState((s) => ({ ...s, status })),
    );
    return () => {
      if (flush) clearInterval(flush);
      handle.close();
    };
  }, [path, throttleMs]);

  return state;
}

export interface MultiWsState<T> {
  /** last parsed message from any of the sockets, with the path it came on */
  last: { path: string; msg: T } | null;
  /** "open" when every socket is open, "closed" when none is, else "connecting" */
  status: WsStatus;
  asOf: number | null;
  /** number of sockets currently open */
  nOpen: number;
}

/** One reconnecting socket per path (#367). Pass a stable, sorted list; a
 * path that leaves the list has its socket closed, a new one is opened. */
export function useMultiWebSocket<T>(
  paths: readonly string[],
  parse: (raw: string) => T | null = defaultParse,
): MultiWsState<T> {
  const key = paths.join("|");
  const [state, setState] = useState<MultiWsState<T>>({ last: null, status: "closed", asOf: null, nOpen: 0 });
  const parseRef = useRef(parse);
  parseRef.current = parse;
  const socketsRef = useRef<Map<string, ManagedSocket>>(new Map());
  const statusRef = useRef<Map<string, WsStatus>>(new Map());

  useEffect(() => {
    const wanted = new Set(key ? key.split("|") : []);
    const sockets = socketsRef.current;
    const statuses = statusRef.current;
    const aggregate = (): void => {
      const all = [...statuses.values()];
      const nOpen = all.filter((s) => s === "open").length;
      const status: WsStatus = all.length === 0 || nOpen === 0
        ? "closed"
        : nOpen === all.length ? "open" : "connecting";
      setState((s) => (s.status === status && s.nOpen === nOpen ? s : { ...s, status, nOpen }));
    };
    for (const [p, h] of sockets) {
      if (!wanted.has(p)) {
        h.close();
        sockets.delete(p);
        statuses.delete(p);
      }
    }
    for (const p of wanted) {
      if (sockets.has(p)) continue;
      statuses.set(p, "connecting");
      sockets.set(p, openManagedSocket(
        p,
        (raw) => {
          const msg = parseRef.current(raw);
          if (msg !== null) setState((s) => ({ ...s, last: { path: p, msg }, asOf: Date.now() }));
        },
        (st) => {
          statuses.set(p, st);
          aggregate();
        },
      ));
    }
    aggregate();
  }, [key]);

  // Close everything on unmount only (the path-diff effect above handles updates).
  useEffect(() => {
    const sockets = socketsRef.current;
    return () => {
      for (const h of sockets.values()) h.close();
      sockets.clear();
    };
  }, []);

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
