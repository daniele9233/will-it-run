import { create } from "zustand";

import { api } from "./api";
import { type Lang, resolveLang, storeLang } from "./i18n";
import type { HardwareResponse, LiveState, LMStudioStatus, Settings } from "./types";
import { applyZoom, clampZoom } from "./zoom";

export type PageId =
  | "home"
  | "hardware"
  | "models"
  | "teams"
  | "workflows"
  | "benchmarks"
  | "arena"
  | "downloads"
  | "images"
  | "chat"
  | "clone"
  | "sessions"
  | "brain"
  | "logs"
  | "guide"
  | "settings";

export interface Toast {
  id: number;
  tone: "ok" | "bad" | "info";
  title: string;
  body?: string;
}

interface AppState {
  page: PageId;
  params: Record<string, string>;
  navigate: (page: PageId, params?: Record<string, string>) => void;

  hardware: HardwareResponse | null;
  lmstudio: LMStudioStatus | null;
  settings: Settings | null;
  live: LiveState | null;
  paletteOpen: boolean;
  toasts: Toast[];

  setPalette: (open: boolean) => void;
  toast: (toast: Omit<Toast, "id">) => void;
  dismissToast: (id: number) => void;
  loadHardware: (refresh?: boolean) => Promise<void>;
  loadLMStudio: () => Promise<void>;
  loadSettings: () => Promise<void>;
  saveSettings: (patch: Record<string, unknown>) => Promise<void>;
  loadLive: () => Promise<void>;
  setZoom: (value: number) => void;
  setLanguage: (language: "system" | Lang) => Promise<void>;
}

const PAGE_KEY = "agentfit.page";

/** After a language switch the window reloads; come back to the page the user was on. */
function initialPage(): { page: PageId; params: Record<string, string> } {
  try {
    const saved = sessionStorage.getItem(PAGE_KEY);
    sessionStorage.removeItem(PAGE_KEY);
    if (saved) return JSON.parse(saved) as { page: PageId; params: Record<string, string> };
  } catch {
    // Storage unavailable: start on Home.
  }
  return { page: "home", params: {} };
}

let toastId = 0;

export const useApp = create<AppState>((set, get) => ({
  ...initialPage(),
  navigate: (page, params = {}) => set({ page, params, paletteOpen: false }),

  hardware: null,
  lmstudio: null,
  settings: null,
  live: null,
  paletteOpen: false,
  toasts: [],

  setPalette: (open) => set({ paletteOpen: open }),
  toast: (toast) => {
    const id = ++toastId;
    set({ toasts: [...get().toasts, { ...toast, id }] });
    window.setTimeout(() => get().dismissToast(id), toast.tone === "bad" ? 9000 : 5000);
  },
  dismissToast: (id) => set({ toasts: get().toasts.filter((t) => t.id !== id) }),

  loadHardware: async (refresh = false) => {
    set({ hardware: await api.get<HardwareResponse>(`/api/hardware${refresh ? "?refresh=true" : ""}`) });
  },
  loadLMStudio: async () => {
    set({ lmstudio: await api.get<LMStudioStatus>("/api/lmstudio") });
  },
  loadSettings: async () => {
    set({ settings: await api.get<Settings>("/api/settings") });
  },
  saveSettings: async (patch) => {
    set({ settings: await api.put<Settings>("/api/settings", patch) });
  },
  loadLive: async () => {
    try {
      set({ live: await api.get<LiveState>("/api/live") });
    } catch {
      // The status bar simply keeps its last reading.
    }
  },
  setZoom: (value) => {
    const zoom = clampZoom(value);
    const settings = get().settings;
    if (!settings || zoom === settings.ui.zoom) return;
    // Apply at once so repeated presses add up; the save follows.
    set({ settings: { ...settings, ui: { ...settings.ui, zoom } } });
    void get().saveSettings({ ui: { zoom } });
  },
  setLanguage: async (language) => {
    await get().saveSettings({ ui: { language } });
    if (storeLang(resolveLang(language))) {
      try {
        sessionStorage.setItem(PAGE_KEY, JSON.stringify({ page: get().page, params: get().params }));
      } catch {
        // Not essential.
      }
      window.location.reload();
    }
  },
}));

/** Apply theme, accent and zoom from settings to the document. */
export function applyAppearance(settings: Settings | null): void {
  const root = document.documentElement;
  void applyZoom(settings?.ui.zoom ?? 1);
  const theme = settings?.ui.theme ?? "dark";
  const resolved = theme === "system" ? (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark") : theme;
  root.dataset.theme = resolved;
  const accent = settings?.ui.accent;
  if (accent && /^#[0-9a-f]{6}$/i.test(accent)) {
    root.style.setProperty("--accent", accent);
    // Text on the accent must stay readable whatever color the user picks.
    root.style.setProperty("--accent-ink", relativeLuminance(accent) > 0.3 ? "#0b141c" : "#ffffff");
  } else {
    root.style.removeProperty("--accent");
    root.style.removeProperty("--accent-ink");
  }
}

/** WCAG relative luminance of a #rrggbb color. */
export function relativeLuminance(hex: string): number {
  const channel = (offset: number) => {
    const value = parseInt(hex.slice(offset, offset + 2), 16) / 255;
    return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5);
}
