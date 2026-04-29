/**
 * Order Submit — 3 modes de tickets (Futures / Options / Multi-leg) en
 * **dry-run only** : aucune requête sortante, le payload est validé puis
 * un order_id mock est retourné côté frontend.
 *
 * Le but est de valider :
 *   - les forms se remplissent correctement
 *   - les payloads JSON respectent ce qu'attendrait POST /api/v1/orders
 *   - on peut prévisualiser sans toucher IB
 *
 * Le vrai endpoint /api/v1/orders viendra dans une PR séparée avec
 * confirmation explicite + intégration tests IB. Pour l'instant ce tab
 * est purement client-side.
 */
import { useState } from "react";

type Mode = "futures" | "options" | "multi";

type Side = "BUY" | "SELL";
type Right = "CALL" | "PUT";

const TENORS = ["1M", "2M", "3M", "4M", "5M", "6M"] as const;
const STRUCTURES = ["StraddleATM", "RiskReversal25d", "Butterfly25d", "CalendarSpread"] as const;

const MODES: Record<Mode, string> = {
  futures: "📊 Futures",
  options: "📉 Options vanilla",
  multi:   "🔗 Multi-leg",
};

interface FuturesForm {
  symbol: string;
  side: Side;
  qty: number;
  limit_price: number;
}

interface OptionsForm {
  symbol: string;
  right: Right;
  strike: number;
  tenor: (typeof TENORS)[number];
  side: Side;
  qty: number;
  limit_price: number;
}

interface MultiForm {
  structure: (typeof STRUCTURES)[number];
  tenor: (typeof TENORS)[number];
  side: Side;
  qty: number;
}

const DEFAULTS = {
  futures: { symbol: "6E", side: "BUY", qty: 1, limit_price: 1.17 } as FuturesForm,
  options: { symbol: "EUU", right: "CALL", strike: 1.17, tenor: "3M", side: "BUY", qty: 1, limit_price: 0.0040 } as OptionsForm,
  multi:   { structure: "StraddleATM", tenor: "3M", side: "BUY", qty: 10 } as MultiForm,
};

interface SubmitResult {
  order_id: string;
  payload: unknown;
  validated_at: string;
  warnings: string[];
}

export function OrderSubmit(): JSX.Element {
  const [mode, setMode] = useState<Mode>("futures");
  const [futures, setFutures] = useState<FuturesForm>(DEFAULTS.futures);
  const [options, setOptions] = useState<OptionsForm>(DEFAULTS.options);
  const [multi, setMulti] = useState<MultiForm>(DEFAULTS.multi);
  const [result, setResult] = useState<SubmitResult | null>(null);

  const submit = () => {
    let payload: unknown;
    const warnings: string[] = [];
    if (mode === "futures") {
      payload = { type: "FUT", ...futures };
      if (futures.qty <= 0) warnings.push("qty <= 0");
      if (futures.limit_price <= 0) warnings.push("limit_price <= 0");
    } else if (mode === "options") {
      payload = { type: "OPT", ...options };
      if (options.strike <= 0) warnings.push("strike <= 0");
      if (options.qty <= 0) warnings.push("qty <= 0");
    } else {
      payload = { type: "MULTI_LEG", ...multi };
      if (multi.qty <= 0) warnings.push("qty <= 0");
    }
    setResult({
      order_id: `MOCK-${Date.now().toString(36).toUpperCase()}`,
      payload,
      validated_at: new Date().toISOString(),
      warnings,
    });
  };

  const reset = () => setResult(null);

  return (
    <div style={{ padding: 16 }}>
      {/* Banner d'avertissement */}
      <div style={{
        background: "#3a2a00", border: "1px solid #cc6", color: "#cc6",
        padding: "8px 12px", borderRadius: 4, fontSize: 13, marginBottom: 12,
      }}>
        ⚠ <strong>DRY-RUN ONLY</strong> — ce tab ne fait aucune requête sortante.
        Le payload est validé localement puis un <code>order_id</code> mock est retourné.
        Le vrai endpoint <code>POST /api/v1/orders</code> arrivera dans une PR séparée
        avec confirmation explicite + tests IB.
      </div>

      {/* Mode picker */}
      <div style={{ display: "flex", gap: 4, marginBottom: 16 }}>
        {(Object.keys(MODES) as Mode[]).map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => { setMode(m); reset(); }}
            style={{
              padding: "6px 14px",
              background: m === mode ? "#2a4a6a" : "transparent",
              color: m === mode ? "#fff" : "#aaa",
              border: "1px solid #333",
              borderRadius: 3,
              cursor: "pointer",
              fontSize: 13,
            }}
          >
            {MODES[m]}
          </button>
        ))}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", gap: 16 }}>
        {/* Form */}
        <section className="panel">
          <header className="panel-header"><h2 style={{ fontSize: 13 }}>Form — {MODES[mode]}</h2></header>
          <div className="panel-body" style={{ padding: 12 }}>
            {mode === "futures" && <FuturesFormView f={futures} set={setFutures} />}
            {mode === "options" && <OptionsFormView f={options} set={setOptions} />}
            {mode === "multi" && <MultiFormView f={multi} set={setMulti} />}
            <button onClick={submit} style={{ ...btnStyle, marginTop: 12, width: "100%" }}>
              Validate (dry-run) ▶
            </button>
          </div>
        </section>

        {/* Result */}
        <section className="panel">
          <header className="panel-header"><h2 style={{ fontSize: 13 }}>Result (mock)</h2></header>
          <div className="panel-body" style={{ padding: 12 }}>
            {result ? (
              <>
                <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 10 }}>
                  <span style={{ color: "#7af", fontSize: 11, textTransform: "uppercase", letterSpacing: 1 }}>
                    Order ID
                  </span>
                  <code style={{ fontSize: 14, color: "#fff" }}>{result.order_id}</code>
                </div>
                <div style={{ fontSize: 12, color: "#aaa", marginBottom: 8 }}>
                  validated at {result.validated_at}
                </div>
                {result.warnings.length > 0 && (
                  <div style={{ background: "#3a2a00", color: "#cc6", padding: "6px 10px", borderRadius: 3, fontSize: 12, marginBottom: 8 }}>
                    ⚠ {result.warnings.length} warning(s) :{" "}
                    {result.warnings.join(", ")}
                  </div>
                )}
                <div style={subTitleStyle}>Payload (would be POST'd to /api/v1/orders)</div>
                <pre style={preStyle}>{JSON.stringify(result.payload, null, 2)}</pre>
              </>
            ) : (
              <div style={{ color: "#666", fontSize: 12 }}>(submit pour voir le payload mock)</div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}

