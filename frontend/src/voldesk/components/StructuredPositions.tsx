/**
 * VOLDESK — booking-driven Open positions (Trade tab). Groups live positions by the
 * booked trade_structure (so a Risk Reversal reads as ONE 2-leg group labelled from
 * structure_type + the traded tenor), with live marks/greeks attached per leg, and a
 * separate "Unlinked" section for IB-account positions not booked through the desk.
 * Fixes the flat, symbol-derived, whole-account view (see /positions/structured).
 */
import { gk$, pnlCls } from "./format";
import { fetchStructuredPositions, type StructuredPositions } from "../../api/endpoints";
import { useFetch } from "../../hooks/useFetch";

// clean structure name from the enum ("risk_reversal" → "Risk Reversal")
function prettyStruct(t: string, label: string | null): string {
  const map: Record<string, string> = {
    vanilla_call: "Vanilla Call", vanilla_put: "Vanilla Put", straddle_atm: "Straddle",
    strangle: "Strangle", butterfly: "Butterfly", risk_reversal: "Risk Reversal",
    calendar: "Calendar", future: "Future",
  };
  if (map[t]) return map[t];
  return (label || t || "structure").replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

// Pull the structure's Δ bucket ("25" / "10") off its label so each leg can show
// its WING-tagged delta: a Risk Reversal's two legs sit on opposite wings even at
// the same |Δ| → call = "25Δc", put = "25Δp". Null when the structure declares no
// bucket (calendar, ATM straddle, older rows) → no leg tag.
function structDeltaLevel(product_label: string | null, structure_type: string): string | null {
  const m = /(\d+)\s*d/.exec(`${product_label ?? ""} ${structure_type ?? ""}`.toLowerCase());
  return m ? m[1]! : null;
}
function legWingTag(contract_type: string, level: string | null): string {
  const ct = (contract_type ?? "").toLowerCase();
  if (!level || (ct !== "call" && ct !== "put")) return "";
  return ` ${level}Δ${ct === "call" ? "c" : "p"}`;
}

export function StructuredPositions(): JSX.Element {
  const q = useFetch<StructuredPositions>(() => fetchStructuredPositions(), 60_000, true, 20_000);
  const data = q.data;
  const structures = data?.structures ?? [];
  const unlinked = data?.unlinked ?? [];

  if (q.status === "missing" && !data) {
    return <div className="dim small mono sp-empty">positions unavailable — needs the API + execution engine up</div>;
  }
  if (structures.length === 0 && unlinked.length === 0) {
    return <div className="dim small mono sp-empty">no open positions booked through the desk</div>;
  }

  return (
    <div className="table-scroll">
      <table className="dt sp-table">
        <thead>
          <tr>
            <th className="l">Structure / leg</th><th>Side</th><th className="r">Contracts</th>
            <th className="r">Strike</th><th className="r">Delta</th><th className="r">Vega</th>
            <th className="r">P&amp;L</th><th className="l">State</th>
          </tr>
        </thead>
        <tbody>
          {structures.map((s) => (
            <StructureGroup key={s.structure_id} s={s} />
          ))}
          {unlinked.length > 0 && (
            <>
              <tr className="sp-group sp-unlinked-head">
                <td className="l" colSpan={8}>
                  Unlinked <span className="dim small">· IB account · not booked through the desk ({unlinked.length})</span>
                </td>
              </tr>
              {unlinked.map((p) => (
                <tr key={"u" + p.id} className="sp-unlinked">
                  <td className="l"><span className="mono dim">{p.symbol}</span> {p.product_label ?? ""}</td>
                  <td><span className={"side-pill " + (p.side === "BUY" ? "long" : "short")}>{p.side}</span></td>
                  <td className="r mono">{p.qty ?? "—"}</td>
                  <td className="r mono dim">—</td>
                  <td className={"r mono " + (p.delta_usd != null ? pnlCls(p.delta_usd) : "dim")}>{gk$(p.delta_usd)}</td>
                  <td className={"r mono " + (p.vega_usd != null ? pnlCls(p.vega_usd) : "dim")}>{gk$(p.vega_usd)}</td>
                  <td className={"r mono " + (p.pnl_usd != null ? pnlCls(p.pnl_usd) : "dim")}>{gk$(p.pnl_usd)}</td>
                  <td className="l"><span className="dim small mono">{p.tenor ?? "—"}</span></td>
                </tr>
              ))}
            </>
          )}
        </tbody>
      </table>
    </div>
  );
}

// fill summary from the leg states: how many filled, and whether a sold leg is live
// while a bought (protective) leg isn't yet — i.e. an unhedged / naked residual.
function fillSummary(legs: StructuredPositions["structures"][number]["legs"]): { filled: number; total: number; naked: boolean } {
  const filled = legs.filter((l) => l.state === "filled").length;
  const naked =
    legs.some((l) => l.side === "SELL" && l.state === "filled") &&
    legs.some((l) => l.side === "BUY" && l.state !== "filled");
  return { filled, total: legs.length, naked };
}

function StructureGroup({ s }: { s: StructuredPositions["structures"][number] }): JSX.Element {
  const fill = fillSummary(s.legs);
  const dLevel = structDeltaLevel(s.product_label, s.structure_type);
  return (
    <>
      <tr className="sp-group">
        <td className="l">
          <b>{prettyStruct(s.structure_type, s.product_label)}</b> <span className="dim">· {s.tenor}</span>
          <span className="dim small mono"> · #{s.structure_id}</span>
          <span className={"dim small mono" + (fill.filled < fill.total ? " sp-fill-partial" : "")}> · {fill.filled}/{fill.total} filled</span>
          {fill.naked && (
            <span className="sp-naked" title="a sold leg filled but its long hedge leg hasn't — unbounded tail until it does or is cancelled">
              ⚠ naked residual
            </span>
          )}
        </td>
        <td className="dim small mono">{s.base_qty}×</td>
        <td className="r"></td>
        <td className="r"></td>
        <td className={"r mono " + pnlCls(s.net.delta_usd)}>{gk$(s.net.delta_usd)}</td>
        <td className={"r mono " + pnlCls(s.net.vega_usd)}>{gk$(s.net.vega_usd)}</td>
        <td className={"r mono " + pnlCls(s.net.pnl_usd)}>{gk$(s.net.pnl_usd)}</td>
        <td className="l"><span className={"sp-state " + stateTone(s.state)}>{s.state}</span></td>
      </tr>
      {s.legs.map((lg) => (
        <tr key={lg.leg_idx} className="sp-leg">
          <td className="l sp-leg-name">↳ {lg.contract_type}{legWingTag(lg.contract_type, dLevel) ? <span className="dim small"> {legWingTag(lg.contract_type, dLevel).trim()}</span> : ""}{lg.ib_local_symbol ? <span className="dim small mono"> · {lg.ib_local_symbol}</span> : ""}</td>
          <td><span className={"side-pill " + (lg.side === "BUY" ? "long" : "short")}>{lg.side}</span></td>
          <td className="r mono">{lg.qty}</td>
          <td className="r mono dim">{lg.strike != null ? lg.strike.toFixed(4) : "—"}</td>
          <td className={"r mono " + (lg.delta_usd != null ? pnlCls(lg.delta_usd) : "dim")}>{gk$(lg.delta_usd)}</td>
          <td className={"r mono " + (lg.vega_usd != null ? pnlCls(lg.vega_usd) : "dim")}>{gk$(lg.vega_usd)}</td>
          <td className={"r mono " + (lg.pnl_usd != null ? pnlCls(lg.pnl_usd) : "dim")}>{gk$(lg.pnl_usd)}</td>
          <td className="l"><span className={"sp-state " + stateTone(lg.state)}>{lg.state}</span></td>
        </tr>
      ))}
    </>
  );
}

function stateTone(s: string): string {
  const t = s.toLowerCase();
  if (/reject|cancel|fail|expire/.test(t)) return "bad";
  if (/fill/.test(t)) return "good";
  if (/pending|submit|partial|acknowledg/.test(t)) return "pend";
  return "neutral";
}
