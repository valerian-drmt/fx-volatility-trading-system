/**
 * One line at the top of the data views while the FX market is closed
 * (#347): "Markets closed · last complete grid from <stamp> UTC". The
 * topbar badge says closed; this says what the grids on screen are.
 */
import { useMarketOpen } from "../../hooks/useMarketOpen";
import { closedBanner } from "../../utils/marketHours";
import { useDeskData } from "../data/deskData";

export function ClosedBanner(): JSX.Element | null {
  const marketOpen = useMarketOpen();
  const { surface } = useDeskData();
  const text = closedBanner(marketOpen, surface.data?.asOf ?? surface.asOf, surface.data?.source ?? null);
  if (text === null) return null;
  return (
    <div className="closed-banner mono small" data-pp="closed-banner" role="status">
      {text}
    </div>
  );
}
