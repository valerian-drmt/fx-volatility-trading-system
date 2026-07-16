/**
 * VOLDESK — Settings (versioned, append-only trading config).
 *
 * HYBRID read model (R11 PR 2r.2, decision 2026-06-16):
 *   - version-history table  ← /admin/config/history
 *   - current config (VolConfig) ← /admin/config, split by section into the
 *     four domain panels (trade / signal / risk / portfolio) next to their
 *     hot-applied config_scalar knobs. One Save per panel commits both:
 *     scalar edits upsert config_scalar, config edits append a new version.
 */
import { useState } from "react";
import { fetchDomainSettings, putConfig, putDomainSettings, revertConfig } from "../../api/endpoints";
import { useFetch } from "../../hooks/useFetch";
import { Panel } from "../components/common";
import { FreshBadge } from "../components/FreshBadge";
import type { ConfigSection } from "../data/deskData";
import { useDeskData } from "../data/deskData";
import { WRITE_ENABLED } from "../data/writeEnabled";
import { useAuthStore } from "../../store/authStore";

interface SettingParam { name: string; value: number; default: number; unit: string; description: string; isDefault: boolean; }
interface DomainSettings { title: string; params: SettingParam[]; }

/** VolConfig top-level sections rendered inside each domain panel. */
const DOMAIN_CONFIG_SECTIONS: Record<string, string[]> = {
  trade: ["structures", "delta_hedge"],
  signal: ["signal", "regime"],
  risk: ["sizing", "exit_rules"],
  portfolio: ["surface", "calibration"],
};

function adaptDomainSettings(raw: unknown): DomainSettings {
  const o = (raw ?? {}) as {
    title?: string;
    params?: { name?: string; value?: number; default?: number; unit?: string; description?: string; is_default?: boolean }[];
  };
  return {
    title: o.title ?? "Settings",
    params: (o.params ?? []).map((p) => ({
      name: p.name ?? "",
      value: Number(p.value ?? 0),
      default: Number(p.default ?? 0),
      unit: p.unit ?? "",
      description: p.description ?? "",
      isDefault: !!p.is_default,
    })),
  };
}

const GATE_TITLE = "writes disabled — auth required";
const SEP = "␟"; // edits-map key separator: `${section}${SEP}${dotted.field.key}`

/** "1.5"→1.5, "true/false"→bool, else string. Best-effort; Pydantic re-validates server-side. */
function coerce(s: string): unknown {
  if (s === "true") return true;
  if (s === "false") return false;
  if (s.trim() !== "" && !Number.isNaN(Number(s))) return Number(s);
  return s;
}

/** Set a (possibly nested, dotted) path on a plain object, creating objects en route. */
function setDeep(obj: Record<string, unknown>, path: string[], value: unknown): void {
  let cur = obj;
  path.forEach((key, i) => {
    if (i === path.length - 1) {
      cur[key] = value;
      return;
    }
    if (typeof cur[key] !== "object" || cur[key] === null) cur[key] = {};
    cur = cur[key] as Record<string, unknown>;
  });
}

/** Deep-merge patch from a `${section}${SEP}${dotted.key}` → raw-string edits map. */
function buildConfigPatch(edits: Record<string, string>): Record<string, unknown> {
  const patch: Record<string, unknown> = {};
  for (const [mapKey, raw] of Object.entries(edits)) {
    const sepAt = mapKey.indexOf(SEP);
    const section = mapKey.slice(0, sepAt);
    const fieldKey = mapKey.slice(sepAt + SEP.length);
    // "general" holds top-level scalars; other sections nest under their name.
    const path = section === "general" ? fieldKey.split(".") : [section, ...fieldKey.split(".")];
    setDeep(patch, path, coerce(raw));
  }
  return patch;
}

/** Versioned-config sections (read from /admin/config) as editable field rows. */
function CfgSectionFields({
  sections, canWrite, busy, edits, setEdits,
}: {
  sections: ConfigSection[];
  canWrite: boolean;
  busy: boolean;
  edits: Record<string, string>;
  setEdits: React.Dispatch<React.SetStateAction<Record<string, string>>>;
}): JSX.Element {
  return (
    <>
      {sections.map((s) => (
        <div key={s.name} className="cfg-section">
          <div className="cfg-section-head mono">{s.name}</div>
          {s.fields.map((f) => {
            const mapKey = `${s.name}${SEP}${f.key}`;
            return (
              <div key={f.key} className="cfg-field">
                <span className="cfg-key mono dim">{f.key}</span>
                {canWrite ? (
                  <input
                    className="cfg-val-input mono"
                    value={edits[mapKey] ?? f.value}
                    disabled={busy}
                    onChange={(e) =>
                      setEdits((prev) => {
                        const next = { ...prev };
                        if (e.target.value === f.value) delete next[mapKey];
                        else next[mapKey] = e.target.value;
                        return next;
                      })
                    }
                  />
                ) : (
                  <span className="cfg-val mono accent">{f.value}</span>
                )}
              </div>
            );
          })}
        </div>
      ))}
    </>
  );
}

