/**
 * Pricing — exercise les 3 endpoints /api/v1/{price,greeks,iv} de manière
 * isolée. Mode picker + form + bouton Compute → résultat JSON.
 *
 * Layout 2-colonnes : form à gauche, request/response JSON à droite.
 *
 * Sanity check round-trip suggéré (cf. docs/VOL_TRADING_USER_GUIDE.md) :
 *   Price : ATM call 1M σ=10% → ~0.0040, delta ≈ 0.5
 *   IV inversion : passer le price retourné → retrouver σ ≈ 10%
 */
import { useState, type CSSProperties } from "react";

type Mode = "price" | "greeks" | "iv";
type OptionType = "CALL" | "PUT";

const ENDPOINTS: Record<Mode, { path: string; label: string }> = {
  price:  { path: "/api/v1/price",  label: "💵 Price (BS)" },
  greeks: { path: "/api/v1/greeks", label: "Δ Greeks" },
  iv:     { path: "/api/v1/iv",     label: "🔄 IV inversion" },
};

interface FormState {
  spot: number;
  strike: number;
  maturity_days: number;
  option_type: OptionType;
  volatility: number;     // utilisé en mode price/greeks
  market_price: number;   // utilisé en mode iv
}

const DEFAULT_FORM: FormState = {
  spot: 1.17,
  strike: 1.17,
  maturity_days: 30,
  option_type: "CALL",
  volatility: 0.10,
  market_price: 0.0040,
};

export function Pricing(): JSX.Element {
  const [mode, setMode] = useState<Mode>("price");
  const [form, setForm] = useState<FormState>(DEFAULT_FORM);
  const [request, setRequest] = useState<unknown>(null);
  const [response, setResponse] = useState<unknown>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const compute = async () => {
    setLoading(true);
    setError(null);
    setResponse(null);
    const body: Record<string, unknown> = {
      spot: form.spot,
      strike: form.strike,
      maturity_days: form.maturity_days,
      option_type: form.option_type,
    };
    if (mode === "iv") {
      body.market_price = form.market_price;
    } else {
      body.volatility = form.volatility;
    }
    setRequest(body);
    try {
      const r = await fetch(ENDPOINTS[mode].path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const txt = await r.text();
        throw new Error(`HTTP ${r.status}: ${txt.slice(0, 200)}`);
      }
      setResponse(await r.json());
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  const set = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((f) => ({ ...f, [k]: v }));

  return (
    <div style={{ padding: 16 }}>
      {/* Mode picker */}
      <div style={{ display: "flex", gap: 4, marginBottom: 16 }}>
        {(Object.keys(ENDPOINTS) as Mode[]).map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => setMode(m)}
            style={modeBtnStyle(m === mode)}
          >
            {ENDPOINTS[m].label}
          </button>
        ))}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", gap: 16 }}>
        {/* Left : form */}
        <section className="panel">
          <header className="panel-header"><h2 style={{ fontSize: 13 }}>Form — POST {ENDPOINTS[mode].path}</h2></header>
          <div className="panel-body" style={{ padding: 12 }}>
            <Row label="Spot (F)">
              <input type="number" step={0.0001} value={form.spot} onChange={(e) => set("spot", num(e.target.value))} style={inputStyle} />
            </Row>
            <Row label="Strike (K)">
              <input type="number" step={0.0001} value={form.strike} onChange={(e) => set("strike", num(e.target.value))} style={inputStyle} />
            </Row>
            <Row label="Maturity (days)">
              <input type="number" min={1} max={3650} value={form.maturity_days} onChange={(e) => set("maturity_days", parseInt(e.target.value || "0", 10))} style={inputStyle} />
            </Row>
            <Row label="Option type">
              <select value={form.option_type} onChange={(e) => set("option_type", e.target.value as OptionType)} style={inputStyle}>
                <option value="CALL">CALL</option>
                <option value="PUT">PUT</option>
              </select>
            </Row>
            {mode === "iv" ? (
              <Row label="Market price">
                <input type="number" step={0.00001} value={form.market_price} onChange={(e) => set("market_price", num(e.target.value))} style={inputStyle} />
              </Row>
            ) : (
              <Row label="σ (decimal, e.g. 0.10)">
                <input type="number" step={0.001} min={0.001} max={5} value={form.volatility} onChange={(e) => set("volatility", num(e.target.value))} style={inputStyle} />
              </Row>
            )}
            <button onClick={compute} disabled={loading} style={{ ...btnStyle, marginTop: 12, width: "100%" }}>
              {loading ? "…" : "Compute ▶"}
            </button>
          </div>
        </section>

        {/* Right : request + response JSON */}
        <section className="panel">
          <header className="panel-header"><h2 style={{ fontSize: 13 }}>Result</h2></header>
          <div className="panel-body" style={{ padding: 12 }}>
            {error && <div style={{ color: "#e66", marginBottom: 8, fontSize: 12 }}>{error}</div>}
            <div style={{ marginBottom: 8 }}>
              <div style={subTitleStyle}>Request body</div>
              <pre style={preStyle}>{request ? JSON.stringify(request, null, 2) : "(submit to see)"}</pre>
            </div>
            <div>
              <div style={subTitleStyle}>Response</div>
              <pre style={preStyle}>{response ? JSON.stringify(response, null, 2) : "(submit to see)"}</pre>
            </div>
            {mode === "greeks" && response && typeof response === "object"
              ? <GreeksSummary g={response as Record<string, number>} />
              : null}
          </div>
        </section>
      </div>
    </div>
  );
}

function GreeksSummary({ g }: { g: Record<string, number> }): JSX.Element {
  const fields: { key: string; hint?: string }[] = [
    { key: "price",  hint: "BS premium" },
    { key: "delta",  hint: "Spot sensitivity ∈ [-1, 1]" },
    { key: "gamma",  hint: "Δ-of-Δ ; >0 long convexity" },
    { key: "vega",   hint: "Vol sensitivity (per 1pt)" },
    { key: "theta",  hint: "Time decay ($/day)" },
  ];
  return (
    <table style={{ ...tableStyle, marginTop: 12 }}>
      <thead><tr><th style={th}>Field</th><th style={th}>Value</th><th style={th}>Hint</th></tr></thead>
      <tbody>
        {fields.map((f) => {
          const v = g[f.key];
          return (
            <tr key={f.key}>
              <td style={td}><code>{f.key}</code></td>
              <td style={td}>{typeof v === "number" ? v.toFixed(6) : "—"}</td>
              <td style={{ ...td, color: "#888" }}>{f.hint}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
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

function num(s: string): number {
  const n = Number(s);
  return Number.isFinite(n) ? n : 0;
}

function modeBtnStyle(active: boolean): CSSProperties {
  return {
    padding: "6px 14px",
    background: active ? "#2a4a6a" : "transparent",
    color: active ? "#fff" : "#aaa",
    border: "1px solid #333",
    borderRadius: 3,
    cursor: "pointer",
    fontSize: 13,
  };
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
