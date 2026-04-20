export interface MetricTileProps {
  label: string;
  value: string | number;
  hint?: string;
}

export function MetricTile({ label, value, hint }: MetricTileProps): JSX.Element {
  return (
    <div className="metric-tile" data-testid={`metric-${label}`}>
      <span className="metric-label">{label}</span>
      <span className="metric-value">{value}</span>
      {hint ? <span className="metric-hint">{hint}</span> : null}
    </div>
  );
}
