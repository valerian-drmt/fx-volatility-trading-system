import { ConnectionIndicator } from "../common/ConnectionIndicator";
import { useSelectionStore } from "../../store/selectionStore";

export function StatusBadge(): JSX.Element {
  const symbol = useSelectionStore((s) => s.symbol);
  return (
    <div className="status-badge">
      <span className="status-symbol">{symbol}</span>
      <ConnectionIndicator />
    </div>
  );
}
