/**
 * Portfolio panel — account-level view (cf. PORTFOLIO_PANEL.md).
 *
 * P1 scope (this file) :
 *  A. Account header  : NetLiq / Cash / Margin / Cushion / # positions
 *  E. Open positions  : reuses /api/v1/positions/active (booked + IB live)
 *  F. Open orders     : /api/v1/orders
 *  G. Trades / fills  : /api/v1/dev/tables/trades
 *  I. Position snaps  : /api/v1/dev/tables/position_snapshots
 *
 * Phase 2 will add B (equity curve), C (aggregate greeks), D (vega per
 * tenor). Phase 3 adds H (hedge log + multi-window cumul).
 */
import { useEffect, useRef, useState, type CSSProperties } from "react";

interface AccountSnap {
  timestamp: string | null;
  net_liq_usd: number | null;
  cash_usd: number | null;
  unrealized_pnl_usd: number | null;
  gross_position_value: number | null;
  init_margin_req: number | null;
  maint_margin_req: number | null;
  excess_liquidity: number | null;
  cushion: number | null;
  open_positions_count: number | null;
  currencies: Record<string, Record<string, number | string | null>>;
}
interface AccountResponse {
  latest: AccountSnap | null;
  prev_24h: AccountSnap | null;
  freshness: "fresh" | "stale" | "missing";
}

interface HeaderSummary {
  computed_at: string;
  account: {
    net_liq_usd: number | null;
    cash_usd: number | null;
    init_margin_req: number | null;
    excess_liquidity: number | null;
    cushion: number | null;
    util_pct: number | null;
    n_open_positions: number;
  };
  pnl: {
    total_24h_usd: number | null;
    open_unrealized_usd: number;
  };
  greeks: {
    delta_usd: number;
    gamma_usd: number;
    vega_usd: number;
    theta_usd: number;
  };
  var_1d_99: {
    usd: number | null;
    n_days: number;
    method: string;
  };
}

interface StressGridPayload {
  current_spot: number | null;
  spot_bins_bps: number[];
  vol_bins_vps: number[];
  grid: number[][];
  n_positions: number;
}

interface GreeksLadderRow {
  dspot_bps: number;
  spot: number;
  pnl_usd: number;
  delta_usd: number;
  gamma_usd_per_pip: number;
  vega_usd_per_volpt: number;
  hedge_delta_usd: number;
}
interface GreeksLadderPayload {
  current_spot: number | null;
  spot_bins_bps: number[];
  rows: GreeksLadderRow[];
  n_positions?: number;
}

interface HedgeWindow { n_hedges: number; cum_cost_usd: number }
interface HedgeSummary {
  today: HedgeWindow; wtd: HedgeWindow; mtd: HedgeWindow;
  ytd: HedgeWindow; rolling_7d: HedgeWindow; rolling_30d: HedgeWindow;
  computed_at: string;
}

interface ActivePosition {
  id: number;
  source: "booked" | "ib_live";
  side: string | null;
  structure_type: string | null;
  expiry_date: string | null;
  tenor: string | null;
  state: string;
  current_pnl_gross_usd: number | null;
  current_vega_usd_per_volpt: number | null;
  current_gamma_usd_per_pip2: number | null;
  current_theta_usd_per_day: number | null;
  current_delta_unhedged: number | null;
  ib_qty_total: number | null;
  nominal_eur: number | null;
  contract_price_entry: number | null;
  contract_price_market: number | null;
  iv: number | null;
  vanna_usd: number | null;
  volga_usd: number | null;
}
interface OrderRow {
  id?: number;
  order_id?: number;
  symbol: string;
  sec_type?: string;
  expiry: string | null;
  strike: number | null;
  right: string | null;
  side: string;
  qty?: number;
  quantity?: number;
  limit_price: number | null;
  status: string;
  filled?: number;
  filled_qty?: number;
}
interface TableRow { [k: string]: unknown }

