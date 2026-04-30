/**
 * Onglet Vol — dashboard complet d'analyse vol. 5 sections empilées :
 *   1. Surface live (smile + term structure + SVI/SSVI diag)
 *   2. Estimators : RV / HAR / GARCH / σ_fair_q par tenor
 *   3. Signals : distribution + table avec full P-measure + VRP
 *   4. Vol config editor (signal.* hot-reloadable, autres read-only)
 *
 * Tout auto-refresh sur leurs propres timers internes (pas de coordination).
 * Cf. docs/VOL_ENGINE_REFERENCE.md pour ce qui est calculé.
 */
import { VolConfigEditor } from "./VolConfigEditor";
import { VolEstimators } from "./VolEstimators";
import { VolSignals } from "./VolSignals";
import { VolSurface } from "./VolSurface";

export function VolDashboard(): JSX.Element {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, padding: 12 }}>
      <Section title="1 · Surface — smile, term structure, SVI/SSVI diagnostics">
        <VolSurface />
      </Section>
      <Section title="2 · Estimators — Yang-Zhang RV / HAR / GARCH / fair Q par tenor">
        <VolEstimators />
      </Section>
      <Section title="3 · Signals — distribution + détail (sigma_fair_p, VRP)">
        <VolSignals />
      </Section>
      <Section title="4 · Vol config editor — signal.* hot-reloadable">
        <VolConfigEditor />
      </Section>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }): JSX.Element {
  return (
    <div style={{
      background: "#0a0a0a", border: "1px solid #222", borderRadius: 4, overflow: "hidden",
    }}>
      <div style={{
        padding: "5px 12px", background: "#1a1a1a", borderBottom: "1px solid #333",
        color: "#7af", fontSize: 11, fontWeight: 600, letterSpacing: 1,
      }}>
        {title}
      </div>
      <div>{children}</div>
    </div>
  );
}
