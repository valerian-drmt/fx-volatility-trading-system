/**
 * Engine Health — 4 cards engines + 1 card IB Gateway, alimentées par
 * GET /api/v1/dev/engines. Refresh manuel + auto-refresh 5s (toggle).
 */
import { useEffect, useRef, useState } from "react";

interface EngineInfo {
  name: string;
  status: "OK" | "STALE" | "DOWN";
  hb_age_s: number | null;
  hb_ttl_s: number | null;
  stale_threshold_s: number;
  out_key: string | null;
  out_age_s: number | null;
}

interface IbInfo {
  status: "OK" | "DOWN";
  host: string;
  port: number;
  error?: string;
}

interface Resp {
  engines: EngineInfo[];
  ib_gateway: IbInfo;
  timestamp: string;
}

const POLL_MS = 5_000;

export function EngineHealth(): JSX.Element {
  const [data, setData] = useState<Resp | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const timerRef = useRef<number | null>(null);

  const fetchData = async () => {
    setError(null);
    try {
      const r = await fetch("/api/v1/dev/engines");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setData(await r.json());
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    fetchData();
    if (!autoRefresh) return;
    timerRef.current = window.setInterval(fetchData, POLL_MS);
    return () => {
      if (timerRef.current) window.clearInterval(timerRef.current);
    };
  }, [autoRefresh]);

  return (
    <div style={{ padding: 16 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 16 }}>
        <button onClick={fetchData} style={btnStyle}>Refresh</button>
        <label style={{ color: "#aaa", fontSize: 13 }}>
          <input
            type="checkbox"
            checked={autoRefresh}
            onChange={(e) => setAutoRefresh(e.target.checked)}
            style={{ marginRight: 6 }}
          />
          Auto-refresh ({POLL_MS / 1000}s)
        </label>
        {data && (
          <span style={{ color: "#666", fontSize: 12, marginLeft: "auto" }}>
            last fetch: {new Date(data.timestamp).toLocaleTimeString()}
          </span>
        )}
      </div>

      {error && <div style={{ color: "#e66", marginBottom: 16 }}>{error}</div>}

      {data && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 12 }}>
          {data.engines.map((e) => <EngineCard key={e.name} engine={e} />)}
          <IbCard ib={data.ib_gateway} />
        </div>
      )}
    </div>
  );
}

function EngineCard({ engine }: { engine: EngineInfo }): JSX.Element {
  const color =
    engine.status === "OK" ? "#6c6" :
    engine.status === "STALE" ? "#cc6" : "#e66";

  return (
    <section className="panel" style={{ borderTop: `3px solid ${color}` }}>
      <header className="panel-header">
        <h2>{engine.name}</h2>
        <span style={{ color, fontWeight: 600, fontSize: 12 }}>● {engine.status}</span>
      </header>
      <div className="panel-body" style={{ padding: 12, fontSize: 13 }}>
        <Row label="HB age" value={fmtAge(engine.hb_age_s)} />
        <Row label="HB TTL" value={engine.hb_ttl_s !== null ? `${engine.hb_ttl_s}s` : "—"} />
        <Row label="Stale threshold" value={`${engine.stale_threshold_s}s`} dim />
        {engine.out_key && (
          <>
            <Row label="Output key" value={engine.out_key} dim />
            <Row label="Last output" value={fmtAge(engine.out_age_s)} />
          </>
        )}
      </div>
    </section>
  );
}

function IbCard({ ib }: { ib: IbInfo }): JSX.Element {
  const color = ib.status === "OK" ? "#6c6" : "#e66";
  return (
    <section className="panel" style={{ borderTop: `3px solid ${color}` }}>
      <header className="panel-header">
        <h2>ib-gateway</h2>
        <span style={{ color, fontWeight: 600, fontSize: 12 }}>● {ib.status}</span>
      </header>
      <div className="panel-body" style={{ padding: 12, fontSize: 13 }}>
        <Row label="Host" value={`${ib.host}:${ib.port}`} dim />
        <Row label="Probe" value="TCP connect" dim />
        {ib.error && <Row label="Error" value={ib.error} />}
      </div>
    </section>
  );
}

function Row({ label, value, dim }: { label: string; value: string; dim?: boolean }): JSX.Element {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", padding: "3px 0", color: dim ? "#888" : "#ddd" }}>
      <span style={{ color: "#888" }}>{label}</span>
      <span style={{ fontFamily: "Consolas, monospace", fontSize: 12 }}>{value}</span>
    </div>
  );
}

function fmtAge(s: number | null): string {
  if (s === null) return "—";
  if (s < 60) return `${s.toFixed(1)}s`;
  if (s < 3600) return `${(s / 60).toFixed(1)}min`;
  return `${(s / 3600).toFixed(1)}h`;
}

const btnStyle = {
  padding: "4px 12px",
  background: "#2a4a6a",
  color: "#fff",
  border: "none",
  borderRadius: 3,
  cursor: "pointer",
  fontSize: 13,
};
