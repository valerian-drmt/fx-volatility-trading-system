/**
 * Step 1 — Regime gating panel.
 *
 * 6 zones (cf. docs/vol_trading_pca/specs/STEP1_REGIME_GATING.md §3) :
 *   1. Badge régime (label + couleur)
 *   2. Probabilités GMM        — null en MVP, "not available"
 *   3. Features (3 lignes)     — value + z-score + qualifier
 *   4. Prochain event          — type + countdown
 *   5. VRP attendu (1M-6M)     — placeholder
 *   6. Event dampener          — badge ON/OFF
 *   + Gate decision            — authorized + reason + size_mult
 *
 * Auto-refresh 3s. Affiche "stale" si timestamp > 200s.
 */
import { useEffect, useState } from "react";

interface Feature { value: number | null; z: number | null }
interface Gate { authorized: boolean; reason: string; size_mult: number }
interface NextEvent {
  event_type: string;
  impact: string;
  region: string;
  scheduled_at: string;
  days_remaining: number;
  description: string | null;
}
interface RegimeState {
  timestamp: string;
  symbol: string;
  label: "calm" | "stressed" | "pre_event";
  method: string;
  event_dampener: boolean;
  days_to_next_event: number | null;
  next_event_type: string | null;
  next_event_any: NextEvent | null;
  next_event_high: NextEvent | null;
  features: { vol_level: Feature; vol_of_vol: Feature; term_slope: Feature };
  gate: Gate;
  probabilities?: { calm: number; stressed: number; pre_event: number } | null;
  p_calm?: number | null;
  p_stressed?: number | null;
  p_pre_event?: number | null;
}

const REGIME_COLORS: Record<string, string> = {
  calm: "#6c6", stressed: "#e66", pre_event: "#fc6",
};

interface GmmShadow {
  n_total: number;
  n_with_gmm: number;
  agreement_ratio: number | null;
  ready_to_promote: boolean;
  promotion_gates?: {
    n_required: number;
    n_with_gmm_ok: boolean;
    agreement_ratio_required: number;
    agreement_ratio_ok: boolean;
    manual_check_needed: string;
  };
  reason?: string;
}

