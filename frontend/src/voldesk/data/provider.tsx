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
  fetchConfig,
  fetchConfigHistory,
  fetchDevEngines,
  fetchHealthExtended,
  fetchOpenPositions,
  fetchPcaHistory,
  fetchPcaModel,
  fetchPcaState,
  fetchPnlAttribution,
  fetchPortfolioAccount,
  fetchPortfolioCash,
  fetchPortfolioDailyPnl,
  fetchPortfolioStats,
  fetchPortfolioVar,
  fetchRegimeEvents,
  fetchRiskPerTenor,
  fetchTermStructure,
  fetchTradeBook,
  fetchTradeLimits,
  fetchVegaPerTenor,
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
  account as mockAccount,
  cash as mockCash,
  events as mockEvents,
  greeks as mockGreeks,
  limits as mockLimits,
  positions as mockPositions,
} from "./core";
import {
  type ConfigData,
  type ConfigSection,
  type DeskData,
  DeskDataContext,
  type PcaData,
  type PortfolioData,
  type SurfaceData,
  type SystemData,
  type TradeData,
  type VarData,
} from "./deskData";
import {
  bookComposition as mockBookComposition,
  config as mockConfig,
  dailyPnl as mockDailyPnl,
  engines as mockEngines,
  perfStats as mockPerfStats,
  stack as mockStack,
  vannaPerTenor as mockVannaPerTenor,
  vegaPerTenor as mockVegaPerTenor,
  volgaPerTenor as mockVolgaPerTenor,
  waterfall as mockWaterfall,
} from "./extended";
import { type Fresh, makeFresh } from "./freshness";
import { adaptConfig } from "./live/config";
import { adaptPca } from "./live/pca";
import {
  adaptAccount as adaptPortfolioAccountSnap,
  adaptDailyPnl,
  adaptPerfStats,
  adaptRiskPerTenor,
  adaptVar,
  adaptVegaPerTenor,
  adaptWaterfallGreek,
  deriveBookComposition,
} from "./live/portfolio";
import { adaptIvSurface } from "./live/surface";
import { adaptSystem } from "./live/system";
import { adaptTermStructure } from "./live/termStructure";
import { adaptAccount, adaptCash, adaptEvents, adaptLimits, adaptPositions, deriveNetGreeks } from "./live/trade";

const MOCK_PCA: PcaData = {
  pcs: mockPcs.map((p) => ({ ...p, zHistory: [] })),
  model: mockPcaModel,
};
const MOCK_SYSTEM: SystemData = { engines: mockEngines, stack: mockStack };
const SYSTEM_POLL_MS = 10_000; // engine heartbeats: no WS push → poll
const SYSTEM_WARN_MS = 20_000;
const MOCK_TRADE: TradeData = {
  positions: mockPositions,
  greeks: deriveNetGreeks(mockPositions),
  account: mockAccount,
  limits: mockLimits,
  events: mockEvents,
  cash: mockCash,
};
const TRADE_POLL_MS = 15_000; // positions mtm: snapshot + modest poll (WS in 6w)
const TRADE_WARN_MS = 30_000;
const MOCK_PORTFOLIO: PortfolioData = {
  account: mockAccount,
  greeks: deriveNetGreeks(mockPositions),
  positions: mockPositions,
  vegaPerTenor: mockVegaPerTenor,
  perfStats: mockPerfStats,
  dailyPnl: mockDailyPnl,
  waterfallGreek: mockWaterfall["greek"] ?? [],
  bookComposition: mockBookComposition,
};
const PORTFOLIO_WARN_MS = 120_000; // history-ish; light poll
const PORTFOLIO_POLL_MS = 60_000;
const MOCK_RISK: VarData = {
  var95: mockGreeks.var1d95,
  var99: mockGreeks.var1d99,
  es99: +(mockGreeks.var1d99 * 1.16).toFixed(1),
  nDays: 504,
  hist: [],
  perTenor: mockVegaPerTenor.map((r) => ({
    tenor: r.tenor,
    vega: r.vega,
    vanna: mockVannaPerTenor.find((x) => x.tenor === r.tenor)?.v ?? 0,
    volga: mockVolgaPerTenor.find((x) => x.tenor === r.tenor)?.v ?? 0,
    n: r.n,
  })),
};
const CONFIG_WARN_MS = Number.POSITIVE_INFINITY; // config rarely changes → never "stale"

