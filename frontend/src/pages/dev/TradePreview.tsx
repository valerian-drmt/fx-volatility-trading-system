/**
 * Trade Preview — exercise POST /api/v1/vol/trade-preview avec **toutes**
 * les structures supportées, request body + response JSON visibles.
 *
 * Reprend la logique de TradePreviewPanel (frontend/src/components/panels/)
 * mais avec :
 *   - Toggle visible request body / curl-equivalent
 *   - JSON response complète (pas juste les legs comme la prod panel)
 *   - Stats greeks affichés en MetricTile cohérent
 *
 * Layout 2-col : form à gauche, request+response+result à droite.
 */
import { useState } from "react";
import { fetchTradePreview, type TradePreviewResponse } from "../../api/cockpit";

const STRUCTURES = ["StraddleATM", "RiskReversal25d", "Butterfly25d", "CalendarSpread"] as const;
const TENORS = ["1M", "2M", "3M", "4M", "5M", "6M"] as const;
const SIDES = ["BUY", "SELL"] as const;

interface FormState {
  structure: (typeof STRUCTURES)[number];
  tenor: (typeof TENORS)[number];
  tenor_far: (typeof TENORS)[number];
  side: (typeof SIDES)[number];
  qty: number;
}

const DEFAULT_FORM: FormState = {
  structure: "StraddleATM",
  tenor: "3M",
  tenor_far: "6M",
  side: "BUY",
  qty: 10,
};

