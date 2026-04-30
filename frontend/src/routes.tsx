// Routing shim. The real router lands in a later PR once panels exist.
// Keeping the file now so imports in App.tsx are stable across the stack.
export function Routes(): JSX.Element {
  return (
    <section className="app-placeholder">
      <p>Panels will land in the following R5 PRs (layout, charts, order ticket, book).</p>
    </section>
  );
}
