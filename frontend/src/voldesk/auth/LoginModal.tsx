import { useEffect, useRef, useState, type FormEvent } from "react";

import { useAuthStore } from "../../store/authStore";

/**
 * Minimal single-trader login. Posts credentials → backend sets the httpOnly
 * cookie → write endpoints unlock. Closes itself on success.
 */
export function LoginModal({ onClose }: { onClose: () => void }): JSX.Element {
  const login = useAuthStore((s) => s.login);
  const error = useAuthStore((s) => s.error);
  const [username, setUsername] = useState("trader");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const passwordRef = useRef<HTMLInputElement>(null);

  // Dialog behaviour (#349): focus lands on the password (the user is
  // prefilled), Escape closes, focus returns to whatever opened the dialog.
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    passwordRef.current?.focus();
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      opener?.focus?.();
    };
  }, [onClose]);

  const submit = async (e: FormEvent): Promise<void> => {
    e.preventDefault();
    setBusy(true);
    const ok = await login(username, password);
    setBusy(false);
    if (ok) onClose();
  };

  return (
    <div className="auth-overlay" data-testid="login-overlay" onClick={onClose}>
      <form
        className="auth-modal"
        data-testid="login-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="login-title"
        onClick={(e) => e.stopPropagation()}
        onSubmit={submit}
      >
        <h2 id="login-title">Sign in</h2>
        <p className="auth-sub">Write access (orders, config). Reads stay open.</p>
        <label>
          <span>User</span>
          <input
            name="username"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
        </label>
        <label>
          <span>Password</span>
          <input
            name="password"
            type="password"
            ref={passwordRef}
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error && (
          <div className="auth-error" role="alert">
            {error}
          </div>
        )}
        <div className="auth-actions">
          <button type="button" className="btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn-primary" disabled={busy}>
            {busy ? "…" : "Sign in"}
          </button>
        </div>
      </form>
    </div>
  );
}
