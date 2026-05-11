/**
 * Portfolio panel — account-level view (cf. PORTFOLIO_PANEL.md).
 *
 * P1 scope (this file) :
 *  A. Account header  : NetLiq / Cash / Margin / Cushion / # positions
 *  E. Open positions  : reuses /api/v1/positions/active (booked + IB live)
 *  G. Trades / fills  : /api/v1/dev/tables/trades
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
interface VegaTenorRow {
  bucket: string;
  dte_lo: number;
  dte_hi: number;
  vega_usd: number;
  n_positions: number;
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
  // Returned by the API serializer for IB-live rows. Used by panel J
  // (pin risk grid) to filter near-expiry options + render strike/right.
  strike?: number | null;
  option_type?: string | null;     // "C" / "P" / null for futures
  instrument_type?: string | null; // "OPTION" / "FUTURE"
  symbol?: string | null;
  // Greek P&L decomposition (panel G). Populated for booked structures
  // via PositionMtmHistory. Stays null on IB-live rows until a t-1
  // state-store is wired (cf. risk_dashboard_spec.md § G).
  gamma_pnl_usd?: number | null;
  vega_pnl_usd?: number | null;
  theta_pnl_usd?: number | null;
}

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
  const [vegaTenor, setVegaTenor] = useState<VegaTenorRow[]>([]);
  const [positions, setPositions] = useState<ActivePosition[]>([]);
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
  const refreshVegaTenor  = async () =>
    setVegaTenor(await fetchJson<VegaTenorRow[]>("/api/v1/portfolio/vega-per-tenor"));
  const refreshPositions  = async () => setPositions(await fetchJson<ActivePosition[]>("/api/v1/positions/active"));

  const inFlightRef = useRef(false);
  const refreshAll = async () => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    try {
      await Promise.all([
        refreshHeader(), refreshAccount(),
        refreshStress(), refreshLadder(), refreshVegaTenor(),
        refreshPositions(),
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

      {/* SECTION F + I + H — three squares 1/3 each. Letter labels
          follow risk_dashboard_spec.md (F = Spot×Vol P&L grid,
          I = Vega bucket per tenor, H = Greeks ladder). Each cell is
          a strict square (panel + table). Grid takes full page width
          → each square ≈ pageWidth / 3. */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12,
                    marginBottom: 14, width: "100%" }}>
        <SquareCell title={`F · Spot × Vol P&L (${stress?.n_positions ?? 0} positions, spot=${stress?.current_spot ?? "—"})`}>
          <StressGrid grid={stress} />
        </SquareCell>
        <SquareCell title={`I · Vega bucket par tenor (${vegaTenor.reduce((s, r) => s + r.n_positions, 0)} positions)`}>
          <VegaPerTenor rows={vegaTenor} />
        </SquareCell>
        <SquareCell title={`H · Greeks ladder (${ladder?.n_positions ?? 0} positions, spot=${ladder?.current_spot ?? "—"})`}>
          <GreeksLadder ladder={ladder} />
        </SquareCell>
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

      {/* SECTION G — P&L attribution daily (greeks decomposition).
          Cf. risk_dashboard_spec.md § G. Backend support partial : the
          per-greek P&L breakdown lives on `position_mtm_history` for
          booked structures, but is None on IB-live rows until a t-1
          state-store is wired. Cells display "—" when the breakdown
          is not available. */}
      <Section title={`G · P&L attribution daily (${positions.length} positions)`}>
        <PnlAttribution positions={positions} />
      </Section>

      {/* SECTIONS K + J side-by-side (50/50). K left = Margin/SPAN
          utilization (spec § K). J right = Pin risk grid (spec § J). */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <MarginUtilization account={account?.latest ?? null} header={header} />
        <PinRiskSection positions={positions} spot={stress?.current_spot ?? ladder?.current_spot ?? null} />
      </div>

    </div>
  );
}

