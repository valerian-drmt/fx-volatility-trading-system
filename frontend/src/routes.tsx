// Routing shim. The real router lands in a later PR once panels exist.
// R9 admin-config T1 : a minimal path-based switch surfaces /settings
// without pulling in react-router (the real router is still planned in
// a later R5 PR). Navigate via the browser URL or `window.location`.
import { Settings } from "./pages/Settings";

export function Routes(): JSX.Element {
  const path = typeof window !== "undefined" ? window.location.pathname : "/";
  if (path.startsWith("/settings")) {
    return <Settings />;
  }
  return (
    <section className="app-placeholder">
      <p>Panels will land in the following R5 PRs (layout, charts, order ticket, book).</p>
      <p>
        <a href="/settings">Open admin settings</a>
      </p>
    </section>
  );
}
