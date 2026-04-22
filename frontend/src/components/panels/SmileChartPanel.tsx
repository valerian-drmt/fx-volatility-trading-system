import { useEffect, useState } from "react";
import { fetchSmile } from "../../api/endpoints";
import { SmileChart, type SmilePoint } from "../charts/SmileChart";
import { useSelectionStore } from "../../store/selectionStore";

const TENORS = ["1M", "2M", "3M", "4M", "5M", "6M"] as const;

type ApiPoint = { strike: number; iv_pct: number; delta_label: string };

export function SmileChartPanel(): JSX.Element {
  const symbol = useSelectionStore((s) => s.symbol);
  const tenor = useSelectionStore((s) => s.tenor);
  const setTenor = useSelectionStore((s) => s.setTenor);
  const [apiPoints, setApiPoints] = useState<ApiPoint[]>([]);

  useEffect(() => {
    fetchSmile(tenor, symbol)
      .then((r) => setApiPoints(r.points))
      .catch(() => setApiPoints([]));
  }, [symbol, tenor]);

  const chartPoints: SmilePoint[] = apiPoints.map((p) => ({
    strike: p.strike,
    vol: p.iv_pct,
  }));

  const atm = apiPoints.find((p) => p.delta_label === "ATM");
  const atmIv = atm?.iv_pct;

  return (
    <section className="panel smile-panel" data-testid="smile-panel">
      <header className="panel-header">
        <h2>Smile</h2>
        <select
          aria-label="tenor"
          data-testid="smile-tenor-select"
          className="panel-select"
          value={tenor}
          onChange={(e) => setTenor(e.target.value)}
        >
          {TENORS.map((t) => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>
      </header>
      <div className="panel-body smile-body">
        <div className="smile-chart-wrap">
          <SmileChart points={chartPoints} tenor={tenor} />
        </div>
        <table className="smile-table" data-testid="smile-table">
          <thead>
            <tr>
              <th>Δ</th>
              <th>Strike</th>
              <th>IV mid</th>
              <th>Skew</th>
            </tr>
          </thead>
          <tbody>
            {apiPoints.map((p) => {
              const skewBp =
                atmIv !== undefined && p.delta_label !== "ATM"
                  ? Math.round((p.iv_pct - atmIv) * 100)
                  : null;
              const skewClass =
                skewBp === null ? "" : skewBp > 0 ? "skew-pos" : skewBp < 0 ? "skew-neg" : "";
              return (
                <tr key={p.delta_label}>
                  <td>{p.delta_label}</td>
                  <td>{p.strike.toFixed(4)}</td>
                  <td>{p.iv_pct.toFixed(2)}%</td>
                  <td className={skewClass}>
                    {skewBp === null
                      ? "—"
                      : `${skewBp > 0 ? "+" : ""}${skewBp} bp`}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
