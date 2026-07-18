import { StatusBadge } from "./StatusBadge";

export function Header(): JSX.Element {
  // Base-aware (deploy subpath, e.g. "/fx-volatility-trading-system/"): strip
  // the base before matching the route, and prefix the nav links with it.
  const base = import.meta.env.BASE_URL.replace(/\/$/, "");
  const raw = typeof window !== "undefined" ? window.location.pathname : "/";
  const path = base && raw.startsWith(base) ? raw.slice(base.length) || "/" : raw;
  const isDev = path.startsWith("/dev");

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
      <a href={isDev ? `${base}/` : `${base}/dev`} style={{ ...btn, marginLeft: 24 }}>
        {isDev ? "← Live" : "Dev →"}
      </a>
      <StatusBadge />
    </header>
  );
}
