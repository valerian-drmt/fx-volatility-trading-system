import { useEffect, useRef, useState } from "react";
import { useTicks, type Tick } from "../../hooks/useTicks";
import { TickChart } from "../charts/TickChart";

const MAX_HISTORY = 300;

export function ChartPanel(): JSX.Element {
  const { last } = useTicks();
  const [history, setHistory] = useState<Tick[]>([]);
  const lastRef = useRef<Tick | null>(null);

  useEffect(() => {
    if (!last || last === lastRef.current) return;
    lastRef.current = last;
    setHistory((prev) => {
      const next = [...prev, last];
      return next.length > MAX_HISTORY ? next.slice(-MAX_HISTORY) : next;
    });
  }, [last]);

  return (
    <section className="panel chart-panel" data-testid="chart-panel">
      <header className="panel-header">
        <h2>Tick Chart</h2>
        <span className="panel-count">{history.length}</span>
      </header>
      <div className="panel-body">
        <TickChart history={history} />
      </div>
    </section>
  );
}
