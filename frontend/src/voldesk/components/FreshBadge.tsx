/**
 * Freshness badge (R11 PR F). Renders a domain's live/stale/missing status next
 * to its panel title — the visual half of the "never fabricate data" contract
 * (decision 2026-06-16): on stale/missing the view keeps the last real value and
 * this badge tells the user the feed is behind / absent.
 *
 * In mock mode every domain reports `live` with infinite freshness, so the badge
 * reads "live" exactly as the prototype's static caption did.
 */
import type { Fresh } from "../data/freshness";

function ageLabel(ageMs: number | null): string {
  if (ageMs === null || !Number.isFinite(ageMs)) return "";
  const s = Math.round(ageMs / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m`;
  return `${Math.round(m / 60)}h`;
}

export function FreshBadge({
  fresh,
  label,
}: {
  fresh: Fresh<unknown>;
  label?: string;
}): JSX.Element {
  const { status, ageMs } = fresh;
  const word = status === "live" ? "live" : status === "stale" ? "stale" : "no data";
  const cls =
    status === "live" ? "pos" : status === "stale" ? "warn" : "neg";
  const age = status === "stale" ? ageLabel(ageMs) : "";
  return (
    <span className="dim mono small fresh-badge">
      {label ? `${label} · ` : ""}
      <span className={cls}>
        <i className={"fresh-dot " + cls} aria-hidden="true" /> {word}
        {age ? ` ${age}` : ""}
      </span>
    </span>
  );
}
