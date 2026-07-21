/**
 * Hardware / resource monitor — oriented to "how much does the stack consume
 * and what EC2 size does it need". Data: GET /api/v1/dev/containers/metrics
 * (Prometheus/cAdvisor, or the Docker-socket fallback) + a host snapshot from
 * GET /api/v1/dev/hardware. Read-only, dev-only.
 */
import type { Data, Layout } from "plotly.js";
import { apiFetch } from "../../api/client";
import { useEffect, useRef, useState } from "react";

import { PlotlyChart } from "../../components/charts/PlotlyChart";

interface Cpu { percent: number; cores: number; per_core: number[]; load_avg: number[]; }
interface Mem { total_gb: number; used_gb: number; percent: number; }
interface Disk { total_gb: number; used_gb: number; percent: number; }
interface Gpu { name: string; util_percent: number | null; mem_used_mb: number | null; mem_total_mb: number | null; temp_c: number | null; }
interface Hw { cpu: Cpu; memory: Mem; disk: Disk; gpu: Gpu[]; }
interface Series { name: string; points: [number, number][]; }
interface ContainerMetrics { reachable: boolean; source?: string; cpu: Series[]; mem: Series[]; }

const POLL_MS = 10_000;
const WINDOWS = [{ lbl: "5m", m: 5 }, { lbl: "15m", m: 15 }, { lbl: "1h", m: 60 }];
const PALETTE = ["#3ec46d", "#5b8fd6", "#d9a441", "#a77bd6", "#e0726a", "#38c8c0", "#e0a060", "#7fa8e0", "#cf6bce", "#5fce93", "#d9b86a", "#8fb0d8", "#b0d86a", "#d86ab0"];
const col = (p: number): string => (p > 85 ? "#e0564f" : p > 65 ? "#d9a441" : "#3ec46d");
const last = (s: Series): number => (s.points.length ? s.points[s.points.length - 1]![1] : 0);
/** mean of a series over the fetched window (drives the share donuts). */
const avg = (s: Series): number => (s.points.length ? s.points.reduce((a, p) => a + p[1], 0) / s.points.length : 0);
const short = (n: string): string => n.replace("fxvol-", "");
const GB = 1_073_741_824;

/** total across containers per time-index (series share timestamps) → now/peak/avg. */
function stackTotal(series: Series[]): { now: number; peak: number } {
  const maxLen = series.reduce((m, s) => Math.max(m, s.points.length), 0);
  let peak = 0;
  for (let i = 0; i < maxLen; i++) {
    let t = 0;
    for (const s of series) { const p = s.points[i]; if (p) t += p[1]; }
    if (t > peak) peak = t;
  }
  return { now: series.reduce((a, s) => a + last(s), 0), peak };
}

function Gauge({ label, pct, detail }: { label: string; pct: number; detail?: string }): JSX.Element {
  return (
    <div style={{ flex: 1, minWidth: 140 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", fontSize: 11, marginBottom: 4 }}>
        <span style={{ color: "#9aa1ae" }}>{label}</span>
        <span style={{ fontFamily: "Consolas, monospace" }}><b style={{ color: col(pct) }}>{pct.toFixed(0)}%</b>{detail ? <span style={{ color: "#666" }}> · {detail}</span> : null}</span>
      </div>
      <div style={{ height: 8, background: "#181b22", borderRadius: 4, overflow: "hidden", border: "1px solid #23272f" }}>
        <div style={{ height: "100%", width: Math.min(100, pct) + "%", background: col(pct), transition: "width .3s" }} />
      </div>
    </div>
  );
}

function Tile({ label, value, sub }: { label: string; value: string; sub?: string }): JSX.Element {
  return (
    <div style={{ background: "#0d0f13", border: "1px solid #23272f", borderRadius: 6, padding: "10px 14px", minWidth: 130 }}>
      <div style={{ fontSize: 10, letterSpacing: ".1em", color: "#6b7180", textTransform: "uppercase" }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, color: "#e6eaf1", fontFamily: "Consolas, monospace", marginTop: 2 }}>{value}</div>
      {sub ? <div style={{ fontSize: 10.5, color: "#7b8494", fontFamily: "Consolas, monospace" }}>{sub}</div> : null}
    </div>
  );
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }): JSX.Element {
  return (
    <section style={{ background: "#0a0a0a", border: "1px solid #222", borderRadius: 6, padding: "12px 14px" }}>
      <div style={{ fontSize: 11, letterSpacing: ".14em", color: "#6b7180", fontWeight: 700, marginBottom: 8 }}>{title}{hint ? <span style={{ color: "#4d5360", letterSpacing: 0 }}> · {hint}</span> : null}</div>
      {children}
    </section>
  );
}

