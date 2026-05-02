import { StatusBadge } from "./StatusBadge";

export function Header(): JSX.Element {
  return (
    <header className="app-header" data-testid="app-header">
      <h1>FX Vol Dashboard</h1>
      <StatusBadge />
    </header>
  );
}
