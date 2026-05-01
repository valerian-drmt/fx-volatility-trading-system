import { create } from "zustand";

export interface SelectionState {
  symbol: string;
  tenor: string;
  strike: number | null;
  setSymbol: (symbol: string) => void;
  setTenor: (tenor: string) => void;
  setStrike: (strike: number | null) => void;
  reset: () => void;
}

const initial = { symbol: "EURUSD", tenor: "1M", strike: null as number | null };

export const useSelectionStore = create<SelectionState>((set) => ({
  ...initial,
  setSymbol: (symbol) => set({ symbol }),
  setTenor: (tenor) => set({ tenor }),
  setStrike: (strike) => set({ strike }),
  reset: () => set(initial),
}));
