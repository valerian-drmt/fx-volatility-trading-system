import { render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { server } from "../../../tests/mocks/handlers";
import { toSemver } from "../../../utils/version";
import { BuildStamp } from "../BuildStamp";

describe("BuildStamp (#353)", () => {
  it("maps pyproject's PEP 440 version to SemVer", () => {
    expect(toSemver("1.0.0rc1")).toBe("1.0.0-rc.1");
    expect(toSemver("1.2.0b3")).toBe("1.2.0-beta.3");
    expect(toSemver("1.0.0")).toBe("1.0.0");
  });

  it("shows the version and short commit /health reports", async () => {
    server.use(http.get("*/api/v1/health", () => HttpResponse.json({ status: "OK", version: "1.0.0rc1", git_sha: "6a6dece3c89930bdfdbada54d3d505d420fa16b4" })));
    render(<BuildStamp />);
    await waitFor(() => expect(screen.getByTestId("build-stamp")).toHaveTextContent("v1.0.0-rc.1 · 6a6dece"));
  });
});