const fmtUsdAbs = (n: number | null | undefined): string =>
  n === null || n === undefined ? "—" : `${Math.round(n).toLocaleString()}$`;
const fmtPct = (n: number | null | undefined, d = 2): string =>
  n === null || n === undefined ? "—" : `${(n * 100).toFixed(d)}%`;
const fmtNum = (n: number | null | undefined, d = 2): string =>
  n === null || n === undefined ? "—" : n.toFixed(d);

const fmtCompactSigned = (
  n: number | null | undefined, suffix = "",
): string => {
  if (n === null || n === undefined) return "—";
  const sign = n >= 0 ? "+" : "-";
  const abs = Math.abs(n);
  if (abs >= 1e9) return `${sign}${(abs / 1e9).toFixed(2)}B${suffix}`;
  if (abs >= 1e6) return `${sign}${(abs / 1e6).toFixed(2)}M${suffix}`;
  if (abs >= 1e3) return `${sign}${(abs / 1e3).toFixed(2)}k${suffix}`;
  return `${sign}${abs.toFixed(2)}${suffix}`;
};

/** Compact magnitude formatter for big numbers in dense tables / cards.
 *  Examples : 1_509_953 → "+1.51M", -417_201 → "-417k", 4369 → "+4.37k", -3 → "-3.00".
 *  Switching from raw thousand-separated to magnitude makes greeks columns
 *  fit visually without changing the order-of-magnitude readability.
 */
const fmtCompact = (
  n: number | null | undefined, d = 2, withSign = true,
): string => {
  if (n === null || n === undefined) return "—";
  const sign = withSign ? (n >= 0 ? "+" : "-") : (n < 0 ? "-" : "");
  const abs = Math.abs(n);
  if (abs >= 1e9) return `${sign}${(abs / 1e9).toFixed(d)}B`;
  if (abs >= 1e6) return `${sign}${(abs / 1e6).toFixed(d)}M`;
  if (abs >= 1e3) return `${sign}${(abs / 1e3).toFixed(d)}k`;
  return `${sign}${abs.toFixed(d)}`;
};
const delta = (cur: number | null, prev: number | null): number | null =>
  cur === null || prev === null ? null : cur - prev;

