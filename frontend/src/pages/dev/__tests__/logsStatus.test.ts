import { describe, expect, it } from "vitest";
import { classifyLogsError } from "../logsStatus";

describe("classifyLogsError (#378)", () => {
  it("the exact prod/local string becomes the Loki-down state with a hint", () => {
    const e = classifyLogsError(503, '{"detail":"loki unreachable : ConnectError"}');
    expect(e.kind).toBe("loki_down");
    expect(e.hint).toMatch(/--profile obs/);
    expect(e.hint).toMatch(/Stack overview/);
    expect(e.detail).toContain("ConnectError");
  });
  it("a Loki HTTP error from the proxy is also Loki-down", () => {
    expect(classifyLogsError(503, '{"detail":"loki returned 500"}').kind).toBe("loki_down");
  });
  it("parses the status out of a thrown message when none is given", () => {
    const e = classifyLogsError(null, 'Error: HTTP 503 : {"detail":"loki unreachable : ConnectError"}');
    expect(e.kind).toBe("loki_down");
  });
  it("401 is a login prompt, not a Loki problem", () => {
    expect(classifyLogsError(401, '{"detail":"authentication required"}').kind).toBe("auth");
    expect(classifyLogsError(null, "ApiError: 401 authentication required").kind).toBe("auth");
  });
  it("anything else keeps the status and a retry hint, and truncates long bodies", () => {
    const e = classifyLogsError(502, "x".repeat(500));
    expect(e.kind).toBe("other");
    expect(e.title).toBe("Log query failed (HTTP 502)");
    expect(e.detail.length).toBeLessThanOrEqual(201);
  });
});
