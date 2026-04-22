import { useEffect, useState } from "react";
import { fetchSmile } from "../../api/endpoints";
import { SmileChart, type SmilePoint } from "../charts/SmileChart";
import { useSelectionStore } from "../../store/selectionStore";

const TENORS = ["1M", "2M", "3M", "4M", "5M", "6M"] as const;

export function SmileChartPanel(): JSX.Element {
  const symbol = useSelectionStore((s) => s.symbol);
  const tenor = useSelectionStore((s) => s.tenor);
  const setTenor = useSelectionStore((s) => s.setTenor);
  const [points, setPoints] = useState<SmilePoint[]>([]);

  useEffect(() => {
    fetchSmile(tenor, symbol)
      .then((r) => setPoints(r.points.map((p) => ({ strike: p.strike, vol: p.iv_pct }))))
      .catch(() => setPoints([]));
  }, [symbol, tenor]);

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
      <div className="panel-body">
        <SmileChart points={points} tenor={tenor} />
      </div>
    </section>
  );
}
