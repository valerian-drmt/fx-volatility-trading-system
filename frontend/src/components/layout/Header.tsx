import { StatusBadge } from "./StatusBadge";

export function Header(): JSX.Element {
  const path = typeof window !== "undefined" ? window.location.pathname : "/";
  const isDev = path.startsWith("/dev");

  return (
    <header className="app-header" data-testid="app-header">
      <h1>FX Vol Dashboard</h1>
      <a
        href={isDev ? "/" : "/dev"}
        style={{
          marginLeft: 24,
          padding: "4px 12px",
          color: "#fff",
          background: "#2a4a6a",
          textDecoration: "none",
          borderRadius: 3,
          fontSize: 13,
          fontWeight: 500,
        }}
      >
        {isDev ? "← Live" : "Dev →"}
      </a>
      <StatusBadge />
    </header>
  );
}
