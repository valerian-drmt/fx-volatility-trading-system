/**
 * Vol Surface — exercise les 4 endpoints /api/v1/vol/* avec **les mêmes
 * charts que la prod** (SmileChart, TermStructureChart). Le but est que
 * ce qui est validé ici puisse être copié-collé dans le dashboard prod
 * sans modif.
 *
 * 4 sections empilées (cf. docs/VOL_TRADING_USER_GUIDE.md § Panel 5) :
 *   1. Surface — top-level shape : flags présence + raw JSON
 *   2. Term structure — table + TermStructureChart (ATM vs fair)
 *   3. Smile @ tenor — table + SmileChart (observed + SVI fit + refs)
 *   4. No-arb health — table par tenor : RMSE, butterfly_g_min, status
 */
import { Suspense, lazy, useEffect, useState } from "react";

const SmileChart = lazy(() =>
  import("../../components/charts/SmileChart").then((m) => ({ default: m.SmileChart })),
);
const TermStructureChart = lazy(() =>
  import("../../components/charts/TermStructureChart").then((m) => ({ default: m.TermStructureChart })),
);

const DEFAULT_SYMBOL = "EURUSD";
const TENORS = ["1M", "2M", "3M", "4M", "5M", "6M"];

interface SurfacePayload {
  symbol: string;
  timestamp: string;
  surface: Record<string, unknown>;
}

interface TermRow {
  tenor: string;
  dte: number | null;
  sigma_atm_pct: number | null;
  sigma_fair_pct: number | null;
  sigma_fair_p_pct: number | null;
  sigma_fair_q_pct: number | null;
  vrp_vol_pts: number | null;
  regime: string | null;
}

interface TermStructureResp {
  symbol: string;
  timestamp: string;
  pillars: TermRow[];
}

interface ApiSmilePoint {
  strike: number;
  iv_pct: number;
  delta_label: string;
}

interface SmileResp {
  symbol: string;
  timestamp: string;
  tenor: string;
  dte: number | null;
  points: ApiSmilePoint[];
  sigma_fair_pct: number | null;
  rv_pct: number | null;
  svi_curve: ApiSmilePoint[] | null;
}

interface SviParams {
  a: number;
  b: number;
  rho: number;
  m: number;
  sigma: number;
  rmse_fit: number;
  butterfly_g_min: number;
}

const CHART_LOADING = <div style={{ color: "#666", fontSize: 12 }}>loading chart…</div>;