// Mock config is a flat key/value list; fold it into the hybrid (sections + history) shape.
const MOCK_CONFIG: ConfigData = (() => {
  const bySection = new Map<string, ConfigSection>();
  for (const c of mockConfig) {
    const [head, ...rest] = c.key.split(".");
    const name = head ?? "general";
    if (!bySection.has(name)) bySection.set(name, { name, fields: [] });
    bySection.get(name)!.fields.push({ key: rest.length ? rest.join(".") : c.key, value: c.value });
  }
  return {
    currentVersion: Math.max(...mockConfig.map((c) => c.v)),
    sections: [...bySection.values()],
    history: mockConfig.map((c) => ({ version: c.v, by: c.by, comment: c.note, at: null })),
  };
})();

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
  const liveConfig = useFetch<ConfigData>(
    async () => {
      const [current, history] = await Promise.all([fetchConfig(), fetchConfigHistory()]);
      return adaptConfig(current, history);
    },
    CONFIG_WARN_MS,
    !mock,
  );
  const liveTrade = useFetch<TradeData>(
    async () => {
      const [pos, lim, evts, book, cash] = await Promise.all([
        fetchOpenPositions(),
        fetchTradeLimits(),
        fetchRegimeEvents(),
        fetchTradeBook(),
        fetchPortfolioCash(),
      ]);
      const positions = adaptPositions(pos, Date.now());
      return {
        positions,
        greeks: deriveNetGreeks(positions),
        account: adaptAccount(book),
        limits: adaptLimits(lim),
        events: adaptEvents(evts, Date.now()),
        cash: adaptCash(cash),
      };
    },
    TRADE_WARN_MS,
    !mock,
    TRADE_POLL_MS,
  );

  const livePortfolio = useFetch<PortfolioData>(
    async () => {
      const [acct, vega, stats, daily, attrib, pos] = await Promise.all([
        fetchPortfolioAccount(),
        fetchVegaPerTenor(),
        fetchPortfolioStats(),
        fetchPortfolioDailyPnl(),
        fetchPnlAttribution(),
        fetchOpenPositions(),
      ]);
      const positions = adaptPositions(pos, Date.now());
      return {
        account: adaptPortfolioAccountSnap(acct),
        greeks: deriveNetGreeks(positions),
        positions,
        vegaPerTenor: adaptVegaPerTenor(vega),
        perfStats: adaptPerfStats(stats),
        dailyPnl: adaptDailyPnl(daily),
        waterfallGreek: adaptWaterfallGreek(attrib),
        bookComposition: deriveBookComposition(positions),
      };
    },
    PORTFOLIO_WARN_MS,
    !mock,
    PORTFOLIO_POLL_MS,
  );

  const liveRisk = useFetch<VarData>(
    async () => {
      const [v, rpt] = await Promise.all([fetchPortfolioVar(), fetchRiskPerTenor()]);
      return { ...adaptVar(v), perTenor: adaptRiskPerTenor(rpt) };
    },
    PORTFOLIO_WARN_MS,
    !mock,
    PORTFOLIO_POLL_MS,
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

  const config: Fresh<ConfigData> = mock
    ? makeFresh(MOCK_CONFIG, Date.now(), Number.POSITIVE_INFINITY)
    : liveConfig;

  const trade: Fresh<TradeData> = mock
    ? makeFresh(MOCK_TRADE, Date.now(), Number.POSITIVE_INFINITY)
    : liveTrade;

  const portfolio: Fresh<PortfolioData> = mock
    ? makeFresh(MOCK_PORTFOLIO, Date.now(), Number.POSITIVE_INFINITY)
    : livePortfolio;

  const risk: Fresh<VarData> = mock
    ? makeFresh(MOCK_RISK, Date.now(), Number.POSITIVE_INFINITY)
    : liveRisk;

  const value: DeskData = { termStructure, surface, pca, system, config, trade, portfolio, risk };
  return (
    <DeskDataContext.Provider value={value}>{children}</DeskDataContext.Provider>
  );
}