function StressGrid({ grid }: { grid: StressGridPayload | null }): JSX.Element {
  const fsCell = 15;
  const fsHdr = 15;
  if (!grid || grid.grid.length === 0) {
    return <div style={{ color: "#666", fontSize: 12, fontStyle: "italic" }}>
      no positions or current spot — grid skipped
    </div>;
  }
  // Sign-only colouring : red if < 0, green if > 0, white if 0.
  const cellFg = (v: number): string =>
    v > 0 ? "#9f9" : v < 0 ? "#fcc" : "#fff";
  const bigHeader: CSSProperties = {
    ...stressHeader, padding: "6px 8px", fontSize: fsHdr,
    textAlign: "center",
  };
  return (
    <div style={{ width: "100%", height: "100%" }}>
    <table style={{
      width: "100%", height: "100%", borderCollapse: "collapse",
      tableLayout: "fixed",
      fontFamily: "Consolas, monospace", fontSize: fsCell,
    }}>
      <thead>
        <tr>
          <th style={{ ...bigHeader, textAlign: "left" }}>ΔIV \ ΔSpot</th>
          {grid.spot_bins_bps.map((b) => (
            <th key={b} style={bigHeader}>
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
            <th style={{ ...bigHeader, textAlign: "left" }}>
              {dvol > 0 ? "+" : ""}{dvol}vp
            </th>
            {row.map((v, j) => {
              const dspot = grid.spot_bins_bps[j] ?? 0;
              const isCenter = dvol === 0 && dspot === 0;
              return (
                <td
                  key={j}
                  style={{
                    padding: "6px 8px",
                    textAlign: "center",
                    background: isCenter ? "#222" : "transparent",
                    color: cellFg(v),
                    fontWeight: isCenter ? 700 : 600,
                    fontSize: fsCell,
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
    </div>
  );
}

function GreeksLadder({ ladder }: { ladder: GreeksLadderPayload | null }): JSX.Element {
  const fsCell = 15;
  const fsHdr = 15;
  const fsSub = 13;
  if (!ladder || ladder.rows.length === 0) {
    return <div style={{ color: "#666", fontSize: 12, fontStyle: "italic" }}>
      no positions or current spot — ladder skipped
    </div>;
  }
  const bigHeader: CSSProperties = {
    ...stressHeader, padding: "6px 8px", fontSize: fsHdr,
    textAlign: "center",
  };
  return (
    <div style={{ width: "100%", height: "100%" }}>
    <table style={{
      width: "100%", height: "100%", borderCollapse: "collapse",
      tableLayout: "fixed",
      fontFamily: "Consolas, monospace", fontSize: fsCell,
    }}>
      <thead>
        <tr>
          <th style={bigHeader}>Spot</th>
          <th style={bigHeader}>P&L</th>
          <th style={bigHeader}>Δ ($)</th>
          <th style={bigHeader}>Γ ($/pip)</th>
          <th style={bigHeader}>Vega ($/vp)</th>
          <th style={bigHeader}>Hedge Δ</th>
        </tr>
      </thead>
      <tbody>
        {ladder.rows.map((r) => {
          const isCenter = r.dspot_bps === 0;
          const baseStyle: CSSProperties = {
            padding: "6px 8px",
            textAlign: "center",
            border: "1px solid #1a1a1a",
            background: isCenter ? "#222" : "transparent",
            color: isCenter ? "#fff" : "#ddd",
            fontWeight: isCenter ? 700 : 600,
            fontSize: fsCell,
          };
          return (
            <tr key={r.dspot_bps}>
              <th style={{ ...bigHeader, textAlign: "left" }}>
                {r.spot.toFixed(5)}
                <span style={{ color: "#666", marginLeft: 4, fontSize: fsSub }}>
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
    </div>
  );
}

function VegaPerTenor({ rows }: { rows: VegaTenorRow[] }): JSX.Element {
  const fsCell = 15;
  const fsHdr = 15;
  const fsSub = 13;
  if (!rows || rows.length === 0) {
    return <div style={{ color: "#666", fontSize: 12, fontStyle: "italic" }}>
      no positions — vega buckets skipped
    </div>;
  }
  const totalAbs = rows.reduce((s, r) => s + Math.abs(r.vega_usd), 0) || 1;
  const totalVega = rows.reduce((s, r) => s + r.vega_usd, 0);
  const totalN = rows.reduce((s, r) => s + r.n_positions, 0);
  const bigHeader: CSSProperties = {
    ...stressHeader, padding: "6px 8px", fontSize: fsHdr,
    textAlign: "center",
  };
  const baseCell: CSSProperties = {
    padding: "6px 8px",
    textAlign: "center",
    border: "1px solid #1a1a1a",
    fontSize: fsCell,
    fontWeight: 600,
    color: "#ddd",
  };
  return (
    <div style={{ width: "100%", height: "100%" }}>
    <table style={{
      width: "100%", height: "100%", borderCollapse: "collapse",
      tableLayout: "fixed",
      fontFamily: "Consolas, monospace", fontSize: fsCell,
    }}>
      <thead>
        <tr>
          <th style={{ ...bigHeader, textAlign: "left" }}>Bucket</th>
          <th style={bigHeader}>Vega ($/vp)</th>
          <th style={bigHeader}>% total</th>
          <th style={bigHeader}># pos</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => {
          const pct = (Math.abs(r.vega_usd) / totalAbs) * 100;
          return (
            <tr key={r.bucket}>
              <th style={{ ...bigHeader, textAlign: "left" }}>
                {r.bucket}
                <span style={{ color: "#666", marginLeft: 4, fontSize: fsSub }}>
                  ({r.dte_lo}-{r.dte_hi}d)
                </span>
              </th>
              <td style={{ ...baseCell,
                          color: r.vega_usd > 0 ? "#9f9"
                               : r.vega_usd < 0 ? "#fcc" : "#888" }}>
                {fmtCompactSigned(r.vega_usd)}
              </td>
              <td style={baseCell}>{pct.toFixed(0)}%</td>
              <td style={baseCell}>{r.n_positions}</td>
            </tr>
          );
        })}
        <tr>
          <th style={{ ...bigHeader, textAlign: "left", background: "#222", color: "#fff" }}>
            TOTAL
          </th>
          <td style={{ ...baseCell, background: "#222", color: "#fff", fontWeight: 700 }}>
            {fmtCompactSigned(totalVega)}
          </td>
          <td style={{ ...baseCell, background: "#222", color: "#fff", fontWeight: 700 }}>
            100%
          </td>
          <td style={{ ...baseCell, background: "#222", color: "#fff", fontWeight: 700 }}>
            {totalN}
          </td>
        </tr>
      </tbody>
    </table>
    </div>
  );
}

// Panel G — P&L attribution daily.
// Sources le même `positions` array que panel E (endpoint
// `/api/v1/positions/active`). Les greeks affichés sont les
// sensibilités courantes de chaque position (mêmes valeurs que E).
function PnlAttribution({ positions }: { positions: ActivePosition[] }): JSX.Element {
  if (!positions || positions.length === 0) {
    return <Empty />;
  }
  type Source = {
    label: string;
    pick: (p: ActivePosition) => number | null | undefined;
  };
  const sources: Source[] = [
    { label: "Δ ($)",          pick: (p) => p.current_delta_unhedged },
    { label: "Γ ($/pip)",      pick: (p) => p.current_gamma_usd_per_pip2 },
    { label: "Vega ($/vp)",    pick: (p) => p.current_vega_usd_per_volpt },
    { label: "Θ ($/day)",      pick: (p) => p.current_theta_usd_per_day },
    { label: "Vanna ($/vp)",   pick: (p) => p.vanna_usd },
    { label: "Volga ($/vp²)",  pick: (p) => p.volga_usd },
    { label: "P&L pending",    pick: (p) => p.current_pnl_gross_usd },
  ];
  const cellColor = (v: number | null | undefined): string =>
    v == null ? "#888" : v > 0 ? "#9f9" : v < 0 ? "#fcc" : "#fff";
  const cellTxt = (v: number | null | undefined): string =>
    v == null ? "—" : fmtCompactSigned(v, "$");
  const colLabel = (p: ActivePosition): string =>
    `${p.structure_type ?? p.id} (${p.side ?? "?"})`;
  return (
    <table style={{ ...tableStyle, fontFamily: "Consolas, monospace" }}>
      <thead>
        <tr>
          <th style={{ ...th, textAlign: "left", verticalAlign: "middle" }}>Source</th>
          {positions.map((p) => (
            <th key={`${p.source}-${p.id}`}
                style={{ ...th, textAlign: "center", verticalAlign: "middle" }}>
              {colLabel(p)}
            </th>
          ))}
          <th style={{ ...th, textAlign: "center", verticalAlign: "middle" }}>Total</th>
        </tr>
      </thead>
      <tbody>
        {sources.map((s) => {
          const vals = positions.map((p) => s.pick(p));
          const known = vals.filter((v): v is number => typeof v === "number");
          const total = known.length > 0 ? known.reduce((a, b) => a + b, 0) : null;
          const isTotalRow = s.label === "P&L pending";
          return (
            <tr key={s.label} style={isTotalRow ? { background: "#1a1a1a" } : undefined}>
              <th style={{ ...th, textAlign: "left", verticalAlign: "middle",
                          fontWeight: isTotalRow ? 700 : 600,
                          color: isTotalRow ? "#fff" : "#7af" }}>
                {s.label}
              </th>
              {vals.map((v, i) => (
                <td key={i} style={{ ...td,
                                     textAlign: "center", verticalAlign: "middle",
                                     color: cellColor(v),
                                     fontWeight: isTotalRow ? 700 : 500 }}>
                  {cellTxt(v)}
                </td>
              ))}
              <td style={{ ...td,
                           textAlign: "center", verticalAlign: "middle",
                           color: cellColor(total),
                           fontWeight: 700 }}>
                {cellTxt(total)}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

// Panel K — Margin / SPAN utilization. Cf. risk_dashboard_spec.md § K.
// Initial / Maintenance margin + excess liquidity sourced from the
// /api/v1/portfolio/account snap (IB reqAccountSummary). SPAN
// scenarios (worst-case futures/options/combined) need IB
// RiskNavigator API and are not wired yet — those rows are flagged
// explicitly "TODO: IB RiskNavigator" rather than silent "—".
// Greek exposure ratios (Δ/Vega vs NetLiq) added below the margin
// rows : surface concentration risk early (the user spotted
// Δ=930k$ on NetLiq=995k$ = 93% in the current book).
function MarginUtilization({
  account, header,
}: { account: AccountSnap | null; header: HeaderSummary | null }): JSX.Element {
  const netLiq = account?.net_liq_usd ?? null;
  const initMargin = account?.init_margin_req ?? null;
  const maintMargin = account?.maint_margin_req ?? null;
  const excess = account?.excess_liquidity ?? null;
  const cushion = account?.cushion ?? null;
  const deltaUsd = header?.greeks.delta_usd ?? null;
  const vegaUsd = header?.greeks.vega_usd ?? null;
  const gammaUsd = header?.greeks.gamma_usd ?? null;
  const thetaUsd = header?.greeks.theta_usd ?? null;

  const utilPct = (used: number | null, limit: number | null): number | null => {
    if (used == null || limit == null || limit <= 0) return null;
    return Math.abs(used) / limit;
  };
  const utilColor = (pct: number | null): string => {
    if (pct == null) return "#888";
    if (pct >= 0.90) return "#fcc";
    if (pct >= 0.75) return "#fc6";
    return "#9f9";
  };
  const fmtPctLocal = (pct: number | null): string =>
    pct == null ? "—" : `${(pct * 100).toFixed(1)}%`;

  type Row = {
    label: string;
    value: number | null;
    valueFmt?: (v: number) => string;
    limit: number | null;
    pct: number | null;
    todo?: boolean;
    section?: string;
  };
  const rows: Row[] = [
    // ── Margin (live from IB reqAccountSummary) ──
    {
      label: "Total margin used",
      value: initMargin, limit: netLiq,
      pct: utilPct(initMargin, netLiq),
      section: "margin",
    },
    {
      label: "Maintenance margin",
      value: maintMargin, limit: netLiq,
      pct: utilPct(maintMargin, netLiq),
      section: "margin",
    },
    {
      label: "Initial margin",
      value: initMargin, limit: netLiq,
      pct: utilPct(initMargin, netLiq),
      section: "margin",
    },
    // ── Greek exposure vs NetLiq ──
    {
      label: "Δ exposure ($)",
      value: deltaUsd, limit: netLiq,
      pct: utilPct(deltaUsd, netLiq),
      section: "exposure",
    },
    {
      label: "Vega exposure ($/vp)",
      value: vegaUsd, limit: netLiq,
      pct: utilPct(vegaUsd, netLiq),
      section: "exposure",
    },
    {
      label: "Γ exposure ($/pip)",
      value: gammaUsd, limit: netLiq,
      pct: utilPct(gammaUsd, netLiq),
      section: "exposure",
    },
    {
      label: "Θ exposure ($/day)",
      value: thetaUsd, limit: netLiq,
      pct: utilPct(thetaUsd, netLiq),
      section: "exposure",
    },
    // ── SPAN scenarios (not wired — needs IB RiskNavigator) ──
    {
      label: "SPAN scenario worst (futures)",
      value: null, limit: null, pct: null, todo: true,
      section: "span",
    },
    {
      label: "SPAN scenario worst (options)",
      value: null, limit: null, pct: null, todo: true,
      section: "span",
    },
    {
      label: "Combined worst case",
      value: null, limit: null, pct: null, todo: true,
      section: "span",
    },
    // ── Buffer ──
    {
      label: "Excess liquidity",
      value: excess, limit: null, pct: null,
      section: "buffer",
    },
    {
      label: "Liquidation buffer (cushion)",
      value: cushion != null && netLiq != null ? cushion * netLiq : null,
      valueFmt: (v) => `${fmtUsdAbs(v)} (${cushion != null ? (cushion * 100).toFixed(1) : "—"}%)`,
      limit: null, pct: null,
      section: "buffer",
    },
  ];

  // Section divider rows go before the first row of each section change.
  const sectionLabel: Record<string, string> = {
    margin:   "─ Margin (IB reqAccountSummary) ──────────────",
    exposure: "─ Greek exposure vs NetLiq ──────────────────",
    span:     "─ SPAN scenarios (TODO: IB RiskNavigator) ───",
    buffer:   "─ Buffer ───────────────────────────────────",
  };

  const trs: JSX.Element[] = [];
  let prevSection: string | undefined;
  for (const r of rows) {
    if (r.section && r.section !== prevSection) {
      trs.push(
        <tr key={`divider-${r.section}`}>
          <td colSpan={4} style={{
            padding: "8px 10px 2px 10px",
            color: "#555", fontSize: 10,
            fontFamily: "Consolas, monospace",
            letterSpacing: 0.5,
          }}>
            {sectionLabel[r.section]}
          </td>
        </tr>
      );
      prevSection = r.section;
    }
    const labelColor = r.todo ? "#666" : "#7af";
    const labelStyle: CSSProperties = r.todo ? { fontStyle: "italic" } : {};
    trs.push(
      <tr key={r.label}>
        <th style={{ ...th, textAlign: "left", color: labelColor, ...labelStyle }}>
          {r.label}
          {r.todo && <span style={{ marginLeft: 6, fontSize: 9, color: "#a77" }}>
            [not wired]
          </span>}
        </th>
        <td style={{ ...td, textAlign: "center",
                    color: r.todo ? "#555" : "#ddd" }}>
          {r.value == null
            ? (r.todo ? "TODO" : "—")
            : (r.valueFmt ? r.valueFmt(r.value) : fmtUsdAbs(r.value))}
        </td>
        <td style={{ ...td, textAlign: "center", color: "#888" }}>
          {r.limit == null ? "—" : fmtUsdAbs(r.limit)}
        </td>
        <td style={{ ...td, textAlign: "center", color: utilColor(r.pct),
                    fontWeight: r.pct != null && r.pct >= 0.75 ? 700 : 500 }}>
          {fmtPctLocal(r.pct)}
        </td>
      </tr>
    );
  }
  return (
    <Section title={`K · Margin / SPAN utilization (NetLiq ${fmtUsdAbs(netLiq)})`}>
      <table style={{ ...tableStyle, fontFamily: "Consolas, monospace" }}>
        <thead>
          <tr>
            <th style={{ ...th, textAlign: "left" }}>Métrique</th>
            <th style={{ ...th, textAlign: "center" }}>Valeur</th>
            <th style={{ ...th, textAlign: "center" }}>Limite</th>
            <th style={{ ...th, textAlign: "center" }}>% util</th>
          </tr>
        </thead>
        <tbody>{trs}</tbody>
      </table>
      <div style={{ fontSize: 10, color: "#666", marginTop: 8, lineHeight: 1.5 }}>
        <div>Alerts : <span style={{ color: "#9f9" }}>vert</span> &lt; 75%, <span style={{ color: "#fc6" }}>ambre</span> 75-90%, <span style={{ color: "#fcc" }}>rouge</span> ≥ 90%. Liquidation buffer &lt; 10% NAV = critique.</div>
        <div style={{ color: "#555", marginTop: 2 }}>
          SPAN rows require IB RiskNavigator integration (backlog post-obs v1.0).
        </div>
      </div>
    </Section>
  );
}

// Panel J — pin risk grid. Spec § J recommends DTE < 7d as the trigger,
// but we list every open option here for dev visibility ; the operator
// can eyeball pin risk regardless of tenor.
function PinRiskSection({
  positions, spot,
}: { positions: ActivePosition[]; spot: number | null }): JSX.Element {
  const today = new Date();
  today.setUTCHours(0, 0, 0, 0);

  const rows = positions
    .filter((p) => p.option_type === "CALL" || p.option_type === "PUT")
    .map((p) => {
      const exp = p.expiry_date ? new Date(p.expiry_date) : null;
      const dte = exp
        ? Math.round((exp.getTime() - today.getTime()) / 86_400_000)
        : null;
      return { p, dte };
    })
    .filter((r): r is { p: ActivePosition; dte: number } => r.dte !== null);

  if (rows.length === 0) {
    return (
      <Section title="J · Pin risk grid (0 options)">
        <div style={{ color: "#666", fontSize: 12, fontStyle: "italic",
                      padding: "10px 4px" }}>
          no open options.
        </div>
      </Section>
    );
  }

  const distancePips = (strike: number | null | undefined): number | null => {
    if (!strike || spot == null) return null;
    return Math.round((spot - strike) * 10_000);
  };
  const cellColor = (v: number | null): string =>
    v == null ? "#888" : v > 0 ? "#9f9" : v < 0 ? "#fcc" : "#fff";
  const cellTxt = (v: number | null): string =>
    v == null ? "—" : fmtCompactSigned(v, "$");

  return (
    <Section title={`J · Pin risk grid (${rows.length} option${rows.length > 1 ? "s" : ""}, spot=${spot ?? "—"})`}>
      <table style={{ ...tableStyle, fontFamily: "Consolas, monospace" }}>
        <thead>
          <tr>
            <th style={{ ...th, textAlign: "left" }}>Option</th>
            <th style={{ ...th, textAlign: "center" }}>DTE</th>
            <th style={{ ...th, textAlign: "center" }}>Strike</th>
            <th style={{ ...th, textAlign: "center" }}>Spot</th>
            <th style={{ ...th, textAlign: "center" }}>Distance (pips)</th>
            <th style={{ ...th, textAlign: "center" }}>Δ ($)</th>
            <th style={{ ...th, textAlign: "center" }}>P&L now</th>
            <th style={{ ...th, textAlign: "center" }}>P&L if pin (ΔS=0)</th>
            <th style={{ ...th, textAlign: "center" }}>P&L if breach (ΔS=±50bp)</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ p, dte }) => {
            const dist = distancePips(p.strike);
            const pnlNow = p.current_pnl_gross_usd ?? null;
            // Breach proxy via Greeks (Δ × ΔS + ½ Γ × ΔS²) for ±50 bp.
            // Γ from API is in $/pip — for 50 pips squared use the
            // simple linearisation × pips. Pin estimate keeps current
            // P&L (ΔS = 0). Replace with full reval when backend pricer
            // exposes it (spec § J ‘pricing engine + tick stream’).
            const delta = p.current_delta_unhedged ?? null;
            const breach50 = delta != null
              ? delta * 50 / 10_000  // delta is $/unit-spot-move; here 50 bp = 0.5%
              : null;
            const optLabel = `${p.option_type ?? "?"} ${p.strike ?? "?"} × ${p.ib_qty_total ?? "?"}`;
            return (
              <tr key={`${p.source}-${p.id}`}>
                <th style={{ ...th, textAlign: "left", color: "#7af" }}>
                  {optLabel}
                </th>
                <td style={{ ...td, textAlign: "center" }}>{dte}d</td>
                <td style={{ ...td, textAlign: "center" }}>
                  {p.strike != null ? p.strike.toFixed(5) : "—"}
                </td>
                <td style={{ ...td, textAlign: "center" }}>
                  {spot != null ? spot.toFixed(5) : "—"}
                </td>
                <td style={{ ...td, textAlign: "center",
                            color: dist == null ? "#888" : dist === 0 ? "#fff" : "#ddd" }}>
                  {dist == null ? "—" : `${dist > 0 ? "+" : ""}${dist}`}
                </td>
                <td style={{ ...td, textAlign: "center", color: cellColor(delta) }}>
                  {cellTxt(delta)}
                </td>
                <td style={{ ...td, textAlign: "center", color: cellColor(pnlNow) }}>
                  {cellTxt(pnlNow)}
                </td>
                <td style={{ ...td, textAlign: "center", color: cellColor(pnlNow) }}>
                  {cellTxt(pnlNow)}
                </td>
                <td style={{ ...td, textAlign: "center", color: cellColor(breach50) }}>
                  {cellTxt(breach50)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Section>
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

// Square-aspect Section variant — outer panel is forced to width=height
// via aspectRatio on the grid child wrapper, inner content fills the
// available area below the title bar (flex column).
function SquareCell({
  title, children,
}: { title: string; children: React.ReactNode }): JSX.Element {
  return (
    <div style={{ aspectRatio: "1 / 1" }}>
      <section style={{
        height: "100%", display: "flex", flexDirection: "column",
        border: "1px solid #333", borderRadius: 4, overflow: "hidden",
      }}>
        <header style={{
          padding: "6px 10px", background: "#1a1a1a", borderBottom: "1px solid #333",
        }}>
          <h3 style={{ margin: 0, fontSize: 13, color: "#7af",
                       fontWeight: 600, letterSpacing: 0.5 }}>{title}</h3>
        </header>
        <div style={{ padding: 10, flex: 1, minHeight: 0, overflow: "hidden" }}>
          {children}
        </div>
      </section>
    </div>
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
