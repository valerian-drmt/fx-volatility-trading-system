import { Routes } from "./routes";

export default function App(): JSX.Element {
  return (
    <main className="app-shell">
      <header className="app-header">
        <h1>FX Vol Dashboard</h1>
        <span className="app-version">v0.1.0 · scaffold</span>
      </header>
      <Routes />
    </main>
  );
}
