/**
 * VOLDESK — System (container stack, engine heartbeats, schema, backtest).
 * Faithful 1:1 port of the prototype's `js/views_misc.jsx` SystemView.
 * Mock data for now; wires to the backend in a later lot.
 */
import { MetricTile, Panel, StatusDot } from "../components/common";
import { FreshBadge } from "../components/FreshBadge";
import { DATA2 } from "../data";
import { useDeskData } from "../data/deskData";

const layerColor: Record<string, string> = {
  EDGE: "#a78bfa",
  APP: "var(--accent)",
  ENGINES: "var(--pos)",
  DATA: "var(--warn)",
  OBS: "var(--muted)",
};

const erGroups: [string, string[]][] = [
  ["market", ["ticks", "ohlc_bars", "vol_surfaces"]],
  ["signals", ["pca_state", "regime", "events"]],
  ["trading", ["positions", "packages", "orders"]],
  ["portfolio", ["account", "equity_curve", "pnl_attr"]],
];

export function SystemView(): JSX.Element {
  const { system } = useDeskData();
  const stack = system.data?.stack ?? [];
  const engines = system.data?.engines ?? [];
  return (
    <div className="system-grid">
      <Panel title="Container stack" right={<FreshBadge fresh={system} label="containers · Docker · AWS" />} className="stack-panel">
        <div className="stack">
          {stack.map((l) => (
            <div key={l.layer} className="stack-layer">
              <span className="stack-layer-tag" style={{ color: layerColor[l.layer] ?? "var(--muted)" }}>
                {l.layer}
              </span>
              <div className="stack-items">
                {l.items.map((it) => (
                  <div key={it.name} className={"stack-box " + it.status}>
                    <div className="stack-box-head">
                      <StatusDot status={it.status} />
                      <b>{it.name}</b>
                    </div>
                    <span className="dim small">{it.meta}</span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </Panel>
      <Panel title="Engine heartbeats" right={<FreshBadge fresh={system} />}>
        {engines.length === 0 && (
          <div className="dim small mono ivz-empty">heartbeats indisponibles (/dev gated ou engines arrêtés)</div>
        )}
        {engines.map((e) => (
          <div key={e.name} className="eng-row">
            <StatusDot status={e.status} />
            <span className="eng-name mono">{e.name}</span>
            <div className="eng-track">
              <div
                className="eng-fill"
                style={{
                  width: Math.min(100, (e.hb / e.stale) * 100) + "%",
                  background: e.hb / e.stale > 0.7 ? "var(--warn)" : "var(--pos)",
                }}
              />
            </div>
            <span className="dim mono">
              {e.hb}s / {e.stale}s
            </span>
          </div>
        ))}
      </Panel>
      <Panel title="Database schema" right={<span className="dim mono">drag to pan (mock)</span>}>
        <div className="er-diagram">
          {erGroups.map(([grp, tbls]) => (
            <div key={grp} className="er-group">
              <div className="er-group-head">{grp}</div>
              {tbls.map((t) => (
                <div key={t} className="er-table mono">
                  {t}
                </div>
              ))}
            </div>
          ))}
        </div>
      </Panel>
      <Panel title="Backtest" right={<span className="dim mono small">mock · pas de moteur live</span>}>
        <div className="bt-tiles">
          <MetricTile label="Sharpe" value={DATA2.backtest.sharpe.toFixed(2)} tone="pos" />
          <MetricTile label="Max DD" value={DATA2.backtest.maxDd + "%"} tone="neg" />
          <MetricTile label="Total return" value={"+" + DATA2.backtest.totalReturn + "%"} tone="pos" />
          <MetricTile label="Trades" value={DATA2.backtest.trades} sub={DATA2.backtest.winRate + "% win"} />
        </div>
      </Panel>
    </div>
  );
}