const STACK_LAYOUT: Partial<Layout> = {
  showlegend: true,
  legend: { orientation: "h", font: { size: 9 }, y: -0.2 },
  margin: { t: 8, r: 12, b: 40, l: 50 },
  xaxis: { type: "date", gridcolor: "#262a33" },
};

export function Hardware(): JSX.Element {
  const [hw, setHw] = useState<Hw | null>(null);
  const [cm, setCm] = useState<ContainerMetrics | null>(null);
  const [minutes, setMinutes] = useState(15);
  const [sort, setSort] = useState<"cpu" | "mem">("cpu");
  const [err, setErr] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  const load = async (mins: number): Promise<void> => {
    try {
      const [h, c] = await Promise.all([
        apiFetch("/api/v1/dev/hardware").then((r) => (r.ok ? r.json() : null)),
        apiFetch(`/api/v1/dev/containers/metrics?minutes=${mins}`).then((r) => (r.ok ? r.json() : null)),
      ]);
      setHw(h as Hw | null);
      setCm(c as ContainerMetrics | null);
      setErr(null);
    } catch (e) { setErr(String(e)); }
  };
  useEffect(() => {
    void load(minutes);
    timer.current = window.setInterval(() => void load(minutes), POLL_MS);
    return () => { if (timer.current) window.clearInterval(timer.current); };
  }, [minutes]);

  // colour per container, stable across donut / table / area.
  const colorMap: Record<string, string> = {};
  (cm?.cpu ?? []).map((s) => s.name).sort().forEach((n, i) => { colorMap[n] = PALETTE[i % PALETTE.length]!; });

  const toArea = (series: Series[], scale: number): Data[] =>
    series.map((s) => ({
      x: s.points.map((p) => new Date(p[0] * 1000)),
      y: s.points.map((p) => p[1] * scale),
      type: "scatter", mode: "lines", stackgroup: "one", name: short(s.name),
      line: { color: colorMap[s.name] ?? "#888", width: 0.5 },
    })) as unknown as Data[];

  const donut = (items: { name: string; v: number }[]): Data[] => [{
    type: "pie", hole: 0.64, sort: false,
    labels: items.map((i) => short(i.name)), values: items.map((i) => i.v),
    marker: { colors: items.map((i) => colorMap[i.name] ?? "#888") },
    textinfo: "none", hoverinfo: "label+value+percent",
  }] as unknown as Data[];
  const donutLayout = (center: string): Partial<Layout> => ({
    showlegend: false, margin: { t: 6, r: 6, b: 6, l: 6 },
    annotations: [{ text: center, showarrow: false, font: { size: 16, color: "#e6eaf1" } }],
  });

  const cpuTot = cm ? stackTotal(cm.cpu) : { now: 0, peak: 0 };
  const memTot = cm ? stackTotal(cm.mem) : { now: 0, peak: 0 };
  const winLabel = WINDOWS.find((w) => w.m === minutes)?.lbl ?? `${minutes}m`;
  // Donuts = share AVERAGED over the selected window (so 5m/15m/1h changes them).
  const cpuShare = (cm?.cpu ?? []).map((s) => ({ name: s.name, v: avg(s) })).filter((x) => x.v > 0.05);
  const memShare = (cm?.mem ?? []).map((s) => ({ name: s.name, v: avg(s) })).filter((x) => x.v > 0);
  const cpuShareTot = cpuShare.reduce((a, x) => a + x.v, 0);
  const memShareTot = memShare.reduce((a, x) => a + x.v, 0);
  // Table = current snapshot (latest sample), docker-stats style.
  const memByName: Record<string, number> = {};
  (cm?.mem ?? []).forEach((s) => { memByName[s.name] = last(s); });
  const tableRows = (cm?.cpu ?? []).map((s) => ({ name: s.name, cpu: last(s), mem: memByName[s.name] ?? 0 }))
    .sort((a, b) => (sort === "cpu" ? b.cpu - a.cpu : b.mem - a.mem));
  const maxCpu = Math.max(0.01, ...tableRows.map((r) => r.cpu));
  const maxMem = Math.max(1, ...tableRows.map((r) => r.mem));

  return (
    <div style={{ padding: 14, fontFamily: "'IBM Plex Sans', system-ui, sans-serif", color: "#d4d8e0", display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
        <span style={{ fontSize: 14, fontWeight: 700, color: "#e6eaf1" }}>🖥 Hardware</span>
        <span style={{ fontSize: 11, color: "#666", fontFamily: "Consolas, monospace" }}>per-container consumption{cm?.source ? ` · ${cm.source}` : ""}</span>
        <span style={{ flex: 1 }} />
        <div style={{ display: "flex", gap: 4 }}>
          {WINDOWS.map((w) => (
            <button key={w.m} onClick={() => setMinutes(w.m)} style={{ padding: "3px 10px", fontSize: 11, borderRadius: 5, cursor: "pointer", border: "1px solid " + (minutes === w.m ? "#2f5c3f" : "#23272f"), background: minutes === w.m ? "rgba(62,196,109,.12)" : "transparent", color: minutes === w.m ? "#cdebd6" : "#8a909c" }}>{w.lbl}</button>
          ))}
        </div>
      </div>

      {err ? <div style={{ color: "#fbb", fontSize: 12, background: "#3a1a1a", padding: "6px 10px", borderRadius: 4 }}>{err}</div> : null}

      {hw ? (
        <div style={{ display: "flex", gap: 18, alignItems: "center", background: "#0a0a0a", border: "1px solid #222", borderRadius: 6, padding: "10px 16px" }}>
          <span style={{ fontSize: 10, letterSpacing: ".14em", color: "#6b7180", fontWeight: 700 }}>HOST</span>
          <Gauge label={`CPU · ${hw.cpu.cores} cores`} pct={hw.cpu.percent} detail={`load ${hw.cpu.load_avg.map((x) => x.toFixed(1)).join(" ")}`} />
          <Gauge label="RAM" pct={hw.memory.percent} detail={`${hw.memory.used_gb.toFixed(1)}/${hw.memory.total_gb.toFixed(1)} GB`} />
          <Gauge label="Disk" pct={hw.disk.percent} detail={`${hw.disk.used_gb.toFixed(0)}/${hw.disk.total_gb.toFixed(0)} GB`} />
          {hw.gpu.length ? <Gauge label={`GPU · ${short(hw.gpu[0]!.name)}`} pct={hw.gpu[0]!.util_percent ?? 0} /> : <span style={{ fontSize: 11, color: "#4d5360" }}>no GPU</span>}
        </div>
      ) : null}

      {cm && !cm.reachable ? (
        <div style={{ color: "#d9b86a", fontSize: 12, background: "#23200f", border: "1px solid #3a3417", padding: "10px 14px", borderRadius: 6, lineHeight: 1.6 }}>
          No per-container metrics yet. Linux/EC2: <code style={{ color: "#cdd1d8" }}>docker compose --profile obs up -d cadvisor prometheus</code>. Docker Desktop: recreate the api for the socket <code style={{ color: "#cdd1d8" }}>docker compose up -d api</code> — graphs then build live.
        </div>
      ) : cm ? (
        <>
          {/* sizing tiles — full width, aligned with the toolbar/graphs */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10 }}>
            <Tile label="Stack CPU" value={`${cpuTot.now.toFixed(0)}%`} sub={`peak ${cpuTot.peak.toFixed(0)}% · ${(cpuTot.peak / 100).toFixed(1)} vCPU`} />
            <Tile label="Stack RAM" value={`${(memTot.now / GB).toFixed(1)} GB`} sub={`peak ${(memTot.peak / GB).toFixed(1)} GB`} />
            <Tile label="Containers" value={`${tableRows.length}`} sub={cm.source === "docker" ? "docker socket" : "cAdvisor"} />
            <Tile label="EC2 sizing" value={`~${Math.max(1, Math.ceil(cpuTot.peak / 100))} vCPU`} sub={`~${Math.max(1, Math.ceil(memTot.peak / GB * 1.3))} GB (1.3× peak)`} />
          </div>

          {/* donuts — same 1fr 1fr grid as the graphs below (right edge aligned with the toolbar) */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
            <Section title="CPU SHARE" hint={`${winLabel} avg`}>
              <PlotlyChart data={donut(cpuShare)} layout={donutLayout(`${cpuShareTot.toFixed(0)}%`)} height={220} />
            </Section>
            <Section title="RAM SHARE" hint={`${winLabel} avg`}>
              <PlotlyChart data={donut(memShare)} layout={donutLayout(`${(memShareTot / GB).toFixed(1)}GB`)} height={220} />
            </Section>
          </div>

          {/* top consumers — full width */}
          <Section title="TOP CONSUMERS" hint={`sort: ${sort}`}>
            <table className="dt" style={{ width: "100%", fontSize: 12, tableLayout: "fixed" }}>
              <thead>
                <tr style={{ color: "#6b7180" }}>
                  <th style={{ textAlign: "left", padding: "3px 8px", width: "22%" }}>Container</th>
                  <th onClick={() => setSort("cpu")} style={{ textAlign: "right", padding: "3px 8px", width: 64, cursor: "pointer", color: sort === "cpu" ? "#cdebd6" : "#6b7180" }}>CPU% ▾</th>
                  <th style={{ padding: "3px 8px" }} />
                  <th onClick={() => setSort("mem")} style={{ textAlign: "right", padding: "3px 8px", width: 84, cursor: "pointer", color: sort === "mem" ? "#cdebd6" : "#6b7180" }}>RAM ▾</th>
                  <th style={{ padding: "3px 8px" }} />
                </tr>
              </thead>
              <tbody>
                {tableRows.map((r) => (
                  <tr key={r.name}>
                    <td style={{ padding: "3px 8px", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}><span style={{ display: "inline-block", width: 8, height: 8, borderRadius: 2, background: colorMap[r.name] ?? "#888", marginRight: 6 }} />{short(r.name)}</td>
                    <td style={{ textAlign: "right", padding: "3px 8px", fontFamily: "Consolas, monospace", color: col(r.cpu) }}>{r.cpu.toFixed(1)}</td>
                    <td style={{ padding: "3px 8px" }}><div style={{ background: "#181b22", borderRadius: 3, height: 7, overflow: "hidden" }}><div style={{ width: (r.cpu / maxCpu) * 100 + "%", height: "100%", background: colorMap[r.name] ?? "#888" }} /></div></td>
                    <td style={{ textAlign: "right", padding: "3px 8px", fontFamily: "Consolas, monospace", color: "#cdd1d8" }}>{r.mem >= GB ? (r.mem / GB).toFixed(2) + " GB" : (r.mem / 1_048_576).toFixed(0) + " MB"}</td>
                    <td style={{ padding: "3px 8px" }}><div style={{ background: "#181b22", borderRadius: 3, height: 7, overflow: "hidden" }}><div style={{ width: (r.mem / maxMem) * 100 + "%", height: "100%", background: colorMap[r.name] ?? "#888" }} /></div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Section>

          {/* stacked-area time-series */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
            <Section title="CPU OVER TIME" hint="stacked · % of one core">
              <PlotlyChart data={toArea(cm.cpu, 1)} layout={{ ...STACK_LAYOUT, yaxis: { gridcolor: "#262a33", ticksuffix: "%" } }} height={300} />
            </Section>
            <Section title="RAM OVER TIME" hint="stacked · GB">
              <PlotlyChart data={toArea(cm.mem, 1 / GB)} layout={{ ...STACK_LAYOUT, yaxis: { gridcolor: "#262a33", ticksuffix: " GB" } }} height={300} />
            </Section>
          </div>
        </>
      ) : <div style={{ color: "#666", fontSize: 12 }}>loading…</div>}
    </div>
  );
}
