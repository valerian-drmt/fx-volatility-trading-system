/**
 * Hardware / resource monitor.
 *
 * Per-container CPU% and RAM over time (the main view) — data lives in
 * Prometheus (scraped from cAdvisor), this range-queries it via
 * GET /api/v1/dev/containers/metrics and plots the curves. Plus a compact
 * host snapshot (GET /api/v1/dev/hardware) for a quick "is the box ok" glance.
 * Read-only, dev-only. Needs the `obs` compose profile for the graphs.
 */
import type { Data, Layout } from "plotly.js";
import { useEffect, useRef, useState } from "react";

import { PlotlyChart } from "../../components/charts/PlotlyChart";

interface Cpu { percent: number; cores: number; per_core: number[]; load_avg: number[]; }
interface Mem { total_gb: number; used_gb: number; percent: number; }
interface Disk { total_gb: number; used_gb: number; percent: number; }
interface Gpu { name: string; util_percent: number | null; mem_used_mb: number | null; mem_total_mb: number | null; temp_c: number | null; }
interface Hw { cpu: Cpu; memory: Mem; disk: Disk; gpu: Gpu[]; }

interface Series { name: string; points: [number, number][]; }
interface ContainerMetrics { reachable: boolean; cpu: Series[]; mem: Series[]; }

const POLL_MS = 10_000;
const WINDOWS = [{ lbl: "5m", m: 5 }, { lbl: "15m", m: 15 }, { lbl: "1h", m: 60 }];
const PALETTE = ["#3ec46d", "#5b8fd6", "#d9a441", "#a77bd6", "#e0726a", "#38c8c0", "#e0a060", "#7fa8e0", "#cf6bce", "#5fce93", "#d9b86a", "#8fb0d8"];
const col = (p: number): string => (p > 85 ? "#e0564f" : p > 65 ? "#d9a441" : "#3ec46d");

