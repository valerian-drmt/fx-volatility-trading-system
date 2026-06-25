import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it } from "vitest";

import { server } from "../../tests/mocks/handlers";
import { useAuthStore } from "../authStore";

const reset = (): void =>
  useAuthStore.setState({ authenticated: false, ready: false, error: null });

describe("authStore", () => {
  afterEach(reset);

  it("refresh reflects /me (logged out by default)", async () => {
    await useAuthStore.getState().refresh();
    expect(useAuthStore.getState().authenticated).toBe(false);
    expect(useAuthStore.getState().ready).toBe(true);
  });

  it("login success sets authenticated", async () => {
    const ok = await useAuthStore.getState().login("trader", "pw");
    expect(ok).toBe(true);
    expect(useAuthStore.getState().authenticated).toBe(true);
    expect(useAuthStore.getState().error).toBeNull();
  });

  it("login failure (401) keeps logged out and sets error", async () => {
    server.use(
      http.post("*/api/v1/auth/login", () => new HttpResponse(null, { status: 401 })),
    );
    const ok = await useAuthStore.getState().login("trader", "bad");
    expect(ok).toBe(false);
    expect(useAuthStore.getState().authenticated).toBe(false);
    expect(useAuthStore.getState().error).toBe("invalid credentials");
  });

  it("logout clears authenticated", async () => {
    useAuthStore.setState({ authenticated: true });
    await useAuthStore.getState().logout();
    expect(useAuthStore.getState().authenticated).toBe(false);
  });
});
