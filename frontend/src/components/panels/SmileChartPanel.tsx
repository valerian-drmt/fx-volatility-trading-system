import { useEffect, useState } from "react";
import { fetchSmile } from "../../api/endpoints";
import { SmileChart, type SmilePoint } from "../charts/SmileChart";
import { useSelectionStore } from "../../store/selectionStore";

export function SmileChartPanel(): JSX.Element {
  const symbol = useSelectionStore((s) => s.symbol);
  const tenor = useSelectionStore((s) => s.tenor);
  const [points, setPoints] = useState<SmilePoint[]>([]);

  useEffect(() => {
    fetchSmile(tenor, symbol)
      .then((r) => setPoints(r.points.map((p) => ({ strike: p.strike, vol: p.iv_pct }))))
      .catch(() => setPoints([]));
  }, [symbol, tenor]);

  return (
    <section className="panel smile-panel" data-testid="smile-panel">
      <header className="panel-header">
        <h2>Smile · {tenor}</h2>
      </header>
      <div className="panel-body">
        <SmileChart points={points} tenor={tenor} />
      </div>
    </section>
  );
}
