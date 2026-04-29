/**
 * Order Submit — 3 panels avec payloads **figés** (objectif : valider la
 * propagation aux 4 tables, pas paramétrer un trade).
 *
 *   Panel 1 — Futures   : 1 FUT EUR loin du marché (BUY @ 1.10)
 *   Panel 2 — Option    : 1 FOP EUU CALL strike 1.18 loin (BUY @ 0.001)
 *   Panel 3 — Complex   : Straddle ATM = 2 orders (CALL + PUT même strike)
 *
 * Toutes les valeurs sont hardcodées → click Send → POST /api/v1/orders.
 * Les ordres sont volontairement loin du marché ⇒ ne fillent pas, restent
 * pending, observables dans les tables.
 *
 * 4 tables affichées en dessous (orders / trades / positions / snapshots
 * via /api/v1/dev/tables/*) avec refresh + actions Cancel/Close.
 */
import { useEffect, useState } from "react";

// Expiries connues OK avec IB paper (cf. vol-engine smoke logs) :
//   FUT EUR  → 20260615 (June 2026, 3rd Wed minus 2 days)
//   FOP EUU  → 20260605 (June 2026 monthly options, 1st Fri)
const FUT_EXPIRY = "20260615";
const FOP_EXPIRY = "20260605";
const TRADING_CLASS_FOP = "EUU";

const PRESETS = {
  futures: {
    label: "📊 Futures",
    desc: "1 FUT EUR (June 2026), BUY @ 1.10 — loin du marché, ne fillera pas",
    payloads: [{
      symbol: "EUR", sec_type: "FUT", side: "BUY", qty: 1, limit_price: 1.10,
      expiry: FUT_EXPIRY,
    }],
  },
  option: {
    label: "📉 Option simple",
    desc: "1 FOP EUU CALL strike 1.18, BUY @ 0.001 — loin du marché",
    payloads: [{
      symbol: "EUR", sec_type: "FOP", side: "BUY", qty: 1, limit_price: 0.001,
      expiry: FOP_EXPIRY, strike: 1.18, right: "C", trading_class: TRADING_CLASS_FOP,
    }],
  },
} as const;

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
}
interface PositionRow {
  con_id: number;
  local_symbol: string;
  position: number;
  avg_cost: number;
}
interface TableRow { [k: string]: unknown }

