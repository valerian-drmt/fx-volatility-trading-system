import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useTicks } from "../useTicks";
import { useConnectionStore } from "../../store/connectionStore";

// Minimal WebSocket stub. Only the events the hook actually listens to.
class FakeWS {
  static instances: FakeWS[] = [];
  static OPEN = 1;
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  constructor(public url: string) {
    FakeWS.instances.push(this);
  }
  close(): void {
    this.readyState = 3;
    this.onclose?.();
  }
}

describe("useTicks", () => {
  beforeEach(() => {
    FakeWS.instances = [];
    useConnectionStore.getState().reset();
    vi.stubGlobal("WebSocket", FakeWS);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("opens a socket on /ws/ticks and parses incoming frames", () => {
    const { result } = renderHook(() => useTicks());
    expect(FakeWS.instances).toHaveLength(1);
    expect(FakeWS.instances[0]?.url).toContain("/ws/ticks");
    expect(result.current.last).toBeNull();

    const ws = FakeWS.instances[0]!;
    act(() => {
      ws.onopen?.();
      ws.onmessage?.({
        data: JSON.stringify({ symbol: "EURUSD", bid: 1.0849, ask: 1.0851, mid: 1.085 }),
      });
    });

    expect(result.current.last).toMatchObject({ symbol: "EURUSD", mid: 1.085 });
    expect(result.current.count).toBe(1);
    expect(useConnectionStore.getState().status).toBe("open");
  });

  it("drops malformed frames without crashing or updating state", () => {
    const { result } = renderHook(() => useTicks());
    const ws = FakeWS.instances[0]!;
    act(() => {
      ws.onopen?.();
      ws.onmessage?.({ data: "not json" });
    });
    expect(result.current.last).toBeNull();
    expect(result.current.count).toBe(0);
  });

  it("flips connectionStore to retry when the socket closes", () => {
    renderHook(() => useTicks());
    const ws = FakeWS.instances[0]!;
    act(() => {
      ws.onclose?.();
    });
    const s = useConnectionStore.getState();
    expect(s.status).toBe("retry");
    expect(s.retryCount).toBe(1);
  });
});
