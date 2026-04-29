/**
 * Order Submit — vrai trading via /api/v1/orders + tables Orders / Positions
 * avec actions Cancel / Close.
 *
 * ⚠ Compte paper IB par défaut (TRADING_MODE=paper). À blinder avant prod
 * (auth, qty cap, confirmation modale plus stricte).
 *
 * Layout :
 *   ┌─ Banner LIVE TRADING + dernière action ──────────────┐
 *   ├─ Form (BUY/SELL FUT/FOP) ─── Submit Result ──────────┤
 *   ├─ Open orders [Refresh]   ─── actions Cancel ─────────┤
 *   └─ Live positions [Refresh] ─── actions Close ─────────┘
 */
import { useEffect, useState } from "react";

type SecType = "FUT" | "FOP";
type Side = "BUY" | "SELL";
type Right = "C" | "P";

interface PlaceOrderForm {
  symbol: string;
  sec_type: SecType;
  side: Side;
  qty: number;
  limit_price: number;
  expiry: string;       // YYYYMMDD
  strike: number;
  right: Right;
  trading_class: string;
}

interface OrderRow {
  order_id: number;
  symbol: string;
  sec_type: string;
  expiry: string | null;
  strike: number | null;
  right: string | null;
  side: string;
  qty: number;
  limit_price: number | null;
  status: string;
  filled: number;
  remaining: number;
  avg_fill_price: number | null;
}

interface PositionRow {
  account: string;
  symbol: string;
  sec_type: string;
  expiry: string | null;
  strike: number | null;
  right: string | null;
  local_symbol: string;
  con_id: number;
  position: number;
  avg_cost: number;
}

const DEFAULT_FORM: PlaceOrderForm = {
  symbol: "EUR",
  sec_type: "FUT",
  side: "BUY",
  qty: 1,
  limit_price: 1.17,
  expiry: "",
  strike: 1.17,
  right: "C",
  trading_class: "",
};

