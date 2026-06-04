/**
 * Portfolio panel — account-level view (cf. PORTFOLIO_PANEL.md).
 *
 * P1 scope (this file) :
 *  A. Account header  : NetLiq / Cash / Margin / Cushion / # positions
 *  E. Open positions  : /api/v1/positions/open (raw open_position table)
 *  G. Trades / fills  : /api/v1/dev/tables/trades
 *
 * Phase 2 will add B (equity curve), C (aggregate greeks), D (vega per
 * tenor). Phase 3 adds H (hedge log + multi-window cumul).
 */
import type Plotly from "plotly.js";
import { useEffect, useRef, useState, type CSSProperties } from "react";

import { PlotlyChart } from "../../components/charts/PlotlyChart";
import {
  OpenPositionsTable,
  type OpenPositionRow,
} from "../../components/panels/OpenPositionsTable";

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

interface EquityPoint {
  timestamp: string;     // ISO with TZ
  net_liq_usd: number | null;
  is_eod: boolean;
}

type EquityWindow = "1d" | "7d" | "30d" | "1y" | "all";

// Panel B (Equity curve) — cap on the number of points rendered to keep
// the chart responsive. The server already downsamples via SQL bucketing
// but the larger windows ("1y" / "all") can still return ~2k samples ;
// we keep the most recent N to stay snappy.
const EQUITY_MAX_POINTS = 1500;

interface PnlAttribRow {
  id: number;
  source: "booked" | "ib_live";
  structure: string | null;
  product_label: string | null;
  side: string | null;
  actual_pnl_usd: number | null;
  delta_pnl_usd: number | null;
  gamma_pnl_usd: number | null;
  vega_pnl_usd: number | null;
  theta_pnl_usd: number | null;
  residual_usd: number | null;
}

interface PnlAttribution {
  lookback_hours: number;
  computed_at: string;
  totals: {
    actual_pnl_usd: number | null;
    delta_pnl_usd: number | null;
    gamma_pnl_usd: number | null;
    vega_pnl_usd: number | null;
    theta_pnl_usd: number | null;
    residual_usd: number | null;
  };
  per_position: PnlAttribRow[];
}

type AttribWindow = 1 | 6 | 24 | 168;

interface PinRiskRow {
  id: number;
  structure: string;
  product_label: string | null;
  side: string | null;
  option_type: "CALL" | "PUT";
  strike: number;
  expiry: string;
  dte_days: number;
  qty: number;
  distance_pips: number;
  pnl_now_usd: number | null;
  delta_usd: number | null;
  pnl_at_pin_usd: number;
  pnl_at_breach_up_usd: number;
  pnl_at_breach_dn_usd: number;
}

interface PinRiskPayload {
  current_spot: number | null;
  breach_bps: number;
  rows: PinRiskRow[];
  n_options: number;
}

interface ScenarioPoint {
  pnl_usd: number;
  delta_usd: number;
  gamma_usd_per_pip: number;
  vega_usd_per_volpt: number;
  theta_usd_per_day: number;
}

interface ScenarioSpotPoint extends ScenarioPoint {
  step_pct: number;
  spot: number;
}

interface ScenarioIvPoint extends ScenarioPoint {
  step_vp: number;
}

interface ScenariosPayload {
  current_spot: number | null;
  current_iv_avg_pct: number | null;
  spot_steps_pct: number[];
  iv_steps_volpt: number[];
  by_spot: ScenarioSpotPoint[];
  by_iv: ScenarioIvPoint[];
  n_positions: number;
}

const fmtUsdAbs = (n: number | null | undefined): string =>
  n === null || n === undefined ? "—" : `${Math.round(n).toLocaleString()}$`;
const fmtPct = (n: number | null | undefined, d = 2): string =>
  n === null || n === undefined ? "—" : `${(n * 100).toFixed(d)}%`;
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

const delta = (cur: number | null, prev: number | null): number | null =>
  cur === null || prev === null ? null : cur - prev;

