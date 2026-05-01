import { beforeEach, describe, expect, it } from "vitest";
import { useSelectionStore } from "../selectionStore";

describe("selectionStore", () => {
  beforeEach(() => useSelectionStore.getState().reset());

  it("defaults to EURUSD 1M and no strike", () => {
    const s = useSelectionStore.getState();
    expect(s.symbol).toBe("EURUSD");
    expect(s.tenor).toBe("1M");
    expect(s.strike).toBeNull();
  });

  it("persists setters independently", () => {
    const { setSymbol, setTenor, setStrike } = useSelectionStore.getState();
    setSymbol("GBPUSD");
    setTenor("3M");
    setStrike(1.26);
    const s = useSelectionStore.getState();
    expect(s).toMatchObject({ symbol: "GBPUSD", tenor: "3M", strike: 1.26 });
  });

  it("reset restores defaults", () => {
    useSelectionStore.getState().setSymbol("USDJPY");
    useSelectionStore.getState().reset();
    expect(useSelectionStore.getState().symbol).toBe("EURUSD");
  });
});
