/**
 * VOLDESK live-data provider (R11).
 *
 * Views consume `useDeskData()` (from `./deskData`) per domain. Every domain is
 * live: an HTTP fetch + adapter, invalidated by a WS stream (vol cycle / risk
 * beat) or a light poll. There is no mock mode — the desk always runs against
 * the real backend (api → engines → Redis/Postgres). When a source has no data
 * yet (cold start / market closed) the domain is simply `missing`/`stale` via
 * the `Fresh<T>` contract and the views render the empty/neutral state.
 */
import { type ReactNode, useEffect, useMemo } from "react";
import {
  fetchConfig,
  fetchConfigHistory,
  fetchBookPositions,
  fetchDevEngines,
  fetchHealthExtended,
  fetchPcaHistory,
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
import { type TickMsg, useRiskStream, useTicks, useVolStream } from "../../hooks/streams";
import { deltas as DELTA_LABELS, type TermPoint } from "./core";
import {
  type ConfigData,
  type DeskData,
  DeskDataContext,
  type PcaData,
  type PortfolioData,
  type SurfaceData,
  type SystemData,
  TicksContext,
  type TradeData,
  type VarData,
} from "./deskData";
import { type Fresh } from "./freshness";
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
import { adaptSurface } from "./live/surface";
import { adaptSystem } from "./live/system";
import { adaptTermStructure } from "./live/termStructure";
import { adaptAccount, adaptCash, adaptEvents, adaptLimits, adaptPositions, deriveNetGreeks } from "./live/trade";

const SYSTEM_POLL_MS = 10_000; // engine heartbeats: no WS push → poll
const SYSTEM_WARN_MS = 20_000;
const TRADE_POLL_MS = 15_000; // positions mtm: snapshot + modest poll (WS in 6w)
const TRADE_WARN_MS = 30_000;
const PORTFOLIO_WARN_MS = 120_000; // history-ish; light poll
const PORTFOLIO_POLL_MS = 60_000;
const CONFIG_WARN_MS = Number.POSITIVE_INFINITY; // config rarely changes → never "stale"
const VOL_WARN_MS = 240_000; // vol-engine cycle ~3 min

export function DataProvider({ children }: { children: ReactNode }): JSX.Element {
  const liveTerm = useFetch<TermPoint[]>(
    async () => adaptTermStructure(await fetchTermStructure()),
    VOL_WARN_MS,
  );
  const liveSurface = useFetch<{
    tenors: string[];
    ivSurface: number[][];
    ivZ: number[][];
    sources: ("listed" | "interp" | "missing")[];
  }>(
    async () => adaptSurface(await fetchVolSurface()),
    VOL_WARN_MS,
  );
  const livePca = useFetch<PcaData>(
    async () => {
      // /signals/pca/model (variance_explained eigen bars) is no longer fetched:
      // the mode-stability / eigengap panel was dropped from the Signal tab, so
      // nothing rendered consumes `pca.model`. The cards (`pca.pcs`) only need
      // state + history; eigen meta falls back to the state's top-3 variance.
      const [state, h1, h2, h3] = await Promise.all([
        fetchPcaState(),
        fetchPcaHistory(1),
        fetchPcaHistory(2),
        fetchPcaHistory(3),
      ]);
      return adaptPca(state, null, [h1, h2, h3]);
    },
    VOL_WARN_MS,
  );
  const liveSystem = useFetch<SystemData>(
    async () => {
      // /dev/engines is auth-gated in prod → tolerate failure, compose from health.
      const [health, dev] = await Promise.allSettled([fetchHealthExtended(), fetchDevEngines()]);
      if (health.status !== "fulfilled") throw new Error("health/extended unavailable");
      return adaptSystem(health.value, dev.status === "fulfilled" ? dev.value : null);
    },
    SYSTEM_WARN_MS,
    true,
    SYSTEM_POLL_MS,
  );
  const liveConfig = useFetch<ConfigData>(
    async () => {
      const [current, history] = await Promise.all([fetchConfig(), fetchConfigHistory()]);
      return adaptConfig(current, history);
    },
    CONFIG_WARN_MS,
  );
  const liveTrade = useFetch<TradeData>(
    async () => {
      // Positions are the primary signal — they drive the domain freshness (and
      // the Open-positions pipeline). The four secondary reads degrade on their
      // own (caps/cash/events/book): a single one 404-ing in read-only mode must
      // NOT take the whole trade domain "missing" and red-out the pipeline.
      const pos = await fetchBookPositions();
      const [lim, evts, book, cash] = await Promise.all([
        fetchTradeLimits().catch(() => null),
        fetchRegimeEvents().catch(() => null),
        fetchTradeBook().catch(() => null),
        fetchPortfolioCash().catch(() => null),
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
    true,
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
        fetchBookPositions(),
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
    true,
    PORTFOLIO_POLL_MS,
  );

  const liveRisk = useFetch<VarData>(
    async () => {
      const [v, rpt] = await Promise.all([fetchPortfolioVar(), fetchRiskPerTenor()]);
      return { ...adaptVar(v), perTenor: adaptRiskPerTenor(rpt) };
    },
    PORTFOLIO_WARN_MS,
    true,
    PORTFOLIO_POLL_MS,
  );

  // Re-fetch surface + term + pca on each vol-engine cycle push (~3 min).
  const vol = useVolStream();
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

  // RT.2/RT.3 — the risk-engine cycle (~2s) is the realtime heartbeat: its raw
  // greeks payload isn't USD-scaled, so we use it only to invalidate
  // trade+portfolio -> re-fetch the USD-scaled REST snapshot (greeks + positions
  // mtm) = correct numbers, updated live.
  const riskBeat = useRiskStream();
  const reloadTrade = liveTrade.reload;
  const reloadPortfolio = livePortfolio.reload;
  useEffect(() => {
    if (riskBeat.asOf !== null) {
      reloadTrade();
      reloadPortfolio();
    }
  }, [riskBeat.asOf, reloadTrade, reloadPortfolio]);

  // Derived `surface` object — memoized on the live slice so its identity stays
  // stable when the surface data is unchanged (memo on the Signal panels is
  // otherwise defeated by a fresh object every render).
  const surface = useMemo<Fresh<SurfaceData>>(
    () => ({
      status: liveSurface.status,
      asOf: liveSurface.asOf,
      ageMs: liveSurface.ageMs,
      data: liveSurface.data
        ? {
            ivSurface: liveSurface.data.ivSurface,
            ivZ: liveSurface.data.ivZ,
            tenors: liveSurface.data.tenors,
            deltas: DELTA_LABELS,
            sources: liveSurface.data.sources,
          }
        : null,
    }),
    [liveSurface.status, liveSurface.asOf, liveSurface.ageMs, liveSurface.data],
  );

  // Memoize the desk-data value on the actual `Fresh` slices so the context
  // object identity is stable across renders that don't change any slice. This
  // is what keeps `useDeskData()` consumers from re-rendering on every tick.
  const value = useMemo<DeskData>(
    () => ({
      termStructure: liveTerm,
      surface,
      pca: livePca,
      system: liveSystem,
      config: liveConfig,
      trade: liveTrade,
      portfolio: livePortfolio,
      risk: liveRisk,
    }),
    [liveTerm, surface, livePca, liveSystem, liveConfig, liveTrade, livePortfolio, liveRisk],
  );
  return (
    <DeskDataContext.Provider value={value}>
      <TicksProvider>{children}</TicksProvider>
    </DeskDataContext.Provider>
  );
}

/**
 * High-frequency tick provider, kept separate from `DataProvider`'s value so the
 * ~1 Hz spot stream only re-renders the components that subscribe via
 * `useTicks()` — not every `useDeskData()` consumer (RT.1).
 */
function TicksProvider({ children }: { children: ReactNode }): JSX.Element {
  const ticks: Fresh<TickMsg> = useTicks();
  return <TicksContext.Provider value={ticks}>{children}</TicksContext.Provider>;
}
