/**
 * Stub component used while the dev tab is not coded yet.
 * R9 sandbox — replaced by the real component as each tab is implemented.
 */
export function DevPlaceholder({ name }: { name: string }): JSX.Element {
  return (
    <section className="panel" style={{ margin: 16 }}>
      <header className="panel-header">
        <h2>TODO: {name}</h2>
      </header>
      <div className="panel-body" style={{ padding: 16, color: "#888" }}>
        Cet onglet est planifié dans le sandbox R9 mais pas encore codé.
        Cf. <code>releases/r9-frontend-validation-today-plan.md</code>.
      </div>
    </section>
  );
}
