import { beforeEach, describe, expect, it } from "vitest";
import { useOrderDraftStore } from "../orderDraftStore";

describe("orderDraftStore", () => {
  beforeEach(() => useOrderDraftStore.getState().reset());

  it("defaults to BUY CALL qty=1 with empty strike/tenor/price", () => {
    expect(useOrderDraftStore.getState()).toMatchObject({
      side: "BUY",
      optionType: "CALL",
      quantity: 1,
      strike: null,
      tenor: null,
      limitPrice: null,
    });
  });

  it("setField updates a single key without touching others", () => {
    useOrderDraftStore.getState().setField("strike", 1.08);
    useOrderDraftStore.getState().setField("tenor", "1M");
    const s = useOrderDraftStore.getState();
    expect(s.strike).toBe(1.08);
    expect(s.tenor).toBe("1M");
    expect(s.quantity).toBe(1);
  });

  it("is invalid for option without strike/tenor", () => {
    expect(useOrderDraftStore.getState().isValid()).toBe(false);
    useOrderDraftStore.getState().setField("strike", 1.08);
    expect(useOrderDraftStore.getState().isValid()).toBe(false);
    useOrderDraftStore.getState().setField("tenor", "1M");
    expect(useOrderDraftStore.getState().isValid()).toBe(true);
  });

  it("FUT order requires a limit price, not a strike/tenor", () => {
    const { setField } = useOrderDraftStore.getState();
    setField("optionType", "FUT");
    expect(useOrderDraftStore.getState().isValid()).toBe(false);
    setField("limitPrice", 1.0852);
    expect(useOrderDraftStore.getState().isValid()).toBe(true);
  });

  it("rejects zero or negative quantity", () => {
    const { setField } = useOrderDraftStore.getState();
    setField("strike", 1.08);
    setField("tenor", "1M");
    setField("quantity", 0);
    expect(useOrderDraftStore.getState().isValid()).toBe(false);
  });

  it("reset clears every field back to defaults", () => {
    const { setField, reset } = useOrderDraftStore.getState();
    setField("side", "SELL");
    setField("strike", 1.08);
    reset();
    expect(useOrderDraftStore.getState()).toMatchObject({ side: "BUY", strike: null });
  });
});
