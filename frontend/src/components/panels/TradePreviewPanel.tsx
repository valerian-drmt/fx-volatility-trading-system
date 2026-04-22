import { useState } from "react";
import { fetchTradePreview, type TradePreviewResponse } from "../../api/cockpit";

const STRUCTURES = ["StraddleATM", "RiskReversal25d", "Butterfly25d", "CalendarSpread"] as const;
const TENORS = ["1M", "2M", "3M", "4M", "5M", "6M"] as const;
const SIDES = ["BUY", "SELL"] as const;

export function TradePreviewPanel(): JSX.Element {
  const [structure, setStructure] = useState<(typeof STRUCTURES)[number]>("StraddleATM");
  const [tenor, setTenor] = useState<(typeof TENORS)[number]>("3M");
  const [tenorFar, setTenorFar] = useState<(typeof TENORS)[number]>("6M");
  const [side, setSide] = useState<(typeof SIDES)[number]>("BUY");
  const [qty, setQty] = useState(10);
  const [result, setResult] = useState<TradePreviewResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const preview = async () => {
    setError(null);
    try {
      const body: {
        structure: string;
        tenor: string;
        side?: string;
        qty?: number;
        tenor_far?: string;
      } = { structure, tenor, side, qty };
      if (structure === "CalendarSpread") body.tenor_far = tenorFar;
      const r = await fetchTradePreview(body);
      setResult(r);
    } catch (e) {
      setError(String(e));
      setResult(null);
    }
  };

  return (
    <section className="panel trade-preview-panel" data-testid="trade-preview-panel">
      <header className="panel-header"><h2>Trade Preview</h2></header>
      <div className="panel-body">
        <div className="ticket-row">
          <label>Structure
            <select className="panel-select" value={structure} onChange={(e) => setStructure(e.target.value as typeof STRUCTURES[number])}>
              {STRUCTURES.map((s) => <option key={s}>{s}</option>)}
            </select>
          </label>
          <label>Tenor
            <select className="panel-select" value={tenor} onChange={(e) => setTenor(e.target.value as typeof TENORS[number])}>
              {TENORS.map((t) => <option key={t}>{t}</option>)}
            </select>
          </label>
          {structure === "CalendarSpread" && (
            <label>Far tenor
              <select className="panel-select" value={tenorFar} onChange={(e) => setTenorFar(e.target.value as typeof TENORS[number])}>
                {TENORS.map((t) => <option key={t}>{t}</option>)}
              </select>
            </label>
          )}
          <label>Side
            <select className="panel-select" value={side} onChange={(e) => setSide(e.target.value as typeof SIDES[number])}>
              {SIDES.map((s) => <option key={s}>{s}</option>)}
            </select>
          </label>
          <label>Qty
            <input type="number" min={1} value={qty} onChange={(e) => setQty(Number(e.target.value))} />
          </label>
        </div>
        <button type="button" className="ticket-submit" onClick={preview}>Preview</button>
        {error && <div className="panel-error">{error}</div>}
        {result && (
          <>
            <div style={{ margin: "10px 0 4px", fontWeight: 600, fontSize: 12 }}>Legs</div>
            <table className="smile-table" style={{ width: "100%" }}>
              <thead><tr><th>Instrument</th><th>Side</th><th>Qty</th><th>Strike</th><th>Tenor</th><th>IV</th><th>Premium</th></tr></thead>
              <tbody>
                {result.legs.map((l, i) => (
                  <tr key={i}>
                    <td>{l.instrument}</td>
                    <td>{l.side}</td>
                    <td>{l.qty}</td>
                    <td>{l.strike ?? "—"}</td>
                    <td>{l.tenor}</td>
                    <td>{l.iv != null ? (l.iv * 100).toFixed(2) + "%" : "—"}</td>
                    <td>{l.premium_per_contract.toFixed(4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div style={{ marginTop: 10, display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 6 }}>
              <Stat label="Vega" value={result.net_vega.toFixed(0)} />
              <Stat label="Gamma" value={result.net_gamma.toFixed(1)} />
              <Stat label="Theta/day" value={result.net_theta.toFixed(1)} />
              <Stat label="Delta" value={result.net_delta.toFixed(2)} />
            </div>
            <div style={{ marginTop: 8, fontSize: 11 }}>
              Total premium: <strong>{result.total_premium.toFixed(2)}</strong>
            </div>
          </>
        )}
      </div>
    </section>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ border: "1px solid var(--border)", padding: "4px 6px", borderRadius: 3, fontSize: 11 }}>
      <div style={{ color: "var(--muted)" }}>{label}</div>
      <div style={{ fontWeight: 600 }}>{value}</div>
    </div>
  );
}
