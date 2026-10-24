// Typed client for the local Will It Run core.
//
// Inside the desktop shell, the port and bearer token come from the Rust side
// (`backend_info`). In a plain browser (UI development), they come from Vite env vars.

import { getLang } from "./i18n";
import type { BusEvent } from "./types";

interface Backend {
  url: string;
  token: string;
}

export class ApiError extends Error {
  status: number;
  kind: string | null;
  data: Record<string, unknown>;

  constructor(message: string, status: number, data: Record<string, unknown> = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.kind = typeof data.kind === "string" ? data.kind : null;
    this.data = data;
  }
}

export const inTauri = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

let backend: Promise<Backend> | null = null;

async function resolveBackend(): Promise<Backend> {
  if (inTauri) {
    const { invoke } = await import("@tauri-apps/api/core");
    // The core starts alongside the window; wait until it reports its port.
    for (let attempt = 0; attempt < 240; attempt++) {
      const info = await invoke<{ port: number; token: string; error: string | null }>("backend_info");
      if (info.error) throw new ApiError(info.error, 0);
      if (info.port > 0) return { url: `http://127.0.0.1:${info.port}`, token: info.token };
      await new Promise((r) => setTimeout(r, 250));
    }
    throw new ApiError("The Will It Run core did not start.", 0);
  }
  return {
    url: import.meta.env.VITE_AGENTFIT_API ?? "http://127.0.0.1:8765",
    token: import.meta.env.VITE_AGENTFIT_TOKEN ?? "",
  };
}

export function getBackend(): Promise<Backend> {
  backend ??= resolveBackend();
  return backend;
}

export async function waitForCore(timeoutMs = 60000): Promise<void> {
  const { url } = await getBackend();
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    try {
      const response = await fetch(`${url}/api/health`);
      if (response.ok) return;
    } catch {
      // not up yet
    }
    if (Date.now() > deadline) throw new ApiError("The Will It Run core is not responding.", 0);
    await new Promise((r) => setTimeout(r, 300));
  }
}

async function request<T>(method: string, path: string, body?: unknown, raw = false): Promise<T> {
  const { url, token } = await getBackend();
  // The core answers in the language the interface is showing.
  const headers: Record<string, string> = { "X-WillItRun-Lang": getLang() };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let response: Response;
  try {
    response = await fetch(`${url}${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError("Cannot reach the Will It Run core.", 0);
  }
  if (!response.ok) {
    let data: Record<string, unknown> = {};
    try {
      data = (await response.json()) as Record<string, unknown>;
    } catch {
      // non-JSON error body
    }
    const detail = typeof data.detail === "string" ? data.detail : `Request failed (${response.status})`;
    throw new ApiError(detail, response.status, data);
  }
  return (raw ? await response.text() : await response.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  text: (path: string) => request<string>("GET", path, undefined, true),
  post: <T>(path: string, body: unknown = {}) => request<T>("POST", path, body),
  put: <T>(path: string, body: unknown) => request<T>("PUT", path, body),
  del: <T>(path: string) => request<T>("DELETE", path),
};

export function query(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

type Listener = (event: BusEvent) => void;
const listeners = new Set<Listener>();
let stream: Promise<void> | null = null;

/** Open the event stream once. The promise is the guard: callers arriving before the first one finishes share it. */
function ensureStream(): Promise<void> {
  stream ??= getBackend().then(({ url, token }) => {
    const source = new EventSource(`${url}/api/events${query({ token })}`);
    source.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data as string) as BusEvent;
        for (const listener of listeners) listener(event);
      } catch {
        // ignore malformed frames
      }
    };
  });
  return stream;
}

/** Subscribe to the core's live event stream. Returns an unsubscribe function. */
export function onEvent(listener: Listener): () => void {
  listeners.add(listener);
  void ensureStream();
  return () => {
    listeners.delete(listener);
  };
}

export async function openExternal(url: string): Promise<void> {
  if (inTauri) {
    const { openUrl } = await import("@tauri-apps/plugin-opener");
    await openUrl(url);
  } else {
    window.open(url, "_blank", "noopener");
  }
}

export async function notify(title: string, body: string): Promise<void> {
  if (!inTauri) return;
  try {
    const { isPermissionGranted, requestPermission, sendNotification } = await import("@tauri-apps/plugin-notification");
    let granted = await isPermissionGranted();
    if (!granted) granted = (await requestPermission()) === "granted";
    if (granted) sendNotification({ title, body });
  } catch {
    // Notifications are best-effort.
  }
}
