import { useEffect, useState } from "react";
import { ConnectionIndicator } from "../common/ConnectionIndicator";

function isFxMarketOpen(now: Date): boolean {
  // FX cash market: Sun 22:00 UTC → Fri 22:00 UTC.
  const dow = now.getUTCDay();           // 0 = Sun, 5 = Fri, 6 = Sat
  const h = now.getUTCHours();
  if (dow === 6) return false;
  if (dow === 0) return h >= 22;
  if (dow === 5) return h < 22;
  return true;
}

export function StatusBadge(): JSX.Element {
  const [open, setOpen] = useState(() => isFxMarketOpen(new Date()));
  useEffect(() => {
    const id = window.setInterval(() => setOpen(isFxMarketOpen(new Date())), 60_000);
    return () => window.clearInterval(id);
  }, []);

  const color = open ? "#3fb950" : "#f85149";
  return (
    <div className="status-badge">
      <span
        className="status-symbol"
        style={{ color, fontWeight: 600 }}
        data-testid="market-status"
        data-open={open}
      >
        {open ? "Market Open" : "Market Closed"}
      </span>
      <ConnectionIndicator />
    </div>
  );
}