export function OrderSubmit(): JSX.Element {
  const [form, setForm] = useState<PlaceOrderForm>(DEFAULT_FORM);
  const [submitResult, setSubmitResult] = useState<unknown>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const [orders, setOrders] = useState<OrderRow[]>([]);
  const [ordersError, setOrdersError] = useState<string | null>(null);
  const [positions, setPositions] = useState<PositionRow[]>([]);
  const [positionsError, setPositionsError] = useState<string | null>(null);

  const set = <K extends keyof PlaceOrderForm>(k: K, v: PlaceOrderForm[K]) =>
    setForm((f) => ({ ...f, [k]: v }));

  const buildPayload = (): Record<string, unknown> => {
    const body: Record<string, unknown> = {
      symbol: form.symbol,
      sec_type: form.sec_type,
      side: form.side,
      qty: form.qty,
      limit_price: form.limit_price,
      expiry: form.expiry || null,
    };
    if (form.sec_type === "FOP") {
      body.strike = form.strike;
      body.right = form.right;
      if (form.trading_class) body.trading_class = form.trading_class;
    }
    return body;
  };

  const submit = async () => {
    if (!confirm(`Vraiment ${form.side} ${form.qty} ${form.sec_type} ${form.symbol} @ ${form.limit_price} ?`)) return;
    setSubmitting(true);
    setSubmitError(null);
    setSubmitResult(null);
    try {
      const r = await fetch("/api/v1/orders", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(buildPayload()),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${JSON.stringify(j)}`);
      setSubmitResult(j);
      void refreshOrders();
      void refreshPositions();
    } catch (e) {
      setSubmitError(String(e));
    } finally {
      setSubmitting(false);
    }
  };

  const refreshOrders = async () => {
    setOrdersError(null);
    try {
      const r = await fetch("/api/v1/orders");
      const j = await r.json();
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${JSON.stringify(j)}`);
      setOrders(j.orders);
    } catch (e) {
      setOrdersError(String(e));
    }
  };

  const refreshPositions = async () => {
    setPositionsError(null);
    try {
      const r = await fetch("/api/v1/exec/positions");
      const j = await r.json();
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${JSON.stringify(j)}`);
      setPositions(j.positions);
    } catch (e) {
      setPositionsError(String(e));
    }
  };

  const cancelOrder = async (id: number) => {
    if (!confirm(`Cancel order ${id} ?`)) return;
    try {
      const r = await fetch(`/api/v1/orders/${id}`, { method: "DELETE" });
      const j = await r.json();
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${JSON.stringify(j)}`);
      void refreshOrders();
    } catch (e) {
      alert(`Cancel failed: ${e}`);
    }
  };

  const closePosition = async (p: PositionRow) => {
    const lp = window.prompt(`Limit price for closing ${p.local_symbol} (qty ${p.position}) ?`, "1.17");
    if (!lp) return;
    const limit_price = Number(lp);
    if (!Number.isFinite(limit_price) || limit_price <= 0) {
      alert("Invalid limit price");
      return;
    }
    if (!confirm(`Close ${p.local_symbol} qty ${p.position} @ ${limit_price} ?`)) return;
    try {
      const r = await fetch(`/api/v1/exec/positions/${p.con_id}/close`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ limit_price }),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${JSON.stringify(j)}`);
      void refreshOrders();
      void refreshPositions();
    } catch (e) {
      alert(`Close failed: ${e}`);
    }
  };

  useEffect(() => {
    void refreshOrders();
    void refreshPositions();
  }, []);

  return (
    <div style={{ padding: 16 }}>
      <div style={liveBannerStyle}>
        🔴 <strong>LIVE TRADING</strong> — chaque submit envoie un vrai ordre via IB Gateway
        (account paper si <code>TRADING_MODE=paper</code>). Cancel / close = idem.
      </div>

      {/* Form + Submit Result */}
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", gap: 16, marginBottom: 16 }}>
        <section className="panel">
          <header className="panel-header"><h2 style={{ fontSize: 13 }}>Place order — POST /api/v1/orders</h2></header>
          <div className="panel-body" style={{ padding: 12 }}>
            <Row label="Sec type">
              <select value={form.sec_type} onChange={(e) => set("sec_type", e.target.value as SecType)} style={inputStyle}>
                <option>FUT</option><option>FOP</option>
              </select>
            </Row>
            <Row label="Symbol"><input value={form.symbol} onChange={(e) => set("symbol", e.target.value.toUpperCase())} style={inputStyle} /></Row>
            <Row label="Expiry (YYYYMMDD)"><input value={form.expiry} onChange={(e) => set("expiry", e.target.value)} placeholder="20260619" style={inputStyle} /></Row>
            {form.sec_type === "FOP" && (
              <>
                <Row label="Strike"><input type="number" step={0.0001} value={form.strike} onChange={(e) => set("strike", Number(e.target.value) || 0)} style={inputStyle} /></Row>
                <Row label="Right">
                  <select value={form.right} onChange={(e) => set("right", e.target.value as Right)} style={inputStyle}>
                    <option value="C">C (CALL)</option><option value="P">P (PUT)</option>
                  </select>
                </Row>
                <Row label="Trading class"><input value={form.trading_class} onChange={(e) => set("trading_class", e.target.value)} placeholder="EUU" style={inputStyle} /></Row>
              </>
            )}
            <Row label="Side">
              <select value={form.side} onChange={(e) => set("side", e.target.value as Side)} style={inputStyle}>
                <option>BUY</option><option>SELL</option>
              </select>
            </Row>
            <Row label="Qty"><input type="number" min={1} value={form.qty} onChange={(e) => set("qty", parseInt(e.target.value || "0", 10))} style={inputStyle} /></Row>
            <Row label="Limit price"><input type="number" step={0.00001} value={form.limit_price} onChange={(e) => set("limit_price", Number(e.target.value) || 0)} style={inputStyle} /></Row>
            <button onClick={submit} disabled={submitting} style={{ ...submitBtnStyle, marginTop: 12, width: "100%" }}>
              {submitting ? "…" : "🔴 Submit ▶"}
            </button>
          </div>
        </section>

        <section className="panel">
          <header className="panel-header"><h2 style={{ fontSize: 13 }}>Last submit result</h2></header>
          <div className="panel-body" style={{ padding: 12 }}>
            {submitError && <div style={{ color: "#e66", marginBottom: 8 }}>{submitError}</div>}
            {submitResult ? (
              <pre style={preStyle}>{JSON.stringify(submitResult, null, 2)}</pre>
            ) : !submitError ? (
              <div style={{ color: "#666", fontSize: 12 }}>(submit pour voir le Trade IB renvoyé)</div>
            ) : null}
          </div>
        </section>
      </div>

      {/* Open orders table */}
      <section className="panel" style={{ marginBottom: 16 }}>
        <header className="panel-header" style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <h2 style={{ fontSize: 13, flex: 1 }}>Open orders ({orders.length}) — GET /api/v1/orders</h2>
          <button onClick={refreshOrders} style={btnStyle}>Refresh</button>
        </header>
        <div className="panel-body" style={{ padding: 12 }}>
          {ordersError && <div style={{ color: "#e66", marginBottom: 8 }}>{ordersError}</div>}
          {!ordersError && orders.length === 0 && <div style={{ color: "#666", fontSize: 12 }}>(no open orders)</div>}
          {orders.length > 0 && (
            <table style={tableStyle}>
              <thead>
                <tr>
                  <th style={th}>ID</th><th style={th}>Symbol</th><th style={th}>Type</th>
                  <th style={th}>Side</th><th style={th}>Qty</th><th style={th}>Limit</th>
                  <th style={th}>Status</th><th style={th}>Filled</th><th style={th}>Action</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((o) => (
                  <tr key={o.order_id} style={{ borderTop: "1px solid #222" }}>
                    <td style={td}>{o.order_id}</td>
                    <td style={td}>{o.symbol} {o.expiry ?? ""} {o.strike ?? ""} {o.right ?? ""}</td>
                    <td style={td}>{o.sec_type}</td>
                    <td style={td}>{o.side}</td>
                    <td style={td}>{o.qty}</td>
                    <td style={td}>{o.limit_price?.toFixed(5) ?? "—"}</td>
                    <td style={td}>{o.status}</td>
                    <td style={td}>{o.filled} / {o.qty}</td>
                    <td style={td}><button onClick={() => cancelOrder(o.order_id)} style={dangerBtnStyle}>Cancel</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>

      {/* Live positions table */}
      <section className="panel">
        <header className="panel-header" style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <h2 style={{ fontSize: 13, flex: 1 }}>Live positions ({positions.length}) — GET /api/v1/exec/positions</h2>
          <button onClick={refreshPositions} style={btnStyle}>Refresh</button>
        </header>
        <div className="panel-body" style={{ padding: 12 }}>
          {positionsError && <div style={{ color: "#e66", marginBottom: 8 }}>{positionsError}</div>}
          {!positionsError && positions.length === 0 && <div style={{ color: "#666", fontSize: 12 }}>(no live positions)</div>}
          {positions.length > 0 && (
            <table style={tableStyle}>
              <thead>
                <tr>
                  <th style={th}>conId</th><th style={th}>Local symbol</th>
                  <th style={th}>Position</th><th style={th}>Avg cost</th><th style={th}>Action</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p) => (
                  <tr key={p.con_id} style={{ borderTop: "1px solid #222" }}>
                    <td style={td}>{p.con_id}</td>
                    <td style={td}>{p.local_symbol}</td>
                    <td style={{ ...td, color: p.position > 0 ? "#6c6" : "#e66" }}>{p.position}</td>
                    <td style={td}>{p.avg_cost.toFixed(5)}</td>
                    <td style={td}><button onClick={() => closePosition(p)} style={dangerBtnStyle}>Close</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>
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

const liveBannerStyle = {
  background: "#3a0000",
  border: "1px solid #e66",
  color: "#e66",
  padding: "8px 12px",
  borderRadius: 4,
  fontSize: 13,
  marginBottom: 12,
};
const inputStyle = {
  background: "#1a1a1a", color: "#ddd", border: "1px solid #333", borderRadius: 3,
  padding: "4px 8px", fontSize: 13, width: "100%", boxSizing: "border-box" as const,
};
const btnStyle = {
  padding: "4px 12px", background: "#2a4a6a", color: "#fff", border: "none",
  borderRadius: 3, cursor: "pointer", fontSize: 12,
};
const submitBtnStyle = {
  padding: "8px 12px", background: "#7a1a1a", color: "#fff", border: "none",
  borderRadius: 3, cursor: "pointer", fontSize: 13, fontWeight: 600,
};
const dangerBtnStyle = {
  padding: "2px 8px", background: "#5a1a1a", color: "#fff", border: "1px solid #7a3a3a",
  borderRadius: 3, cursor: "pointer", fontSize: 11,
};
const tableStyle = { borderCollapse: "collapse" as const, fontSize: 12, fontFamily: "Consolas, monospace", width: "100%" };
const th = { padding: "4px 12px", textAlign: "left" as const, color: "#888", borderBottom: "1px solid #333" };
const td = { padding: "3px 12px", borderBottom: "1px solid #222", whiteSpace: "nowrap" as const };
const preStyle = {
  margin: 0, padding: 10, background: "#000", color: "#cdc", fontSize: 12,
  fontFamily: "Consolas, monospace", overflow: "auto" as const, maxHeight: "40vh",
  whiteSpace: "pre-wrap" as const, wordBreak: "break-all" as const,
};
