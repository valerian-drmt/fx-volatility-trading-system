/**
 * VOLDESK — user-facing trading desk shell.
 *
 * Ported from the standalone prototype (`frontend/Option Trading System/`,
 * `js/app.jsx`) into the Vite/TS app. Lot 1 = shell only: Topbar + left Rail
 * (incl. the Dev tab) + hash routing. The five business views land in later
 * lots; for now each route renders a <ComingSoon> placeholder.
 *
 * Routing model:
 *   - internal views route by hash (#trade, #signals…) — cheap, no reload.
 *   - the "Dev" rail item is a FULL-PAGE nav to `${BASE_URL}dev` (the dev
 *     console is a separate path-routed root in main.tsx). Base-aware via
 *     import.meta.env.BASE_URL so it survives the deploy subpath.
 */
import { useEffect, useRef, useState, type ReactNode } from "react";
import "./voldesk.css";
import { useAuthStore } from "../store/authStore";
import { LoginModal } from "./auth/LoginModal";
import { useDeskData } from "./data/deskData";
import { useTweaks } from "./useTweaks";
import { DashboardView } from "./views/DashboardView";
import { TradeView } from "./views/TradeView";
import { SignalsView } from "./views/SignalsView";
import { RiskView } from "./views/RiskView";
import { PortfolioView } from "./views/PortfolioView";
import { SystemView } from "./views/SystemView";
import { SettingsView } from "./views/SettingsView";

const ICONS: Record<string, ReactNode> = {
  dashboard: (
    <g>
      <rect x="2" y="2" width="6" height="6" rx="1" />
      <rect x="10" y="2" width="6" height="6" rx="1" />
      <rect x="2" y="10" width="6" height="6" rx="1" />
      <rect x="10" y="10" width="6" height="6" rx="1" />
    </g>
  ),
  trade: (
    <g>
      <line x1="5" y1="2" x2="5" y2="16" />
      <rect x="3" y="5" width="4" height="7" />
      <line x1="13" y1="3" x2="13" y2="15" />
      <rect x="11" y="7" width="4" height="6" />
    </g>
  ),
  signals: (
    <g>
      <polyline points="2,2 2,16 16,16" fill="none" />
      <rect x="4" y="10" width="2.4" height="4" />
      <rect x="7.5" y="6" width="2.4" height="8" />
      <rect x="11" y="8" width="2.4" height="6" />
    </g>
  ),
  risk: (
    <g>
      <rect x="2" y="2" width="14" height="14" rx="1" fill="none" />
      <line x1="2" y1="7" x2="16" y2="7" />
      <line x1="2" y1="11" x2="16" y2="11" />
      <line x1="7" y1="2" x2="7" y2="16" />
      <line x1="11" y1="2" x2="11" y2="16" />
    </g>
  ),
  portfolio: (
    <g>
      <circle cx="9" cy="9" r="7" fill="none" />
      <path d="M9 9 L9 2 A7 7 0 0 1 15 12 Z" />
    </g>
  ),
  system: (
    <g>
      <rect x="2" y="2" width="14" height="3.5" rx="1" />
      <rect x="2" y="7.5" width="14" height="3.5" rx="1" />
      <rect x="2" y="13" width="14" height="3.5" rx="1" />
    </g>
  ),
  settings: (
    <g>
      <line x1="2" y1="5" x2="16" y2="5" />
      <circle cx="12" cy="5" r="2" fill="var(--bg)" />
      <line x1="2" y1="13" x2="16" y2="13" />
      <circle cx="6" cy="13" r="2" fill="var(--bg)" />
    </g>
  ),
};

interface NavItem {
  id: string;
  label: string;
}

const NAV: NavItem[] = [
  { id: "dashboard", label: "Dashboard" },
  { id: "trade", label: "Trade" },
  { id: "signals", label: "Signal" },
  { id: "risk", label: "Risk" },
  { id: "portfolio", label: "Portfolio" },
];

const TITLES: Record<string, [string, string]> = {
  dashboard: ["Dashboard", "Command center"],
  trade: ["Trade", ""],
  signals: ["Signal", "IV surface · PCA modes · fair vol gate"],
  risk: ["Risk", ""],
  portfolio: ["Portfolio", "Account · equity · attribution"],
  system: ["System", "Infrastructure & diagnostics"],
  settings: ["Settings", "Versioned configuration"],
};

interface TickerView {
  mid: number | null;
  bid: number | null;
  ask: number | null;
  dir: number;
  status: "live" | "stale" | "missing";
}

function Topbar({
  ticker,
  clock,
  authed,
  onAuthClick,
}: {
  ticker: TickerView;
  clock: string;
  authed: boolean;
  onAuthClick: () => void;
}): JSX.Element {
  const { mid, bid, ask, dir, status } = ticker;
  const has = mid !== null;
  const arrow = dir >= 0 ? "▲" : "▼";
  const dotColor = status === "live" ? "var(--pos)" : status === "stale" ? "var(--warn)" : "var(--neg)";
  const stateLabel = status === "live" ? "Market open" : status === "stale" ? "feed stale" : "no feed";
  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark">◤</span> VOLDESK <span className="brand-sub mono">FX VOL</span>
      </div>
      <div className="pair-sel">
        <select defaultValue="EURUSD">
          <option>EURUSD</option>
        </select>
      </div>
      <div className="ticker">
        <span className={"ticker-mid mono " + (dir >= 0 ? "pos" : "neg")}>
          {has ? mid.toFixed(5) : "—"} {has ? arrow : ""}
        </span>
        <span className="ticker-ba mono dim">
          {bid !== null && ask !== null ? `${bid.toFixed(5)} / ${ask.toFixed(5)}` : "— / —"}
        </span>
      </div>
      <div className="tb-spacer" />
      <span className={"tb-badge " + (status === "live" ? "open" : "")}>
        <span className="status-dot" style={{ background: dotColor }} />
        {stateLabel}
      </span>
      <span className="tb-badge paper">PAPER</span>
      <button
        className={"tb-badge auth" + (authed ? " in" : "")}
        onClick={onAuthClick}
        title={authed ? "Sign out" : "Sign in"}
        data-testid="auth-btn"
      >
        {authed ? "● Trader" : "Sign in"}
      </button>
      <span className="tb-clock mono">
        {clock} <span className="dim">UTC+1</span>
      </span>
    </header>
  );
}

