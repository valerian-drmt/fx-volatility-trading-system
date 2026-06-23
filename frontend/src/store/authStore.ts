import { create } from "zustand";

import { fetchAuthMe, postLogin, postLogout } from "../api/endpoints";

/**
 * Single-trader auth state. The desk is public read-only; logging in sets an
 * httpOnly cookie that unlocks the write endpoints (orders, config, …).
 * `authenticated` drives both the topbar control and the write-action gate.
 */
export interface AuthState {
  authenticated: boolean;
  ready: boolean; // false until the initial /me probe resolves
  error: string | null;
  refresh: () => Promise<void>;
  login: (username: string, password: string) => Promise<boolean>;
  logout: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set) => ({
  authenticated: false,
  ready: false,
  error: null,
  refresh: async () => {
    try {
      const r = await fetchAuthMe();
      set({ authenticated: r.authenticated, ready: true });
    } catch {
      set({ authenticated: false, ready: true });
    }
  },
  login: async (username, password) => {
    try {
      const r = await postLogin({ username, password });
      set({ authenticated: r.authenticated, error: r.authenticated ? null : "invalid credentials" });
      return r.authenticated;
    } catch {
      set({ authenticated: false, error: "invalid credentials" });
      return false;
    }
  },
  logout: async () => {
    try {
      await postLogout();
    } catch {
      /* best-effort — clear local state regardless */
    }
    set({ authenticated: false, error: null });
  },
}));
