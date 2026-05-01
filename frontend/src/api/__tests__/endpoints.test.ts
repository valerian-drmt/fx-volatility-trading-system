import { describe, expect, it } from "vitest";
import { fetchHealth, fetchVolSurface, postPrice, ApiError } from "../endpoints";
import { server } from "../../tests/mocks/handlers";
import { http, HttpResponse } from "msw";

describe("endpoints", () => {
  it("fetchHealth hits /api/v1/health and returns the JSON body", async () => {
    await expect(fetchHealth()).resolves.toEqual({ status: "OK" });
  });

  it("fetchVolSurface forwards the symbol query param", async () => {
    const res = await fetchVolSurface("GBPUSD");
    expect(res).toMatchObject({ symbol: "GBPUSD" });
  });

  it("postPrice sends a JSON body and gets back the mocked price", async () => {
    const res = await postPrice({
      spot: 1.08,
      strike: 1.08,
      time_to_expiry: 30 / 365,
      volatility: 0.075,
      risk_free_rate: 0.04,
      dividend_yield: 0,
      option_type: "CALL",
    } as never);
    expect(res).toMatchObject({ price: 1.08 * 0.075 });
  });

  it("raises ApiError on a non-2xx response", async () => {
    server.use(
      http.get("*/api/v1/health", () =>
        HttpResponse.json({ detail: "nope" }, { status: 503 }),
      ),
    );
    await expect(fetchHealth()).rejects.toBeInstanceOf(ApiError);
  });
});
