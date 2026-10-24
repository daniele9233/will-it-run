// Whole-interface zoom. Inside the desktop shell this is the webview's own zoom (the
// same thing Ctrl + does in a browser); in a plain browser it falls back to CSS zoom.

import { inTauri } from "./api";

export const ZOOM_MIN = 0.8;
export const ZOOM_MAX = 1.6;
export const ZOOM_STEP = 0.1;

export function clampZoom(value: number): number {
  if (!Number.isFinite(value)) return 1;
  return Math.round(Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, value)) * 100) / 100;
}

export async function applyZoom(factor: number): Promise<void> {
  const value = clampZoom(factor);
  if (inTauri) {
    try {
      const { getCurrentWebview } = await import("@tauri-apps/api/webview");
      await getCurrentWebview().setZoom(value);
      return;
    } catch {
      // Fall through to CSS zoom.
    }
  }
  document.documentElement.style.setProperty("zoom", value === 1 ? "" : String(value));
}