function Gauge({ label, pct, detail }: { label: string; pct: number; detail?: string }): JSX.Element {
  return (
    <div style={{ flex: 1, minWidth: 150 }}>
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

const toTraces = (series: Series[], scale: number): Data[] =>
  series.map((s, i) => ({
    x: s.points.map((p) => new Date(p[0] * 1000)),
    y: s.points.map((p) => p[1] * scale),
    type: "scatter", mode: "lines", name: s.name.replace("fxvol-", ""),
    line: { color: PALETTE[i % PALETTE.length], width: 1.5 },
  })) as unknown as Data[];

const CHART_LAYOUT: Partial<Layout> = {
  showlegend: true,
  legend: { orientation: "h", font: { size: 9 }, y: -0.18 },
  margin: { t: 8, r: 12, b: 36, l: 48 },
  xaxis: { type: "date", gridcolor: "#262a33" },
};

export function Hardware(): JSX.Element {
  const [hw, setHw] = useState<Hw | null>(null);
  const [cm, setCm] = useState<ContainerMetrics | null>(null);
  const [minutes, setMinutes] = useState(15);
  const [err, setErr] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  const load = async (mins: number): Promise<void> => {
    try {
      const [h, c] = await Promise.all([
        fetch("/api/v1/dev/hardware").then((r) => (r.ok ? r.json() : null)),
        fetch(`/api/v1/dev/containers/metrics?minutes=${mins}`).then((r) => (r.ok ? r.json() : null)),
      ]);
      setHw(h as Hw | null);
      setCm(c as ContainerMetrics | null);
      setErr(null);
    } catch (e) {
      setErr(String(e));
    }
  };

  useEffect(() => {
    void load(minutes);
    timer.current = window.setInterval(() => void load(minutes), POLL_MS);
    return () => { if (timer.current) window.clearInterval(timer.current); };
  }, [minutes]);

  return (
    <div style={{ padding: 14, fontFamily: "'IBM Plex Sans', system-ui, sans-serif", color: "#d4d8e0" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 12 }}>
        <span style={{ fontSize: 14, fontWeight: 700, color: "#e6eaf1" }}>🖥 Hardware</span>
        <span style={{ fontSize: 11, color: "#666", fontFamily: "Consolas, monospace" }}>per-container CPU/RAM · cAdvisor → Prometheus</span>
        <span style={{ flex: 1 }} />
        <div style={{ display: "flex", gap: 4 }}>
          {WINDOWS.map((w) => (
            <button key={w.m} onClick={() => setMinutes(w.m)} style={{ padding: "3px 10px", fontSize: 11, borderRadius: 5, cursor: "pointer", border: "1px solid " + (minutes === w.m ? "#2f5c3f" : "#23272f"), background: minutes === w.m ? "rgba(62,196,109,.12)" : "transparent", color: minutes === w.m ? "#cdebd6" : "#8a909c" }}>{w.lbl}</button>
          ))}
        </div>
      </div>

      {err ? <div style={{ color: "#fbb", fontSize: 12, background: "#3a1a1a", padding: "6px 10px", borderRadius: 4, marginBottom: 12 }}>{err}</div> : null}

      {/* Host snapshot strip */}
      {hw ? (
        <div style={{ display: "flex", gap: 18, alignItems: "center", background: "#0a0a0a", border: "1px solid #222", borderRadius: 6, padding: "10px 16px", marginBottom: 14 }}>
          <span style={{ fontSize: 10, letterSpacing: ".14em", color: "#6b7180", fontWeight: 700, whiteSpace: "nowrap" }}>HOST</span>
          <Gauge label={`CPU · ${hw.cpu.cores} cores`} pct={hw.cpu.percent} detail={`load ${hw.cpu.load_avg.map((x) => x.toFixed(1)).join(" ")}`} />
          <Gauge label="RAM" pct={hw.memory.percent} detail={`${hw.memory.used_gb.toFixed(1)}/${hw.memory.total_gb.toFixed(1)} GB`} />
          <Gauge label="Disk" pct={hw.disk.percent} detail={`${hw.disk.used_gb.toFixed(0)}/${hw.disk.total_gb.toFixed(0)} GB`} />
          {hw.gpu.length ? <Gauge label={`GPU · ${hw.gpu[0]!.name}`} pct={hw.gpu[0]!.util_percent ?? 0} /> : <span style={{ fontSize: 11, color: "#4d5360" }}>no GPU</span>}
        </div>
      ) : null}

      {/* Per-container time-series */}
      {cm && !cm.reachable ? (
        <div style={{ color: "#d9b86a", fontSize: 12, background: "#23200f", border: "1px solid #3a3417", padding: "10px 14px", borderRadius: 6, lineHeight: 1.6 }}>
          No per-container metrics yet. On Linux/EC2: start the obs profile{" "}
          <code style={{ color: "#cdd1d8" }}>docker compose --profile obs up -d cadvisor prometheus</code>.{" "}
          On Docker Desktop (cAdvisor can't read per-container cgroups): recreate the api so it mounts the Docker socket{" "}
          <code style={{ color: "#cdd1d8" }}>docker compose up -d api</code> — graphs then build live.
        </div>
      ) : cm ? (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
          <section style={{ background: "#0a0a0a", border: "1px solid #222", borderRadius: 6, padding: "12px 14px" }}>
            <div style={{ fontSize: 11, letterSpacing: ".14em", color: "#6b7180", fontWeight: 700, marginBottom: 6 }}>CPU % PER CONTAINER <span style={{ color: "#4d5360", letterSpacing: 0 }}>· % of one core</span></div>
            <PlotlyChart data={toTraces(cm.cpu, 1)} layout={{ ...CHART_LAYOUT, yaxis: { gridcolor: "#262a33", ticksuffix: "%" } }} height={300} />
          </section>
          <section style={{ background: "#0a0a0a", border: "1px solid #222", borderRadius: 6, padding: "12px 14px" }}>
            <div style={{ fontSize: 11, letterSpacing: ".14em", color: "#6b7180", fontWeight: 700, marginBottom: 6 }}>RAM PER CONTAINER <span style={{ color: "#4d5360", letterSpacing: 0 }}>· working set (GB)</span></div>
            <PlotlyChart data={toTraces(cm.mem, 1 / 1_073_741_824)} layout={{ ...CHART_LAYOUT, yaxis: { gridcolor: "#262a33", ticksuffix: " GB" } }} height={300} />
          </section>
        </div>
      ) : (
        <div style={{ color: "#666", fontSize: 12 }}>loading…</div>
      )}
    </div>
  );
}