function NavIcon({ id, fill = "currentColor" }: { id: string; fill?: string }): JSX.Element {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" stroke="currentColor" strokeWidth="1.5" fill={fill}>
      {ICONS[id]}
    </svg>
  );
}

function Rail({
  route,
  go,
  labels,
}: {
  route: string;
  go: (r: string) => void;
  labels: boolean;
}): JSX.Element {
  // Dev console lives at a separate path-routed root (main.tsx). Full-page nav,
  // base-aware so it stays correct under the deploy subpath.
  const openDev = (): void => {
    window.location.href = `${import.meta.env.BASE_URL}dev`;
  };
  return (
    <nav className={"rail " + (labels ? "" : "rail-icons")}>
      {NAV.map((n) => (
        <button
          key={n.id}
          className={"rail-item " + (route === n.id ? "on" : "")}
          onClick={() => go(n.id)}
          title={n.label}
        >
          <NavIcon id={n.id} />
          {labels && <span>{n.label}</span>}
          {route === n.id && <span className="rail-active" />}
        </button>
      ))}
      <div className="rail-sep" />
      <button
        className={"rail-item " + (route === "settings" ? "on" : "")}
        onClick={() => go("settings")}
        title="Settings"
      >
        <NavIcon id="settings" fill="none" />
        {labels && <span>Settings</span>}
      </button>
      <button className="rail-item" onClick={openDev} title="Dev console">
        <NavIcon id="system" />
        {labels && <span>Dev</span>}
      </button>
    </nav>
  );
}

export default function VoldeskApp(): JSX.Element {
  const [t, setTweak] = useTweaks();
  const [route, setRoute] = useState<string>(() => location.hash.slice(1) || "dashboard");
  const [clock, setClock] = useState<string>("");
  // Live EURUSD ticks via the shared desk domain (/ws/ticks, one WS connection).
  const { ticks: tick } = useDeskData();
  const prevMidRef = useRef<number | null>(null);
  // setTweak is wired for the (not-yet-ported) Settings/Tweaks UI.
  void setTweak;

  // Auth — probe /me once on mount; the topbar control toggles login/logout.
  const authenticated = useAuthStore((s) => s.authenticated);
  const refreshAuth = useAuthStore((s) => s.refresh);
  const logout = useAuthStore((s) => s.logout);
  const [loginOpen, setLoginOpen] = useState(false);
  useEffect(() => {
    void refreshAuth();
  }, [refreshAuth]);
  const onAuthClick = (): void => {
    if (authenticated) void logout();
    else setLoginOpen(true);
  };

  const go = (r: string): void => {
    setRoute(r);
    location.hash = r;
  };

  // Clock — always ticking (1s), independent of the price feed.
  useEffect(() => {
    setClock(new Date().toLocaleTimeString("en-GB"));
    const id = setInterval(() => setClock(new Date().toLocaleTimeString("en-GB")), 1000);
    return () => clearInterval(id);
  }, []);

  // Track previous mid to colour the up/down arrow.
  const liveMid = tick.data?.mid ?? null;
  const prevMid = prevMidRef.current;
  useEffect(() => {
    if (liveMid !== null) prevMidRef.current = liveMid;
  }, [liveMid]);

  const ticker: TickerView = {
    mid: liveMid,
    bid: tick.data?.bid ?? null,
    ask: tick.data?.ask ?? null,
    dir: liveMid !== null && prevMid !== null && liveMid >= prevMid ? 1 : -1,
    status: tick.status,
  };

  useEffect(() => {
    const h = (): void => setRoute(location.hash.slice(1) || "dashboard");
    window.addEventListener("hashchange", h);
    return () => window.removeEventListener("hashchange", h);
  }, []);

  useEffect(() => {
    document.documentElement.style.setProperty("--accent", t.accent);
  }, [t.accent]);

  const [title, sub] = TITLES[route] ?? (["Dashboard", "Command center"] as [string, string]);
  const tweaks = { density: t.density, showGreeks: t.showGreeks };

  return (
    <div className={"shell density-" + t.density}>
      <Topbar ticker={ticker} clock={clock} authed={authenticated} onAuthClick={onAuthClick} />
      <div className="body">
        <Rail route={route} go={go} labels={t.railLabels} />
        <main className="content">
          <div className="page-head">
            <div>
              <h1>{title}</h1>
              <span className="page-sub">{sub}</span>
            </div>
          </div>
          <div className="page-body">
            {route === "dashboard" && <DashboardView go={go} />}
            {route === "trade" && <TradeView tweaks={tweaks} />}
            {route === "signals" && <SignalsView />}
            {route === "risk" && <RiskView />}
            {route === "portfolio" && <PortfolioView />}
            {route === "system" && <SystemView />}
            {route === "settings" && <SettingsView />}
          </div>
        </main>
      </div>
      {loginOpen && <LoginModal onClose={() => setLoginOpen(false)} />}
    </div>
  );
}
