/**
 * VOLDESK live-data provider (R11 PR F).
 *
 * The swap point: views consume `useDeskData()` (from `./deskData`) per domain
 * instead of importing the mock `DATA` directly. Each domain resolves to either
 * the synthetic mock (when `mock=true` / a domain isn't wired yet) or the live
 * source (HTTP fetch + adapter, optionally invalidated by a WS stream) — both as
 * `Fresh<T>`.
 *
 * F wires ONE pilot domain (term-structure) end-to-end to prove the mechanism +
 * freshness contract. PRs 1–6 add the other domains here and migrate their views.
 *
 * `VITE_USE_MOCK` (default "true") flips the global default; per-domain overrides
 * land as each view is wired.
 */
import { type ReactNode } from "react";
import { fetchTermStructure } from "../../api/endpoints";
import { useFetch } from "../../hooks/useFetch";
import { termStructure as mockTermStructure, type TermPoint } from "./core";
import { type DeskData, DeskDataContext } from "./deskData";
import { type Fresh, makeFresh } from "./freshness";
import { adaptTermStructure } from "./live/termStructure";

const DEFAULT_MOCK = (import.meta.env["VITE_USE_MOCK"] ?? "true") !== "false";

const TERM_WARN_MS = 240_000; // vol-engine cycle ~3 min

export function DataProvider({
  children,
  mock = DEFAULT_MOCK,
}: {
  children: ReactNode;
  mock?: boolean;
}): JSX.Element {
  // Hooks called unconditionally; `enabled=!mock` skips the live fetch in mock
  // mode so no network call fires.
  const liveTerm = useFetch<TermPoint[]>(
    async () => adaptTermStructure(await fetchTermStructure()),
    TERM_WARN_MS,
    !mock,
  );

  const termStructure: Fresh<TermPoint[]> = mock
    ? makeFresh(mockTermStructure, Date.now(), Number.POSITIVE_INFINITY)
    : liveTerm;

  const value: DeskData = { termStructure };
  return (
    <DeskDataContext.Provider value={value}>{children}</DeskDataContext.Provider>
  );
}
