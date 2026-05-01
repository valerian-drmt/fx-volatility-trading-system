import { useState } from "react";
import { useWebSocket } from "./useWebSocket";

export interface SystemAlert {
  severity: "INFO" | "WARN" | "ERROR";
  message: string;
  ts?: string;
}

const MAX_ALERTS = 50;

/** Subscribe to backend system alerts. Keeps the last 50 in memory (FIFO). */
export function useSystemAlerts(): SystemAlert[] {
  const [alerts, setAlerts] = useState<SystemAlert[]>([]);
  useWebSocket<SystemAlert>("/ws/system_alerts", {
    parse: (raw) => JSON.parse(raw) as SystemAlert,
    onMessage: (a) =>
      setAlerts((prev) => {
        const next = [...prev, a];
        return next.length > MAX_ALERTS ? next.slice(-MAX_ALERTS) : next;
      }),
  });
  return alerts;
}
