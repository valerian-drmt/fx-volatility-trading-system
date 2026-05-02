/**
 * Step 3 — Trade preview panel.
 *
 * Layout :
 *   GREEN  block  : explicit user inputs (product, side, tenor, delta, strike, size)
 *   YELLOW block  : market data auto-fetched (spot, IV per pillar of the chosen tenor)
 *   RED    block  : computed outputs after Review (premium, greeks)
 *   GREY   block  : pre-submit checks (below the 3 coloured ones)
 *
 * 6 products : Future / Butterfly / Straddle / Strangle / Vanilla call / Vanilla put.
 * Side = BUY / SELL universally.
 * Delta buttons (10Δp, 25Δp, ATM, 25Δc, 10Δc) auto-fill the strike input from the
 * live surface — user can edit the strike afterwards.
 *
 * Two buttons :
 *   - Review : compute the red block.
 *   - Book   : placeholder for STEP4 execution (disabled).
 */
import { useEffect, useState } from "react";

type Product = "future" | "butterfly" | "straddle" | "strangle" | "calendar" | "vanilla_call" | "vanilla_put";
type Side = "buy" | "sell";
type Pillar = "10dp" | "25dp" | "atm" | "25dc" | "10dc";

interface Leg {
  leg_idx: number;
  contract_type: "call" | "put" | "future";
  tenor: string;
  expiry: string;
  dte: number;
  strike: number | null;
  qty_factor: number;
  qty: number;
  side: "BUY" | "SELL";
  entry_iv_pct: number | null;
  entry_price_per_contract_usd: number;
}
interface Greeks {
  vega_usd_per_volpt: number; gamma_usd_per_pip2: number;
  theta_usd_per_day: number; delta_unhedged: number; delta_post_hedge: number;
}
interface LegGreeks {
  leg_idx: number; type: "call" | "put" | "future"; strike: number | null;
  side: "BUY" | "SELL"; qty_factor: number;
  vega: number; gamma: number; theta: number; delta: number;
}
interface PnlGridCell { div_volpts: number; pnl_usd: number; is_current: boolean }
interface PnlGridRow { ds_pct: number; cells: PnlGridCell[] }
interface PnlGrid {
  spot_moves_pct: number[]; iv_moves_volpts: number[]; rows: PnlGridRow[];
}
interface Preview {
  preview_id: string;
  mode: "manual" | "from_signal";
  structure: { type: string; reference_tenor: string; tenor_far: string | null;
               requires_delta_hedge: boolean; vega_sign: string; legs: Leg[] };
  greeks_net: Greeks;
  pricing: { premium_paid_usd: number; max_loss_usd: number;
             max_loss_at_expiry_only: boolean; breakeven_pips_each_side: number | null };
  costs?: { premium_per_contract_usd: number; commission_usd: number; total_trade_cost_usd: number };
  legs_greeks?: LegGreeks[];
  pnl_grid?: PnlGrid;
  state: "valid_for_submit" | "blocked" | "expired" | "submitted" | "cancelled";
  blocking_reasons: string[];
  surface_age_seconds: number;
  pre_submit_checks: { name: string; passed: boolean; details: Record<string, unknown> }[];
}

interface SurfaceNode { iv?: number; strike?: number }
type SurfaceTenor = Partial<Record<Pillar, SurfaceNode>>;

const TENORS = ["1M", "2M", "3M", "4M", "5M", "6M"] as const;
const PILLARS: Pillar[] = ["10dp", "25dp", "atm", "25dc", "10dc"];
const PILLAR_LABEL: Record<Pillar, string> = {
  "10dp": "10Δp", "25dp": "25Δp", "atm": "ATM", "25dc": "25Δc", "10dc": "10Δc",
};

const PRODUCTS: { value: Product; label: string }[] = [
  { value: "future",       label: "Future" },
  { value: "butterfly",    label: "Butterfly" },
  { value: "straddle",     label: "Straddle" },
  { value: "strangle",     label: "Strangle" },
  { value: "calendar",     label: "Calendar" },
  { value: "vanilla_call", label: "Vanilla call" },
  { value: "vanilla_put",  label: "Vanilla put" },
];