function FuturesFormView({ f, set }: { f: FuturesForm; set: (v: FuturesForm) => void }): JSX.Element {
  return (
    <>
      <Row label="Symbol"><input value={f.symbol} onChange={(e) => set({ ...f, symbol: e.target.value.toUpperCase() })} style={inputStyle} /></Row>
      <Row label="Side">
        <select value={f.side} onChange={(e) => set({ ...f, side: e.target.value as Side })} style={inputStyle}>
          <option>BUY</option><option>SELL</option>
        </select>
      </Row>
      <Row label="Qty"><input type="number" min={1} value={f.qty} onChange={(e) => set({ ...f, qty: parseInt(e.target.value || "0", 10) })} style={inputStyle} /></Row>
      <Row label="Limit price"><input type="number" step={0.0001} value={f.limit_price} onChange={(e) => set({ ...f, limit_price: Number(e.target.value) || 0 })} style={inputStyle} /></Row>
    </>
  );
}

function OptionsFormView({ f, set }: { f: OptionsForm; set: (v: OptionsForm) => void }): JSX.Element {
  return (
    <>
      <Row label="Symbol"><input value={f.symbol} onChange={(e) => set({ ...f, symbol: e.target.value.toUpperCase() })} style={inputStyle} /></Row>
      <Row label="Right">
        <select value={f.right} onChange={(e) => set({ ...f, right: e.target.value as Right })} style={inputStyle}>
          <option>CALL</option><option>PUT</option>
        </select>
      </Row>
      <Row label="Strike"><input type="number" step={0.0001} value={f.strike} onChange={(e) => set({ ...f, strike: Number(e.target.value) || 0 })} style={inputStyle} /></Row>
      <Row label="Tenor">
        <select value={f.tenor} onChange={(e) => set({ ...f, tenor: e.target.value as OptionsForm["tenor"] })} style={inputStyle}>
          {TENORS.map((t) => <option key={t}>{t}</option>)}
        </select>
      </Row>
      <Row label="Side">
        <select value={f.side} onChange={(e) => set({ ...f, side: e.target.value as Side })} style={inputStyle}>
          <option>BUY</option><option>SELL</option>
        </select>
      </Row>
      <Row label="Qty"><input type="number" min={1} value={f.qty} onChange={(e) => set({ ...f, qty: parseInt(e.target.value || "0", 10) })} style={inputStyle} /></Row>
      <Row label="Limit price"><input type="number" step={0.00001} value={f.limit_price} onChange={(e) => set({ ...f, limit_price: Number(e.target.value) || 0 })} style={inputStyle} /></Row>
    </>
  );
}

function MultiFormView({ f, set }: { f: MultiForm; set: (v: MultiForm) => void }): JSX.Element {
  return (
    <>
      <Row label="Structure">
        <select value={f.structure} onChange={(e) => set({ ...f, structure: e.target.value as MultiForm["structure"] })} style={inputStyle}>
          {STRUCTURES.map((s) => <option key={s}>{s}</option>)}
        </select>
      </Row>
      <Row label="Tenor">
        <select value={f.tenor} onChange={(e) => set({ ...f, tenor: e.target.value as MultiForm["tenor"] })} style={inputStyle}>
          {TENORS.map((t) => <option key={t}>{t}</option>)}
        </select>
      </Row>
      <Row label="Side">
        <select value={f.side} onChange={(e) => set({ ...f, side: e.target.value as Side })} style={inputStyle}>
          <option>BUY</option><option>SELL</option>
        </select>
      </Row>
      <Row label="Qty"><input type="number" min={1} value={f.qty} onChange={(e) => set({ ...f, qty: parseInt(e.target.value || "0", 10) })} style={inputStyle} /></Row>
      <div style={{ fontSize: 11, color: "#888", marginTop: 8 }}>
        Note : un multi-leg ne crée pas N orders ; il pousse un seul payload type-strategy
        au backend qui décompose en legs (cf. /vol/trade-preview pour la logique).
      </div>
    </>
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
  maxHeight: "40vh",
  whiteSpace: "pre-wrap" as const,
  wordBreak: "break-all" as const,
};
