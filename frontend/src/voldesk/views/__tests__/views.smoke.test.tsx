/**
 * View smoke tests (#340, audit 07#14): every desk view mounts inside the real
 * DataProvider against an API that answers empty payloads, renders its
 * skeleton and survives the first fetch round. They pin the "no data yet"
 * path, the one a fresh deploy or a closed market shows, and give
 * `voldesk/views` a coverage floor instead of 0%.
 */
import { act, render } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { server } from "../../../tests/mocks/handlers";
import { DataProvider } from "../../data/provider";
import { TWEAK_DEFAULTS } from "../../useTweaks";
import { DashboardView } from "../DashboardView";
import { PortfolioView } from "../PortfolioView";
import { RiskView } from "../RiskView";
import { SettingsView } from "../SettingsView";
import { SignalsView } from "../SignalsView";
import { SystemView } from "../SystemView";
import { TradeView } from "../TradeView";

// Plotly needs a real canvas; the smoke test is about the view, not the chart.
vi.mock("../../../components/charts/PlotlyChart", () => ({
  PlotlyChart: () => <div data-testid="plotly-stub" />,
  default: () => <div data-testid="plotly-stub" />,
}));

class SilentWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) {}
  send(): void {}
  close(): void {
    this.readyState = 3;
  }
  addEventListener(): void {}
  removeEventListener(): void {}
}

beforeEach(() => {
  vi.stubGlobal("WebSocket", SilentWebSocket);
  server.use(
    http.get("*/api/v1/*", () => HttpResponse.json({})),
    http.get("*/ws/*", () => new HttpResponse(null, { status: 404 })),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const VIEWS: [string, () => ReactElement][] = [
  ["Dashboard", () => <DashboardView go={() => undefined} />],
  ["Trade", () => <TradeView tweaks={TWEAK_DEFAULTS} />],
  ["Signals", () => <SignalsView />],
  ["Risk", () => <RiskView />],
  ["Portfolio", () => <PortfolioView />],
  ["System", () => <SystemView />],
  ["Settings", () => <SettingsView />],
];

describe("desk views on an empty API", () => {
  it.each(VIEWS)("%s mounts and survives the first fetch round", async (_name, view) => {
    const errors: unknown[] = [];
    const onError = (e: ErrorEvent): void => {
      errors.push(e.error);
    };
    window.addEventListener("error", onError);
    try {
      const { container, unmount } = render(<DataProvider>{view()}</DataProvider>);
      await act(async () => {
        await new Promise((r) => setTimeout(r, 50));
      });
      expect(container.firstChild).not.toBeNull();
      expect(container.textContent?.length ?? 0).toBeGreaterThan(0);
      unmount();
    } finally {
      window.removeEventListener("error", onError);
    }
    expect(errors).toEqual([]);
  });
});