// (product, side) → backend structure_type. side=buy → long, sell → short.
const STRUCTURE_MAP: Record<Product, Record<Side, string>> = {
  future:       { buy: "future_buy",         sell: "future_sell" },
  butterfly:    { buy: "long_butterfly_25d", sell: "short_butterfly_25d" },
  straddle:     { buy: "straddle_atm",       sell: "short_straddle_atm" },
  strangle:     { buy: "long_strangle_25d",  sell: "short_strangle" },
  calendar:     { buy: "calendar_long",      sell: "calendar_short" },
  vanilla_call: { buy: "vanilla_call",       sell: "short_vanilla_call" },
  vanilla_put:  { buy: "vanilla_put",        sell: "short_vanilla_put" },
};

// Single-leg products : strike override + IV override fully controlled by user.
const SINGLE_LEG: ReadonlySet<Product> = new Set(["vanilla_call", "vanilla_put", "future"]);

// All option products accept delta override ; only Future stays at template.
const ACCEPTS_DELTA_OVERRIDE: ReadonlySet<Product> = new Set([
  "vanilla_call", "vanilla_put", "straddle", "strangle", "butterfly", "calendar",
]);

// Products that need a far tenor (calendar only).
const REQUIRES_FAR_TENOR: ReadonlySet<Product> = new Set(["calendar"]);

// Color palette per the user spec.
const GREEN_BG = "rgba(34, 197, 94, 0.13)";
const GREEN_BORDER = "#22c55e";
const YELLOW_BG = "rgba(234, 179, 8, 0.13)";
const YELLOW_BORDER = "#eab308";
const RED_BG = "rgba(239, 68, 68, 0.13)";
const RED_BORDER = "#ef4444";

