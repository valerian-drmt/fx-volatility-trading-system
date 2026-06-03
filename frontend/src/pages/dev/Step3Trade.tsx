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
// CME EUR/USD future contract size — "full" = 6E (125k EUR notional),
// "micro" = M6E (12.5k EUR notional). Only relevant when product='future'.
type FutureContractSize = "full" | "micro";

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
               requires_delta_hedge: boolean; vega_sign: string; legs: Leg[];
               product_label?: string | null };
  greeks_net: Greeks;
  pricing: { premium_paid_usd: number; max_loss_usd: number;
             max_loss_at_expiry_only: boolean; breakeven_pips_each_side: number | null };
  costs?: { premium_per_contract_usd: number; commission_usd: number; total_trade_cost_usd: number };
  legs_greeks?: LegGreeks[];
  pnl_grid?: PnlGrid;
  state: "valid_for_submit" | "blocked" | "expired" | "submitted" | "cancelled";
  blocking_reasons: string[];
  surface_age_seconds: number;
  spot: number | null;
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

// Products where the user can override the delta pillar. Butterfly is
// excluded : its wings (10dc / 10dp) are fixed by template ; an override
// would collapse all 3 legs onto the same strike and produce a net-zero
// position. The wing width is part of the product definition.
const ACCEPTS_DELTA_OVERRIDE: ReadonlySet<Product> = new Set([
  "vanilla_call", "vanilla_put", "straddle", "strangle", "calendar",
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
  // Future-only : EUR/USD contract size on CME. Two products supported :
  //   - 6E  (full)  → €125,000 notional / contract
  //   - M6E (micro) → €12,500  notional / contract
  // No effect for option products (their multiplier is fixed at 125 000 by
  // the local symbol parser).
  const [futureContractSize, setFutureContractSize] = useState<FutureContractSize>("full");

  // YELLOW market data
  const [spot, setSpot] = useState<number | null>(null);
  const [surfaceTenors, setSurfaceTenors] = useState<Record<string, SurfaceTenor>>({});
  const [surfaceAge, setSurfaceAge] = useState<number | null>(null);

  // RED outputs
  const [preview, setPreview] = useState<Preview | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [booking, setBooking] = useState(false);
  const [, setError] = useState<string | null>(null);

  // Order-draft state — Book pops up a right-side panel with Send / Cancel.
  // We freeze the preview at the moment Book was clicked so subsequent
  // edits in the GREEN block don't mutate the order under the user.
  const [orderDraft, setOrderDraft] = useState<Preview | null>(null);
  const [submitResult, setSubmitResult] = useState<{
    success: boolean; structure_id?: number; position_id?: number; message?: string;
  } | null>(null);
  // Default to LIVE — real submit to IB Gateway. The connected account
  // is shown by the "PAPER / LIVE" badge in the top-right header; the
  // operator can read that to know whether the order will hit a paper
  // sandbox or a real-money account.
  const executionMode = "live" as "mock" | "live";

  const structureType = STRUCTURE_MAP[product][side];
  const isFuture = product === "future";
  const isSingleLeg = SINGLE_LEG.has(product);
  const acceptsDeltaOverride = ACCEPTS_DELTA_OVERRIDE.has(product);
  const needsFarTenor = REQUIRES_FAR_TENOR.has(product);

  // Reset preview + any pending order draft on input changes — the user
  // mutated the form so the previous preview is stale.
  useEffect(() => {
    setPreview(null);
    setOrderDraft(null);
    setSubmitResult(null);
  }, [product, side, tenor, tenorFar, delta, strikeStr, qty]);

  // Term-structure poll → spot + ATM IVs. MERGES into existing
  // surfaceTenors so the wing pillars added by the smile poll don't
  // disappear on each 5s refresh (the previous reset-then-refill cycle
  // caused IV cells to flicker between value and "—").
  useEffect(() => {
    const load = async () => {
      try {
        const r = await fetch("/api/v1/vol/term-structure?symbol=EURUSD");
        if (!r.ok) return;
        const j = await r.json();
        let detectedSpot: number | null = null;
        for (const p of j.pillars ?? []) {
          if (!detectedSpot && p.forward) detectedSpot = p.forward;
        }
        setSurfaceTenors((prev) => {
          const next = { ...prev };
          for (const p of j.pillars ?? []) {
            const tenor = p.tenor as string;
            const existing: SurfaceTenor = next[tenor] ?? {};
            next[tenor] = {
              ...existing,
              atm: { iv: (p.sigma_atm_pct ?? 0) / 100,
                     strike: p.forward ?? p.strike_atm },
            };
          }
          return next;
        });
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

  // Auto-fill strike when the user clicks a delta button OR changes tenor.
  // We DO NOT depend on ``surfaceTenors`` — that would re-fire on every
  // 5s smile poll, overwriting any manual strike entry AND triggering the
  // input-change reset (which closes the Order panel mid-Book).
  useEffect(() => {
    if (isFuture) return;
    const k = surfaceTenors[tenor]?.[delta]?.strike;
    if (k != null) setStrikeStr(k.toFixed(4));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [delta, tenor, isFuture]);

  // Book = run Preview (so the order panel sees the freshest pricing)
  // then open the right-side Order panel. Does NOT submit — the user has
  // to click Send inside the panel to actually fire /trade/submit.
  const onBook = async () => {
    setSubmitResult(null);
    const fresh = await runPreview();
    if (fresh) setOrderDraft(fresh);
  };

  // Send (from the Order panel) = actually submit to /trade/submit.
  const onSend = async () => {
    if (!orderDraft) return;
    setBooking(true); setError(null); setSubmitResult(null);
    try {
      const r = await fetch("/api/v1/trade/submit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          preview_id: orderDraft.preview_id,
          execution_mode: executionMode,
        }),
      });
      const j = await r.json();
      if (!r.ok) {
        const detail = typeof j.detail === "object"
          ? JSON.stringify(j.detail) : (j.detail ?? `HTTP ${r.status}`);
        throw new Error(detail);
      }
      const msg = executionMode === "live"
        ? "live submit · waiting for fills"
        : `mock fill : premium=$${j.total_premium_paid_usd ?? 0}, commission=$${j.total_commission_usd ?? 0}`;
      setSubmitResult({
        success: true, structure_id: j.structure_id,
        position_id: j.position_id ?? undefined,
        message: msg,
      });
    } catch (e) {
      setSubmitResult({ success: false, message: String(e) });
    } finally { setBooking(false); }
  };

  // Cancel (from the Order panel) = close panel + drop the draft.
  const onCancelOrder = () => {
    setOrderDraft(null);
    setSubmitResult(null);
    setBooking(false);
  };

  // WS subscription to /ws/orders/{structure_id} was here — removed
  // for the UI-only milestone (no backend Preview/Book yet). To be
  // restored when the post-submit live tracker UX is reintroduced.

  // Shared core — builds the preview body from current inputs, POSTs to
  // /trade/preview, returns the parsed preview (also setting state).
  // Both onReview and onBook use this so the two buttons stay in sync.
  const runPreview = async (): Promise<Preview | null> => {
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
      if (isFuture) {
        body.future_contract_size = futureContractSize;
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
      return j as Preview;
    } catch (e) {
      setError(String(e));
      return null;
    } finally { setReviewing(false); }
  };

  const onReview = async () => { await runPreview(); };

  // Yellow-block always displays the 5 pillars for the chosen tenor — no
  // structure-specific filtering. User decides what's relevant.
  const pillarsToShow: Pillar[] = isFuture ? [] : PILLARS;

  return (
    <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 14 }}>

      {/* ── A · centered column (1/3 width) + Order panel right when set ── */}
      <div style={{ display: "flex", justifyContent: "center",
                    alignItems: "flex-start", gap: 12 }}>
      <div style={{ width: "33.33%",
                    display: "flex", flexDirection: "column", gap: 12 }}>
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
        {isFuture && (
          <Row name="Contract size" value={
            <select
              value={futureContractSize}
              onChange={(e) => setFutureContractSize(e.target.value as FutureContractSize)}
              style={selectStyle}
              title="6E = €125 000 notional · M6E = €12 500 notional">
              <option value="full">6E · €125 000</option>
              <option value="micro">M6E · €12 500</option>
            </select>
          } />
        )}
        {!isFuture && (
          <Row name="Tenor" value={
            <select value={tenor} onChange={(e) => setTenor(e.target.value)} style={selectStyle}>
              {TENORS.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          } />
        )}
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
        {/* Prefer the spot echoed back by the last Preview response (Redis
            surface fed the pricing), fall back to the term-structure poll
            that runs in the background. Avoids "—" when the FX market is
            closed and the term-structure endpoint returns empty pillars. */}
        <Row name="Spot" value={fmt(preview?.spot ?? spot, 4)} />
        {!isFuture && (
          <Row name="Surface age (s)" value={surfaceAge !== null ? fmt(surfaceAge, 0) : (surfaceTenors[tenor] ? "≤5" : "—")} />
        )}
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

      {/* RED — outputs (per-leg greeks + net + costs) — col 3 of the strip */}
      <Block bg={RED_BG} border={RED_BORDER} title="Outputs">
        {!preview ? (
          <div style={{ padding: 8, fontStyle: "italic", color: "#888", fontSize: 11 }}>
            (no review yet — fill inputs and click <strong>Preview</strong> below)
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

      {/* ── A · buttons — inside the 1/3-width column, below RED ── */}
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 4 }}>
        <button type="button" onClick={onReview} disabled={reviewing}
                style={{ ...btnPrimary, opacity: reviewing ? 0.5 : 1 }}>
          {reviewing ? "Previewing…" : "Preview"}
        </button>
        <button type="button" onClick={onBook}
                disabled={reviewing || booking}
                style={{
                  ...btnSecondary,
                  background: (reviewing || booking) ? "#1a1a1a" : "#1a6a2a",
                  color: (reviewing || booking) ? "#777" : "#cfc",
                  cursor: (reviewing || booking) ? "not-allowed" : "pointer",
                  opacity: (reviewing || booking) ? 0.5 : 1,
                  borderColor: (reviewing || booking) ? "#444" : "#22c55e",
                }}
                title="Auto-runs Preview then opens the Order panel">
          {reviewing ? "Previewing…" : booking ? "Booking…" : "Book"}
        </button>
      </div>

      </div>{/* end A column */}

      {/* ── Order panel — appears to the right of A when Book was clicked ── */}
      {orderDraft && (
        <OrderPanel
          draft={orderDraft}
          executionMode={executionMode}
          submitting={booking}
          result={submitResult}
          onSend={onSend}
          onCancel={onCancelOrder}
        />
      )}

      {/* ── Close-position panel — permanent, to the right of Order panel ── */}
      <ClosePanel />

      </div>{/* end A + Order row */}

      {/* ── B · Current positions ────────────────────────────────────── */}
      <DbTablePanel
        label="B · Current positions (position)"
        table="position"
        limit={50}
        columns={["id", "structure", "product_label", "side", "tenor", "expiry", "quantity",
                  "market_price", "current_pnl_usd",
                  "delta_usd", "gamma_usd", "vega_usd", "theta_usd",
                  "updated_at"]}
        stateColumn={null}
      />

      {/* ── C · Previews (trade_preview) ─────────────────────────────── */}
      <DbTablePanel
        label="C · Previews (trade_preview)"
        table="trade_preview"
        limit={20}
        columns={["id", "preview_id", "created_at", "expires_at",
                  "structure_type", "product_label", "reference_tenor",
                  "armed_z_score", "armed_signal_label",
                  "state", "user_action", "user_action_at",
                  "submitted_trade_id"]}
        stateColumn="state"
      />

      {/* ── D · Submitted trades (trade_structure) ───────────────────── */}
      <DbTablePanel
        label="D · Submitted trades (trade_structure)"
        table="trade_structure"
        limit={20}
        columns={["id", "created_at", "structure_type", "product_label", "reference_tenor",
                  "base_qty", "state", "execution_mode",
                  "total_premium_paid_usd", "total_commission_usd",
                  "fully_filled_at", "closed_at"]}
        stateColumn="state"
      />

      {/* ── E · Per-leg orders (trade_order) ─────────────────────────── */}
      <DbTablePanel
        label="E · Per-leg orders (trade_order)"
        table="trade_order"
        limit={20}
        columns={["id", "structure_id", "leg_idx", "order_role",
                  "contract_type", "contract_strike", "side", "qty",
                  "qty_filled", "avg_fill_price", "limit_price",
                  "state", "ib_order_id"]}
        stateColumn="state"
      />

      {/* ── F · Fills (trade_fill) ───────────────────────────────────── */}
      <DbTablePanel
        label="F · Fills (trade_fill)"
        table="trade_fill"
        limit={20}
        columns={["id", "order_id", "ib_execution_id", "timestamp",
                  "qty_filled", "fill_price", "commission_usd", "side"]}
        stateColumn={null}
      />

      {/* ── G · Audit log (trade_event) ──────────────────────────────── */}
      <DbTablePanel
        label="G · Audit log (trade_event)"
        table="trade_event"
        limit={20}
        columns={["id", "ts", "event_type", "severity",
                  "structure_id", "order_id", "description"]}
        stateColumn="severity"
      />

    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────
// DbTablePanel — generic compact view over /api/v1/dev/tables/{name}.
//   - polls every 5 s
//   - `columns` restricts which fields to render (whitelist)
//   - `stateColumn` triggers color-coding (state machines: pending /
//      submitted / filled / cancelled / rejected etc.) — null disables
// ──────────────────────────────────────────────────────────────────────

interface DbTableResponse {
  table: string;
  total: number;
  limit: number;
  offset: number;
  columns: string[];
  rows: Record<string, unknown>[];
}

function DbTablePanel({
  label, table, limit, columns, stateColumn,
}: {
  label: string;
  table: string;
  limit: number;
  columns: string[];
  stateColumn: string | null;
}): JSX.Element {
  const [data, setData] = useState<DbTableResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const r = await fetch(`/api/v1/dev/tables/${table}?limit=${limit}`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        setData(await r.json()); setError(null);
      } catch (e) { setError(String(e)); }
    };
    void load();
    const id = window.setInterval(load, 5_000);
    return () => window.clearInterval(id);
  }, [table, limit]);

  const stateColor = (s: unknown): string => {
    const v = String(s ?? "").toLowerCase();
    if (["filled", "fully_filled", "done", "closed", "info",
         "valid_for_submit"].includes(v)) return "#9f9";
    if (["rejected", "failed", "fully_failed", "cancelled", "error",
         "critical", "blocked", "expired"].includes(v)) return "#fcc";
    if (["pending", "submitted", "partial_fill", "partially_filled",
         "in_progress", "warning"].includes(v)) return "#fc6";
    return "#ddd";
  };
  const fmtCell = (v: unknown, col: string): string => {
    if (v === null || v === undefined) return "—";
    if (typeof v === "number") {
      if (col.endsWith("_usd") || col.includes("price") || col.includes("premium")
          || col.includes("commission")) return v.toFixed(2);
      if (col.includes("_at") || col === "ts" || col === "timestamp") return String(v);
      return String(v);
    }
    if (typeof v === "string") {
      // Truncate ISO timestamps to second precision.
      if (/^\d{4}-\d{2}-\d{2}T/.test(v)) return v.replace("T", " ").slice(0, 19);
      if (v.length > 60) return v.slice(0, 57) + "…";
      return v;
    }
    if (typeof v === "object") {
      const s = JSON.stringify(v);
      return s.length > 60 ? s.slice(0, 57) + "…" : s;
    }
    return String(v);
  };

  const thStyle: React.CSSProperties = {
    padding: "4px 8px", textAlign: "left", color: "#7af",
    fontSize: 11, borderBottom: "1px solid #1f2937",
    whiteSpace: "nowrap",
  };
  const tdStyle: React.CSSProperties = {
    padding: "3px 8px", color: "#ddd", fontSize: 11,
    fontFamily: "Consolas, monospace",
    borderBottom: "1px solid #161616",
    whiteSpace: "nowrap",
  };

  return (
    <section style={{
      background: "#10141c", border: "1px solid #1f2937", borderRadius: 4,
      padding: 10, marginTop: 10,
    }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 6 }}>
        <span style={{ color: "#aef", fontWeight: 600, fontSize: 12 }}>{label}</span>
        <span style={{ color: "#666", fontSize: 10, fontFamily: "Consolas, monospace" }}>
          /api/v1/dev/tables/{table}
        </span>
        {data && (
          <span style={{ color: "#666", fontSize: 10, marginLeft: "auto" }}>
            {data.rows.length} / {data.total} rows
          </span>
        )}
      </div>
      {error && <div style={{ color: "#fcc", fontSize: 11 }}>Error: {error}</div>}
      {data && data.rows.length === 0 ? (
        <div style={{ color: "#666", fontStyle: "italic", fontSize: 11, padding: 6 }}>
          empty.
        </div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>{columns.map((c) => <th key={c} style={thStyle}>{c}</th>)}</tr>
            </thead>
            <tbody>
              {data?.rows.map((row, i) => (
                <tr key={i}>
                  {columns.map((c) => {
                    const v = row[c];
                    const isStateCol = stateColumn === c;
                    return (
                      <td key={c} style={{
                        ...tdStyle,
                        color: isStateCol ? stateColor(v) : tdStyle.color,
                        fontWeight: isStateCol ? 600 : 400,
                      }}>
                        {fmtCell(v, c)}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function LegsGreeksTable({ legs, net }: { legs: LegGreeks[]; net: Greeks }): JSX.Element {
  // Vertical per-leg breakdown. Each leg is rendered as a label/value
  // 2-column table — same density as the GREEN/YELLOW blocks above,
  // easier to scan than the wide horizontal layout for multi-leg
  // structures (e.g. straddles, calendars).
  const thLeft: React.CSSProperties = { ...ggThStyle, textAlign: "left",
                                        color: "#7af", width: "45%" };
  const tdRight: React.CSSProperties = { ...ggTdStyle, textAlign: "right" };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10,
                  fontSize: 11, fontFamily: "Consolas, monospace" }}>
      {legs.map((leg) => (
        <table key={leg.leg_idx} style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              <th colSpan={2} style={{
                padding: "4px 8px", textAlign: "left",
                color: "#aef", fontSize: 11, fontWeight: 700,
                borderBottom: "1px solid #2a2a2a",
                background: "rgba(174,238,255,0.04)",
              }}>
                Leg {leg.leg_idx}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr><th style={thLeft}>Type</th>
                <td style={tdRight}>{leg.type}</td></tr>
            <tr><th style={thLeft}>Strike</th>
                <td style={tdRight}>{leg.strike?.toFixed(4) ?? "—"}</td></tr>
            <tr><th style={thLeft}>Side</th>
                <td style={{ ...tdRight,
                             color: leg.side === "BUY" ? "#6c6" : "#e66" }}>
                  {leg.side}
                </td></tr>
            <tr><th style={thLeft}>Qty</th>
                <td style={tdRight}>{leg.qty_factor}</td></tr>
            <tr><th style={thLeft}>Vega ($/vp)</th>
                <td style={tdRight}>{signedFmt(leg.vega, 2)}</td></tr>
            <tr><th style={thLeft}>Gamma ($/pip)</th>
                <td style={tdRight}>{signedFmt(leg.gamma, 4)}</td></tr>
            <tr><th style={thLeft}>Theta ($/day)</th>
                <td style={tdRight}>{signedFmt(leg.theta, 2)}</td></tr>
            <tr><th style={thLeft}>Delta</th>
                <td style={tdRight}>{signedFmtK(leg.delta)}</td></tr>
          </tbody>
        </table>
      ))}

      {/* NET row block — same vertical shape, distinct background */}
      <table style={{ width: "100%", borderCollapse: "collapse",
                      background: "rgba(174,238,255,0.06)",
                      borderTop: "1px solid #2a4a6a" }}>
        <thead>
          <tr>
            <th colSpan={2} style={{
              padding: "4px 8px", textAlign: "left",
              color: "#aef", fontSize: 11, fontWeight: 700,
              borderBottom: "1px solid #2a4a6a",
            }}>
              NET (structure total)
            </th>
          </tr>
        </thead>
        <tbody>
          <tr><th style={thLeft}>Vega ($/vp)</th>
              <td style={tdRight}>{signedFmt(net.vega_usd_per_volpt, 2)}</td></tr>
          <tr><th style={thLeft}>Gamma ($/pip)</th>
              <td style={tdRight}>{signedFmt(net.gamma_usd_per_pip2, 4)}</td></tr>
          <tr><th style={thLeft}>Theta ($/day)</th>
              <td style={tdRight}>{signedFmt(net.theta_usd_per_day, 2)}</td></tr>
          <tr><th style={thLeft}>Delta</th>
              <td style={tdRight}>{signedFmtK(net.delta_unhedged)}</td></tr>
        </tbody>
      </table>
    </div>
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

// Compact format for large $-greeks (typically delta_usd) — divides by 1k
// and shows 2 decimals + " k". Same convention as the trader's mental model:
// "buy 10 6E → +1 470.49 k delta".
function signedFmtK(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const k = v / 1_000;
  return (k >= 0 ? "+" : "") + k.toFixed(2) + " k";
}

// ──────────────────────────────────────────────────────────────────────
// OrderPanel — confirmation step between Book click and actual /trade/submit.
// Shows the frozen draft, then Send (submit) or Cancel (drop draft).
// ──────────────────────────────────────────────────────────────────────

function OrderPanel({
  draft, executionMode, submitting, result, onSend, onCancel,
}: {
  draft: Preview;
  executionMode: "mock" | "live";
  submitting: boolean;
  result: { success: boolean; structure_id?: number; position_id?: number; message?: string } | null;
  onSend: () => void;
  onCancel: () => void;
}): JSX.Element {
  const greeks = draft.greeks_net;
  const ibSymbol = (draft.structure as { ib_symbol?: string | null }).ib_symbol ?? null;
  const isSubmitted = result?.success ?? false;

  // Read the IB account type (paper / live) once for the badge — clearer
  // semantic for the operator than the internal "execution_mode" flag.
  const [accountType, setAccountType] = useState<string | null>(null);
  useEffect(() => {
    const load = async () => {
      try {
        const r = await fetch("/api/v1/dev/tables/ib_session_state?limit=1");
        if (!r.ok) return;
        const j = await r.json();
        setAccountType(j.rows?.[0]?.account_type ?? null);
      } catch { /* keep last */ }
    };
    void load();
  }, []);
  const badgeLabel = accountType ? accountType.toUpperCase() : executionMode.toUpperCase();
  const badgeColor =
    accountType === "live" ? "#a04"
    : accountType === "paper" ? "#a87a00"
    : executionMode === "live" ? "#a04"
    : "#444";

  return (
    <div style={{
      width: "33.33%",
      background: "#0c1117", border: "1px solid #22c55e", borderRadius: 4,
      padding: 12,
    }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 8 }}>
        <span style={{ color: "#cfc", fontWeight: 600, fontSize: 13 }}>📝 Order draft</span>
        <span style={{
          fontSize: 10, padding: "1px 6px", borderRadius: 2,
          background: badgeColor,
          color: "#fff", textTransform: "uppercase", fontWeight: 700,
        }}>
          {badgeLabel}
        </span>
      </div>

      <table style={kvTableStyle}>
        <tbody>
          <OrderRow label="Structure"     value={draft.structure.type} />
          {draft.structure.product_label && (
            <OrderRow label="Product"     value={draft.structure.product_label} />
          )}
          {ibSymbol && (
            <OrderRow label="IB symbol"   value={ibSymbol} />
          )}
          <OrderRow label="Preview id"    value={draft.preview_id} mono />
          <OrderRow label="State"         value={draft.state} />
          <OrderRow label="Premium"       value={`$${(draft.pricing.premium_paid_usd ?? 0).toFixed(2)}`} />
          <OrderRow label="Max loss"      value={`$${(draft.pricing.max_loss_usd ?? 0).toFixed(2)}`} />
          <OrderRow label="Commission"    value={`$${(draft.costs?.commission_usd ?? 0).toFixed(2)}`} />
          <OrderRow label="Total cost"    value={`$${(draft.costs?.total_trade_cost_usd ?? 0).toFixed(2)}`} />
          <OrderRow label="Δ unhedged"    value={signedFmtK(greeks.delta_unhedged)} />
          {greeks.vega_usd_per_volpt !== 0 && (
            <OrderRow label="Vega"        value={signedFmtK(greeks.vega_usd_per_volpt)} />
          )}
        </tbody>
      </table>

      {/* Pre-submit checks summary — show only the blockers */}
      {draft.blocking_reasons && draft.blocking_reasons.length > 0 && (
        <div style={{
          marginTop: 8, padding: 6, borderRadius: 3,
          background: "rgba(239,68,68,0.10)", border: "1px solid #ef4444",
          color: "#fcc", fontSize: 11,
        }}>
          ⚠ blocked : {draft.blocking_reasons.join(", ")}
        </div>
      )}

      {/* Action buttons */}
      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        <button type="button" onClick={onSend}
                disabled={submitting || isSubmitted}
                style={{
                  flex: 1, padding: "6px 14px", borderRadius: 3,
                  border: "1px solid #22c55e", fontWeight: 700, fontSize: 12,
                  background: (submitting || isSubmitted) ? "#1a1a1a" : "#1a6a2a",
                  color: (submitting || isSubmitted) ? "#777" : "#cfc",
                  cursor: (submitting || isSubmitted) ? "not-allowed" : "pointer",
                }}>
          {submitting ? "Sending…" : isSubmitted ? "Sent ✓" : "Send"}
        </button>
        <button type="button" onClick={onCancel}
                disabled={submitting}
                style={{
                  flex: 1, padding: "6px 14px", borderRadius: 3,
                  border: "1px solid #ef4444", fontWeight: 600, fontSize: 12,
                  background: submitting ? "#1a1a1a" : "#3a0a0a",
                  color: submitting ? "#777" : "#fcc",
                  cursor: submitting ? "not-allowed" : "pointer",
                }}>
          Cancel
        </button>
      </div>

      {/* Result banner — success or error after Send */}
      {result && (
        <div style={{
          marginTop: 10, padding: 8, borderRadius: 3, fontSize: 11,
          fontFamily: "Consolas, monospace",
          background: result.success ? "rgba(34,197,94,0.10)" : "rgba(239,68,68,0.10)",
          border: `1px solid ${result.success ? "#22c55e" : "#ef4444"}`,
          color: result.success ? "#cfc" : "#fcc",
        }}>
          {result.success
            ? <>✓ structure #{result.structure_id}{result.position_id ? ` · position #${result.position_id}` : ""}<br />{result.message}</>
            : <>✗ {result.message}</>}
        </div>
      )}
    </div>
  );
}

function OrderRow({ label, value, mono }: {
  label: string; value: React.ReactNode; mono?: boolean;
}): JSX.Element {
  return (
    <tr>
      <th style={{ ...ggThStyle, color: "#7af", textAlign: "left" }}>{label}</th>
      <td style={{ ...ggTdStyle,
                   textAlign: "right",
                   fontFamily: mono ? "Consolas, monospace" : "inherit" }}>
        {value}
      </td>
    </tr>
  );
}

// ──────────────────────────────────────────────────────────────────────
// ClosePanel — pick a Position id + integer qty. The before/after
// "Risk summary" recomputes automatically on every change. The Close
// button mirrors the Book button visually (green primary) and submits
// POST /api/v1/positions/{id}/close with body { qty }. The API tier
// resolves the IB localSymbol from the DB row + a marketable limit
// price from the latest mark, then forwards to execution-engine which
// submits a reverse LimitOrder for the requested qty.
// ──────────────────────────────────────────────────────────────────────

interface HeaderSummary {
  computed_at: string;
  pnl: { total_24h_usd: number | null; open_unrealized_usd: number };
  greeks: { delta_usd: number; gamma_usd: number; vega_usd: number; theta_usd: number };
  var_1d_99: { usd: number | null; n_days: number; method: string };
}

interface ClosePosition {
  id: number;
  structure: string | null;
  product_label: string | null;
  side: string;
  quantity: number;
  delta_usd: number | null;
  gamma_usd: number | null;
  vega_usd: number | null;
  theta_usd: number | null;
  current_pnl_usd: number | null;
}

function ClosePanel(): JSX.Element {
  const [positions, setPositions] = useState<ClosePosition[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [qty, setQty] = useState<number>(0);
  const [header, setHeader] = useState<HeaderSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [closing, setClosing] = useState(false);
  const [closeResult, setCloseResult] = useState<{
    success: boolean;
    message: string;
    structure_id?: number | undefined;
    order_id?: number | undefined;
  } | null>(null);

  // Positions list — polled every 10s like the DbTable panels.
  useEffect(() => {
    const load = async () => {
      try {
        const r = await fetch("/api/v1/dev/tables/position?limit=200");
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const j: DbTableResponse = await r.json();
        const rows: ClosePosition[] = j.rows.map((row) => ({
          id: row.id as number,
          structure: (row.structure as string | null) ?? null,
          product_label: (row.product_label as string | null) ?? null,
          side: String(row.side ?? ""),
          quantity: Number(row.quantity ?? 0),
          delta_usd: row.delta_usd === null || row.delta_usd === undefined ? null : Number(row.delta_usd),
          gamma_usd: row.gamma_usd === null || row.gamma_usd === undefined ? null : Number(row.gamma_usd),
          vega_usd:  row.vega_usd  === null || row.vega_usd  === undefined ? null : Number(row.vega_usd),
          theta_usd: row.theta_usd === null || row.theta_usd === undefined ? null : Number(row.theta_usd),
          current_pnl_usd: row.current_pnl_usd === null || row.current_pnl_usd === undefined ? null : Number(row.current_pnl_usd),
        }));
        setPositions(rows);
      } catch (e) { setError(String(e)); }
    };
    void load();
    const id = window.setInterval(load, 10_000);
    return () => window.clearInterval(id);
  }, []);

  // Header (book-level greeks) — polled every 5s like the Portfolio tab.
  useEffect(() => {
    const load = async () => {
      try {
        const r = await fetch("/api/v1/portfolio/header");
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        setHeader(await r.json());
      } catch { /* keep last good */ }
    };
    void load();
    const id = window.setInterval(load, 5_000);
    return () => window.clearInterval(id);
  }, []);

  const selected = positions.find((p) => p.id === selectedId) ?? null;
  const maxQty = selected ? Math.floor(Math.abs(selected.quantity)) : 0;
  const qtyValid = qty > 0 && qty <= maxQty;

  // Auto-preview : recomputed on every (selected, qty, header) change.
  // Null when the inputs aren't a valid close request.
  const after: HeaderSummary | null = (header && selected && qtyValid) ? {
    ...header,
    pnl: {
      total_24h_usd: header.pnl.total_24h_usd,
      open_unrealized_usd: header.pnl.open_unrealized_usd
        - (selected.current_pnl_usd ?? 0) * (qty / maxQty),
    },
    greeks: {
      delta_usd: header.greeks.delta_usd - (selected.delta_usd ?? 0) * (qty / maxQty),
      gamma_usd: header.greeks.gamma_usd - (selected.gamma_usd ?? 0) * (qty / maxQty),
      vega_usd:  header.greeks.vega_usd  - (selected.vega_usd  ?? 0) * (qty / maxQty),
      theta_usd: header.greeks.theta_usd - (selected.theta_usd ?? 0) * (qty / maxQty),
    },
    // VaR : non-linear, can't be scaled trivially. Render "n/a" on After.
    var_1d_99: header.var_1d_99,
  } : null;

  const onChangeSel = (v: string): void => {
    setSelectedId(v ? Number(v) : null);
    setQty(0);
    setCloseResult(null);
  };
  // Integer-only qty input — parseInt drops decimals, clamp to [0, maxQty].
  const onChangeQty = (v: string): void => {
    setCloseResult(null);
    if (v === "") { setQty(0); return; }
    const n = Math.floor(Number(v));
    if (!Number.isFinite(n) || n <= 0) { setQty(0); return; }
    setQty(maxQty > 0 ? Math.min(n, maxQty) : n);
  };

  const onClose = async (): Promise<void> => {
    if (!selected || !qtyValid) return;
    setClosing(true);
    setCloseResult(null);
    try {
      const r = await fetch(`/api/v1/positions/${selected.id}/close`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ qty }),
      });
      const j = await r.json().catch(() => ({} as Record<string, unknown>));
      if (r.ok) {
        const ot = (j.order_type as string | undefined) ?? "MKT";
        const lp = j.limit_price as number | null | undefined;
        const priceTag = lp != null ? ` @${lp.toFixed(5)}` : "";
        setCloseResult({ success: true,
          structure_id: j.structure_id as number | undefined,
          order_id: j.order_id as number | undefined,
          message: `closed ${qty}/${maxQty} on position #${selected.id} (${ot}${priceTag})` });
      } else {
        const detail = (j as { detail?: string }).detail;
        setCloseResult({ success: false, message: detail ?? `HTTP ${r.status}` });
      }
    } catch (e) {
      setCloseResult({ success: false, message: String(e) });
    } finally {
      setClosing(false);
    }
  };

  return (
    <div style={{
      width: "33.33%",
      background: "#0c1117", border: "1px solid #ef4444", borderRadius: 4,
      padding: 12,
    }}>
      <div style={{ color: "#fcc", fontWeight: 600, fontSize: 13, marginBottom: 8 }}>
        ✂️ Close position
      </div>

      <table style={kvTableStyle}>
        <tbody>
          <Row name="Position" value={
            <select value={selectedId ?? ""} onChange={(e) => onChangeSel(e.target.value)}
                    style={{ ...selectStyle, minWidth: 180 }}>
              <option value="">— pick an open position —</option>
              {positions.map((p) => (
                <option key={p.id} value={p.id}>
                  #{p.id} {p.product_label ?? p.structure ?? "?"} {p.side} ×{p.quantity}
                </option>
              ))}
            </select>
          } />
          <Row name="Qty to close" value={
            <input type="number" min={1} max={maxQty || undefined} step={1}
                   value={qty || ""} onChange={(e) => onChangeQty(e.target.value)}
                   onKeyDown={(e) => {
                     // Block decimal separators outright — qty is an integer.
                     if (e.key === "." || e.key === "," || e.key === "e") e.preventDefault();
                   }}
                   style={inputStyle} />
          } />
        </tbody>
      </table>

      {/* Before / After Risk summary — auto-recomputed on every input change. */}
      <div style={{ marginTop: 12 }}>
        <BeforeAfterRiskSummary before={header} after={after} />
      </div>

      {/* Close button — same visual treatment as the Book/Send button :
          green primary, "Closing…" while in flight, "Closed ✓" + disabled
          after a successful close so the operator can't double-fire. */}
      {(() => {
        const isClosed = closeResult?.success === true;
        const dis = !qtyValid || closing || isClosed;
        return (
          <div style={{ marginTop: 10 }}>
            <button type="button" onClick={() => void onClose()}
                    disabled={dis}
                    style={{
                      ...btnSecondary,
                      background:  dis ? "#1a1a1a" : "#1a6a2a",
                      color:       dis ? "#777"    : "#cfc",
                      cursor:      dis ? "not-allowed" : "pointer",
                      opacity:     dis ? 0.5 : 1,
                      borderColor: dis ? "#444"    : "#22c55e",
                    }}>
              {closing ? "Closing…" : isClosed ? "Closed ✓" : "Close"}
            </button>
          </div>
        );
      })()}

      {/* Result banner — same shape as the Book / Send banner in OrderPanel. */}
      {closeResult && (
        <div style={{
          marginTop: 10, padding: 8, borderRadius: 3, fontSize: 11,
          fontFamily: "Consolas, monospace",
          background: closeResult.success
            ? "rgba(34,197,94,0.10)" : "rgba(239,68,68,0.10)",
          border: `1px solid ${closeResult.success ? "#22c55e" : "#ef4444"}`,
          color: closeResult.success ? "#cfc" : "#fcc",
        }}>
          {closeResult.success
            ? <>
                ✓ struct #{closeResult.structure_id}
                {closeResult.order_id ? ` · order #${closeResult.order_id}` : ""}
                <br />
                {closeResult.message}
              </>
            : <>✗ {closeResult.message}</>}
        </div>
      )}
      {error && !closeResult && (
        <div style={{ marginTop: 6, color: "#fcc", fontSize: 11 }}>{error}</div>
      )}
    </div>
  );
}


function BeforeAfterRiskSummary({
  before, after,
}: {
  before: HeaderSummary | null;
  after: HeaderSummary | null;
}): JSX.Element {
  const fields: Array<{ key: string; label: string; pick: (h: HeaderSummary) => number | null }> = [
    { key: "pnl24",  label: "Total P&L (24h)",  pick: (h) => h.pnl.total_24h_usd },
    { key: "unrl",   label: "Open unrealized",  pick: (h) => h.pnl.open_unrealized_usd },
    { key: "delta",  label: "Δ net ($)",        pick: (h) => h.greeks.delta_usd },
    { key: "gamma",  label: "Γ net ($/pip)",    pick: (h) => h.greeks.gamma_usd },
    { key: "vega",   label: "Vega net ($/vp)",  pick: (h) => h.greeks.vega_usd },
    { key: "theta",  label: "Θ net ($/day)",    pick: (h) => h.greeks.theta_usd },
    { key: "var",    label: "VaR 1d 99%",       pick: (h) => h.var_1d_99.usd },
  ];
  const cell = (v: number | null): string => v == null ? "—" : Math.round(v).toLocaleString() + "$";
  const colorOf = (v: number | null): string =>
    v == null ? "#888" : v > 0 ? "#9f9" : v < 0 ? "#fcc" : "#ddd";

  return (
    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11,
                    fontFamily: "Consolas, monospace" }}>
      <thead>
        <tr>
          <th style={{ ...ggThStyle, textAlign: "left", color: "#7af" }}>Risk summary</th>
          <th style={{ ...ggThStyle, color: "#aaa" }}>Before</th>
          <th style={{ ...ggThStyle, color: "#fc6" }}>After</th>
        </tr>
      </thead>
      <tbody>
        {fields.map((f) => {
          const b = before ? f.pick(before) : null;
          // VaR : non-linear — show "n/a" on the After column.
          const a = after ? (f.key === "var" ? null : f.pick(after)) : null;
          return (
            <tr key={f.key}>
              <td style={{ ...ggTdStyle, textAlign: "left", color: "#bbb" }}>{f.label}</td>
              <td style={{ ...ggTdStyle, color: colorOf(b) }}>{cell(b)}</td>
              <td style={{ ...ggTdStyle, color: colorOf(a) }}>
                {after === null ? "—" : (f.key === "var" ? "n/a" : cell(a))}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}


const kvTableStyle: React.CSSProperties = {
  width: "100%", borderCollapse: "collapse",
  fontSize: 11,
};

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

