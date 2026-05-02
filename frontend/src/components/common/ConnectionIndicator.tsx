import { useConnectionStore, type WsStatus } from "../../store/connectionStore";

const COLOR: Record<WsStatus, string> = {
  idle: "#8a90a0",
  connecting: "#e0b341",
  open: "#3fb950",
  retry: "#e0b341",
  closed: "#f85149",
};

export function ConnectionIndicator(): JSX.Element {
  const status = useConnectionStore((s) => s.status);
  const retry = useConnectionStore((s) => s.retryCount);
  const label = status === "retry" ? `retry · ${retry}` : status;
  return (
    <span className="conn-indicator" data-testid="conn-indicator" data-status={status}>
      <span className="conn-dot" style={{ background: COLOR[status] }} />
      <span className="conn-label">{label}</span>
    </span>
  );
}
