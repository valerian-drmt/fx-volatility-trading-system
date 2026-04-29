/**
 * Stub component pour les onglets dev pas encore codés. Remplacé par le
 * vrai composant au fur et à mesure des étapes du sandbox R9.
 */
export function DevPlaceholder({ name }: { name: string }): JSX.Element {
  return (
    <section className="panel" style={{ margin: 16 }}>
      <header className="panel-header">
        <h2>TODO: {name}</h2>
      </header>
      <div className="panel-body" style={{ padding: 16, color: "#888" }}>
        Cet onglet est planifié dans le sandbox R9 mais pas encore codé. Cf.{" "}
        <code>releases/r9-frontend-validation-today-plan.md</code>.
      </div>
    </section>
  );
}
