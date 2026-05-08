import { StatusBadge } from "./StatusBadge";

export function Header(): JSX.Element {
  const path = typeof window !== "undefined" ? window.location.pathname : "/";
  const isDev = path.startsWith("/dev");
  const isConfig = path.startsWith("/config");

  const btn: React.CSSProperties = {
    marginLeft: 8,
    padding: "4px 12px",
    color: "#fff",
    background: "#2a4a6a",
    textDecoration: "none",
    borderRadius: 3,
    fontSize: 13,
    fontWeight: 500,
  };

  return (
    <header className="app-header" data-testid="app-header">
      <h1>FX Vol Dashboard</h1>
      <a href={isDev ? "/" : "/dev"} style={{ ...btn, marginLeft: 24 }}>
        {isDev ? "← Live" : "Dev →"}
      </a>
      <a href={isConfig ? "/" : "/config"} style={btn} title="Vol Engine Configs">
        ⚙️ Parameter
      </a>
      <StatusBadge />
    </header>
  );
}
