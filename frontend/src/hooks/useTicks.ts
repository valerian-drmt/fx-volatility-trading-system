import { useRef, useState } from "react";
import { useWebSocket } from "./useWebSocket";

export interface Tick {
  symbol: string;
  bid: number;
  ask: number;
  mid: number;
  ts?: string;
}

const WS_URL = `${import.meta.env["VITE_WS_BASE_URL"] ?? ""}/ws/ticks`;

/** Subscribe to the /ws/ticks stream. Returns the latest tick and the count seen. */
export function useTicks(): { last: Tick | null; count: number } {
  const [last, setLast] = useState<Tick | null>(null);
  const countRef = useRef(0);
  const [count, setCount] = useState(0);

  useWebSocket<Tick>(WS_URL, {
    parse: (raw) => JSON.parse(raw) as Tick,
    onMessage: (tick) => {
      countRef.current += 1;
      setCount(countRef.current);
      setLast(tick);
    },
  });

  return { last, count };
}