export function Portfolio(): JSX.Element {
  const [header, setHeader] = useState<HeaderSummary | null>(null);
  const [account, setAccount] = useState<AccountResponse | null>(null);
  const [stress, setStress] = useState<StressGridPayload | null>(null);
  const [ladder, setLadder] = useState<GreeksLadderPayload | null>(null);
  const [vegaTenor, setVegaTenor] = useState<VegaTenorRow[]>([]);
  const [positions, setPositions] = useState<OpenPositionRow[]>([]);
  const [equity, setEquity] = useState<EquityPoint[]>([]);
  const [equityWindow, setEquityWindow] = useState<EquityWindow>("30d");
  const [attrib, setAttrib] = useState<PnlAttribution | null>(null);
  const [attribWindow, setAttribWindow] = useState<AttribWindow>(24);
  const [pinRisk, setPinRisk] = useState<PinRiskPayload | null>(null);
  const [scenarios, setScenarios] = useState<ScenariosPayload | null>(null);
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
  const refreshPositions  = async () => setPositions(await fetchJson<OpenPositionRow[]>("/api/v1/positions/open"));
  const refreshEquity     = async (w: EquityWindow) =>
    setEquity(await fetchJson<EquityPoint[]>(`/api/v1/portfolio/equity-curve?window=${w}`));
  const refreshAttrib     = async (h: AttribWindow) =>
    setAttrib(await fetchJson<PnlAttribution>(`/api/v1/portfolio/pnl-attribution?lookback_hours=${h}`));
  const refreshPinRisk    = async () =>
    setPinRisk(await fetchJson<PinRiskPayload>("/api/v1/portfolio/pin-risk"));
  const refreshScenarios  = async () =>
    setScenarios(await fetchJson<ScenariosPayload>("/api/v1/portfolio/scenarios"));

  const inFlightRef = useRef(false);
  const refreshAll = async () => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    try {
      await Promise.all([
        refreshHeader(), refreshAccount(),
        refreshStress(), refreshLadder(), refreshVegaTenor(),
        refreshPositions(), refreshEquity(equityWindow),
        refreshAttrib(attribWindow), refreshPinRisk(),
        refreshScenarios(),
      ]);
      setError(null);
    } catch (e) { setError(String(e)); }
    finally { inFlightRef.current = false; }
  };

  useEffect(() => {
    void refreshAll();
    const id = window.setInterval(() => void refreshAll(), 5_000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [equityWindow, attribWindow]);

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

          {/* C2 : per-currency breakdown + book-level risk summary +
                  risk utilization (folded from ex-Panel K). */}
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
                  <KV label={`VaR 1d 99% (${header?.var_1d_99.n_days ?? 0}d)`}
                      value={fmtCompactSigned(header?.var_1d_99.usd ?? null, "$")}
                      delta={header?.var_1d_99.usd ?? null} />
                </tbody>
              </table>
            </SubBlock>
            <SubBlock title="Risk utilization (% NetLiq)">
              <RiskUtilizationTable account={latest} header={header} />
            </SubBlock>
          </div>
        </div>
      </Section>

      {/* SECTION B — Equity curve (NetLiq series). Window selector
          (1d/7d/30d/1y/all) drives the lookback + downsampling.
          Server-side bucketing keeps the payload ≤ 2k points ; we
          further cap at EQUITY_MAX_POINTS (most recent) for render speed. */}
      <Section title={`B · Equity curve (${Math.min(equity.length, EQUITY_MAX_POINTS)} / ${equity.length} points, window=${equityWindow})`}>
        <EquityCurve
          points={equity.slice(-EQUITY_MAX_POINTS)}
          window={equityWindow}
          onWindowChange={setEquityWindow}
        />
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

      {/* SECTION E — open positions (shared component, also mounted in
          Trade Pre/Post Panel B). Backed by ``open_position`` /
          ``open_position_history`` after migration 033. */}
      <Section title={`Open positions (${positions.length})`}>
        <OpenPositionsTable positions={positions} />
      </Section>

      {/* SECTION Scenarios — 5 charts : PnL vs spot (always)
          + Δ / Γ / Vega / Θ each with a (spot|vol) axis toggle.
          Full reval of the live book per shock step. */}
      <Section title={`Scenarios (${scenarios?.n_positions ?? 0} positions)`}>
        <ScenariosPanel data={scenarios} />
      </Section>

      {/* SECTION G — P&L attribution daily (greeks decomposition).
          Cf. risk_dashboard_spec.md § G. Backend support partial : the
          per-greek P&L breakdown lives on `position_mtm_history` for
          booked structures, but is None on IB-live rows until a t-1
          state-store is wired. Cells display "—" when the breakdown
          is not available. */}
      <Section title={`G · P&L attribution daily (${positions.length} positions)`}>
        <PnlAttribution
          attrib={attrib}
          window={attribWindow}
          onWindowChange={setAttribWindow}
        />
      </Section>

      {/* SECTION J — pin risk grid (spec § J). Panel K (Margin / SPAN /
          buffer) was dropped here — SPAN scenarios required IB
          RiskNavigator (out of scope for this project's scale), and the
          Margin / Greek exposure / Buffer rows were folded into Panel A
          "Risk utilization" sub-block. */}
      <PinRiskSection data={pinRisk} />

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
// Panel G — P&L attribution. Reads /api/v1/portfolio/pnl-attribution which
// performs the Taylor decomposition server-side over a user-chosen lookback
// window (1h / 6h / 1d / 7d). Each position contributes :
//   actual_pnl  = pnl_now - pnl_then
//   delta_pnl   = Δ × dspot
//   gamma_pnl   = 0.5 × Γ × dspot²
//   vega_pnl    = V × div
//   theta_pnl   = Θ × dt
//   residual    = actual - (delta+gamma+vega+theta)
// Cells "—" when t-1 state is missing (e.g. new position opened after the
// lookback cutoff).
function PnlAttribution({
  attrib, window, onWindowChange,
}: {
  attrib: PnlAttribution | null;
  window: AttribWindow;
  onWindowChange: (w: AttribWindow) => void;
}): JSX.Element {
  const windows: { v: AttribWindow; label: string }[] = [
    { v: 1, label: "1h" }, { v: 6, label: "6h" },
    { v: 24, label: "1d" }, { v: 168, label: "7d" },
  ];

  const rawRows = attrib?.per_position ?? [];
  // Drop positions that have zero useful data (all greek contributions
  // null AND actual_pnl null). Booked rows with no t-1 snapshot pollute
  // the table otherwise.
  const rows = rawRows.filter((r) =>
    r.actual_pnl_usd != null
      || r.delta_pnl_usd != null
      || r.gamma_pnl_usd != null
      || r.vega_pnl_usd != null
      || r.theta_pnl_usd != null,
  );

  const cellColor = (v: number | null | undefined): string =>
    v == null ? "#888" : v > 0 ? "#9f9" : v < 0 ? "#fcc" : "#fff";
  const cellTxt = (v: number | null | undefined): string =>
    v == null ? "—" : fmtCompactSigned(v, "$");
  const rowLabel = (r: PnlAttribRow): string =>
    `${r.product_label ?? r.structure ?? "#" + r.id} (${r.side ?? r.source})`;

  const T = attrib?.totals;

  return (
    <div>
      <div style={{ display: "flex", gap: 4, marginBottom: 8 }}>
        {windows.map((w) => (
          <button
            key={w.v}
            onClick={() => onWindowChange(w.v)}
            style={{
              padding: "3px 10px",
              background: w.v === window ? "#7af" : "#1f2330",
              color: w.v === window ? "#0f1115" : "#aaa",
              border: "1px solid #333",
              borderRadius: 3,
              cursor: "pointer",
              fontSize: 11,
              fontWeight: w.v === window ? 700 : 400,
            }}
          >{w.label}</button>
        ))}
        <div style={{ marginLeft: 12, fontSize: 11, color: "#888", alignSelf: "center" }}>
          Taylor decomposition over the selected lookback. Empty positions
          (no t-1 snapshot) are filtered out for readability.
        </div>
      </div>
      {rows.length === 0 ? <Empty /> : (
        <table style={{ ...tableStyle, fontFamily: "Consolas, monospace" }}>
          <thead>
            <tr>
              <th style={{ ...th, textAlign: "left" }}>Position</th>
              <th style={{ ...th, textAlign: "right" }}>Δ contrib</th>
              <th style={{ ...th, textAlign: "right" }}>Γ contrib</th>
              <th style={{ ...th, textAlign: "right" }}>Vega contrib</th>
              <th style={{ ...th, textAlign: "right" }}>Θ contrib</th>
              <th style={{ ...th, textAlign: "right" }}>Residual</th>
              <th style={{ ...th, textAlign: "right" }}>Actual P&L</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.source}-${r.id}`}>
                <th style={{ ...th, textAlign: "left", color: "#7af" }}>
                  {rowLabel(r)}
                </th>
                <td style={{ ...td, textAlign: "right", color: cellColor(r.delta_pnl_usd) }}>
                  {cellTxt(r.delta_pnl_usd)}
                </td>
                <td style={{ ...td, textAlign: "right", color: cellColor(r.gamma_pnl_usd) }}>
                  {cellTxt(r.gamma_pnl_usd)}
                </td>
                <td style={{ ...td, textAlign: "right", color: cellColor(r.vega_pnl_usd) }}>
                  {cellTxt(r.vega_pnl_usd)}
                </td>
                <td style={{ ...td, textAlign: "right", color: cellColor(r.theta_pnl_usd) }}>
                  {cellTxt(r.theta_pnl_usd)}
                </td>
                <td style={{ ...td, textAlign: "right", color: cellColor(r.residual_usd) }}>
                  {cellTxt(r.residual_usd)}
                </td>
                <td style={{ ...td, textAlign: "right",
                             color: cellColor(r.actual_pnl_usd), fontWeight: 600 }}>
                  {cellTxt(r.actual_pnl_usd)}
                </td>
              </tr>
            ))}
            {/* Totals row */}
            <tr style={{ background: "#1a1a1a", fontWeight: 700 }}>
              <th style={{ ...th, textAlign: "left", color: "#fff", fontWeight: 700 }}>
                TOTAL
              </th>
              <td style={{ ...td, textAlign: "right",
                           color: cellColor(T?.delta_pnl_usd ?? null), fontWeight: 700 }}>
                {cellTxt(T?.delta_pnl_usd ?? null)}
              </td>
              <td style={{ ...td, textAlign: "right",
                           color: cellColor(T?.gamma_pnl_usd ?? null), fontWeight: 700 }}>
                {cellTxt(T?.gamma_pnl_usd ?? null)}
              </td>
              <td style={{ ...td, textAlign: "right",
                           color: cellColor(T?.vega_pnl_usd ?? null), fontWeight: 700 }}>
                {cellTxt(T?.vega_pnl_usd ?? null)}
              </td>
              <td style={{ ...td, textAlign: "right",
                           color: cellColor(T?.theta_pnl_usd ?? null), fontWeight: 700 }}>
                {cellTxt(T?.theta_pnl_usd ?? null)}
              </td>
              <td style={{ ...td, textAlign: "right",
                           color: cellColor(T?.residual_usd ?? null), fontWeight: 700 }}>
                {cellTxt(T?.residual_usd ?? null)}
              </td>
              <td style={{ ...td, textAlign: "right",
                           color: cellColor(T?.actual_pnl_usd ?? null), fontWeight: 700 }}>
                {cellTxt(T?.actual_pnl_usd ?? null)}
              </td>
            </tr>
          </tbody>
        </table>
      )}
    </div>
  );
}

// Compact risk-utilization table folded into Panel A — Risk utilization
// sub-block (used to be the standalone Panel K, dropped along with SPAN
// scenarios which required IB RiskNavigator out-of-scope for this project).
//
// Each row : Value | % NetLiq with color coding (green<75%, amber 75-90%,
// red ≥90%). SPAN section was removed entirely.
function RiskUtilizationTable({
  account, header,
}: { account: AccountSnap | null; header: HeaderSummary | null }): JSX.Element {
  const netLiq      = account?.net_liq_usd       ?? null;
  const initMargin  = account?.init_margin_req   ?? null;
  const maintMargin = account?.maint_margin_req  ?? null;
  const excess      = account?.excess_liquidity  ?? null;
  const cushion     = account?.cushion           ?? null;
  const deltaUsd    = header?.greeks.delta_usd   ?? null;
  const vegaUsd     = header?.greeks.vega_usd    ?? null;
  const gammaUsd    = header?.greeks.gamma_usd   ?? null;
  const thetaUsd    = header?.greeks.theta_usd   ?? null;

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
    pct: number | null;
    valueFmt?: (v: number) => string;
    section: "margin" | "exposure" | "buffer";
  };
  const rows: Row[] = [
    { label: "Init margin",      value: initMargin,  pct: utilPct(initMargin, netLiq),  section: "margin" },
    { label: "Maint margin",     value: maintMargin, pct: utilPct(maintMargin, netLiq), section: "margin" },
    { label: "Δ exposure",       value: deltaUsd,    pct: utilPct(deltaUsd, netLiq),    section: "exposure" },
    { label: "Γ exposure",       value: gammaUsd,    pct: utilPct(gammaUsd, netLiq),    section: "exposure" },
    { label: "Vega exposure",    value: vegaUsd,     pct: utilPct(vegaUsd, netLiq),     section: "exposure" },
    { label: "Θ exposure",       value: thetaUsd,    pct: utilPct(thetaUsd, netLiq),    section: "exposure" },
    { label: "Excess liquidity", value: excess,      pct: null, section: "buffer" },
    {
      label: "Liquidation cushion",
      value: cushion != null && netLiq != null ? cushion * netLiq : null,
      valueFmt: (v) => `${fmtUsdAbs(v)} (${cushion != null ? (cushion * 100).toFixed(1) : "—"}%)`,
      pct: null, section: "buffer",
    },
  ];

  const sectionLabel: Record<Row["section"], string> = {
    margin:   "─ Margin ────────────",
    exposure: "─ Greek exposure ────",
    buffer:   "─ Buffer ────────────",
  };

  const trs: JSX.Element[] = [];
  let prevSection: Row["section"] | undefined;
  for (const r of rows) {
    if (r.section !== prevSection) {
      trs.push(
        <tr key={`d-${r.section}`}>
          <td colSpan={3} style={{
            padding: "6px 8px 1px 8px",
            color: "#555", fontSize: 10,
            fontFamily: "Consolas, monospace",
          }}>{sectionLabel[r.section]}</td>
        </tr>
      );
      prevSection = r.section;
    }
    trs.push(
      <tr key={r.label}>
        <th style={{ ...th, textAlign: "left", color: "#7af" }}>{r.label}</th>
        <td style={{ ...td, textAlign: "right", color: "#ddd" }}>
          {r.value == null ? "—" : (r.valueFmt ? r.valueFmt(r.value) : fmtUsdAbs(r.value))}
        </td>
        <td style={{ ...td, textAlign: "right", color: utilColor(r.pct),
                    fontWeight: r.pct != null && r.pct >= 0.75 ? 700 : 500 }}>
          {fmtPctLocal(r.pct)}
        </td>
      </tr>
    );
  }
  return (
    <table style={kvTableStyle}>
      <tbody>{trs}</tbody>
    </table>
  );
}

// Panel B — Equity curve. Line chart of NetLiq over time, with a window
// selector (1d / 7d / 30d / 1y / all). Server-side buckets keep payload
// ≤ 2k points. EOD points are highlighted as markers on top of the line.
function EquityCurve({
  points, window, onWindowChange,
}: {
  points: EquityPoint[];
  window: EquityWindow;
  onWindowChange: (w: EquityWindow) => void;
}): JSX.Element {
  const windows: EquityWindow[] = ["1d", "7d", "30d", "1y", "all"];

  // Drop NULL net_liq points (no value to plot) but keep timestamps aligned.
  const valid = points.filter((p) => p.net_liq_usd != null);
  const xs = valid.map((p) => p.timestamp);
  const ys = valid.map((p) => p.net_liq_usd as number);
  const eodXs = valid.filter((p) => p.is_eod).map((p) => p.timestamp);
  const eodYs = valid.filter((p) => p.is_eod).map((p) => p.net_liq_usd as number);

  const first = ys[0] ?? null;
  const last  = ys[ys.length - 1] ?? null;
  const change = (first != null && last != null) ? last - first : null;
  const changeColor = change == null ? "#888" : change >= 0 ? "#9f9" : "#fcc";

  const data: Plotly.Data[] = [
    {
      x: xs,
      y: ys,
      type: "scatter",
      mode: "lines",
      line: { color: "#7af", width: 1.5 },
      hovertemplate: "%{x|%Y-%m-%d %H:%M}<br>$%{y:,.0f}<extra></extra>",
      name: "NetLiq",
    },
    {
      x: eodXs,
      y: eodYs,
      type: "scatter",
      mode: "markers",
      marker: { color: "#fc6", size: 5 },
      hovertemplate: "EOD %{x|%Y-%m-%d}<br>$%{y:,.0f}<extra></extra>",
      name: "EOD",
    },
  ];

  return (
    <div>
      <div style={{ display: "flex", gap: 16, alignItems: "center", marginBottom: 8 }}>
        <div style={{ display: "flex", gap: 4 }}>
          {windows.map((w) => (
            <button
              key={w}
              onClick={() => onWindowChange(w)}
              style={{
                padding: "3px 10px",
                background: w === window ? "#7af" : "#1f2330",
                color: w === window ? "#0f1115" : "#aaa",
                border: "1px solid #333",
                borderRadius: 3,
                cursor: "pointer",
                fontSize: 11,
                fontWeight: w === window ? 700 : 400,
              }}
            >{w}</button>
          ))}
        </div>
        <div style={{ fontSize: 11, color: "#aaa" }}>
          {first != null && last != null && (
            <>
              <span style={{ color: "#888" }}>start </span>${first.toLocaleString(undefined, { maximumFractionDigits: 0 })}
              <span style={{ color: "#888", marginLeft: 12 }}>end </span>${last.toLocaleString(undefined, { maximumFractionDigits: 0 })}
              <span style={{ color: changeColor, marginLeft: 12, fontWeight: 600 }}>
                {change != null && change >= 0 ? "▲" : "▼"} ${Math.abs(change ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}
              </span>
            </>
          )}
        </div>
      </div>
      {valid.length === 0 ? (
        <div style={{ padding: 20, color: "#888", textAlign: "center", fontSize: 12 }}>
          No equity points for this window.
        </div>
      ) : (
        <PlotlyChart
          data={data}
          height={260}
          layout={{
            xaxis: { type: "date", gridcolor: "#262a33" },
            yaxis: { tickprefix: "$", tickformat: ",.0f", gridcolor: "#262a33" },
            margin: { t: 10, r: 10, b: 30, l: 60 },
          }}
        />
      )}
    </div>
  );
}

// Panel J — pin risk grid. Reads /api/v1/portfolio/pin-risk which does the
// full BS revaluation server-side: NPV at strike (pin), NPV at strike ±
// 50 bp (breach). Replaces the earlier client-side linearisation (Δ × ΔS)
// which was incorrect near expiry where Γ dominates.
function PinRiskSection({ data }: { data: PinRiskPayload | null }): JSX.Element {
  const rows = data?.rows ?? [];
  const spot = data?.current_spot ?? null;
  const breachBps = data?.breach_bps ?? 50;

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
            <th style={{ ...th, textAlign: "center" }}>P&L if pin (S→K)</th>
            <th style={{ ...th, textAlign: "center" }}>P&L breach +{breachBps}bp</th>
            <th style={{ ...th, textAlign: "center" }}>P&L breach -{breachBps}bp</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const optLabel = `${r.option_type} ${r.strike.toFixed(5)} × ${r.qty}`;
            return (
              <tr key={r.id}>
                <th style={{ ...th, textAlign: "left", color: "#7af" }}>
                  {optLabel}
                </th>
                <td style={{ ...td, textAlign: "center",
                            color: r.dte_days <= 7 ? "#fc6" : "#ddd",
                            fontWeight: r.dte_days <= 7 ? 700 : 400 }}>
                  {r.dte_days}d
                </td>
                <td style={{ ...td, textAlign: "center" }}>{r.strike.toFixed(5)}</td>
                <td style={{ ...td, textAlign: "center" }}>{spot != null ? spot.toFixed(5) : "—"}</td>
                <td style={{ ...td, textAlign: "center",
                            color: r.distance_pips === 0 ? "#fff" : "#ddd" }}>
                  {r.distance_pips > 0 ? "+" : ""}{r.distance_pips}
                </td>
                <td style={{ ...td, textAlign: "center", color: cellColor(r.delta_usd) }}>
                  {cellTxt(r.delta_usd)}
                </td>
                <td style={{ ...td, textAlign: "center", color: cellColor(r.pnl_now_usd) }}>
                  {cellTxt(r.pnl_now_usd)}
                </td>
                <td style={{ ...td, textAlign: "center", color: cellColor(r.pnl_at_pin_usd) }}>
                  {cellTxt(r.pnl_at_pin_usd)}
                </td>
                <td style={{ ...td, textAlign: "center", color: cellColor(r.pnl_at_breach_up_usd) }}>
                  {cellTxt(r.pnl_at_breach_up_usd)}
                </td>
                <td style={{ ...td, textAlign: "center", color: cellColor(r.pnl_at_breach_dn_usd) }}>
                  {cellTxt(r.pnl_at_breach_dn_usd)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div style={{ fontSize: 10, color: "#666", marginTop: 6, lineHeight: 1.5 }}>
        Full BS revaluation server-side at the same T and IV. "P&L if pin" = NPV(spot=K) - NPV(now).
        "P&L breach ±{breachBps}bp" = NPV(spot=K±{breachBps}bp) - NPV(now). DTE in <span style={{ color: "#fc6" }}>amber</span> when ≤ 7d (high pin risk window).
      </div>
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


// ──────────────────────────────────────────────────────────────────────
// ScenariosPanel — 5 Plotly charts. Chart 1 = PnL vs spot (current spot
// highlighted). Charts 2-5 = Δ / Γ / Vega / Θ vs (spot|vol) with a
// shared (spot|vol) toggle. Axis 'spot' uses the spot shock grid (% move),
// axis 'vol' uses the IV shift grid (vol-points).
// ──────────────────────────────────────────────────────────────────────

type ScenarioAxis = "spot" | "vol";

function ScenariosPanel({ data }: { data: ScenariosPayload | null }): JSX.Element {
  const [axis, setAxis] = useState<ScenarioAxis>("spot");

  if (!data || (data.by_spot.length === 0 && data.by_iv.length === 0)) {
    return (
      <div style={{ color: "#888", fontStyle: "italic", padding: 12, fontSize: 12 }}>
        No scenarios available (no open positions or pricing data missing).
      </div>
    );
  }

  const points = axis === "spot" ? data.by_spot : data.by_iv;
  const xValues: number[] = axis === "spot"
    ? (data.by_spot.map((p) => p.step_pct))
    : (data.by_iv.map((p) => p.step_vp));
  const xLabel = axis === "spot" ? "Spot shock (%)" : "IV shock (vol-pt)";
  const currentX = 0;

  const xMarkerLine = (yMin: number, yMax: number): Plotly.Data => ({
    x: [currentX, currentX], y: [yMin, yMax],
    type: "scatter", mode: "lines",
    line: { color: "#888", width: 1, dash: "dot" },
    hoverinfo: "skip", showlegend: false,
  });

  const lineTrace = (
    ys: number[], color: string, name: string,
  ): Plotly.Data => ({
    x: xValues, y: ys,
    type: "scatter", mode: "lines+markers",
    line: { color, width: 2 },
    marker: { color, size: 4 },
    name,
    hovertemplate: `${name}: %{y:,.0f}<extra></extra>`,
  });

  // PnL chart is hardcoded to spot axis below — no toggle.
  const deltaYs = points.map((p) => p.delta_usd);
  const gammaYs = points.map((p) => p.gamma_usd_per_pip);
  const vegaYs = points.map((p) => p.vega_usd_per_volpt);
  const thetaYs = points.map((p) => p.theta_usd_per_day);

  const yRange = (ys: number[]): [number, number] => {
    if (ys.length === 0) return [-1, 1];
    const lo = Math.min(...ys), hi = Math.max(...ys);
    const pad = Math.max((hi - lo) * 0.1, Math.abs(hi) * 0.05, 1);
    return [lo - pad, hi + pad];
  };

  const chart = (
    title: string, ys: number[], color: string, fmt: string,
  ): JSX.Element => {
    const [lo, hi] = yRange(ys);
    return (
      <div style={{
        background: "#10141c", border: "1px solid #1f2937", borderRadius: 4,
        padding: 8,
      }}>
        <div style={{ color: "#aef", fontWeight: 600, fontSize: 11,
                      marginBottom: 4, textAlign: "center" }}>
          {title}
        </div>
        <PlotlyChart
          data={[lineTrace(ys, color, title), xMarkerLine(lo, hi)]}
          height={200}
          layout={{
            xaxis: { title: { text: xLabel, font: { size: 9 } },
                     gridcolor: "#262a33", zeroline: false },
            yaxis: { gridcolor: "#262a33", zeroline: true,
                     zerolinecolor: "#444",
                     tickprefix: fmt === "$" ? "$" : "",
                     tickformat: ",.0f" },
            margin: { t: 6, r: 6, b: 32, l: 60 },
          }}
        />
      </div>
    );
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {/* Axis toggle (only applies to charts 2-5 ; chart 1 is always spot). */}
      <div style={{ display: "flex", alignItems: "center", gap: 8,
                    fontSize: 11, color: "#aaa" }}>
        <span>Greeks axis:</span>
        {(["spot", "vol"] as ScenarioAxis[]).map((a) => (
          <button
            key={a}
            onClick={() => setAxis(a)}
            style={{
              padding: "3px 10px", borderRadius: 3,
              border: "1px solid #333", cursor: "pointer", fontSize: 11,
              background: axis === a ? "#7af" : "#1f2330",
              color: axis === a ? "#0f1115" : "#aaa",
              fontWeight: axis === a ? 700 : 400,
            }}
          >{a}</button>
        ))}
        <span style={{ marginLeft: 16, color: "#666", fontSize: 10 }}>
          current : spot={data.current_spot ?? "—"}
          {data.current_iv_avg_pct != null
            ? `, iv avg ${data.current_iv_avg_pct}%` : ""}
        </span>
      </div>

      {/* 5-chart grid : PnL chart always vs spot (chart 1), then 4 greeks. */}
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(5, minmax(0, 1fr))",
        gap: 10,
      }}>
        {/* Chart 1 — PnL always vs spot (no toggle) */}
        <div style={{
          background: "#10141c", border: "1px solid #1f2937", borderRadius: 4,
          padding: 8,
        }}>
          <div style={{ color: "#aef", fontWeight: 600, fontSize: 11,
                        marginBottom: 4, textAlign: "center" }}>
            P&amp;L vs spot
          </div>
          {(() => {
            const [lo, hi] = yRange(data.by_spot.map((p) => p.pnl_usd));
            return (
              <PlotlyChart
                data={[
                  lineTrace(
                    data.by_spot.map((p) => p.pnl_usd),
                    "#fc6", "P&L",
                  ),
                  xMarkerLine(lo, hi),
                ]}
                height={200}
                layout={{
                  xaxis: {
                    title: { text: "Spot shock (%)", font: { size: 9 } },
                    gridcolor: "#262a33", zeroline: false,
                  },
                  yaxis: {
                    gridcolor: "#262a33", zeroline: true,
                    zerolinecolor: "#444",
                    tickprefix: "$", tickformat: ",.0f",
                  },
                  margin: { t: 6, r: 6, b: 32, l: 60 },
                }}
              />
            );
          })()}
        </div>

        {/* Charts 2-5 use the toggled axis */}
        {chart("Δ vs " + axis, deltaYs, "#7af", "$")}
        {chart("Γ vs " + axis, gammaYs, "#9f9", "$")}
        {chart("Vega vs " + axis, vegaYs, "#bcf", "$")}
        {chart("Θ vs " + axis, thetaYs, "#fa9", "$")}
      </div>
    </div>
  );
}
