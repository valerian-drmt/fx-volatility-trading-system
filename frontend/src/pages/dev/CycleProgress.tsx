import { fetchCycleProgress } from "../../api/endpoints";
import { useFetch } from "../../hooks/useFetch";

interface CycleProgressData {
  cycle_started_at: string | null;
  stage: string | null;
  task: string | null;
  completed: string[];
}

/**
 * Live vol-engine cycle progress (GET /api/v1/dev/cycle-progress). Shows the
 * tasks completed this cycle, the active `stage · task`, and an explicit idle
 * state when the engine is off / between cycles. Polls every 2s (the engine
 * writes `cycle_progress:vol_engine` as it walks its 5 pipelines). Self-contained
 * so it can be mounted in the PipelineViz inspector — or anywhere — unchanged.
 */
export function CycleProgress(): JSX.Element {
  const live = useFetch<CycleProgressData>(
    () => fetchCycleProgress() as Promise<CycleProgressData>,
    2000,
  );
  const d = live.data;
  const active = d?.stage && d?.task ? `${d.stage} · ${d.task}` : null;
  const done = d?.completed ?? [];
  const idle = !active && done.length === 0;
  return (
    <div
      className="pp-mono"
      style={{ flex: 1, overflow: "auto", minHeight: 0, padding: "8px 12px", fontSize: 11.5, lineHeight: 1.6, color: "#bcd0c6", background: "#08090c" }}
    >
      {idle ? (
        <span style={{ color: "#4d5360" }}>cycle idle — vol-engine off or between cycles</span>
      ) : (
        <>
          <div style={{ color: "#7b8494", marginBottom: 6 }}>
            cycle{d?.cycle_started_at ? " started " + d.cycle_started_at.slice(11, 19) : ""} · {done.length} task{done.length === 1 ? "" : "s"} done
          </div>
          {done.map((c) => (
            <div key={c} style={{ color: "#5fce93" }}>✓ {c}</div>
          ))}
          {active ? <div style={{ color: "#d9b86a" }}>▸ {active}</div> : null}
        </>
      )}
    </div>
  );
}
