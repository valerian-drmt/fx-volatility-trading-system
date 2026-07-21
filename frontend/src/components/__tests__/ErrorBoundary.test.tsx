/**
 * Regression cover for the black-screen failure.
 *
 * A logged-out /dev returned 401 on every dev endpoint; the raw `apiFetch`
 * resolved anyway, the caller decoded the error payload, read `undefined` where
 * an array was expected, and the next render threw. With no boundary in the
 * tree React unmounted everything, leaving an empty `#root` over the near-black
 * `--bg` — a black page with nothing to go on.
 *
 * Two guarantees are pinned here: `apiFetchJson` rejects on a non-2xx instead of
 * handing back a decoded error body, and a throw stays contained in a visible
 * fallback.
 */
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ErrorBoundary } from "../ErrorBoundary";
import { ApiError, apiFetchJson } from "../../api/client";

function Boom({ error }: { error: Error }): JSX.Element {
  throw error;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("apiFetchJson", () => {
  it("rejects with ApiError on 401 instead of returning the error body", async () => {
    // A fresh Response per call — a body can only be read once.
    vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      new Response(JSON.stringify({ detail: "authentication required" }), { status: 401 }),
    );

    const err = await apiFetchJson("/api/v1/dev/tables").catch((e: unknown) => e);

    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(401);
    expect((err as ApiError).body).toEqual({ detail: "authentication required" });
  });

  it("returns the decoded body on success", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ tables: [{ name: "trades" }] }), { status: 200 }),
    );

    await expect(apiFetchJson<{ tables: { name: string }[] }>("/api/v1/dev/tables")).resolves.toEqual({
      tables: [{ name: "trades" }],
    });
  });
});

describe("ErrorBoundary", () => {
  it("renders children when nothing throws", () => {
    render(
      <ErrorBoundary>
        <span>alive</span>
      </ErrorBoundary>,
    );
    expect(screen.getByText("alive")).toBeInTheDocument();
  });

  it("contains a render throw instead of unmounting the tree", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);

    render(
      <ErrorBoundary label="DB Explorer">
        <Boom error={new TypeError("Cannot read properties of undefined (reading 'find')")} />
      </ErrorBoundary>,
    );

    expect(screen.getByText(/Something broke while rendering/)).toBeInTheDocument();
    expect(screen.getByText(/DB Explorer/)).toBeInTheDocument();
    expect(screen.getByText(/reading 'find'/)).toBeInTheDocument();
  });

  it("gives a 401 an actionable message rather than a stack trace", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);

    render(
      <ErrorBoundary>
        <Boom error={new ApiError(401, "/api/v1/dev/tables", { detail: "authentication required" })} />
      </ErrorBoundary>,
    );

    expect(screen.getByText(/401 — authentication required/)).toBeInTheDocument();
    expect(screen.getByText(/Log in on the\s+desk/)).toBeInTheDocument();
  });
});