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
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchConfig,
  fetchConfigHistory,
  fetchDevEngines,
  fetchHealthExtended,
  fetchOpenPositions,
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
import { type FetchResult, useFetch } from "../../hooks/useFetch";
import {
  type AccountSnapshotMsg,
  type PositionsChangedMsg,
  type TickMsg,
  useAccountStream,
  usePositionsStream,
  useRiskStream,
  useTicks,
  useVolStream,
} from "../../hooks/streams";
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
  type VolSource,
} from "./deskData";
import { type Fresh } from "./freshness";
import { adaptConfig } from "./live/config";
import { patchAccountRaw, patchPositions } from "./live/patch";
import { beaconPayload, postBeacon, shouldSample } from "../../utils/beacon";
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
// Positions are PUSHED by the execution engine (#366); this poll is the
// reconciliation, not the feed (hot/cold T3). Keep the warn window above it.
const TRADE_POLL_MS = 60_000;
const TRADE_WARN_MS = 90_000;
// Pushes received in this window before a REST slice landed are kept on top
// of it: the fetch may have read the DB before the engine's commit.
const OVERLAP_MS = 3_000;
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
    asOf?: number | null;
    source?: VolSource;
  }>(
    async () => {
      const raw = await fetchVolSurface();
      // The server's own stamp (vol-engine cycle time): the grid's age is the
      // age of the last complete grid, not of the last successful GET (#369).
      // Contract since #328: as_of + source + stale; timestamp is the legacy
      // name of as_of.
      const r = raw as { as_of?: string | null; timestamp?: string | null; source?: string | null };
      const ts = r.as_of ?? r.timestamp;
      const asOf = ts ? Date.parse(ts) : NaN;
      return {
        ...adaptSurface(raw),
        asOf: Number.isFinite(asOf) ? asOf : null,
        source: (r.source ?? null) as VolSource,
      };
    },
    VOL_WARN_MS,
    true,
    0,
    (d) => d.asOf ?? null,
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
      const pos = await fetchOpenPositions();
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
      // Account snapshot + positions are the primary signal (they feed the
      // Cash & margin tables on Portfolio / Trade / Dashboard). The four
      // analytics reads degrade on their own: one of them failing (e.g.
      // /portfolio/pnl-attribution hitting the DB statement timeout, prod
      // 2026-11-05) must NOT blank the account panel and silently drop the
      // Trade tab onto the trade-book bootstrap mock ($100k / zero margin).
      const [acct, pos] = await Promise.all([fetchPortfolioAccount(), fetchOpenPositions()]);
      const [vega, stats, daily, attrib] = await Promise.all([
        fetchVegaPerTenor().catch(() => null),
        fetchPortfolioStats().catch(() => null),
        fetchPortfolioDailyPnl().catch(() => null),
        fetchPnlAttribution().catch(() => null),
      ]);
      const positions = adaptPositions(pos, Date.now());
      return {
        account: adaptPortfolioAccountSnap(acct),
        accountRaw: acct,
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
      // VaR is the primary signal of the risk domain; the per-tenor read degrades
      // on its own (#369, T5): a 500 on /portfolio/risk-per-tenor must not blank
      // the VaR card nor flip the Risk tab to "missing".
      const v = await fetchPortfolioVar();
      const rpt = await fetchRiskPerTenor().catch(() => null);
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

  // Push channels (#367): patch the REST slices in place from the execution
  // engine's messages. The overlay holds the pushes received since the slice
  // last landed (plus a short overlap); each REST success resets it, so the
  // 60 s poll stays the reconciliation and the pushes the feed.
  const posStream = usePositionsStream();
  const acctStream = useAccountStream();
  const [posOverlay, setPosOverlay] = useState<{ msg: PositionsChangedMsg; at: number }[]>([]);
  // Latency beacon (#370): remember the last push's stamps; the effect below
  // runs after the patched slice has rendered and reports 1 push in 10.
  const pendingBeacon = useRef<{ publishedAt: number | null; receivedAt: number } | null>(null);
  useEffect(() => {
    if (posStream.data && posStream.asOf !== null) {
      const entry = { msg: posStream.data, at: posStream.asOf };
      setPosOverlay((prev) => [...prev.slice(-49), entry]);
      if (shouldSample(Math.random())) {
        const published = Date.parse(posStream.data.timestamp);
        pendingBeacon.current = { publishedAt: Number.isNaN(published) ? null : published, receivedAt: posStream.asOf };
      }
    }
  }, [posStream.data, posStream.asOf]);
  useEffect(() => {
    const landed = liveTrade.asOf;
    if (landed === null) return;
    setPosOverlay((prev) => {
      const kept = prev.filter((e) => e.at > landed - OVERLAP_MS);
      return kept.length === prev.length ? prev : kept;
    });
  }, [liveTrade.asOf]);
  const trade = useMemo<FetchResult<TradeData>>(() => {
    const d = liveTrade.data;
    if (!d || posOverlay.length === 0) return liveTrade;
    const now = Date.now();
    const positions = posOverlay.reduce((acc, e) => patchPositions(acc, e.msg, now), d.positions);
    if (positions === d.positions) return liveTrade;
    return { ...liveTrade, data: { ...d, positions, greeks: deriveNetGreeks(positions) } };
  }, [liveTrade, posOverlay]);

  useEffect(() => {
    const p = pendingBeacon.current;
    if (!p) return;
    pendingBeacon.current = null;
    const payload = beaconPayload("positions", p.publishedAt, p.receivedAt, Date.now());
    if (payload) postBeacon(payload);
  }, [trade]);

  const [acctMsg, setAcctMsg] = useState<AccountSnapshotMsg | null>(null);
  useEffect(() => {
    if (acctStream.data) setAcctMsg(acctStream.data);
  }, [acctStream.data]);
  const portfolio = useMemo<FetchResult<PortfolioData>>(() => {
    const d = livePortfolio.data;
    if (!d || !acctMsg) return livePortfolio;
    const raw = patchAccountRaw(d.accountRaw, acctMsg);
    if (raw === d.accountRaw) return livePortfolio;
    return { ...livePortfolio, data: { ...d, accountRaw: raw, account: adaptPortfolioAccountSnap(raw) } };
  }, [livePortfolio, acctMsg]);
  const streams = useMemo(
    () => ({ positions: posStream.wsStatus, account: acctStream.wsStatus, positionsAsOf: posStream.asOf }),
    [posStream.wsStatus, acctStream.wsStatus, posStream.asOf],
  );
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
            source: liveSurface.data.source ?? null,
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
      trade,
      reloadTrade,
      portfolio,
      risk: liveRisk,
      streams,
    }),
    [liveTerm, surface, livePca, liveSystem, liveConfig, trade, reloadTrade, portfolio, liveRisk, streams],
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
  // Fluidity beacon on the one stream that always flows (#374 follow-up): the
  // positions channel alone gave the host-split decision zero samples. One
  // render in ten reports publisher stamp -> screen and receive -> screen.
  useEffect(() => {
    const msg = ticks.data;
    if (!msg || !shouldSample(Math.random())) return;
    const published = msg.timestamp ? Date.parse(msg.timestamp) : NaN;
    const payload = beaconPayload("ticks", Number.isFinite(published) ? published : null, ticks.asOf, Date.now());
    if (payload) postBeacon(payload);
  }, [ticks.data, ticks.asOf]);
  return <TicksContext.Provider value={ticks}>{children}</TicksContext.Provider>;
}
