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
import {
  fetchDevEngines,
  fetchHealthExtended,
  fetchPcaHistory,
  fetchPcaModel,
  fetchPcaState,
  fetchTermStructure,
  fetchVolSurface,
} from "../../api/endpoints";
import { useFetch } from "../../hooks/useFetch";
import { useVolStream } from "../../hooks/streams";
import {
  deltas as mockDeltas,
  ivSurface as mockIvSurface,
  ivZ as mockIvZ,
  pcaModel as mockPcaModel,
  pcs as mockPcs,
  tenors as mockTenors,
  termStructure as mockTermStructure,
  type TermPoint,
} from "./core";
import {
  type DeskData,
  DeskDataContext,
  type PcaData,
  type SurfaceData,
  type SystemData,
} from "./deskData";
import { engines as mockEngines, stack as mockStack } from "./extended";
import { type Fresh, makeFresh } from "./freshness";
import { adaptPca } from "./live/pca";
import { adaptIvSurface } from "./live/surface";
import { adaptSystem } from "./live/system";
import { adaptTermStructure } from "./live/termStructure";

const MOCK_PCA: PcaData = {
  pcs: mockPcs.map((p) => ({ ...p, zHistory: [] })),
  model: mockPcaModel,
};
const MOCK_SYSTEM: SystemData = { engines: mockEngines, stack: mockStack };
const SYSTEM_POLL_MS = 10_000; // engine heartbeats: no WS push → poll
const SYSTEM_WARN_MS = 20_000;

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
  const livePca = useFetch<PcaData>(
    async () => {
      const [state, model, h1, h2, h3] = await Promise.all([
        fetchPcaState(),
        fetchPcaModel(),
        fetchPcaHistory(1),
        fetchPcaHistory(2),
        fetchPcaHistory(3),
      ]);
      return adaptPca(state, model, [h1, h2, h3]);
    },
    VOL_WARN_MS,
    !mock,
  );
  const liveSystem = useFetch<SystemData>(
    async () => {
      // /dev/engines is auth-gated in prod → tolerate failure, compose from health.
      const [health, dev] = await Promise.allSettled([fetchHealthExtended(), fetchDevEngines()]);
      if (health.status !== "fulfilled") throw new Error("health/extended unavailable");
      return adaptSystem(health.value, dev.status === "fulfilled" ? dev.value : null);
    },
    SYSTEM_WARN_MS,
    !mock,
    SYSTEM_POLL_MS,
  );

  // Re-fetch surface + term + pca on each vol-engine cycle push (~3 min).
  const vol = useVolStream(!mock);
  const reloadTerm = liveTerm.reload;
  const reloadSurface = liveSurface.reload;
  const reloadPca = livePca.reload;
  useEffect(() => {
    if (vol.asOf !== null) {
      reloadTerm();
      reloadSurface();
      reloadPca();
    }
  }, [vol.asOf, reloadTerm, reloadSurface, reloadPca]);

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

  const pca: Fresh<PcaData> = mock
    ? makeFresh(MOCK_PCA, Date.now(), Number.POSITIVE_INFINITY)
    : livePca;

  const system: Fresh<SystemData> = mock
    ? makeFresh(MOCK_SYSTEM, Date.now(), Number.POSITIVE_INFINITY)
    : liveSystem;

  const value: DeskData = { termStructure, surface, pca, system };
  return (
    <DeskDataContext.Provider value={value}>{children}</DeskDataContext.Provider>
  );
}