export function VolSurface(): JSX.Element {
  const [symbol, setSymbol] = useState(DEFAULT_SYMBOL);
  const [tenor, setTenor] = useState("3M");

  const [surface, setSurface] = useState<SurfacePayload | null>(null);
  const [terms, setTerms] = useState<TermStructureResp | null>(null);
  const [smile, setSmile] = useState<SmileResp | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const fetchAll = async () => {
    setLoading(true);
    setError(null);
    try {
      const [s, t, sm] = await Promise.all([
        fetch(`/api/v1/vol/surface?symbol=${symbol}`).then((r) => asJson<SurfacePayload>(r)),
        fetch(`/api/v1/vol/term-structure?symbol=${symbol}`).then((r) => asJson<TermStructureResp>(r)),
        fetch(`/api/v1/vol/smile/${tenor}?symbol=${symbol}`).then((r) => asJson<SmileResp>(r)),
      ]);
      setSurface(s);
      setTerms(t);
      setSmile(sm);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div style={{ padding: 16 }}>
      <div style={{ display: "flex", gap: 8, marginBottom: 16, alignItems: "center" }}>
        <label style={{ color: "#aaa", fontSize: 13 }}>
          Symbol:{" "}
          <input
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            style={{ ...inputStyle, width: 100 }}
          />
        </label>
        <label style={{ color: "#aaa", fontSize: 13 }}>
          Tenor:{" "}
          <select value={tenor} onChange={(e) => setTenor(e.target.value)} style={inputStyle}>
            {TENORS.map((t) => <option key={t}>{t}</option>)}
          </select>
        </label>
        <button onClick={fetchAll} disabled={loading} style={btnStyle}>
          {loading ? "…" : "Fetch all ▶"}
        </button>
        {surface && (
          <span style={{ color: "#666", fontSize: 12, marginLeft: "auto" }}>
            ts: {new Date(surface.timestamp).toLocaleTimeString()}
          </span>
        )}
      </div>

      {error && <div style={{ color: "#e66", marginBottom: 12 }}>{error}</div>}

      {/* 1. Surface raw + summary */}
      <Section title="1. Surface — top-level shape">
        {surface ? <SurfaceSummary payload={surface} /> : <Empty />}
      </Section>

      {/* 2. Term structure — table + chart */}
      <Section title="2. Term structure (ATM IV per tenor)">
        {terms ? <TermStructureView terms={terms} /> : <Empty />}
      </Section>

      {/* 3. Smile par tenor — table + chart */}
      <Section title={`3. Smile @ ${tenor} — observed pillars + SVI fit`}>
        {smile ? <SmileView smile={smile} /> : <Empty />}
      </Section>

      {/* 4. No-arb health */}
      <Section title="4. No-arb health — SVI fits per tenor">
        {surface ? <NoArbTable surface={surface} /> : <Empty />}
      </Section>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }): JSX.Element {
  return (
    <section className="panel" style={{ marginBottom: 16 }}>
      <header className="panel-header"><h2 style={{ fontSize: 13 }}>{title}</h2></header>
      <div className="panel-body" style={{ padding: 12 }}>{children}</div>
    </section>
  );
}

function Empty(): JSX.Element {
  return <div style={{ color: "#666", fontSize: 12 }}>(no data — click Fetch above)</div>;
}

function SurfaceSummary({ payload }: { payload: SurfacePayload }): JSX.Element {
  const { surface } = payload;
  const publicTenors = Object.keys(surface).filter((k) => !k.startsWith("_"));
  const flags: { name: string; present: boolean; detail?: string }[] = [
    { name: "_rv_full_pct", present: typeof surface["_rv_full_pct"] === "number", detail: String(surface["_rv_full_pct"] ?? "—") },
    { name: "_har", present: hasKeys(surface["_har"]) },
    { name: "_garch", present: hasKeys(surface["_garch"]) },
    { name: "_fair_q", present: hasKeys(surface["_fair_q"]) },
    { name: "_svi", present: hasKeys(surface["_svi"]) },
    { name: "_ssvi", present: hasKeys(surface["_ssvi"]) },
  ];
  return (
    <div>
      <div style={{ marginBottom: 8, fontSize: 12 }}>
        <strong>{publicTenors.length} public tenors</strong> : {publicTenors.join(", ") || "(none)"}
      </div>
      <table style={tableStyle}>
        <thead><tr><th style={th}>Component</th><th style={th}>Present</th><th style={th}>Detail</th></tr></thead>
        <tbody>
          {flags.map((f) => (
            <tr key={f.name}>
              <td style={td}><code>{f.name}</code></td>
              <td style={{ ...td, color: f.present ? "#6c6" : "#e66" }}>{f.present ? "✓" : "✗"}</td>
              <td style={td}>{f.detail ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <details style={{ marginTop: 12 }}>
        <summary style={{ color: "#aaa", fontSize: 12, cursor: "pointer" }}>Raw JSON (full payload)</summary>
        <pre style={preStyle}>{JSON.stringify(payload, null, 2)}</pre>
      </details>
    </div>
  );
}

function TermStructureView({ terms }: { terms: TermStructureResp }): JSX.Element {
  // Map to the chart's TermPoint shape (atmVol/fairVol in pct units).
  const chartPoints = terms.pillars.map((p) => ({
    tenor: p.tenor,
    atmVol: p.sigma_atm_pct ?? 0,
    fairVol: p.sigma_fair_pct ?? null,
  }));
  return (
    <div>
      <div style={{ height: 300, marginBottom: 12 }}>
        <Suspense fallback={CHART_LOADING}>
          <TermStructureChart points={chartPoints} />
        </Suspense>
      </div>
      <table style={tableStyle}>
        <thead>
          <tr>
            <th style={th}>Tenor</th>
            <th style={th}>DTE</th>
            <th style={th}>σ ATM (Q)</th>
            <th style={th}>σ fair (P)</th>
            <th style={th}>σ fair (Q)</th>
            <th style={th}>VRP</th>
            <th style={th}>Regime</th>
          </tr>
        </thead>
        <tbody>
          {terms.pillars.map((p) => (
            <tr key={p.tenor}>
              <td style={td}>{p.tenor}</td>
              <td style={td}>{p.dte ?? "—"}</td>
              <td style={td}>{fmtPct(p.sigma_atm_pct)}</td>
              <td style={td}>{fmtPct(p.sigma_fair_p_pct)}</td>
              <td style={td}>{fmtPct(p.sigma_fair_q_pct)}</td>
              <td style={td}>{p.vrp_vol_pts != null ? p.vrp_vol_pts.toFixed(3) : "—"}</td>
              <td style={td}>{p.regime ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SmileView({ smile }: { smile: SmileResp }): JSX.Element {
  const chartPoints = smile.points.map((p) => ({ strike: p.strike, vol: p.iv_pct }));
  const sviCurve = smile.svi_curve
    ? smile.svi_curve.map((p) => ({ strike: p.strike, vol: p.iv_pct }))
    : null;
  return (
    <div>
      <div style={{ height: 300, marginBottom: 12 }}>
        <Suspense fallback={CHART_LOADING}>
          <SmileChart
            points={chartPoints}
            tenor={smile.tenor}
            sviCurve={sviCurve}
            fairVol={smile.sigma_fair_pct ?? null}
            rv={smile.rv_pct ?? null}
          />
        </Suspense>
      </div>
      <div style={{ fontSize: 12, color: "#aaa", marginBottom: 6 }}>
        DTE: {smile.dte ?? "—"} · {smile.points.length} pillars · σ fair:{" "}
        {fmtPct(smile.sigma_fair_pct)} · RV: {fmtPct(smile.rv_pct)}
      </div>
      <table style={tableStyle}>
        <thead><tr><th style={th}>Delta</th><th style={th}>Strike</th><th style={th}>IV</th></tr></thead>
        <tbody>
          {smile.points.map((p, i) => (
            <tr key={i}>
              <td style={td}>{p.delta_label}</td>
              <td style={td}>{p.strike.toFixed(5)}</td>
              <td style={td}>{p.iv_pct.toFixed(3)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function NoArbTable({ surface }: { surface: SurfacePayload }): JSX.Element {
  const svi = (surface.surface["_svi"] as Record<string, SviParams>) ?? {};
  const ssvi = surface.surface["_ssvi"] as Record<string, number> | undefined;
  const tenors = Object.keys(svi);

  if (tenors.length === 0) {
    return <div style={{ color: "#888", fontSize: 12 }}>(_svi absent dans la surface)</div>;
  }

  return (
    <div>
      <table style={tableStyle}>
        <thead>
          <tr>
            <th style={th}>Tenor</th>
            <th style={th}>RMSE fit</th>
            <th style={th}>g(k) min</th>
            <th style={th}>ρ (skew)</th>
            <th style={th}>σ (convex)</th>
            <th style={th}>Status</th>
          </tr>
        </thead>
        <tbody>
          {tenors.map((t) => {
            const p = svi[t];
            const arbOk = p && p.butterfly_g_min >= 0;
            const rmseOk = p && p.rmse_fit < 0.01;
            const status = !p ? "—" : arbOk && rmseOk ? "✓" : "⚠";
            const color = !p ? "#888" : arbOk && rmseOk ? "#6c6" : "#cc6";
            return (
              <tr key={t}>
                <td style={td}>{t}</td>
                <td style={td}>{p?.rmse_fit?.toExponential(2) ?? "—"}</td>
                <td style={{ ...td, color: arbOk ? "#ddd" : "#e66" }}>
                  {p?.butterfly_g_min?.toFixed(6) ?? "—"}
                </td>
                <td style={td}>{p?.rho?.toFixed(3) ?? "—"}</td>
                <td style={td}>{p?.sigma?.toFixed(3) ?? "—"}</td>
                <td style={{ ...td, color, fontWeight: 600 }}>{status}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {ssvi && (
        <details style={{ marginTop: 10 }}>
          <summary style={{ color: "#aaa", fontSize: 12, cursor: "pointer" }}>SSVI surface-level params</summary>
          <pre style={preStyle}>{JSON.stringify(ssvi, null, 2)}</pre>
        </details>
      )}
    </div>
  );
}

function hasKeys(v: unknown): boolean {
  return !!v && typeof v === "object" && Object.keys(v as object).length > 0;
}

function fmtPct(v: number | null | undefined): string {
  return v == null ? "—" : `${v.toFixed(3)}%`;
}

async function asJson<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(`HTTP ${r.status} on ${r.url}`);
  return r.json();
}

const inputStyle = {
  background: "#1a1a1a",
  color: "#ddd",
  border: "1px solid #333",
  borderRadius: 3,
  padding: "3px 8px",
  fontSize: 13,
  marginLeft: 4,
};
const btnStyle = {
  padding: "4px 12px",
  background: "#2a4a6a",
  color: "#fff",
  border: "none",
  borderRadius: 3,
  cursor: "pointer",
  fontSize: 13,
};
const tableStyle = { borderCollapse: "collapse" as const, fontSize: 12, fontFamily: "Consolas, monospace" };
const th = { padding: "4px 12px", textAlign: "left" as const, color: "#888", borderBottom: "1px solid #333" };
const td = { padding: "3px 12px", borderBottom: "1px solid #222" };
const preStyle = {
  margin: 0,
  padding: 12,
  background: "#000",
  color: "#cdc",
  fontSize: 11,
  overflow: "auto" as const,
  maxHeight: "50vh",
  whiteSpace: "pre-wrap" as const,
};
