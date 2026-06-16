/**
 * VOLDESK live-data provider (R11).
 *
 * The swap point: views consume `useDeskData()` (from `./deskData`) per domain
 * instead of importing the mock `DATA` directly. Each domain resolves to either
 * the synthetic mock (when `mock=true` / not wired yet) or the live source (HTTP
 * fetch + adapter, invalidated by a WS stream) — both as `Fresh<T>`.
 *
 * Wired so far:
 *   - termStructure (PR F)        — live via /vol/term-structure
 *   - surface.ivSurface (PR 1)    — live via /vol/surface, invalidated by /ws/vol
 *     surface.ivZ                 — still mock (backend per-cell z gap, see 09)
 *
 * `VITE_USE_MOCK` (default "true") flips the global default.
 */
import { type ReactNode, useEffect } from "react";
import { fetchTermStructure, fetchVolSurface } from "../../api/endpoints";
import { useFetch } from "../../hooks/useFetch";
import { useVolStream } from "../../hooks/streams";
import {
  deltas as mockDeltas,
  ivSurface as mockIvSurface,
  ivZ as mockIvZ,
  tenors as mockTenors,
  termStructure as mockTermStructure,
  type TermPoint,
} from "./core";
import { type DeskData, DeskDataContext, type SurfaceData } from "./deskData";
import { type Fresh, makeFresh } from "./freshness";
import { adaptIvSurface } from "./live/surface";
import { adaptTermStructure } from "./live/termStructure";

const DEFAULT_MOCK = (import.meta.env["VITE_USE_MOCK"] ?? "true") !== "false";
const VOL_WARN_MS = 240_000; // vol-engine cycle ~3 min

export function DataProvider({
  children,
  mock = DEFAULT_MOCK,
}: {
  children: ReactNode;
  mock?: boolean;
}): JSX.Element {
  // Hooks run unconditionally; `enabled=!mock` skips the live fetch in mock mode.
  const liveTerm = useFetch<TermPoint[]>(
    async () => adaptTermStructure(await fetchTermStructure()),
    VOL_WARN_MS,
    !mock,
  );
  const liveSurface = useFetch<number[][]>(
    async () => adaptIvSurface(await fetchVolSurface()),
    VOL_WARN_MS,
    !mock,
  );

  // Re-fetch surface + term on each vol-engine cycle push (~3 min).
  const vol = useVolStream(!mock);
  const reloadTerm = liveTerm.reload;
  const reloadSurface = liveSurface.reload;
  useEffect(() => {
    if (vol.asOf !== null) {
      reloadTerm();
      reloadSurface();
    }
  }, [vol.asOf, reloadTerm, reloadSurface]);

  const termStructure: Fresh<TermPoint[]> = mock
    ? makeFresh(mockTermStructure, Date.now(), Number.POSITIVE_INFINITY)
    : liveTerm;

  const surface: Fresh<SurfaceData> = mock
    ? makeFresh(
        { ivSurface: mockIvSurface, ivZ: mockIvZ, tenors: mockTenors, deltas: mockDeltas },
        Date.now(),
        Number.POSITIVE_INFINITY,
      )
    : {
        status: liveSurface.status,
        asOf: liveSurface.asOf,
        ageMs: liveSurface.ageMs,
        // ivZ stays on the mock until the backend exposes a per-cell z field.
        data: liveSurface.data
          ? {
              ivSurface: liveSurface.data,
              ivZ: mockIvZ,
              tenors: mockTenors,
              deltas: mockDeltas,
            }
          : null,
      };

  const value: DeskData = { termStructure, surface };
  return (
    <DeskDataContext.Provider value={value}>{children}</DeskDataContext.Provider>
  );
}
