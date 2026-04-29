/**
 * Self-contained WebSocket subscription with rolling buffer + pause/clear,
 * pour les dev tabs (R9 sandbox). Différent de useWebSocket :
 *  - état local (pas de connectionStore global) → on peut mount N instances
 *    sans qu'elles polluent le statut global du dashboard
 *  - garde les N derniers messages dans un buffer FIFO
 *  - pause / resume / clear exposés
 */
import { useCallback, useEffect, useRef, useState } from "react";

export type WsStatus = "connecting" | "open" | "retry" | "closed";

export interface LoggedMessage {
  ts: string;          // ISO string client-side (Date.now())
  raw: string;         // payload brut tel que reçu
}

export interface UseWsLogResult {
  status: WsStatus;
  count: number;       // total messages reçus depuis le mount
  messages: LoggedMessage[];
  paused: boolean;
  pause: () => void;
  resume: () => void;
  clear: () => void;
}

const RECONNECT_DELAY_MS = 2_000;

export function useWsLog(url: string, max = 50): UseWsLogResult {
  const [status, setStatus] = useState<WsStatus>("connecting");
  const [count, setCount] = useState(0);
  const [messages, setMessages] = useState<LoggedMessage[]>([]);
  const [paused, setPaused] = useState(false);
  const pausedRef = useRef(paused);
  pausedRef.current = paused;

  useEffect(() => {
    let ws: WebSocket | null = null;
    let reconnect: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;

    const open = () => {
      if (cancelled) return;
      setStatus("connecting");
      ws = new WebSocket(url);
      ws.onopen = () => setStatus("open");
      ws.onmessage = (evt) => {
        if (pausedRef.current) return;
        const raw = typeof evt.data === "string" ? evt.data : String(evt.data);
        const ts = new Date().toISOString();
        setCount((c) => c + 1);
        setMessages((prev) => {
          const next = [{ ts, raw }, ...prev];
          return next.length > max ? next.slice(0, max) : next;
        });
      };
      ws.onerror = () => setStatus("retry");
      ws.onclose = () => {
        if (cancelled) return;
        setStatus("retry");
        reconnect = setTimeout(open, RECONNECT_DELAY_MS);
      };
    };

    open();
    return () => {
      cancelled = true;
      if (reconnect) clearTimeout(reconnect);
      if (ws && ws.readyState <= WebSocket.OPEN) ws.close();
      setStatus("closed");
    };
  }, [url, max]);

  const pause = useCallback(() => setPaused(true), []);
  const resume = useCallback(() => setPaused(false), []);
  const clear = useCallback(() => setMessages([]), []);

  return { status, count, messages, paused, pause, resume, clear };
}