/** One desk domain: hot-applied config_scalar knobs + its versioned-config sections. */
function DomainSettingsPanel({ domain, cfgSections }: { domain: string; cfgSections: ConfigSection[] }): JSX.Element {
  // Scalar knobs are editable as soon as you're logged in (the PUT is
  // cookie-gated server-side); versioned config also unlocks on the
  // local-dev WRITE_ENABLED bypass.
  const authenticated = useAuthStore((s) => s.authenticated);
  const canEditScalars = authenticated;
  const canEditConfig = authenticated || WRITE_ENABLED;
  const live = useFetch<DomainSettings>(() => fetchDomainSettings(domain).then(adaptDomainSettings), 600_000);
  const params = live.data?.params ?? [];
  const [scalarEdits, setScalarEdits] = useState<Record<string, string>>({});
  const [cfgEdits, setCfgEdits] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const scalarDirty = Object.keys(scalarEdits).length;
  const cfgDirty = Object.keys(cfgEdits).length;
  const dirty = scalarDirty + cfgDirty;
  const onCommit = async (): Promise<void> => {
    if (busy || dirty === 0) return;
    setBusy(true);
    try {
      if (scalarDirty > 0) {
        const updates: Record<string, number> = {};
        for (const [k, v] of Object.entries(scalarEdits)) {
          const num = Number(v);
          if (!Number.isNaN(num)) updates[k] = num;
        }
        await putDomainSettings(domain, updates);
      }
      if (cfgDirty > 0) {
        await putConfig(buildConfigPatch(cfgEdits), `edit ${cfgDirty} field${cfgDirty > 1 ? "s" : ""} (desk)`);
      }
      window.location.reload();
    } catch {
      setBusy(false);
    }
  };
  return (
    <Panel title={live.data?.title ?? domain} right={<FreshBadge fresh={live} label="hot-applied" />} className="config-edit settings-domain">
      <div className="cfg-sections">
        <div className="cfg-section">
          {params.map((p) => (
            <div key={p.name} className="cfg-field" title={p.description}>
              <span className="cfg-key mono dim">{p.name}{p.unit && <span className="dim"> · {p.unit}</span>}</span>
              {canEditScalars ? (
                <input
                  className="cfg-val-input mono"
                  type="number"
                  step="any"
                  value={scalarEdits[p.name] ?? String(p.value)}
                  disabled={busy}
                  onChange={(e) =>
                    setScalarEdits((prev) => {
                      const next = { ...prev };
                      if (Number(e.target.value) === p.value) delete next[p.name];
                      else next[p.name] = e.target.value;
                      return next;
                    })
                  }
                />
              ) : (
                <span className="cfg-val mono accent">{p.value}{p.isDefault && <span className="dim small"> · default</span>}</span>
              )}
            </div>
          ))}
          {params.length === 0 && <div className="dim small">unavailable.</div>}
        </div>
        <CfgSectionFields
          sections={cfgSections}
          canWrite={canEditConfig}
          busy={busy}
          edits={cfgEdits}
          setEdits={setCfgEdits}
        />
      </div>
      {(canEditScalars || canEditConfig) ? (
        <div className="cfg-commit-bar">
          <button className="btn-primary" disabled={busy || dirty === 0} onClick={onCommit}>
            {busy ? "Saving…" : dirty > 0 ? `Save ${dirty} change${dirty > 1 ? "s" : ""}` : "Save"}
          </button>
          {dirty > 0 && <button className="row-close" disabled={busy} onClick={() => { setScalarEdits({}); setCfgEdits({}); }}>reset</button>}
        </div>
      ) : null}
      <div className="dim small" style={{ marginTop: 10 }}>
        {canEditScalars || canEditConfig
          ? "Per-field editing · knobs upsert config_scalar (hot-applied); config sections commit a new version + hot-reload."
          : "Read-only · log in (top-right button) to edit these values."}
      </div>
    </Panel>
  );
}

