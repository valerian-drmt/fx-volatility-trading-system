/**
 * `true` while the FX cash market is open (Sun 22:00 UTC -> Fri 22:00 UTC),
 * re-evaluated every minute so the badge flips on its own at the open / close.
 */
import { useEffect, useState } from "react";
import { isFxMarketOpen } from "../utils/marketHours";

export function useMarketOpen(): boolean {
  const [open, setOpen] = useState(() => isFxMarketOpen(new Date()));
  useEffect(() => {
    const id = window.setInterval(() => setOpen(isFxMarketOpen(new Date())), 60_000);
    return () => window.clearInterval(id);
  }, []);
  return open;
}
