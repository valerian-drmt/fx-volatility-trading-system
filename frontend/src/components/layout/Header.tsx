import { StatusBadge } from "./StatusBadge";

export function Header(): JSX.Element {
  const path = typeof window !== "undefined" ? window.location.pathname : "/";
  const isDev = path.startsWith("/dev");

  return (
    <header className="app-header" data-testid="app-header">
      <h1>FX Vol Dashboard</h1>
      <nav style={{ display: "flex", gap: 4, marginLeft: 24 }}>
        <a href="/" style={navStyle(!isDev)}>Live</a>
        <a href="/dev" style={navStyle(isDev)}>Dev</a>
      </nav>
      <StatusBadge />
    </header>
  );
}

function navStyle(active: boolean): React.CSSProperties {
  return {
    padding: "4px 12px",
    color: active ? "#fff" : "#aaa",
    background: active ? "#2a4a6a" : "transparent",
    textDecoration: "none",
    borderRadius: 3,
    fontSize: 13,
    fontWeight: 500,
  };
}
