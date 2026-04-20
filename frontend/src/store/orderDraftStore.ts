import { create } from "zustand";

export type OrderSide = "BUY" | "SELL";
export type OrderType = "CALL" | "PUT" | "FUT";

export interface OrderDraftState {
  side: OrderSide;
  optionType: OrderType;
  quantity: number;
  strike: number | null;
  tenor: string | null;
  limitPrice: number | null;
  setField: <K extends keyof Omit<OrderDraftState, "setField" | "reset" | "isValid">>(
    key: K,
    value: OrderDraftState[K],
  ) => void;
  reset: () => void;
  isValid: () => boolean;
}

const initial = {
  side: "BUY" as OrderSide,
  optionType: "CALL" as OrderType,
  quantity: 1,
  strike: null as number | null,
  tenor: null as string | null,
  limitPrice: null as number | null,
};

export const useOrderDraftStore = create<OrderDraftState>((set, get) => ({
  ...initial,
  setField: (key, value) => set({ [key]: value } as Partial<OrderDraftState>),
  reset: () => set(initial),
  isValid: () => {
    const s = get();
    if (s.quantity <= 0) return false;
    if (s.optionType === "FUT") return s.limitPrice !== null;
    return s.strike !== null && s.tenor !== null;
  },
}));
