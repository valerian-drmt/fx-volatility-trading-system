import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "../../../store/authStore";
import { LoginModal } from "../LoginModal";

afterEach(() => useAuthStore.setState({ authenticated: false, ready: false, error: null }));

describe("LoginModal", () => {
  it("submits credentials and closes on success", async () => {
    const onClose = vi.fn();
    render(<LoginModal onClose={onClose} />);
    fireEvent.change(screen.getByLabelText(/password/i), { target: { value: "pw" } });
    fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(useAuthStore.getState().authenticated).toBe(true);
  });

  it("closes on backdrop click without authenticating", () => {
    const onClose = vi.fn();
    render(<LoginModal onClose={onClose} />);
    fireEvent.click(screen.getByTestId("login-overlay"));
    expect(onClose).toHaveBeenCalled();
    expect(useAuthStore.getState().authenticated).toBe(false);
  });
});