export function TradePreview(): JSX.Element {
  const [form, setForm] = useState<FormState>(DEFAULT_FORM);
  const [request, setRequest] = useState<unknown>(null);
  const [response, setResponse] = useState<TradePreviewResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const set = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((f) => ({ ...f, [k]: v }));

  const submit = async () => {
    setLoading(true);
    setError(null);
    setResponse(null);
    const body: {
      structure: string;
      tenor: string;
      side: string;
      qty: number;
      tenor_far?: string;
    } = {
      structure: form.structure,
      tenor: form.tenor,
      side: form.side,
      qty: form.qty,
    };
    if (form.structure === "CalendarSpread") body.tenor_far = form.tenor_far;
    setRequest(body);
    try {
      const r = await fetchTradePreview(body);
      setResponse(r);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ padding: 16 }}>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 2fr)", gap: 16 }}>
        {/* Left : form */}
        <section className="panel">
          <header className="panel-header"><h2 style={{ fontSize: 13 }}>Form — POST /api/v1/vol/trade-preview</h2></header>
          <div className="panel-body" style={{ padding: 12 }}>
            <Row label="Structure">
              <select value={form.structure} onChange={(e) => set("structure", e.target.value as FormState["structure"])} style={inputStyle}>
                {STRUCTURES.map((s) => <option key={s}>{s}</option>)}
              </select>
            </Row>
            <Row label="Tenor">
              <select value={form.tenor} onChange={(e) => set("tenor", e.target.value as FormState["tenor"])} style={inputStyle}>
                {TENORS.map((t) => <option key={t}>{t}</option>)}
              </select>
            </Row>
            {form.structure === "CalendarSpread" && (
              <Row label="Tenor far">
                <select value={form.tenor_far} onChange={(e) => set("tenor_far", e.target.value as FormState["tenor_far"])} style={inputStyle}>
                  {TENORS.map((t) => <option key={t}>{t}</option>)}
                </select>
              </Row>
            )}
            <Row label="Side">
              <select value={form.side} onChange={(e) => set("side", e.target.value as FormState["side"])} style={inputStyle}>
                {SIDES.map((s) => <option key={s}>{s}</option>)}
              </select>
            </Row>
            <Row label="Qty">
              <input type="number" min={1} value={form.qty} onChange={(e) => set("qty", parseInt(e.target.value || "0", 10))} style={inputStyle} />
            </Row>
            <button onClick={submit} disabled={loading} style={{ ...btnStyle, marginTop: 12, width: "100%" }}>
              {loading ? "…" : "Preview ▶"}
            </button>
          </div>
        </section>

        {/* Right : request + response */}
        <section className="panel">
          <header className="panel-header"><h2 style={{ fontSize: 13 }}>Result</h2></header>
          <div className="panel-body" style={{ padding: 12 }}>
            {error && <div style={{ color: "#e66", marginBottom: 8, fontSize: 12 }}>{error}</div>}

            <div style={subTitleStyle}>Request body</div>
            <pre style={preStyle}>{request ? JSON.stringify(request, null, 2) : "(submit to see)"}</pre>

            {response && (
              <>
                <div style={{ ...subTitleStyle, marginTop: 12 }}>Net greeks</div>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 8, marginBottom: 8 }}>
                  <Stat label="Δ delta" value={response.net_delta.toFixed(4)} />
                  <Stat label="Γ gamma" value={response.net_gamma.toFixed(2)} />
                  <Stat label="V vega" value={response.net_vega.toFixed(4)} />
                  <Stat label="Θ theta/d" value={response.net_theta.toFixed(4)} />
                </div>
                <div style={{ fontSize: 12, color: "#aaa", marginBottom: 12 }}>
                  Total premium: <strong style={{ color: "#fff" }}>{response.total_premium.toFixed(4)}</strong>
                  {response.bootstrap && <span style={{ marginLeft: 12, color: "#cc6" }}>⚠ bootstrap = simulated data</span>}
                </div>

                <div style={subTitleStyle}>Legs ({response.legs.length})</div>
                <table style={tableStyle}>
                  <thead>
                    <tr>
                      <th style={th}>Instrument</th>
                      <th style={th}>Side</th>
                      <th style={th}>Qty</th>
                      <th style={th}>Strike</th>
                      <th style={th}>Tenor</th>
                      <th style={th}>IV</th>
                      <th style={th}>Premium/contract</th>
                    </tr>
                  </thead>
                  <tbody>
                    {response.legs.map((l, i) => (
                      <tr key={i}>
                        <td style={td}>{l.instrument}</td>
                        <td style={td}>{l.side}</td>
                        <td style={td}>{l.qty}</td>
                        <td style={td}>{l.strike?.toFixed(5) ?? "—"}</td>
                        <td style={td}>{l.tenor}</td>
                        <td style={td}>{l.iv != null ? `${(l.iv * 100).toFixed(2)}%` : "—"}</td>
                        <td style={td}>{l.premium_per_contract.toFixed(5)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>

                <details style={{ marginTop: 12 }}>
                  <summary style={{ color: "#aaa", fontSize: 12, cursor: "pointer" }}>Full response JSON</summary>
                  <pre style={preStyle}>{JSON.stringify(response, null, 2)}</pre>
                </details>
              </>
            )}
            {!response && !error && <div style={{ color: "#666", fontSize: 12 }}>(submit to see legs and greeks)</div>}
          </div>
        </section>
      </div>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }): JSX.Element {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "4px 0", gap: 8 }}>
      <span style={{ color: "#aaa", fontSize: 13 }}>{label}</span>
      <div style={{ flex: 1, maxWidth: 180 }}>{children}</div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div style={{ background: "#1a1a1a", border: "1px solid #2a4a6a", borderRadius: 3, padding: "6px 10px" }}>
      <div style={{ color: "#888", fontSize: 11 }}>{label}</div>
      <div style={{ fontWeight: 600, color: "#fff", fontSize: 13, fontFamily: "Consolas, monospace" }}>{value}</div>
    </div>
  );
}

const inputStyle = {
  background: "#1a1a1a",
  color: "#ddd",
  border: "1px solid #333",
  borderRadius: 3,
  padding: "4px 8px",
  fontSize: 13,
  width: "100%",
  boxSizing: "border-box" as const,
};
const btnStyle = {
  padding: "6px 12px",
  background: "#2a4a6a",
  color: "#fff",
  border: "none",
  borderRadius: 3,
  cursor: "pointer",
  fontSize: 13,
};
const subTitleStyle = { color: "#7af", fontSize: 11, textTransform: "uppercase" as const, letterSpacing: 1, marginBottom: 4 };
const preStyle = {
  margin: 0,
  padding: 10,
  background: "#000",
  color: "#cdc",
  fontSize: 12,
  fontFamily: "Consolas, monospace",
  overflow: "auto" as const,
  maxHeight: "30vh",
  whiteSpace: "pre-wrap" as const,
  wordBreak: "break-all" as const,
};
const tableStyle = { borderCollapse: "collapse" as const, fontSize: 12, fontFamily: "Consolas, monospace", width: "100%" };
const th = { padding: "4px 12px", textAlign: "left" as const, color: "#888", borderBottom: "1px solid #333" };
const td = { padding: "3px 12px", borderBottom: "1px solid #222" };