export function Step1Regime(): JSX.Element {
  const [state, setState] = useState<RegimeState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<{ timestamp: string; label: string }[]>([]);
  const [shadow, setShadow] = useState<GmmShadow | null>(null);

  useEffect(() => {
    const fetchAll = async () => {
      try {
        const [s, h, g] = await Promise.all([
          fetch("/api/v1/regime/state").then((r) => r.ok ? r.json() : Promise.reject(`state ${r.status}`)),
          fetch("/api/v1/regime/history?n=20").then((r) => r.ok ? r.json() : []),
          fetch("/api/v1/regime/gmm/shadow?n=500").then((r) => r.ok ? r.json() : null),
        ]);
        setState(s);
        setHistory(h);
        setShadow(g);
        setError(null);
      } catch (e) {
        setError(String(e));
      }
    };
    void fetchAll();
    const id = window.setInterval(fetchAll, 3_000);
    return () => window.clearInterval(id);
  }, []);

  if (error && !state) return <div style={{ color: "#e66", padding: 12 }}>{error}</div>;
  if (!state) return <div style={{ color: "#666", padding: 12 }}>(loading regime…)</div>;

  const ageMs = Date.now() - new Date(state.timestamp).getTime();
  const stale = ageMs > 200_000;

  return (
    <div style={{ padding: 12, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
      {/* ── Zone 1 + Gate ── full row */}
      <section style={{ ...panelStyle, gridColumn: "1 / span 2" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 16, padding: 12 }}>
          <div style={{
            padding: "8px 16px", background: REGIME_COLORS[state.label] ?? "#888",
            color: "#000", fontWeight: 700, borderRadius: 4, fontSize: 18, letterSpacing: 1,
          }}>
            {state.label.toUpperCase()}
          </div>
          <div style={{ color: "#888", fontSize: 12 }}>
            method=<code>{state.method}</code> · {new Date(state.timestamp).toLocaleTimeString()}
            {stale && <span style={{ color: "#e66", marginLeft: 8 }}>· STALE ({Math.round(ageMs/1000)}s)</span>}
          </div>
          <div style={{ marginLeft: "auto", display: "flex", gap: 12, alignItems: "center" }}>
            <GateBadge gate={state.gate} />
          </div>
        </div>
      </section>

      {/* ── Zone 3 : features ── */}
      <section style={panelStyle}>
        <Header>3 · Features live</Header>
        <table style={tableStyle}>
          <thead><tr><th style={th}>feature</th><th style={th}>value</th><th style={th}>z (90d)</th></tr></thead>
          <tbody>
            {(["vol_level", "vol_of_vol", "term_slope"] as const).map((k) => {
              const f = state.features[k];
              return (
                <tr key={k}><td style={td}>{k}</td>
                  <td style={td}>{f.value !== null ? f.value.toFixed(2) : "—"}</td>
                  <td style={{ ...td, color: zColor(f.z) }}>{f.z !== null ? f.z.toFixed(2) : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>

      {/* ── Zone 2 : GMM probas — actives ou shadow ── */}
      <section style={panelStyle}>
        <Header>
          2 · Probabilités GMM · method = {state.method}
          {state.method === "threshold_heuristic" && " · SHADOW"}
        </Header>
        <div style={{ padding: 12 }}>
          {state.probabilities ? (
            <>
              {state.method === "threshold_heuristic" && (
                <NonPertinentBanner shadow={shadow} />
              )}
              <div style={{
                opacity: state.method === "threshold_heuristic" ? 0.5 : 1,
                marginTop: 8,
              }}>
                {(["calm", "stressed", "pre_event"] as const).map((k) => (
                  <ProbaBar key={k} label={k} value={state.probabilities![k]} />
                ))}
              </div>
              <div style={{ color: "#666", fontSize: 10, marginTop: 6 }}>
                3-component GMM (vol_level, vol_of_vol).{" "}
                {state.method === "gmm_v1"
                  ? "Argmax pilote le label."
                  : "Calculé en parallèle, pas utilisé pour le label tant que les gates ne sont pas verts."}
              </div>
              <details style={{ marginTop: 10 }}>
                <summary style={{ cursor: "pointer", color: "#7af", fontSize: 11 }}>
                  Promotion gates →
                </summary>
                <div style={{ marginTop: 8 }}>
                  <ShadowProgress shadow={shadow} />
                </div>
              </details>
            </>
          ) : (
            <ShadowProgress shadow={shadow} />
          )}
        </div>
      </section>

      {/* ── Zone 4 : prochains events (any + high) ── */}
      <section style={panelStyle}>
        <Header>4 · Prochains events</Header>
        <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 10 }}>
          <EventLine label="Any impact" event={state.next_event_any} colorAccent="#7af" />
          <EventLine
            label="High impact"
            event={state.next_event_high}
            colorAccent={state.next_event_high && state.next_event_high.days_remaining < 5 ? "#fc6" : "#e66"}
            highlightDampener
          />
          <div style={{ color: "#666", fontSize: 10, marginTop: 4 }}>
            High impact = ECB / FOMC / NFP / CPI / GDP / BoE — drive le dampener J-5.
          </div>
        </div>
      </section>

      {/* ── Zone 6 : event dampener ── */}
      <section style={panelStyle}>
        <Header>6 · Event dampener</Header>
        <div style={{ padding: 12, fontSize: 14 }}>
          <span style={{
            padding: "4px 10px", borderRadius: 3, fontWeight: 600,
            background: state.event_dampener ? "#fc6" : "#444",
            color: state.event_dampener ? "#000" : "#aaa",
          }}>
            {state.event_dampener ? "ON" : "OFF"}
          </span>
          <div style={{ color: "#888", fontSize: 11, marginTop: 6 }}>
            ON si days_to_next_event &lt; 5 (zone 4 ↑).
          </div>
        </div>
      </section>

      {/* ── History (stability) ── */}
      <section style={{ ...panelStyle, gridColumn: "1 / span 2" }}>
        <Header>Historique des labels (3 derniers = stability gate)</Header>
        <div style={{ padding: 8, display: "flex", gap: 4, flexWrap: "wrap" }}>
          {history.slice(0, 20).map((h, i) => (
            <span key={i} title={new Date(h.timestamp).toLocaleString()} style={{
              padding: "3px 6px", fontSize: 11, borderRadius: 3,
              background: REGIME_COLORS[h.label] ?? "#666", color: "#000",
              opacity: i < 3 ? 1 : 0.5,
            }}>{h.label}</span>
          ))}
        </div>
      </section>
    </div>
  );
}

function NonPertinentBanner({ shadow }: { shadow: GmmShadow | null }): JSX.Element {
  const reasons: string[] = [];
  if (!shadow || !shadow.promotion_gates) {
    reasons.push("shadow GMM diagnostic indisponible");
  } else {
    const g = shadow.promotion_gates;
    if (!g.n_with_gmm_ok) {
      reasons.push(
        `volume insuffisant — ${shadow.n_with_gmm} / ${g.n_required} obs (≈ ${Math.round((g.n_required - shadow.n_with_gmm) * 180 / 3600)}h restant)`,
      );
    }
    if (!g.agreement_ratio_ok) {
      reasons.push(
        `agreement vs heuristic = ${((shadow.agreement_ratio ?? 0) * 100).toFixed(1)}% < ${(g.agreement_ratio_required * 100).toFixed(0)}% requis`,
      );
    } else if (!_hasMultipleLabelsObserved(shadow)) {
      reasons.push(
        `agreement = ${((shadow.agreement_ratio ?? 0) * 100).toFixed(1)}% trivial (1 seul label observé en shadow — métrique non-discriminante)`,
      );
    }
    // Coverage gate is always manual until proven, so always list it.
    reasons.push(
      "training set ne couvre encore aucun event high-impact traversé live (FOMC / NFP / ECB)",
    );
  }
  // Add a structural reason : we know the bootstrap dataset is calm-only.
  reasons.push(
    "données historiques EUR/USD apr 2025 → apr 2026 = quasi-uniformément calm → composantes GMM mappées par tri μ_vol_level mais indistinguables sémantiquement",
  );

  return (
    <div style={{
      padding: "8px 10px", border: "1px solid #fc6", borderRadius: 4,
      background: "#2a2114", marginBottom: 4,
    }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <span style={{
          padding: "2px 8px", background: "#fc6", color: "#000",
          fontWeight: 700, fontSize: 11, borderRadius: 3, letterSpacing: 0.5,
        }}>
          NON PERTINENT
        </span>
        <span style={{ color: "#fc6", fontSize: 12 }}>
          probabilités calculées mais non actionnables
        </span>
      </div>
      <ul style={{
        margin: "8px 0 0 0", paddingLeft: 18,
        color: "#ccc", fontSize: 11, lineHeight: 1.5,
      }}>
        {reasons.map((r, i) => <li key={i}>{r}</li>)}
      </ul>
    </div>
  );
}

function _hasMultipleLabelsObserved(shadow: GmmShadow | null): boolean {
  if (!shadow) return false;
  // Use the agreement_ratio breakdown — by_label keys = distinct heuristic
  // labels seen during shadow window. If only "calm" appears, the agreement
  // metric is trivially 100% and not informative.
  // We don't have direct access to keys here in /shadow response without
  // expanding the schema ; fallback heuristic : if ready_to_promote depends
  // on agreement gate AND the n_with_gmm is small, assume non-discriminant.
  // Better : the API includes by_label dict — count its keys.
  const anyAsGenericObj = shadow as unknown as { by_label?: Record<string, unknown> };
  const labels = anyAsGenericObj.by_label
    ? Object.keys(anyAsGenericObj.by_label)
    : [];
  return labels.length >= 2;
}

function ShadowProgress({ shadow }: { shadow: GmmShadow | null }): JSX.Element {
  if (!shadow) {
    return <div style={{ color: "#888", fontSize: 12 }}>(loading shadow diagnostic…)</div>;
  }
  if (!shadow.promotion_gates) {
    return (
      <div style={{ color: "#888", fontSize: 12 }}>
        GMM shadow not running yet — {shadow.reason ?? "no probas in DB"}.
        Need ≥ 50 obs in <code>feature_history</code> with both <code>iv_atm_3m_pct</code> and{" "}
        <code>vol_of_vol_30d_pct</code> non-null.
      </div>
    );
  }
  const g = shadow.promotion_gates;
  const volPct = Math.min(shadow.n_with_gmm / g.n_required, 1) * 100;
  const agr = shadow.agreement_ratio ?? 0;
  return (
    <div style={{ fontSize: 12, color: "#ccc" }}>
      <div style={{ marginBottom: 8, color: "#aaa" }}>
        Heuristic drives the label. GMM tourne en parallèle, ses sorties sont logguées
        dans <code>regime_snapshots.p_*</code> pour comparison J+30 mais{" "}
        <strong>n'affectent pas le gate decision</strong>.
      </div>

      <Gate label={`Volume ≥ ${g.n_required}`}
            ok={g.n_with_gmm_ok}
            detail={`${shadow.n_with_gmm} / ${g.n_required}`}
            barPct={volPct} />
      <Gate label={`Agreement ≥ ${(g.agreement_ratio_required * 100).toFixed(0)}%`}
            ok={g.agreement_ratio_ok && _hasMultipleLabelsObserved(shadow)}
            detail={
              _hasMultipleLabelsObserved(shadow)
                ? `${(agr * 100).toFixed(1)}%`
                : `${(agr * 100).toFixed(1)}% — non-discriminant (1 seul label observé)`
            }
            barPct={Math.min(agr / 1.0, 1) * 100} />
      <Gate label="Coverage event high-impact traversé"
            ok={false}
            detail="manual check"
            barPct={0} />

      <div style={{
        marginTop: 10, padding: "5px 10px", borderRadius: 3,
        background: shadow.ready_to_promote ? "#363" : "#444",
        color: shadow.ready_to_promote ? "#cfc" : "#aaa",
        fontWeight: 600, fontSize: 11, display: "inline-block",
      }}>
        {shadow.ready_to_promote ? "✓ Ready to promote → gmm_v1" : "✗ Not ready (cf. gates above)"}
      </div>

      <div style={{ marginTop: 8, color: "#666", fontSize: 10 }}>
        Cf. <code>docs/vol_trading_pca/specs/STEP1_REGIME_GATING.md §13</code>
      </div>
    </div>
  );
}

function Gate({
  label, ok, detail, barPct,
}: {
  label: string; ok: boolean; detail: string; barPct: number;
}): JSX.Element {
  const color = ok ? "#6c6" : "#fc6";
  return (
    <div style={{ marginBottom: 6 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11 }}>
        <span style={{ color }}>{ok ? "✓" : "·"} {label}</span>
        <span style={{ color: "#888", fontFamily: "Consolas, monospace" }}>{detail}</span>
      </div>
      <div style={{ height: 4, background: "#222", borderRadius: 2, marginTop: 2 }}>
        <div style={{
          width: `${barPct}%`, height: "100%", background: color,
          borderRadius: 2, transition: "width 0.3s",
        }} />
      </div>
    </div>
  );
}

function ProbaBar({ label, value }: { label: string; value: number }): JSX.Element {
  const color = REGIME_COLORS[label] ?? "#888";
  const pct = Math.round(value * 100);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
      <span style={{ minWidth: 75, fontSize: 11, color: "#aaa" }}>{label}</span>
      <div style={{ flex: 1, height: 14, background: "#222", borderRadius: 2, position: "relative" }}>
        <div style={{
          width: `${pct}%`, height: "100%", background: color,
          borderRadius: 2, transition: "width 0.3s",
        }} />
      </div>
      <span style={{ minWidth: 38, textAlign: "right", fontSize: 11, fontFamily: "Consolas, monospace" }}>
        {pct}%
      </span>
    </div>
  );
}

function EventLine({
  label, event, colorAccent, highlightDampener = false,
}: {
  label: string;
  event: NextEvent | null;
  colorAccent: string;
  highlightDampener?: boolean;
}): JSX.Element {
  if (!event) {
    return (
      <div style={{ fontSize: 12, color: "#888" }}>
        <span style={{ color: "#666", marginRight: 8 }}>{label}</span>
        <span>(aucun event futur dans la table)</span>
      </div>
    );
  }
  const inDampener = highlightDampener && event.days_remaining < 5;
  return (
    <div style={{ fontSize: 13, lineHeight: 1.4 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
        <span style={{ fontSize: 10, color: "#666", letterSpacing: 1, minWidth: 80 }}>
          {label.toUpperCase()}
        </span>
        <strong style={{ color: colorAccent }}>{event.event_type}</strong>
        <span style={{ color: "#888", fontSize: 11 }}>
          [{event.region} · {event.impact}]
        </span>
        {inDampener && (
          <span style={{
            marginLeft: "auto", padding: "1px 6px", background: "#fc6",
            color: "#000", borderRadius: 3, fontSize: 10, fontWeight: 700,
          }}>DAMPENER J-{event.days_remaining.toFixed(1)}</span>
        )}
      </div>
      <div style={{ color: "#aaa", fontSize: 11, marginLeft: 88 }}>
        dans <strong>{event.days_remaining.toFixed(2)} j</strong> · {new Date(event.scheduled_at).toLocaleString()}
      </div>
      {event.description && (
        <div style={{ color: "#666", fontSize: 10, marginLeft: 88, fontStyle: "italic" }}>
          {event.description}
        </div>
      )}
    </div>
  );
}

function GateBadge({ gate }: { gate: Gate }): JSX.Element {
  const bg = gate.authorized ? "#363" : "#633";
  return (
    <div style={{
      padding: "6px 12px", background: bg, color: "#fff",
      borderRadius: 4, fontSize: 13,
    }}>
      gate: <strong>{gate.authorized ? "AUTHORIZED" : "BLOCKED"}</strong>
      {" · "}<code>{gate.reason}</code>
      {" · "}size×{gate.size_mult.toFixed(2)}
    </div>
  );
}

function Header({ children }: { children: React.ReactNode }): JSX.Element {
  return (
    <div style={{
      padding: "5px 12px", background: "#1a1a1a", borderBottom: "1px solid #333",
      color: "#7af", fontSize: 11, fontWeight: 600, letterSpacing: 1,
    }}>{children}</div>
  );
}

function zColor(z: number | null): string {
  if (z === null) return "#888";
  if (Math.abs(z) > 2) return "#e66";
  if (Math.abs(z) > 1) return "#fc6";
  return "#cc6";
}

const panelStyle = {
  background: "#0a0a0a", border: "1px solid #222", borderRadius: 4, overflow: "hidden",
};
const tableStyle = { borderCollapse: "collapse" as const, fontSize: 12, width: "100%", fontFamily: "Consolas, monospace" };
const th = { padding: "4px 8px", textAlign: "left" as const, color: "#888", borderBottom: "1px solid #333" };
const td = { padding: "3px 8px" };
