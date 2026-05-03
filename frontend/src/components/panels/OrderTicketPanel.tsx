import { useEffect, useState } from "react";
import { postGreeks, type GreeksResponse, type PriceRequest } from "../../api/endpoints";
import { useOrderDraftStore } from "../../store/orderDraftStore";
import { useSelectionStore } from "../../store/selectionStore";
import { MetricTile } from "../common/MetricTile";
import { tenorToDays } from "../../utils/tenor";

// Fixed assumptions for the greeks preview — the full input form
// will land after the order-preview endpoint is delivered server-side.
const SPOT = 1.085;
const VOL = 0.075;

export function OrderTicketPanel(): JSX.Element {
  const draft = useOrderDraftStore();
  const symbol = useSelectionStore((s) => s.symbol);
  const [greeks, setGreeks] = useState<GreeksResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const isOption = draft.optionType === "CALL" || draft.optionType === "PUT";
  const days = draft.tenor ? tenorToDays(draft.tenor) : null;
  const canPreview = isOption && draft.strike !== null && days !== null;

  useEffect(() => {
    if (!canPreview) {
      setGreeks(null);
      return;
    }
    const req: PriceRequest = {
      spot: SPOT,
      strike: draft.strike as number,
      maturity_days: days as number,
      option_type: draft.optionType as "CALL" | "PUT",
      volatility: VOL,
    };
    setError(null);
    postGreeks(req)
      .then(setGreeks)
      .catch(() => setError("preview failed"));
  }, [canPreview, draft.strike, days, draft.optionType]);

  const disabled = !draft.isValid();

  return (
    <section className="panel ticket-panel" data-testid="order-ticket-panel">
      <header className="panel-header">
        <h2>Order Ticket · {symbol}</h2>
      </header>
      <div className="panel-body ticket-body">
        <div className="ticket-row">
          <label>
            Side
            <select
              value={draft.side}
              onChange={(e) => draft.setField("side", e.target.value as "BUY" | "SELL")}
            >
              <option>BUY</option>
              <option>SELL</option>
            </select>
          </label>
          <label>
            Type
            <select
              value={draft.optionType}
              onChange={(e) =>
                draft.setField("optionType", e.target.value as "CALL" | "PUT" | "FUT")
              }
            >
              <option>CALL</option>
              <option>PUT</option>
              <option>FUT</option>
            </select>
          </label>
        </div>
        <div className="ticket-row">
          <label>
            Qty
            <input
              type="number"
              min={1}
              value={draft.quantity}
              onChange={(e) => draft.setField("quantity", Number(e.target.value))}
            />
          </label>
          {isOption ? (
            <>
              <label>
                Strike
                <input
                  type="number"
                  step={0.0001}
                  value={draft.strike ?? ""}
                  onChange={(e) =>
                    draft.setField("strike", e.target.value ? Number(e.target.value) : null)
                  }
                />
              </label>
              <label>
                Tenor
                <input
                  type="text"
                  placeholder="1M"
                  value={draft.tenor ?? ""}
                  onChange={(e) => draft.setField("tenor", e.target.value || null)}
                />
              </label>
            </>
          ) : (
            <label>
              Limit
              <input
                type="number"
                step={0.0001}
                value={draft.limitPrice ?? ""}
                onChange={(e) =>
                  draft.setField("limitPrice", e.target.value ? Number(e.target.value) : null)
                }
              />
            </label>
          )}
        </div>

        {error ? <div className="panel-error">{error}</div> : null}

        {canPreview && greeks ? (
          <div className="ticket-greeks" data-testid="ticket-greeks">
            <MetricTile label="Price" value={greeks.price.toFixed(5)} />
            <MetricTile label="Delta" value={greeks.delta.toFixed(3)} />
            <MetricTile label="Gamma" value={greeks.gamma.toFixed(3)} />
            <MetricTile label="Vega" value={greeks.vega.toFixed(3)} />
            <MetricTile label="Theta" value={greeks.theta.toFixed(3)} />
          </div>
        ) : (
          <div className="ticket-hint">fill side, strike and tenor to preview greeks</div>
        )}

        <button
          type="button"
          className="ticket-submit"
          disabled={disabled}
          title={disabled ? "fill required fields" : "submit (orders endpoint pending R7)"}
        >
          Submit
        </button>
      </div>
    </section>
  );
}
