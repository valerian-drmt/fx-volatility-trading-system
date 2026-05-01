import { create } from "zustand";

export type WsStatus = "idle" | "connecting" | "open" | "retry" | "closed";

export interface ConnectionState {
  status: WsStatus;
  retryCount: number;
  lastError: string | null;
  setStatus: (status: WsStatus) => void;
  noteRetry: (error?: string) => void;
  reset: () => void;
}

const initial = { status: "idle" as const, retryCount: 0, lastError: null };

export const useConnectionStore = create<ConnectionState>((set) => ({
  ...initial,
  setStatus: (status) =>
    set((s) => ({
      status,
      retryCount: status === "open" ? 0 : s.retryCount,
      lastError: status === "open" ? null : s.lastError,
    })),
  noteRetry: (error) =>
    set((s) => ({
      status: "retry",
      retryCount: s.retryCount + 1,
      lastError: error ?? s.lastError,
    })),
  reset: () => set(initial),
}));