export function OrderSubmit(): JSX.Element {
  const [results, setResults] = useState<Record<string, unknown>>({});
  const [errors, setErrors] = useState<Record<string, string | null>>({});
  const [submitting, setSubmitting] = useState<Record<string, boolean>>({});

  const [orders, setOrders] = useState<OrderRow[]>([]);
  const [positions, setPositions] = useState<PositionRow[]>([]);
  const [trades, setTrades] = useState<TableRow[]>([]);
  const [snapshots, setSnapshots] = useState<TableRow[]>([]);
  const [events, setEvents] = useState<TableRow[]>([]);

  const sendPreset = async (key: keyof typeof PRESETS) => {
    if (!confirm(`Envoyer ${PRESETS[key].payloads.length} ordre(s) — ${PRESETS[key].label} ?`)) return;
    setSubmitting((s) => ({ ...s, [key]: true }));
    setErrors((e) => ({ ...e, [key]: null }));
    const responses: unknown[] = [];
    try {
      for (const payload of PRESETS[key].payloads) {
        const r = await fetch("/api/v1/orders", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const j = await r.json();
        if (!r.ok) throw new Error(`HTTP ${r.status}: ${JSON.stringify(j)}`);
        responses.push(j);
      }
      setResults((rs) => ({ ...rs, [key]: responses }));
      void refreshAll();
    } catch (e) {
      setErrors((er) => ({ ...er, [key]: String(e) }));
    } finally {
      setSubmitting((s) => ({ ...s, [key]: false }));
    }
  };

  const fetchTable = async <T,>(url: string): Promise<T[]> => {
    const r = await fetch(url);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const j = await r.json();
    return (j.rows ?? j.orders ?? j.positions ?? []) as T[];
  };

  const refreshOrders = async () => {
    try { setOrders(await fetchTable<OrderRow>("/api/v1/orders")); }
    catch (e) { console.error(e); }
  };
  const refreshPositions = async () => {
    try { setPositions(await fetchTable<PositionRow>("/api/v1/exec/positions")); }
    catch (e) { console.error(e); }
  };
  const refreshTrades = async () => {
    try { setTrades(await fetchTable<TableRow>("/api/v1/dev/tables/trades?limit=20")); }
    catch (e) { console.error(e); }
  };
  const refreshSnapshots = async () => {
    try { setSnapshots(await fetchTable<TableRow>("/api/v1/dev/tables/position_snapshots?limit=20")); }
    catch (e) { console.error(e); }
  };
  const refreshEvents = async () => {
    try { setEvents(await fetchTable<TableRow>("/api/v1/dev/tables/order_events?limit=20")); }
    catch (e) { console.error(e); }
  };
  const refreshAll = async () => {
    await Promise.all([refreshEvents(), refreshOrders(), refreshPositions(), refreshTrades(), refreshSnapshots()]);
  };

  const cancelOrder = async (id: number) => {
    if (!confirm(`Cancel order ${id} ?`)) return;
    try {
      const r = await fetch(`/api/v1/orders/${id}`, { method: "DELETE" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await refreshOrders();
    } catch (e) { alert(`Cancel failed: ${e}`); }
  };

  const closePosition = async (p: PositionRow) => {
    const lp = window.prompt(`Limit price pour close ${p.local_symbol} (qty ${p.position}) ?`, "1.17");
    if (!lp) return;
    const limit_price = Number(lp);
    if (!Number.isFinite(limit_price) || limit_price <= 0) { alert("Invalid"); return; }
    if (!confirm(`Close ${p.local_symbol} qty ${p.position} @ ${limit_price} ?`)) return;
    try {
      const r = await fetch(`/api/v1/exec/positions/${p.con_id}/close`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ limit_price }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await refreshAll();
    } catch (e) { alert(`Close failed: ${e}`); }
  };

  useEffect(() => { void refreshAll(); }, []);

  return (
    <div style={{ padding: 16 }}>
      <div style={liveBannerStyle}>
        🔴 <strong>LIVE TRADING</strong> — chaque bouton envoie un vrai ordre via IB Gateway
        (account paper si <code>TRADING_MODE=paper</code>). Les payloads sont figés et loin du
        marché → ils restent pending et observables dans les 4 tables.
      </div>

      {/* 3 panels figés */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 12, marginBottom: 16 }}>
        {(Object.keys(PRESETS) as (keyof typeof PRESETS)[]).map((key) => (
          <PresetPanel
            key={key}
            id={key}
            label={PRESETS[key].label}
            desc={PRESETS[key].desc}
            payloads={PRESETS[key].payloads}
            onSend={() => sendPreset(key)}
            submitting={!!submitting[key]}
            error={errors[key] ?? null}
            result={results[key]}
          />
        ))}
      </div>

      {/* 5 tables : order_events / orders / trades / positions / snapshots */}
      <TableSection title={`Order events (${events.length}) — audit log user → IB`} onRefresh={refreshEvents}>
        <GenericTable rows={events} cols={["id", "timestamp", "action_type", "order_id", "success", "error_message", "request_payload"]} />
      </TableSection>

      <TableSection title={`Open orders (${orders.length}) — GET /api/v1/orders`} onRefresh={refreshOrders}>
        {orders.length === 0 ? <Empty /> : (
          <table style={tableStyle}>
            <thead>
              <tr><th style={th}>ID</th><th style={th}>Symbol</th><th style={th}>Type</th><th style={th}>Side</th>
                  <th style={th}>Qty</th><th style={th}>Limit</th><th style={th}>Status</th><th style={th}>Filled</th><th style={th}>Action</th></tr>
            </thead>
            <tbody>
              {orders.map((o) => (
                <tr key={o.order_id}>
                  <td style={td}>{o.order_id}</td>
                  <td style={td}>{o.symbol} {o.expiry ?? ""} {o.strike ?? ""} {o.right ?? ""}</td>
                  <td style={td}>{o.sec_type}</td><td style={td}>{o.side}</td>
                  <td style={td}>{o.qty}</td>
                  <td style={td}>{o.limit_price?.toFixed(5) ?? "—"}</td>
                  <td style={td}>{o.status}</td>
                  <td style={td}>{o.filled}/{o.qty}</td>
                  <td style={td}><button onClick={() => cancelOrder(o.order_id)} style={dangerBtn}>Cancel</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </TableSection>

      <TableSection title={`Trades (${trades.length}) — GET /api/v1/dev/tables/trades`} onRefresh={refreshTrades}>
        <GenericTable rows={trades} cols={["id", "position_id", "ib_order_id", "side", "quantity", "price", "commission", "timestamp"]} />
      </TableSection>

      <TableSection title={`Live positions (${positions.length}) — GET /api/v1/exec/positions`} onRefresh={refreshPositions}>
        {positions.length === 0 ? <Empty /> : (
          <table style={tableStyle}>
            <thead><tr><th style={th}>conId</th><th style={th}>Local symbol</th><th style={th}>Position</th><th style={th}>Avg cost</th><th style={th}>Action</th></tr></thead>
            <tbody>
              {positions.map((p) => (
                <tr key={p.con_id}>
                  <td style={td}>{p.con_id}</td><td style={td}>{p.local_symbol}</td>
                  <td style={{ ...td, color: p.position > 0 ? "#6c6" : "#e66" }}>{p.position}</td>
                  <td style={td}>{p.avg_cost.toFixed(5)}</td>
                  <td style={td}><button onClick={() => closePosition(p)} style={dangerBtn}>Close</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </TableSection>

      <TableSection title={`Position snapshots (${snapshots.length}) — GET /api/v1/dev/tables/position_snapshots`} onRefresh={refreshSnapshots}>
        <GenericTable rows={snapshots} cols={["id", "position_id", "timestamp", "spot", "iv", "delta_usd", "gamma_usd", "vega_usd", "theta_usd", "pnl_usd"]} />
      </TableSection>
    </div>
  );
}

function PresetPanel({
  label, desc, payloads, onSend, submitting, error, result,
}: {
  id: string; label: string; desc: string; payloads: readonly object[];
  onSend: () => void; submitting: boolean; error: string | null; result: unknown;
}): JSX.Element {
  return (
    <section className="panel">
      <header className="panel-header"><h2 style={{ fontSize: 13 }}>{label}</h2></header>
      <div className="panel-body" style={{ padding: 12 }}>
        <div style={{ fontSize: 12, color: "#aaa", marginBottom: 8 }}>{desc}</div>
        <pre style={{ ...preStyle, maxHeight: 120 }}>{JSON.stringify(payloads, null, 2)}</pre>
        <button onClick={onSend} disabled={submitting} style={{ ...submitBtn, marginTop: 8, width: "100%" }}>
          {submitting ? "…" : "🔴 Send ▶"}
        </button>
        {error && <div style={{ color: "#e66", fontSize: 12, marginTop: 6, wordBreak: "break-all" }}>{error}</div>}
        {!!result && (
          <details style={{ marginTop: 6 }}>
            <summary style={{ color: "#aaa", fontSize: 11, cursor: "pointer" }}>Last response</summary>
            <pre style={{ ...preStyle, maxHeight: 200 }}>{JSON.stringify(result, null, 2)}</pre>
          </details>
        )}
      </div>
    </section>
  );
}

function TableSection({
  title, onRefresh, children,
}: { title: string; onRefresh: () => void | Promise<void>; children: React.ReactNode }): JSX.Element {
  return (
    <section className="panel" style={{ marginBottom: 12 }}>
      <header className="panel-header" style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <h2 style={{ fontSize: 13, flex: 1 }}>{title}</h2>
        <button onClick={() => void onRefresh()} style={btnSmall}>Refresh</button>
      </header>
      <div className="panel-body" style={{ padding: 12 }}>{children}</div>
    </section>
  );
}

function GenericTable({ rows, cols }: { rows: TableRow[]; cols: string[] }): JSX.Element {
  if (rows.length === 0) return <Empty />;
  return (
    <div style={{ overflow: "auto", maxHeight: 250 }}>
      <table style={tableStyle}>
        <thead><tr>{cols.map((c) => <th key={c} style={th}>{c}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>{cols.map((c) => <td key={c} style={td}>{fmt(r[c])}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function fmt(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function Empty(): JSX.Element {
  return <div style={{ color: "#666", fontSize: 12 }}>(no rows)</div>;
}

const liveBannerStyle = {
  background: "#3a0000", border: "1px solid #e66", color: "#e66",
  padding: "8px 12px", borderRadius: 4, fontSize: 13, marginBottom: 12,
};
const submitBtn = {
  padding: "8px 12px", background: "#7a1a1a", color: "#fff", border: "none",
  borderRadius: 3, cursor: "pointer", fontSize: 13, fontWeight: 600,
};
const btnSmall = {
  padding: "3px 10px", background: "#2a4a6a", color: "#fff", border: "none",
  borderRadius: 3, cursor: "pointer", fontSize: 11,
};
const dangerBtn = {
  padding: "2px 8px", background: "#5a1a1a", color: "#fff", border: "1px solid #7a3a3a",
  borderRadius: 3, cursor: "pointer", fontSize: 11,
};
const tableStyle = { borderCollapse: "collapse" as const, fontSize: 12, fontFamily: "Consolas, monospace", width: "100%" };
const th = { padding: "4px 12px", textAlign: "left" as const, color: "#888", borderBottom: "1px solid #333" };
const td = { padding: "3px 12px", borderBottom: "1px solid #222", whiteSpace: "nowrap" as const };
const preStyle = {
  margin: 0, padding: 8, background: "#000", color: "#cdc", fontSize: 11,
  fontFamily: "Consolas, monospace", overflow: "auto" as const,
  whiteSpace: "pre-wrap" as const, wordBreak: "break-all" as const,
};