/** Catch-all for VolConfig sections not mapped to a domain panel (e.g. a
 * newly-added backend section) — rendered only when non-empty so nothing
 * silently disappears from the desk. */
function OtherConfigPanel({ sections }: { sections: ConfigSection[] }): JSX.Element {
  const canWrite = useAuthStore((s) => s.authenticated) || WRITE_ENABLED;
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const dirty = Object.keys(edits).length;
  const onCommit = async (): Promise<void> => {
    if (busy || !canWrite || dirty === 0) return;
    setBusy(true);
    try {
      await putConfig(buildConfigPatch(edits), `edit ${dirty} field${dirty > 1 ? "s" : ""} (desk)`);
      window.location.reload();
    } catch {
      setBusy(false);
    }
  };
  return (
    <Panel title="Other config" className="config-edit settings-domain">
      <div className="cfg-sections">
        <CfgSectionFields sections={sections} canWrite={canWrite} busy={busy} edits={edits} setEdits={setEdits} />
      </div>
      {canWrite && (
        <div className="cfg-commit-bar">
          <button className="btn-primary" disabled={busy || dirty === 0} onClick={onCommit}>
            {busy ? "Saving…" : dirty > 0 ? `Save ${dirty} change${dirty > 1 ? "s" : ""}` : "Save"}
          </button>
          {dirty > 0 && <button className="row-close" disabled={busy} onClick={() => setEdits({})}>reset</button>}
        </div>
      )}
    </Panel>
  );
}

export function SettingsView(): JSX.Element {
  const { config } = useDeskData();
  const data = config.data;
  const [busy, setBusy] = useState(false);
  // Revert duplicates a past version as the new head + hot-reloads the engine.
  // Gated by real login state (auth cookie) OR the local-dev build bypass.
  const canWrite = useAuthStore((s) => s.authenticated) || WRITE_ENABLED;
  const onRevert = async (version: number): Promise<void> => {
    if (busy || !canWrite) return;
    setBusy(true);
    try {
      await revertConfig(version, `revert to v${version} (desk)`);
      window.location.reload();
    } catch {
      setBusy(false);
    }
  };
  const sections = data?.sections ?? [];
  const sectionsFor = (domain: string): ConfigSection[] =>
    sections.filter((s) => (DOMAIN_CONFIG_SECTIONS[domain] ?? []).includes(s.name));
  const mapped = new Set(Object.values(DOMAIN_CONFIG_SECTIONS).flat());
  const leftover = sections.filter((s) => !mapped.has(s.name));
  return (
    <div className="settings-view">
      <Panel
        title={"Configuration — version history" + (data ? ` · current v${data.currentVersion}` : "")}
        right={<FreshBadge fresh={config} label="versioned · append-only · hot-reload" />}
        pad={false}
        className="config-panel"
      >
        <div className="table-scroll">
          <table className="dt config-table">
            <thead>
              <tr>
                <th className="r">Ver</th>
                <th className="l">Changed by</th>
                <th className="l">Comment</th>
                <th className="l">When</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {(data?.history ?? []).map((h) => (
                <tr key={h.version}>
                  <td className="r mono accent">v{h.version}</td>
                  <td className="l mono dim">{h.by}</td>
                  <td className="l dim small">{h.comment || "—"}</td>
                  <td className="l mono dim small">{h.at ? new Date(h.at).toLocaleString() : "—"}</td>
                  <td className="r">
                    <button
                      className="row-close"
                      disabled={!canWrite || busy}
                      title={canWrite ? `revert to v${h.version}` : GATE_TITLE}
                      onClick={() => onRevert(h.version)}
                    >
                      revert
                    </button>
                  </td>
                </tr>
              ))}
              {(!data || data.history.length === 0) && (
                <tr>
                  <td colSpan={5} className="dim small ivz-empty">
                    no version recorded (default config)
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Panel>
      <div className="settings-domains">
        <DomainSettingsPanel domain="trade" cfgSections={sectionsFor("trade")} />
        <DomainSettingsPanel domain="signal" cfgSections={sectionsFor("signal")} />
        <DomainSettingsPanel domain="risk" cfgSections={sectionsFor("risk")} />
        <DomainSettingsPanel domain="portfolio" cfgSections={sectionsFor("portfolio")} />
        {leftover.length > 0 && <OtherConfigPanel sections={leftover} />}
      </div>
    </div>
  );
}