export function Step3Trade(): JSX.Element {
  // GREEN inputs
  const [product, setProduct] = useState<Product>("straddle");
  const [side, setSide] = useState<Side>("buy");
  const [tenor, setTenor] = useState<string>("3M");
  const [tenorFar, setTenorFar] = useState<string>("6M");
  // `delta` is hidden internal state — last button clicked. Used to compute
  // the strike auto-fill AND sent to the backend as delta_pillar override.
  const [delta, setDelta] = useState<Pillar>("atm");
  const [strikeStr, setStrikeStr] = useState<string>("");
  const [qty, setQty] = useState<number>(10);

  // YELLOW market data
  const [spot, setSpot] = useState<number | null>(null);
  const [surfaceTenors, setSurfaceTenors] = useState<Record<string, SurfaceTenor>>({});
  const [surfaceAge, setSurfaceAge] = useState<number | null>(null);

  // RED outputs
  const [preview, setPreview] = useState<Preview | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [booking, setBooking] = useState(false);
  const [bookResult, setBookResult] = useState<{ success: boolean; structure_id?: number; position_id?: number; message?: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const structureType = STRUCTURE_MAP[product][side];
  const isFuture = product === "future";
  const isSingleLeg = SINGLE_LEG.has(product);
  const acceptsDeltaOverride = ACCEPTS_DELTA_OVERRIDE.has(product);
  const needsFarTenor = REQUIRES_FAR_TENOR.has(product);

  // Reset preview + book result on input changes.
  useEffect(() => {
    setPreview(null); setBookResult(null);
  }, [product, side, tenor, tenorFar, delta, strikeStr, qty]);

  // Term-structure poll → spot + ATM IVs.
  useEffect(() => {
    const load = async () => {
      try {
        const r = await fetch("/api/v1/vol/term-structure?symbol=EURUSD");
        if (!r.ok) return;
        const j = await r.json();
        const tenors: Record<string, SurfaceTenor> = {};
        let detectedSpot: number | null = null;
        for (const p of j.pillars ?? []) {
          tenors[p.tenor] = {
            atm: { iv: (p.sigma_atm_pct ?? 0) / 100, strike: p.forward ?? p.strike_atm },
          };
          if (!detectedSpot && p.forward) detectedSpot = p.forward;
        }
        setSurfaceTenors(tenors);
        setSpot(detectedSpot);
      } catch { /* keep last */ }
    };
    void load();
    const id = window.setInterval(load, 5_000);
    return () => window.clearInterval(id);
  }, []);

  // Smile poll for the picked tenors (near + far if calendar). Each gives us
  // 5 (delta, strike, IV) tuples that feed the YELLOW block + the strike
  // auto-fill on delta-button click.
  useEffect(() => {
    if (isFuture) return;
    const tenorsToLoad = needsFarTenor ? [tenor, tenorFar] : [tenor];
    const loadOne = async (t: string) => {
      try {
        const r = await fetch(`/api/v1/vol/smile/${t}?symbol=EURUSD`);
        if (!r.ok) return;
        const j = await r.json();
        setSurfaceTenors((prev) => {
          const next = { ...prev };
          const node: SurfaceTenor = next[t] ?? {};
          for (const p of j.points ?? []) {
            const k = (p.delta_label ?? "").toLowerCase();
            const mapped: Pillar | null =
              k === "atm" ? "atm" :
              k === "10p" || k === "10dp" ? "10dp" :
              k === "25p" || k === "25dp" ? "25dp" :
              k === "25c" || k === "25dc" ? "25dc" :
              k === "10c" || k === "10dc" ? "10dc" : null;
            if (mapped) node[mapped] = { iv: p.iv_pct / 100, strike: p.strike };
          }
          next[t] = node;
          return next;
        });
      } catch { /* */ }
    };
    void Promise.all(tenorsToLoad.map(loadOne));
    const id = window.setInterval(() => Promise.all(tenorsToLoad.map(loadOne)), 5_000);
    return () => window.clearInterval(id);
  }, [tenor, tenorFar, needsFarTenor, isFuture]);

  // Auto-fill strike when delta button or tenor changes. The displayed strike
  // is always the picked pillar's strike — for single-leg products it's also
  // sent to the backend as ``strike_override``; for multi-leg products it is
  // informational (each leg has its own strike, mirrored from delta level).
  useEffect(() => {
    if (isFuture) return;
    const k = surfaceTenors[tenor]?.[delta]?.strike;
    if (k != null) setStrikeStr(k.toFixed(4));
  }, [delta, tenor, isFuture, surfaceTenors]);

  const onBook = async () => {
    if (!preview) return;
    setBooking(true); setError(null); setBookResult(null);
    try {
      const r = await fetch("/api/v1/trade/submit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ preview_id: preview.preview_id }),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail ?? `HTTP ${r.status}`);
      setBookResult({
        success: true, structure_id: j.structure_id, position_id: j.position_id,
        message: `mock fill : ${j.execution_mode}, premium=${j.total_premium_paid_usd ?? 0}, commission=${j.total_commission_usd ?? 0}`,
      });
    } catch (e) {
      setBookResult({ success: false, message: String(e) });
    } finally { setBooking(false); }
  };

  const onReview = async () => {
    setReviewing(true); setError(null); setPreview(null);
    try {
      const body: Record<string, unknown> = {
        structure_type: structureType, tenor, qty,
      };
      if (needsFarTenor) {
        body.tenor_far = tenorFar;
      }
      if (acceptsDeltaOverride) {
        body.delta_pillar = delta;
      }
      if (isSingleLeg && !isFuture) {
        const sNum = parseFloat(strikeStr);
        if (Number.isFinite(sNum) && sNum > 0) body.strike_override = sNum;
      }
      const r = await fetch("/api/v1/trade/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail ?? `HTTP ${r.status}`);
      setPreview(j);
      setSurfaceAge(j.surface_age_seconds ?? null);
    } catch (e) { setError(String(e)); }
    finally { setReviewing(false); }
  };

  // Yellow-block always displays the 5 pillars for the chosen tenor — no
  // structure-specific filtering. User decides what's relevant.
  const pillarsToShow: Pillar[] = isFuture ? [] : PILLARS;

  return (
    <div style={{ padding: 12, display: "grid", gridTemplateColumns: "minmax(0, 720px) minmax(0, 1fr)", gap: 16, alignItems: "start" }}>
      {/* ── LEFT column : the 3 coloured blocks + buttons + pre-submit ── */}
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {/* GREEN — explicit inputs */}
      <Block bg={GREEN_BG} border={GREEN_BORDER} title="Inputs">
        <Row name="Product" value={
          <select value={product} onChange={(e) => setProduct(e.target.value as Product)} style={selectStyle}>
            {PRODUCTS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
          </select>
        } />
        <Row name="Side" value={
          <select value={side} onChange={(e) => setSide(e.target.value as Side)} style={selectStyle}>
            <option value="buy">BUY</option>
            <option value="sell">SELL</option>
          </select>
        } />
        <Row name="Tenor" value={
          <select value={tenor} onChange={(e) => setTenor(e.target.value)} style={selectStyle}>
            {TENORS.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        } />
        {needsFarTenor && (
          <Row name="Far tenor" value={
            <select value={tenorFar} onChange={(e) => setTenorFar(e.target.value)} style={selectStyle}>
              {TENORS.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          } />
        )}
        {!isFuture && (
          <>
            <Row name="Strike" value={
              <input type="number" step="0.0001" value={strikeStr}
                     onChange={(e) => setStrikeStr(e.target.value)}
                     placeholder="click a Δ button below or type"
                     style={{ ...inputStyle, width: 130 }} />
            } />
            <tr>
              <td colSpan={2} style={{ padding: "4px 8px" }}>
                <div style={{ display: "flex", gap: 4, justifyContent: "flex-end" }}>
                  {PILLARS.map((p) => (
                    <button key={p} type="button"
                            onClick={() => setDelta(p)}
                            style={{
                              ...pillarBtnStyle,
                              background: delta === p ? "#1a4a2a" : "#0a0a0a",
                              color: delta === p ? "#cfc" : "#aaa",
                              borderColor: delta === p ? "#22c55e" : "#333",
                              cursor: "pointer",
                            }}
                            title="Click to fill the Strike with this pillar's strike">
                      {PILLAR_LABEL[p]}
                    </button>
                  ))}
                </div>
              </td>
            </tr>
          </>
        )}
        <Row name="Size (contracts)" value={
          <input type="number" min={1} value={qty}
                 onChange={(e) => setQty(Math.max(1, parseInt(e.target.value || "1", 10)))}
                 style={inputStyle} />
        } />
      </Block>

      {/* YELLOW — market data */}
      <Block bg={YELLOW_BG} border={YELLOW_BORDER} title="Market data">
        <Row name="Spot" value={fmt(spot, 4)} />
        <Row name="Surface age (s)" value={surfaceAge !== null ? fmt(surfaceAge, 0) : (surfaceTenors[tenor] ? "≤5" : "—")} />
        {/* All 5 pillars for the near tenor. */}
        {pillarsToShow.map((d) => {
          const node = surfaceTenors[tenor]?.[d];
          return (
            <Row key={`${tenor}-${d}`} name={`IV @ ${tenor} / ${PILLAR_LABEL[d]}`} value={
              node?.iv != null
                ? `${(node.iv * 100).toFixed(2)}%  (K=${node.strike?.toFixed(4) ?? "—"})`
                : "—"
            } />
          );
        })}
        {/* Calendar : repeat the 5 pillars for the far tenor. */}
        {needsFarTenor && pillarsToShow.map((d) => {
          const node = surfaceTenors[tenorFar]?.[d];
          return (
            <Row key={`${tenorFar}-${d}`} name={`IV @ ${tenorFar} / ${PILLAR_LABEL[d]}`} value={
              node?.iv != null
                ? `${(node.iv * 100).toFixed(2)}%  (K=${node.strike?.toFixed(4) ?? "—"})`
                : "—"
            } />
          );
        })}
      </Block>

      {/* Buttons */}
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <button type="button" onClick={onReview} disabled={reviewing}
                style={{ ...btnPrimary, opacity: reviewing ? 0.5 : 1 }}>
          {reviewing ? "Reviewing…" : "Review"}
        </button>
        <button type="button" onClick={onBook}
                disabled={!preview || booking}
                style={{
                  ...btnSecondary,
                  background: preview && !booking ? "#1a6a2a" : "#1a1a1a",
                  color: preview && !booking ? "#cfc" : "#777",
                  cursor: preview && !booking ? "pointer" : "not-allowed",
                  opacity: preview && !booking ? 1 : 0.5,
                  borderColor: preview && !booking ? "#22c55e" : "#444",
                }}
                title={preview ? "Mock execution — creates structure + position in DB (no IB call)" : "Run Review first"}>
          {booking ? "Booking…" : "Book (mock)"}
        </button>
      </div>

      {bookResult && (
        <div style={{
          padding: 8, borderRadius: 3, fontSize: 11, fontFamily: "Consolas, monospace",
          background: bookResult.success ? "rgba(34,197,94,0.10)" : "rgba(239,68,68,0.10)",
          border: `1px solid ${bookResult.success ? "#22c55e" : "#ef4444"}`,
          color: bookResult.success ? "#cfc" : "#fcc",
        }}>
          {bookResult.success
            ? <>✓ booked · structure #{bookResult.structure_id} · position #{bookResult.position_id} · {bookResult.message}</>
            : <>✗ {bookResult.message}</>}
        </div>
      )}

      {error && (
        <div style={{ padding: 8, background: "#3a0a0a", border: "1px solid #b88",
                      color: "#fcc", fontSize: 11, borderRadius: 3 }}>✗ {error}</div>
      )}

      {/* RED — outputs (per-leg greeks + net + costs) */}
      <Block bg={RED_BG} border={RED_BORDER} title="Outputs">
        {!preview ? (
          <div style={{ padding: 8, fontStyle: "italic", color: "#888", fontSize: 11 }}>
            (no review yet — click <strong>Review</strong> above)
          </div>
        ) : (
          <>
            <Row name="Cost per contract (USD)" value={fmt(preview.costs?.premium_per_contract_usd ?? null, 4)} />
            <Row name="Commission (USD)" value={fmt(preview.costs?.commission_usd ?? null, 2)} />
            <Row name="Total trade cost (USD)" value={fmt(preview.costs?.total_trade_cost_usd ?? null, 2)} />
            <Divider />
            {preview.legs_greeks && preview.legs_greeks.length > 0 && (
              <tr>
                <td colSpan={2} style={{ padding: "4px 0" }}>
                  <LegsGreeksTable legs={preview.legs_greeks} net={preview.greeks_net} />
                </td>
              </tr>
            )}
          </>
        )}
      </Block>

      </div>{/* end LEFT column */}

      {/* ── RIGHT column : P&L grid 2D ── */}
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {preview?.pnl_grid && <PnlGrid2DTable grid={preview.pnl_grid} />}
      </div>

    </div>
  );
}

function LegsGreeksTable({ legs, net }: { legs: LegGreeks[]; net: Greeks }): JSX.Element {
  return (
    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11,
                    fontFamily: "Consolas, monospace" }}>
      <thead>
        <tr style={{ color: "#aaa", fontSize: 10 }}>
          <th style={ggThStyle}>Leg</th>
          <th style={ggThStyle}>Type</th>
          <th style={ggThStyle}>Strike</th>
          <th style={ggThStyle}>Side</th>
          <th style={ggThStyle}>Qty</th>
          <th style={ggThStyle}>Vega</th>
          <th style={ggThStyle}>Gamma</th>
          <th style={ggThStyle}>Theta</th>
          <th style={ggThStyle}>Delta</th>
        </tr>
      </thead>
      <tbody>
        {legs.map((leg) => (
          <tr key={leg.leg_idx}>
            <td style={ggTdStyle}>{leg.leg_idx}</td>
            <td style={ggTdStyle}>{leg.type}</td>
            <td style={ggTdStyle}>{leg.strike?.toFixed(4) ?? "—"}</td>
            <td style={{ ...ggTdStyle, color: leg.side === "BUY" ? "#6c6" : "#e66" }}>{leg.side}</td>
            <td style={ggTdStyle}>{leg.qty_factor}</td>
            <td style={ggTdStyle}>{signedFmt(leg.vega, 2)}</td>
            <td style={ggTdStyle}>{signedFmt(leg.gamma, 4)}</td>
            <td style={ggTdStyle}>{signedFmt(leg.theta, 2)}</td>
            <td style={ggTdStyle}>{signedFmt(leg.delta, 3)}</td>
          </tr>
        ))}
        <tr style={{ borderTop: "1px solid #444", fontWeight: 700, color: "#7af" }}>
          <td colSpan={5} style={{ ...ggTdStyle, textAlign: "left", color: "#7af" }}>NET</td>
          <td style={ggTdStyle}>{signedFmt(net.vega_usd_per_volpt, 2)}</td>
          <td style={ggTdStyle}>{signedFmt(net.gamma_usd_per_pip2, 4)}</td>
          <td style={ggTdStyle}>{signedFmt(net.theta_usd_per_day, 2)}</td>
          <td style={ggTdStyle}>{signedFmt(net.delta_unhedged, 3)}</td>
        </tr>
      </tbody>
    </table>
  );
}

function PnlGrid2DTable({ grid }: { grid: PnlGrid }): JSX.Element {
  // Find the max abs(pnl) across the grid for colour scaling.
  const allPnl = grid.rows.flatMap((r) => r.cells.map((c) => c.pnl_usd));
  const maxAbs = Math.max(...allPnl.map((v) => Math.abs(v)), 1);
  const cellBg = (pnl: number) => {
    const intensity = Math.min(Math.abs(pnl) / maxAbs, 1);
    return pnl >= 0
      ? `rgba(34,197,94,${0.10 + 0.55 * intensity})`
      : `rgba(239,68,68,${0.10 + 0.55 * intensity})`;
  };
  return (
    <section style={{
      background: RED_BG, borderLeft: `3px solid ${RED_BORDER}`,
      borderRadius: 3, padding: 8, overflowX: "auto",
    }}>
      <div style={{ fontSize: 10, color: RED_BORDER, fontWeight: 700, letterSpacing: 1,
                    textTransform: "uppercase", marginBottom: 6 }}>
        P&L grid — ΔS × ΔIV
      </div>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11,
                      fontFamily: "Consolas, monospace" }}>
        <thead>
          <tr style={{ color: "#aaa", fontSize: 10 }}>
            <th style={{ ...ggThStyle, textAlign: "left" }}>Δspot \ ΔIV</th>
            {grid.iv_moves_volpts.map((iv) => (
              <th key={iv} style={ggThStyle}>
                {iv >= 0 ? "+" : ""}{iv.toFixed(1)}v
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {grid.rows.map((row) => (
            <tr key={row.ds_pct}>
              <td style={{ ...ggTdStyle, textAlign: "left", color: row.ds_pct === 0 ? "#7af" : "#aaa", fontWeight: row.ds_pct === 0 ? 700 : 400 }}>
                {row.ds_pct >= 0 ? "+" : ""}{row.ds_pct.toFixed(1)}%
              </td>
              {row.cells.map((c) => (
                <td key={c.div_volpts} style={{
                  ...ggTdStyle,
                  background: cellBg(c.pnl_usd),
                  color: "#fff",
                  border: c.is_current ? "1px solid #7af" : "1px solid #1a1a1a",
                  fontWeight: c.is_current ? 700 : 400,
                }}>
                  {c.pnl_usd >= 0 ? "+" : ""}{Math.round(c.pnl_usd)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ fontSize: 10, color: "#666", marginTop: 6, fontStyle: "italic" }}>
        Taylor : P&L ≈ ½γΔS² + V·ΔIV (delta hedged, theta=0). For the signal thesis :
        find your z-implied ΔIV column, read across spot rows.
      </div>
    </section>
  );
}

function Block({ bg, border, title, children }: {
  bg: string; border: string; title: string; children: React.ReactNode;
}): JSX.Element {
  return (
    <section style={{ background: bg, borderLeft: `3px solid ${border}`, borderRadius: 3,
                      padding: 8, display: "flex", flexDirection: "column", gap: 2 }}>
      <div style={{ fontSize: 10, color: border, fontWeight: 700, letterSpacing: 1,
                    textTransform: "uppercase", marginBottom: 4 }}>
        {title}
      </div>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
        <tbody>{children}</tbody>
      </table>
    </section>
  );
}

function Row({ name, value }: { name: string; value: React.ReactNode }): JSX.Element {
  return (
    <tr>
      <td style={{ padding: "3px 8px", color: "#aaa", width: "40%", verticalAlign: "top" }}>{name}</td>
      <td style={{ padding: "3px 8px", color: "#ddd", fontFamily: "Consolas, monospace",
                   textAlign: "right", verticalAlign: "top" }}>
        {value}
      </td>
    </tr>
  );
}

function Divider(): JSX.Element {
  return <tr><td colSpan={2} style={{ height: 1, background: "rgba(255,255,255,0.05)", padding: 0 }} /></tr>;
}

function fmt(v: number | null | undefined, digits: number): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toFixed(digits);
}

function signedFmt(v: number | null | undefined, digits: number): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return (v >= 0 ? "+" : "") + v.toFixed(digits);
}

const selectStyle: React.CSSProperties = {
  background: "#0a0a0a", color: "#ddd", border: "1px solid #333",
  padding: "2px 6px", fontSize: 12,
};
const inputStyle: React.CSSProperties = {
  background: "#0a0a0a", color: "#ddd", border: "1px solid #333",
  padding: "2px 6px", fontSize: 12, width: 80, textAlign: "right",
};
const pillarBtnStyle: React.CSSProperties = {
  padding: "2px 8px", border: "1px solid #333", borderRadius: 3,
  fontSize: 11, fontFamily: "Consolas, monospace",
};
const ggThStyle: React.CSSProperties = {
  padding: "3px 8px", textAlign: "right", fontWeight: 600,
  borderBottom: "1px solid #222",
};
const ggTdStyle: React.CSSProperties = {
  padding: "3px 8px", textAlign: "right", color: "#ddd",
  borderBottom: "1px solid #1a1a1a",
};
const btnPrimary: React.CSSProperties = {
  padding: "6px 14px", borderRadius: 3, border: "none", fontWeight: 600, fontSize: 12,
  background: "#2a4a6a", color: "#fff", cursor: "pointer",
};
const btnSecondary: React.CSSProperties = {
  padding: "6px 14px", borderRadius: 3, border: "1px solid #444",
  background: "#1a1a1a", color: "#aaa", fontSize: 12,
};
