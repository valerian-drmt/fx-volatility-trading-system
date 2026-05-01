import { beforeEach, describe, expect, it } from "vitest";
import { useConnectionStore } from "../connectionStore";

describe("connectionStore", () => {
  beforeEach(() => useConnectionStore.getState().reset());

  it("starts idle with zero retries", () => {
    const s = useConnectionStore.getState();
    expect(s.status).toBe("idle");
    expect(s.retryCount).toBe(0);
    expect(s.lastError).toBeNull();
  });

  it("clears error and retry count when WS opens", () => {
    useConnectionStore.getState().noteRetry("boom");
    useConnectionStore.getState().setStatus("open");
    const s = useConnectionStore.getState();
    expect(s.status).toBe("open");
    expect(s.retryCount).toBe(0);
    expect(s.lastError).toBeNull();
  });

  it("increments retryCount on noteRetry and keeps previous error if none supplied", () => {
    const { noteRetry } = useConnectionStore.getState();
    noteRetry("first");
    noteRetry();
    const s = useConnectionStore.getState();
    expect(s.status).toBe("retry");
    expect(s.retryCount).toBe(2);
    expect(s.lastError).toBe("first");
  });
});
