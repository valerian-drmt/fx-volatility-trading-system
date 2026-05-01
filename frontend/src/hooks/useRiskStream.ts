import { useState } from "react";
import { useWebSocket } from "./useWebSocket";

export interface RiskUpdate {
  delta: number;
  gamma: number;
  vega: number;
  theta: number;
  ts?: string;
}

/** Subscribe to the /ws/risk stream. Returns the latest greeks snapshot. */
export function useRiskStream(): RiskUpdate | null {
  const [latest, setLatest] = useState<RiskUpdate | null>(null);
  useWebSocket<RiskUpdate>("/ws/risk", {
    parse: (raw) => JSON.parse(raw) as RiskUpdate,
    onMessage: setLatest,
  });
  return latest;
}
