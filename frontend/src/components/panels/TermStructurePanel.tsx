import { useEffect, useState } from "react";
import { fetchTermStructure } from "../../api/endpoints";
import { TermStructureChart, type TermPoint } from "../charts/TermStructureChart";
import { useSelectionStore } from "../../store/selectionStore";

export function TermStructurePanel(): JSX.Element {
  const symbol = useSelectionStore((s) => s.symbol);
  const [points, setPoints] = useState<TermPoint[]>([]);

  useEffect(() => {
    fetchTermStructure(symbol)
      .then((r) =>
        setPoints(
          r.pillars
            .filter((p): p is { tenor: string; dte: number | null; sigma_atm_pct: number } =>
              p.sigma_atm_pct !== null,
            )
            .map((p) => ({ tenor: p.tenor, atmVol: p.sigma_atm_pct })),
        ),
      )
      .catch(() => setPoints([]));
  }, [symbol]);

  return (
    <section className="panel term-panel" data-testid="term-panel">
      <header className="panel-header">
        <h2>Term Structure</h2>
      </header>
      <div className="panel-body">
        <TermStructureChart points={points} />
      </div>
    </section>
  );
}
