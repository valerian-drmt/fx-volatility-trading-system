import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { ConnectionIndicator } from "../ConnectionIndicator";
import { useConnectionStore } from "../../../store/connectionStore";

describe("ConnectionIndicator", () => {
  beforeEach(() => useConnectionStore.getState().reset());

  it("reflects the current connection status via data-status", () => {
    useConnectionStore.getState().setStatus("open");
    render(<ConnectionIndicator />);
    expect(screen.getByTestId("conn-indicator")).toHaveAttribute("data-status", "open");
    expect(screen.getByText("open")).toBeInTheDocument();
  });

  it("shows the retry count when retrying", () => {
    useConnectionStore.getState().noteRetry("drop");
    useConnectionStore.getState().noteRetry();
    render(<ConnectionIndicator />);
    expect(screen.getByText(/retry · 2/)).toBeInTheDocument();
  });
});
