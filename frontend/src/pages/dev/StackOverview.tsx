/**
 * Stack Overview — schéma des 10 containers de la stack (cf. docs/schémas/
 * containers-overview.drawio) avec leur status (image attendue / OK / DOWN /
 * STALE) et leurs dépendances. Auto-refresh 5s.
 *
 * Layout = 4 layers verticales empilées top-down :
 *   Edge layer     : frontend, nginx
 *   App layer      : api
 *   Data layer     : redis, postgres, ib-gateway
 *   Engines layer  : market-data, vol-engine, risk-engine, db-writer
 *
 * Pas de SVG arrows pour rester simple — les flux sont implicites via la
 * disposition (top → bottom = dépendant → dépendances) + un descriptif
 * sous chaque box.
 */
import { useEffect, useRef, useState } from "react";

interface Container {
  name: string;
  image: string;
  layer: "edge" | "app" | "data" | "external" | "engines";
  desc: string;
  status: "OK" | "DOWN" | "STALE";
}

interface StackResp {
  containers: Container[];
  edges: { from: string; to: string }[];
  timestamp: string;
}

const POLL_MS = 5_000;

const LAYERS: { key: Container["layer"]; label: string }[] = [
  { key: "edge", label: "Edge — entry point" },
  { key: "app", label: "App — REST/WS" },
  { key: "data", label: "Data sources" },
  { key: "external", label: "External" },
  { key: "engines", label: "Engines — pipeline workers" },
];

export function StackOverview(): JSX.Element {
  const [data, setData] = useState<StackResp | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const timerRef = useRef<number | null>(null);

  const fetchData = async () => {
    setError(null);
    try {
      const r = await fetch("/api/v1/dev/stack");
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
            last: {new Date(data.timestamp).toLocaleTimeString()}
          </span>
        )}
      </div>

      {error && <div style={{ color: "#e66", marginBottom: 12 }}>{error}</div>}

      {data && (
        <div>
          {LAYERS.map((layer) => {
            const containers = data.containers.filter((c) => c.layer === layer.key);
            if (containers.length === 0) return null;
            return (
              <div key={layer.key} style={{ marginBottom: 24 }}>
                <div style={layerLabelStyle}>{layer.label}</div>
                <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
                  {containers.map((c) => <ContainerBox key={c.name} c={c} />)}
                </div>
                <div style={arrowStyle}>↓</div>
              </div>
            );
          })}

          <div style={{ marginTop: 24, fontSize: 12, color: "#888" }}>
            <strong style={{ color: "#7af" }}>Flux des données</strong> (lecture top → bottom = dépendances) :
            <ul style={{ marginTop: 6, paddingLeft: 18 }}>
              {data.edges.map((e, i) => (
                <li key={i}><code>{e.from}</code> → <code>{e.to}</code></li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}

function ContainerBox({ c }: { c: Container }): JSX.Element {
  const color =
    c.status === "OK" ? "#6c6" :
    c.status === "STALE" ? "#cc6" : "#e66";

  return (
    <div
      style={{
        flex: "1 1 200px",
        minWidth: 200,
        maxWidth: 280,
        background: "#1a1a1a",
        border: `2px solid ${color}`,
        borderRadius: 4,
        padding: "10px 12px",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
        <strong style={{ fontSize: 13, color: "#fff" }}>{c.name}</strong>
        <span style={{ color, fontSize: 12, fontWeight: 600 }}>● {c.status}</span>
      </div>
      <div style={{ fontSize: 11, color: "#888", fontFamily: "Consolas, monospace", marginBottom: 4, wordBreak: "break-all" }}>
        {c.image}
      </div>
      <div style={{ fontSize: 11, color: "#aaa" }}>{c.desc}</div>
    </div>
  );
}

const layerLabelStyle = {
  fontSize: 11,
  color: "#7af",
  textTransform: "uppercase" as const,
  letterSpacing: 1,
  marginBottom: 6,
};

const arrowStyle = {
  textAlign: "center" as const,
  fontSize: 18,
  color: "#444",
  margin: "8px 0",
};

const btnStyle = {
  padding: "4px 12px",
  background: "#2a4a6a",
  color: "#fff",
  border: "none",
  borderRadius: 3,
  cursor: "pointer",
  fontSize: 13,
};
