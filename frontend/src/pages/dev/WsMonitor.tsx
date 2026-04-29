/**
 * WS Monitor — 3 panels (ticks / vol / risk), chaque panel affiche en
 * rolling buffer les N derniers messages reçus avec timestamp + payload.
 * Boutons pause / resume / clear par panel.
 *
 * Pas de seed / pas de filtrage : raw stream comme un wireshark dev. Si
 * un panel reste vide > 5s sur ticks ou risk, c'est probablement un soucis
 * pipeline (cf. notebooks smoke market-data/03 et risk/03).
 */
import { useWsLog, type WsStatus } from "../../hooks/useWsLog";

const WS_BASE = (import.meta.env["VITE_WS_BASE_URL"] as string | undefined) ?? "";

const CHANNELS = [
  { key: "ticks", path: "/ws/ticks", label: "📡 ticks", expected: "~1 msg/s" },
  { key: "vol", path: "/ws/vol", label: "🌊 vol", expected: "1 msg/180s" },
  { key: "risk", path: "/ws/risk", label: "📊 risk", expected: "~1 msg/2s" },
] as const;

export function WsMonitor(): JSX.Element {
  return (
    <div style={{ padding: 12, display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
      {CHANNELS.map((c) => (
        <ChannelPanel key={c.key} path={c.path} label={c.label} expected={c.expected} />
      ))}
    </div>
  );
}

function ChannelPanel({
  path, label, expected,
}: { path: string; label: string; expected: string }): JSX.Element {
  const url = `${WS_BASE}${path}`;
  const { status, count, messages, paused, pause, resume, clear } = useWsLog(url, 50);

  return (
    <section className="panel">
      <header className="panel-header" style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <h2 style={{ flex: 1 }}>
          {label}{" "}
          <span style={{ color: "#888", fontSize: 11, fontWeight: "normal" }}>{expected}</span>
        </h2>
        <StatusDot status={status} />
        <span style={{ color: "#aaa", fontSize: 12 }}>{count}</span>
      </header>
      <div className="panel-body" style={{ padding: 8 }}>
        <div style={{ display: "flex", gap: 4, marginBottom: 6 }}>
          {paused ? (
            <button onClick={resume} style={btnStyle}>Resume</button>
          ) : (
            <button onClick={pause} style={btnStyle}>Pause</button>
          )}
          <button onClick={clear} style={btnStyle}>Clear</button>
          <span style={{ color: "#888", fontSize: 11, marginLeft: "auto", alignSelf: "center" }}>
            {messages.length}/50
          </span>
        </div>
        <div
          style={{
            background: "#000",
            color: "#cdc",
            fontSize: 11,
            fontFamily: "Consolas, monospace",
            padding: 8,
            height: "60vh",
            overflow: "auto",
          }}
        >
          {messages.length === 0 ? (
            <div style={{ color: "#666" }}>(no messages yet)</div>
          ) : (
            messages.map((m, i) => (
              <div key={i} style={{ marginBottom: 8, paddingBottom: 6, borderBottom: "1px solid #222" }}>
                <div style={{ color: "#888" }}>{m.ts}</div>
                <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
                  {tryPretty(m.raw)}
                </pre>
              </div>
            ))
          )}
        </div>
      </div>
    </section>
  );
}

function tryPretty(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}

function StatusDot({ status }: { status: WsStatus }): JSX.Element {
  const color = status === "open" ? "#6c6" : status === "connecting" ? "#cc6" : "#e66";
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11, color }}>
      <span style={{ width: 8, height: 8, borderRadius: "50%", background: color }} />
      {status}
    </span>
  );
}

const btnStyle = {
  padding: "3px 10px",
  background: "#2a4a6a",
  color: "#fff",
  border: "none",
  borderRadius: 3,
  cursor: "pointer",
  fontSize: 11,
};
