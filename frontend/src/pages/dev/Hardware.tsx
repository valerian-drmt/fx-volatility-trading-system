/**
 * Hardware monitor — host CPU / RAM / disk + GPU consumption of the machine
 * running the stack. Polls GET /api/v1/dev/hardware (reads /proc + nvidia-smi
 * server-side; browsers can't read host hardware). Read-only, dev-only.
 */
import { useEffect, useRef, useState } from "react";

interface Cpu { percent: number; cores: number; per_core: number[]; load_avg: number[]; }
interface Mem { total_gb: number; used_gb: number; percent: number; }
interface Disk { total_gb: number; used_gb: number; percent: number; }
interface Gpu {
  name: string;
  util_percent: number | null;
  mem_used_mb: number | null;
  mem_total_mb: number | null;
  temp_c: number | null;
}
interface Hw { cpu: Cpu; memory: Mem; disk: Disk; gpu: Gpu[]; timestamp: string; }

const POLL_MS = 2_000;
const col = (p: number): string => (p > 85 ? "#e0564f" : p > 65 ? "#d9a441" : "#3ec46d");

function Gauge({ label, pct, detail }: { label: string; pct: number; detail?: string }): JSX.Element {
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", fontSize: 12, marginBottom: 5 }}>
        <span style={{ color: "#cdd1d8", fontWeight: 600 }}>{label}</span>
        <span style={{ fontFamily: "Consolas, monospace" }}>
          <b style={{ color: col(pct) }}>{pct.toFixed(0)}%</b>
          {detail ? <span style={{ color: "#666" }}> · {detail}</span> : null}
        </span>
      </div>
      <div style={{ height: 11, background: "#181b22", borderRadius: 6, overflow: "hidden", border: "1px solid #23272f" }}>
        <div style={{ height: "100%", width: Math.min(100, pct) + "%", background: col(pct), transition: "width .3s, background .3s" }} />
      </div>
    </div>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }): JSX.Element {
  return (
    <section style={{ background: "#0a0a0a", border: "1px solid #222", borderRadius: 6, padding: "14px 16px" }}>
      <div style={{ fontSize: 11, letterSpacing: ".14em", color: "#6b7180", fontWeight: 700, marginBottom: 12 }}>{title}</div>
      {children}
    </section>
  );
}

export function Hardware(): JSX.Element {
  const [data, setData] = useState<Hw | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [auto, setAuto] = useState(true);
  const timer = useRef<number | null>(null);

  const load = async (): Promise<void> => {
    try {
      const r = await fetch("/api/v1/dev/hardware");
      if (!r.ok) throw new Error("HTTP " + r.status);
      setData((await r.json()) as Hw);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    void load();
    if (auto) timer.current = window.setInterval(() => void load(), POLL_MS);
    return () => { if (timer.current) window.clearInterval(timer.current); };
  }, [auto]);

  const c = data?.cpu;
  const m = data?.memory;
  const d = data?.disk;
  return (
    <div style={{ padding: 14, fontFamily: "'IBM Plex Sans', system-ui, sans-serif", color: "#d4d8e0" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 14 }}>
        <span style={{ fontSize: 14, fontWeight: 700, color: "#e6eaf1" }}>🖥 Hardware</span>
        <span style={{ fontSize: 11, color: "#666", fontFamily: "Consolas, monospace" }}>
          host CPU/RAM/GPU · /api/v1/dev/hardware
        </span>
        <span style={{ flex: 1 }} />
        <label style={{ fontSize: 11, color: "#9aa1ae", display: "flex", alignItems: "center", gap: 5, cursor: "pointer" }}>
          <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} /> auto {POLL_MS / 1000}s
        </label>
        <button onClick={() => void load()} style={{ padding: "3px 11px", fontSize: 11, borderRadius: 4, border: "1px solid #2a3040", background: "#141a22", color: "#8fb0d8", cursor: "pointer" }}>↻ refresh</button>
      </div>

      {error ? <div style={{ color: "#fbb", fontSize: 12, background: "#3a1a1a", padding: "6px 10px", borderRadius: 4, marginBottom: 12 }}>{error}</div> : null}
      {!data ? <div style={{ color: "#666", fontSize: 12 }}>loading…</div> : null}

      {data ? (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
          <Card title="CPU">
            {c ? (
              <>
                <Gauge label="Overall" pct={c.percent} detail={`${c.cores} cores · load ${c.load_avg.map((x) => x.toFixed(2)).join(" ")}`} />
                <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 8 }}>
                  {c.per_core.map((p, i) => (
                    <div key={i} title={`core ${i}: ${p.toFixed(0)}%`} style={{ width: 26, height: 40, background: "#181b22", borderRadius: 3, display: "flex", flexDirection: "column", justifyContent: "flex-end", overflow: "hidden", border: "1px solid #23272f" }}>
                      <div style={{ height: Math.min(100, p) + "%", background: col(p) }} />
                    </div>
                  ))}
                </div>
              </>
            ) : null}
          </Card>

          <Card title="Memory">
            {m ? <Gauge label="RAM" pct={m.percent} detail={`${m.used_gb.toFixed(1)} / ${m.total_gb.toFixed(1)} GB`} /> : null}
            {d ? <Gauge label="Disk /" pct={d.percent} detail={`${d.used_gb.toFixed(0)} / ${d.total_gb.toFixed(0)} GB`} /> : null}
          </Card>

          <div style={{ gridColumn: "1 / -1" }}>
            <Card title="GPU">
              {data.gpu.length === 0 ? (
                <div style={{ color: "#666", fontSize: 12 }}>No NVIDIA GPU exposed to the container (nvidia-smi unavailable).</div>
              ) : (
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(260px,1fr))", gap: 12 }}>
                  {data.gpu.map((g, i) => (
                    <div key={i} style={{ background: "#0d0f13", border: "1px solid #23272f", borderRadius: 5, padding: "10px 12px" }}>
                      <div style={{ fontSize: 12, fontWeight: 600, color: "#cdd1d8", marginBottom: 8 }}>
                        {g.name}{g.temp_c != null ? <span style={{ float: "right", color: col(g.temp_c) }}>{g.temp_c.toFixed(0)}°C</span> : null}
                      </div>
                      <Gauge label="Util" pct={g.util_percent ?? 0} />
                      {g.mem_total_mb ? (
                        <Gauge label="VRAM" pct={100 * (g.mem_used_mb ?? 0) / g.mem_total_mb} detail={`${((g.mem_used_mb ?? 0) / 1024).toFixed(1)} / ${(g.mem_total_mb / 1024).toFixed(1)} GB`} />
                      ) : null}
                    </div>
                  ))}
                </div>
              )}
            </Card>
          </div>
        </div>
      ) : null}
    </div>
  );
}