export function Portfolio(): JSX.Element {
  const [header, setHeader] = useState<HeaderSummary | null>(null);
  const [account, setAccount] = useState<AccountResponse | null>(null);
  const [stress, setStress] = useState<StressGridPayload | null>(null);
  const [ladder, setLadder] = useState<GreeksLadderPayload | null>(null);
  const [positions, setPositions] = useState<ActivePosition[]>([]);
  const [orders, setOrders] = useState<OrderRow[]>([]);
  const [trades, setTrades] = useState<TableRow[]>([]);
  const [snapshots, setSnapshots] = useState<TableRow[]>([]);
  const [hedgeSummary, setHedgeSummary] = useState<HedgeSummary | null>(null);
  const [hedgeOrders, setHedgeOrders] = useState<TableRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  const fetchJson = async <T,>(url: string): Promise<T> => {
    const r = await fetch(url);
    if (!r.ok) throw new Error(`${url} → HTTP ${r.status}`);
    return (await r.json()) as T;
  };

  const refreshHeader     = async () => setHeader(await fetchJson<HeaderSummary>("/api/v1/portfolio/header"));
  const refreshAccount    = async () => setAccount(await fetchJson<AccountResponse>("/api/v1/portfolio/account"));
  const refreshStress     = async () =>
    setStress(await fetchJson<StressGridPayload>("/api/v1/portfolio/stress-grid"));
  const refreshLadder     = async () =>
    setLadder(await fetchJson<GreeksLadderPayload>("/api/v1/portfolio/greeks-ladder"));
  const refreshPositions  = async () => setPositions(await fetchJson<ActivePosition[]>("/api/v1/positions/active"));
  const refreshOrders     = async () => {
    const j = await fetchJson<{ orders?: OrderRow[]; rows?: OrderRow[] } | OrderRow[]>("/api/v1/orders");
    const list = Array.isArray(j) ? j : (j.orders ?? j.rows ?? []);
    setOrders(list);
  };
  const refreshTrades     = async () => {
    const j = await fetchJson<{ rows?: TableRow[] } | TableRow[]>("/api/v1/dev/tables/trades?limit=50");
    setTrades(Array.isArray(j) ? j : (j.rows ?? []));
  };
  const refreshSnapshots  = async () => {
    const j = await fetchJson<{ rows?: TableRow[] } | TableRow[]>("/api/v1/dev/tables/position_snapshots?limit=100");
    setSnapshots(Array.isArray(j) ? j : (j.rows ?? []));
  };
  const refreshHedgeSummary = async () =>
    setHedgeSummary(await fetchJson<HedgeSummary>("/api/v1/portfolio/hedge-summary"));
  const refreshHedgeOrders  = async () => {
    const j = await fetchJson<{ rows?: TableRow[] } | TableRow[]>("/api/v1/dev/tables/hedge_orders?limit=100");
    setHedgeOrders(Array.isArray(j) ? j : (j.rows ?? []));
  };

  const inFlightRef = useRef(false);
  const refreshAll = async () => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    try {
      await Promise.all([
        refreshHeader(), refreshAccount(),
        refreshStress(), refreshLadder(),
        refreshPositions(), refreshOrders(),
        refreshTrades(), refreshSnapshots(),
        refreshHedgeSummary(), refreshHedgeOrders(),
      ]);
      setError(null);
    } catch (e) { setError(String(e)); }
    finally { inFlightRef.current = false; }
  };

  useEffect(() => {
    void refreshAll();
    const id = window.setInterval(() => void refreshAll(), 5_000);
    return () => window.clearInterval(id);
  }, []);

  const latest = account?.latest ?? null;
  const prev   = account?.prev_24h ?? null;

  return (
    <div style={{ padding: 16, color: "#ddd" }}>
      <h2 style={{ margin: "0 0 12px 0" }}>💼 Portfolio</h2>
      {error && <div style={{ color: "#fcc", marginBottom: 8 }}>Error: {error}</div>}

      {/* SECTION A · Account detail (2 columns : summary + currency+risk) */}
      <Section title="A · Account detail">
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          {/* C1 : account summary */}
          <SubBlock title="Account summary">
            <table style={kvTableStyle}>
              <tbody>
                <KV label="Net Liq" value={fmtUsdAbs(latest?.net_liq_usd ?? null)}
                    delta={delta(latest?.net_liq_usd ?? null, prev?.net_liq_usd ?? null)} />
                <KV label="Cash" value={fmtUsdAbs(latest?.cash_usd ?? null)}
                    delta={delta(latest?.cash_usd ?? null, prev?.cash_usd ?? null)} />
                <KV label="Unrealized P&L"
                    value={fmtUsdAbs(latest?.unrealized_pnl_usd ?? null)} />
                <KV label="Gross position value"
                    value={fmtUsdAbs(latest?.gross_position_value ?? null)} />
                <KV label="Init margin req"
                    value={fmtUsdAbs(latest?.init_margin_req ?? null)} />
                <KV label="Maint margin req"
                    value={fmtUsdAbs(latest?.maint_margin_req ?? null)} />
                <KV label="Excess liquidity"
                    value={fmtUsdAbs(latest?.excess_liquidity ?? null)}
                    warn={latest?.excess_liquidity != null && latest?.net_liq_usd != null
                          && latest.excess_liquidity < 0.05 * latest.net_liq_usd} />
                <KV label="Cushion" value={fmtPct(latest?.cushion ?? null, 2)}
                    warn={latest?.cushion != null && latest.cushion < 0.05} />
                <KV label="# open positions"
                    value={latest?.open_positions_count != null
                          ? String(latest.open_positions_count) : "—"} />
              </tbody>
            </table>
          </SubBlock>

          {/* C2 : per-currency breakdown + book-level risk summary */}
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <SubBlock title="Currency summary">
              <CurrencyTable data={latest?.currencies ?? {}} />
            </SubBlock>
            <SubBlock title="Risk summary">
              <table style={kvTableStyle}>
                <tbody>
                  <KV label="Total P&L (24h)"
                      value={fmtCompactSigned(header?.pnl.total_24h_usd ?? null, "$")}
                      delta={header?.pnl.total_24h_usd ?? null} />
                  <KV label="Open unrealized"
                      value={fmtCompactSigned(header?.pnl.open_unrealized_usd ?? null, "$")}
                      delta={header?.pnl.open_unrealized_usd ?? null} />
                  <KV label="Δ net ($)"
                      value={fmtCompactSigned(header?.greeks.delta_usd ?? null)}
                      delta={header?.greeks.delta_usd ?? null} />
                  <KV label="Γ net ($/pip)"
                      value={fmtCompactSigned(header?.greeks.gamma_usd ?? null)}
                      delta={header?.greeks.gamma_usd ?? null} />
                  <KV label="Vega net ($/vp)"
                      value={fmtCompactSigned(header?.greeks.vega_usd ?? null)}
                      delta={header?.greeks.vega_usd ?? null} />
                  <KV label="Θ net ($/day)"
                      value={fmtCompactSigned(header?.greeks.theta_usd ?? null)}
                      delta={header?.greeks.theta_usd ?? null} />
                  <KV label="Util %"
                      value={header?.account.util_pct != null
                        ? `${(header.account.util_pct * 100).toFixed(1)}%` : "—"}
                      warn={header?.account.util_pct != null
                            && header.account.util_pct > 0.75} />
                  <KV label={`VaR 1d 99% (${header?.var_1d_99.n_days ?? 0}d)`}
                      value={fmtCompactSigned(header?.var_1d_99.usd ?? null, "$")}
                      delta={header?.var_1d_99.usd ?? null} />
                </tbody>
              </table>
            </SubBlock>
          </div>
        </div>
      </Section>

      {/* SECTIONS B + H side-by-side. B = Spot × IV stress P&L grid (spec § F).
          H = Greeks ladder per spot bucket (spec § H). */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12,
                    marginBottom: 14 }}>
        <Section title={`B · Spot × IV stress P&L (${stress?.n_positions ?? 0} positions, spot=${stress?.current_spot ?? "—"})`}>
          <StressGrid grid={stress} />
        </Section>
        <Section title={`H · Greeks ladder (${ladder?.n_positions ?? 0} positions, spot=${ladder?.current_spot ?? "—"})`}>
          <GreeksLadder ladder={ladder} />
        </Section>
      </div>

      {/* SECTION E — open positions (réutilise endpoint Step 5) */}
      <Section title={`E · Open positions (${positions.length})`}>
        {positions.length === 0 ? <Empty /> : (
          <table style={tableStyle}>
            <thead>
              <tr>
                <th style={th}>ID</th>
                <th style={th}>Structure</th>
                <th style={th}>Side</th>
                <th style={th}>Tenor</th>
                <th style={th}>Expiry</th>
                <th style={th}>Qty</th>
                <th style={th}>Nominal (EUR)</th>
                <th style={th}>Contract price</th>
                <th style={th}>Market price</th>
                <th style={th}>P&L (pending)</th>
                <th style={th}>Δ ($)</th>
                <th style={th}>Γ ($/pip)</th>
                <th style={th}>Vega ($/volpt)</th>
                <th style={th}>Θ ($/day)</th>
                <th style={th}>IV (%)</th>
                <th style={th}>Vanna ($/vp)</th>
                <th style={th}>Volga ($/vp²)</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((p) => (
                <tr key={`${p.source}-${p.id}`}>
                  <td style={td}>{p.id}</td>
                  <td style={td}>{p.structure_type ?? "—"}</td>
                  <td style={{ ...td, fontWeight: 600,
                              color: p.side === "BUY" ? "#6c6"
                                   : p.side === "SELL" ? "#e66" : "#888" }}>
                    {p.side ?? "—"}
                  </td>
                  <td style={td}>{p.tenor ?? "—"}</td>
                  <td style={td}>{p.expiry_date ?? "—"}</td>
                  <td style={td}>
                    {p.ib_qty_total != null ? Math.abs(p.ib_qty_total) : "—"}
                  </td>
                  <td style={td}>{fmtCompact(p.nominal_eur, 2, false)} €</td>
                  <td style={td}>{fmtNum(p.contract_price_entry, 5)}</td>
                  <td style={td}>{fmtNum(p.contract_price_market, 5)}</td>
                  <td style={{ ...td, color: (p.current_pnl_gross_usd ?? 0) >= 0 ? "#6c6" : "#e66" }}>
                    {fmtCompact(p.current_pnl_gross_usd)}$
                  </td>
                  <td style={td}>{fmtCompact(p.current_delta_unhedged)}</td>
                  <td style={td}>{fmtCompact(p.current_gamma_usd_per_pip2)}</td>
                  <td style={td}>{fmtCompact(p.current_vega_usd_per_volpt)}</td>
                  <td style={td}>{fmtCompact(p.current_theta_usd_per_day)}</td>
                  <td style={td}>{p.iv != null ? `${(p.iv * 100).toFixed(2)}%` : "—"}</td>
                  <td style={td}>{fmtCompact(p.vanna_usd)}</td>
                  <td style={td}>{fmtCompact(p.volga_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      {/* SECTION F — open orders */}
      <Section title={`F · Open orders (${orders.length})`}>
        {orders.length === 0 ? <Empty /> : (
          <table style={tableStyle}>
            <thead>
              <tr>
                <th style={th}>ID</th><th style={th}>Symbol</th><th style={th}>Type</th>
                <th style={th}>Side</th><th style={th}>Qty</th><th style={th}>Limit</th>
                <th style={th}>Status</th><th style={th}>Filled</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((o) => {
                const id  = o.id ?? o.order_id ?? "?";
                const qty = o.qty ?? o.quantity ?? 0;
                const fil = o.filled ?? o.filled_qty ?? 0;
                return (
                  <tr key={String(id)}>
                    <td style={td}>{id}</td>
                    <td style={td}>{o.symbol} {o.expiry ?? ""} {o.strike ?? ""} {o.right ?? ""}</td>
                    <td style={td}>{o.sec_type ?? "—"}</td>
                    <td style={td}>{o.side}</td>
                    <td style={td}>{qty}</td>
                    <td style={td}>{o.limit_price?.toFixed(5) ?? "—"}</td>
                    <td style={td}>{o.status}</td>
                    <td style={td}>{fil}/{qty}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Section>

      {/* SECTION G — trades / fills */}
      <Section title={`G · Trades / fills (${trades.length})`}>
        <GenericTable rows={trades} cols={[
          "id", "position_id", "ib_order_id", "side",
          "quantity", "price", "commission", "timestamp",
        ]} />
      </Section>

      {/* SECTION H — hedge log + multi-window cumul */}
      <Section title={`H · Hedges (${hedgeOrders.length})`}>
        <div style={{
          display: "grid", gridTemplateColumns: "repeat(6, 1fr)",
          gap: 6, marginBottom: 8,
        }}>
          <HedgeStat label="Today"      w={hedgeSummary?.today} />
          <HedgeStat label="WTD"        w={hedgeSummary?.wtd} />
          <HedgeStat label="MTD"        w={hedgeSummary?.mtd} />
          <HedgeStat label="YTD"        w={hedgeSummary?.ytd} />
          <HedgeStat label="Rolling 7d"  w={hedgeSummary?.rolling_7d} />
          <HedgeStat label="Rolling 30d" w={hedgeSummary?.rolling_30d} />
        </div>
        <GenericTable rows={hedgeOrders} cols={[
          "id", "position_id", "triggered_at", "side", "hedge_qty",
          "delta_imbalance_at_trigger", "fill_price", "total_cost_usd", "state",
        ]} />
      </Section>

      {/* SECTION I — position snapshots */}
      <Section title={`I · Position snapshots (${snapshots.length})`}>
        <GenericTable rows={snapshots} cols={[
          "id", "position_id", "timestamp", "structure", "side", "tenor",
          "expiry", "quantity", "nominal_eur", "contract_price_entry",
          "market_price", "current_pnl_usd", "delta_usd", "gamma_usd",
          "vega_usd", "theta_usd", "iv", "vanna_usd", "volga_usd",
        ]} />
      </Section>
    </div>
  );
}

function StressGrid({ grid }: { grid: StressGridPayload | null }): JSX.Element {
  if (!grid || grid.grid.length === 0) {
    return <div style={{ color: "#666", fontSize: 12, fontStyle: "italic" }}>
      no positions or current spot — grid skipped
    </div>;
  }
  // Symmetric colour intensity vs the absolute max P&L on the grid.
  const flat = grid.grid.flat();
  const maxAbs = Math.max(1, ...flat.map(Math.abs));
  const cellBg = (v: number): string => {
    const t = Math.min(1, Math.abs(v) / maxAbs);
    // alpha-blend a green/red over the dark theme.
    if (v > 0) return `rgba(58, 167, 99, ${0.10 + 0.55 * t})`;
    if (v < 0) return `rgba(180, 70, 70, ${0.10 + 0.55 * t})`;
    return "transparent";
  };
  const cellFg = (v: number): string =>
    v > 0 ? "#9f9" : v < 0 ? "#fcc" : "#aaa";
  return (
    <table style={{
      width: "100%", borderCollapse: "collapse",
      fontFamily: "Consolas, monospace", fontSize: 12,
    }}>
      <thead>
        <tr>
          <th style={{ ...stressHeader, textAlign: "left" }}>ΔIV \ ΔSpot</th>
          {grid.spot_bins_bps.map((b) => (
            <th key={b} style={stressHeader}>
              {b > 0 ? "+" : ""}{b}bp
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {grid.grid.map((row, i) => {
          const dvol = grid.vol_bins_vps[i] ?? 0;
          return (
          <tr key={dvol}>
            <th style={{ ...stressHeader, textAlign: "left" }}>
              {dvol > 0 ? "+" : ""}{dvol}vp
            </th>
            {row.map((v, j) => {
              const dspot = grid.spot_bins_bps[j] ?? 0;
              const isCenter = dvol === 0 && dspot === 0;
              return (
                <td
                  key={j}
                  style={{
                    padding: "6px 10px",
                    textAlign: "right",
                    background: isCenter ? "#222" : cellBg(v),
                    color: isCenter ? "#fff" : cellFg(v),
                    fontWeight: isCenter ? 700 : 600,
                    border: "1px solid #1a1a1a",
                  }}
                  title={`${v.toLocaleString(undefined, { maximumFractionDigits: 0 })} $`}
                >
                  {fmtCompactSigned(v, "$")}
                </td>
              );
            })}
          </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function GreeksLadder({ ladder }: { ladder: GreeksLadderPayload | null }): JSX.Element {
  if (!ladder || ladder.rows.length === 0) {
    return <div style={{ color: "#666", fontSize: 12, fontStyle: "italic" }}>
      no positions or current spot — ladder skipped
    </div>;
  }
  return (
    <table style={{
      width: "100%", borderCollapse: "collapse",
      fontFamily: "Consolas, monospace", fontSize: 12,
    }}>
      <thead>
        <tr>
          <th style={stressHeader}>Spot</th>
          <th style={stressHeader}>P&L</th>
          <th style={stressHeader}>Δ ($)</th>
          <th style={stressHeader}>Γ ($/pip)</th>
          <th style={stressHeader}>Vega ($/vp)</th>
          <th style={stressHeader}>Hedge Δ</th>
        </tr>
      </thead>
      <tbody>
        {ladder.rows.map((r) => {
          const isCenter = r.dspot_bps === 0;
          const baseStyle: CSSProperties = {
            padding: "6px 10px",
            textAlign: "right",
            border: "1px solid #1a1a1a",
            background: isCenter ? "#222" : "transparent",
            color: isCenter ? "#fff" : "#ddd",
            fontWeight: isCenter ? 700 : 600,
          };
          return (
            <tr key={r.dspot_bps}>
              <th style={{ ...stressHeader, textAlign: "left" }}>
                {r.spot.toFixed(5)}
                <span style={{ color: "#666", marginLeft: 4, fontSize: 10 }}>
                  ({r.dspot_bps > 0 ? "+" : ""}{r.dspot_bps}bp)
                </span>
              </th>
              <td style={{ ...baseStyle,
                          color: isCenter ? "#fff" : (r.pnl_usd >= 0 ? "#9f9" : "#fcc") }}>
                {fmtCompactSigned(r.pnl_usd, "$")}
              </td>
              <td style={baseStyle}>{fmtCompactSigned(r.delta_usd)}</td>
              <td style={baseStyle}>{fmtCompactSigned(r.gamma_usd_per_pip)}</td>
              <td style={baseStyle}>{fmtCompactSigned(r.vega_usd_per_volpt)}</td>
              <td style={{ ...baseStyle,
                          color: isCenter ? "#fff" : (r.hedge_delta_usd >= 0 ? "#9f9" : "#fcc") }}>
                {fmtCompactSigned(r.hedge_delta_usd)}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

const stressHeader: CSSProperties = {
  padding: "6px 10px",
  background: "#161616",
  color: "#7af",
  fontSize: 11,
  fontWeight: 600,
  textTransform: "uppercase",
  letterSpacing: 0.3,
  borderBottom: "1px solid #333",
  textAlign: "right",
};

function HedgeStat({
  label, w,
}: { label: string; w: HedgeWindow | undefined }): JSX.Element {
  const cost = w?.cum_cost_usd ?? null;
  const color = cost === null ? "#888"
              : cost < 0 ? "#e66" : cost > 0 ? "#6c6" : "#ddd";
  return (
    <div style={{
      padding: "5px 8px", background: "#0e0e0e",
      border: "1px solid #222", borderRadius: 3,
    }}>
      <div style={{ fontSize: 10, color: "#888", textTransform: "uppercase",
                    letterSpacing: 0.3 }}>{label}</div>
      <div style={{ fontSize: 14, fontWeight: 600, color }}>
        {cost === null ? "—" : `${cost >= 0 ? "+" : ""}${Math.round(cost).toLocaleString()}$`}
      </div>
      <div style={{ fontSize: 10, color: "#666" }}>
        {w?.n_hedges ?? 0} hedges
      </div>
    </div>
  );
}

function Section({
  title, children,
}: { title: string; children: React.ReactNode }): JSX.Element {
  return (
    <section style={{ marginBottom: 14, border: "1px solid #333", borderRadius: 4 }}>
      <header style={{
        padding: "6px 10px", background: "#1a1a1a", borderBottom: "1px solid #333",
      }}>
        <h3 style={{ margin: 0, fontSize: 13, color: "#7af",
                     fontWeight: 600, letterSpacing: 0.5 }}>{title}</h3>
      </header>
      <div style={{ padding: 10 }}>{children}</div>
    </section>
  );
}

function SubBlock({ title, children }: { title: string; children: React.ReactNode }): JSX.Element {
  return (
    <div style={{ background: "#0e0e0e", border: "1px solid #222", borderRadius: 3 }}>
      <div style={{
        padding: "4px 10px", fontSize: 11, color: "#7af", fontWeight: 600,
        textTransform: "uppercase", letterSpacing: 0.5,
        background: "#161616", borderBottom: "1px solid #222",
      }}>{title}</div>
      <div style={{ padding: 8 }}>{children}</div>
    </div>
  );
}

function KV({
  label, value, delta: d, warn,
}: { label: string; value: string; delta?: number | null; warn?: boolean }): JSX.Element {
  const dColor = d == null ? undefined : d > 0 ? "#6c6" : d < 0 ? "#e66" : "#888";
  return (
    <tr style={{ background: warn ? "#3a1a1a" : "transparent" }}>
      <td style={{ ...kvLabel, color: warn ? "#fcc" : "#888" }}>{label}</td>
      <td style={{ ...kvValue, color: warn ? "#fcc" : "#ddd" }}>{value}</td>
      <td style={{ ...kvDelta, color: dColor }}>
        {d != null ? `${d > 0 ? "+" : ""}${Math.round(d).toLocaleString()}$ / 24h` : ""}
      </td>
    </tr>
  );
}

function CurrencyTable({
  data,
}: { data: Record<string, Record<string, number | string | null>> }): JSX.Element {
  const ccys = Object.keys(data).filter((k) => k !== "BASE").sort();
  if (ccys.length === 0) return <Empty />;
  // Collect every metric key seen across currencies (different IB tags
  // may surface for paper vs live, so don't hardcode).
  const metrics = Array.from(new Set(ccys.flatMap((c) => Object.keys(data[c] ?? {}))));
  metrics.sort();
  return (
    <div style={{ overflow: "auto" }}>
      <table style={tableStyle}>
        <thead>
          <tr>
            <th style={th}>Currency</th>
            {metrics.map((m) => <th key={m} style={th}>{m}</th>)}
          </tr>
        </thead>
        <tbody>
          {ccys.map((c) => (
            <tr key={c}>
              <td style={{ ...td, fontWeight: 600, color: "#ddd" }}>{c}</td>
              {metrics.map((m) => (
                <td key={m} style={td}>{fmtCcyValue(data[c]?.[m])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function fmtCcyValue(v: number | string | null | undefined): string {
  if (v === null || v === undefined || v === "") return "—";
  const n = typeof v === "string" ? Number(v) : v;
  if (Number.isFinite(n)) return Math.round(n as number).toLocaleString();
  return String(v);
}

function GenericTable({ rows, cols }: { rows: TableRow[]; cols: string[] }): JSX.Element {
  if (rows.length === 0) return <Empty />;
  return (
    <div style={{ overflow: "auto", maxHeight: 280 }}>
      <table style={tableStyle}>
        <thead><tr>{cols.map((c) => <th key={c} style={th}>{c}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {cols.map((c) => <td key={c} style={td}>{fmt(r[c])}</td>)}
            </tr>
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
  return <div style={{ color: "#666", fontSize: 12, fontStyle: "italic" }}>(no rows)</div>;
}

const tableStyle: CSSProperties = {
  borderCollapse: "collapse", fontSize: 12,
  fontFamily: "Consolas, monospace", width: "100%",
};
const th: CSSProperties = {
  padding: "4px 10px", textAlign: "left", color: "#888",
  borderBottom: "1px solid #333",
};
const td: CSSProperties = {
  padding: "3px 10px", borderBottom: "1px solid #222", whiteSpace: "nowrap",
};
const kvTableStyle: CSSProperties = {
  width: "100%", borderCollapse: "collapse", fontSize: 12,
  fontFamily: "Consolas, monospace",
};
const kvLabel: CSSProperties = {
  padding: "3px 8px", textAlign: "left", color: "#888",
  textTransform: "uppercase", fontSize: 10, letterSpacing: 0.3,
  borderBottom: "1px solid #1a1a1a",
};
const kvValue: CSSProperties = {
  padding: "3px 8px", textAlign: "right", fontSize: 13, fontWeight: 600,
  color: "#ddd", borderBottom: "1px solid #1a1a1a",
};
const kvDelta: CSSProperties = {
  padding: "3px 8px", textAlign: "right", fontSize: 10,
  borderBottom: "1px solid #1a1a1a",
};
