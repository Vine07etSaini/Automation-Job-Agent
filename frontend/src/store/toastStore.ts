import { create } from "zustand";

interface Toast {
  id: number;
  kind: "info" | "error";
  text: string;
}

interface ToastState {
  toasts: Toast[];
  push: (text: string, kind?: Toast["kind"]) => void;
  dismiss: (id: number) => void;
}

let nextId = 1;

export const useToastStore = create<ToastState>()((set, get) => ({
  toasts: [],
  push: (text, kind = "info") => {
    const id = nextId++;
    set((s) => ({ toasts: [...s.toasts, { id, kind, text }] }));
    setTimeout(() => get().dismiss(id), 5000);
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));
