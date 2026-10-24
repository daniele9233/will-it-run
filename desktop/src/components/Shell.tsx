import {
  Activity,
  BookOpen,
  Boxes,
  Brain,
  Copy,
  Cpu,
  Download,
  Gauge,
  Home,
  MessageSquare,
  ScrollText,
  Search,
  Settings as SettingsIcon,
  Trophy,
  Users,
  Workflow,
  X,
} from "lucide-react";
import type { ComponentType, ReactNode } from "react";
import { useEffect, useMemo, useRef, useState } from "react";

import { gbFromMb } from "../lib/format";
import { useInterval } from "../lib/hooks";
import { type PageId, useApp } from "../lib/store";
import { ZOOM_STEP } from "../lib/zoom";
import { Kbd, Meter, StatusDot, cx } from "./ui";
import { getLang, t } from "../lib/i18n";

interface NavItem {
  id: PageId;
  label: string;
  icon: ComponentType<{ size?: number; className?: string }>;
}

const NAV: NavItem[][] = [
  [{ id: "home", label: t("Home"), icon: Home }],
  [
    { id: "hardware", label: t("Hardware"), icon: Cpu },
    { id: "models", label: t("Models"), icon: Boxes },
    { id: "teams", label: t("AI Teams"), icon: Users },
    { id: "workflows", label: t("Workflows"), icon: Workflow },
    { id: "benchmarks", label: t("Benchmarks"), icon: Gauge },
    { id: "arena", label: t("Arena"), icon: Trophy },
    { id: "downloads", label: t("Downloads"), icon: Download },
  ],
  [
    { id: "chat", label: t("Chat"), icon: MessageSquare },
    { id: "clone", label: t("Clone a site"), icon: Copy },
    { id: "sessions", label: t("Sessions"), icon: Activity },
    { id: "brain", label: t("Memory"), icon: Brain },
    { id: "logs", label: t("Logs"), icon: ScrollText },
  ],
  [
    { id: "guide", label: t("Guide"), icon: BookOpen },
    { id: "settings", label: t("Settings"), icon: SettingsIcon },
  ],
];

function Sidebar() {
  const page = useApp((s) => s.page);
  const navigate = useApp((s) => s.navigate);
  const setPalette = useApp((s) => s.setPalette);
  return (
    <nav aria-label={t("Main")} className="flex w-[212px] shrink-0 flex-col border-r border-line bg-panel">
      <div className="flex h-12 items-center gap-2 px-4">
        <Mark />
        <span className="text-sm font-semibold tracking-tight">Will It Run</span>
      </div>
      <button
        type="button"
        onClick={() => setPalette(true)}
        className="mx-3 mb-2 flex h-8 items-center gap-2 rounded-md border border-line bg-bg px-2.5 text-xs text-ink-3 transition-colors duration-150 hover:border-line-strong hover:text-ink-2"
      >
        <Search size={13} aria-hidden />
        <span className="flex-1 truncate text-left">{t("Commands")}</span>
        <Kbd>{t("Ctrl K")}</Kbd>
      </button>
      <div className="flex-1 overflow-y-auto px-2 pb-2">
        {NAV.map((group, index) => (
          <ul key={index} className={cx("grid gap-px", index > 0 && "mt-3 border-t border-line pt-3")}>
            {group.map((item) => {
              const active = item.id === page;
              return (
                <li key={item.id}>
                  <button
                    type="button"
                    aria-current={active ? "page" : undefined}
                    onClick={() => navigate(item.id)}
                    className={cx(
                      "flex h-8 w-full items-center gap-2.5 rounded-md px-2.5 text-sm transition-colors duration-150",
                      active ? "bg-raised font-medium text-ink" : "text-ink-2 hover:bg-surface hover:text-ink",
                    )}
                  >
                    <item.icon size={15} className={active ? "text-accent" : "text-ink-3"} />
                    {item.label}
                  </button>
                </li>
              );
            })}
          </ul>
        ))}
      </div>
      <StatusArea />
    </nav>
  );
}

/** Will It Run mark: a chip with a play button on it. Does this hardware run it? Original artwork. */
export function Mark({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 18 18" fill="none" aria-hidden>
      <path
        d="M6.75 1v1.6M11.25 1v1.6M6.75 15.4V17M11.25 15.4V17M1 6.75h1.6M1 11.25h1.6M15.4 6.75H17M15.4 11.25H17"
        stroke="var(--ink-3)"
        strokeWidth="1.4"
        strokeLinecap="round"
      />
      <rect x="3.25" y="3.25" width="11.5" height="11.5" rx="2.75" stroke="var(--ink-2)" strokeWidth="1.5" />
      <path d="M7.7 6.4v5.2L12 9 7.7 6.4Z" fill="var(--accent)" stroke="var(--accent)" strokeWidth="1" strokeLinejoin="round" />
    </svg>
  );
}

function StatusArea() {
  const live = useApp((s) => s.live);
  const hardware = useApp((s) => s.hardware);
  const lmstudio = useApp((s) => s.lmstudio);
  const loadLive = useApp((s) => s.loadLive);
  const navigate = useApp((s) => s.navigate);
  useEffect(() => {
    void loadLive();
  }, [loadLive]);
  useInterval(() => void loadLive(), 3000);

  const connected = live?.lmstudio_connected ?? lmstudio?.api_reachable ?? false;
  const installed = lmstudio?.installed ?? true;
  const gpu = live?.gpus[0];
  const gpuTotal = gpu?.vram_total_mb ?? hardware?.profile.gpus.find((g) => !g.is_integrated)?.vram_total_mb ?? null;
  const ramTotal = live?.ram_total_bytes ?? hardware?.profile.ram.total_bytes ?? 0;
  const ramUsed = live?.ram_used_bytes ?? 0;
  const loaded = live?.loaded.length ?? 0;

  return (
    <div className="grid gap-2.5 border-t border-line px-4 py-3 text-xs">
      <button
        type="button"
        onClick={() => navigate("settings")}
        className="flex items-center gap-2 text-left text-ink-2 hover:text-ink"
        title={connected ? t("LM Studio local server is reachable") : t("LM Studio local server is not reachable")}
      >
        <StatusDot tone={connected ? "ok" : installed ? "warn" : "off"} />
        <span className="flex-1">LM Studio</span>
        <span className={connected ? "text-ok" : "text-ink-3"}>{connected ? t("Connected") : installed ? t("Offline") : t("Not installed")}</span>
      </button>
      {gpuTotal ? (
        <div className="grid gap-1">
          <div className="flex justify-between text-ink-3">
            <span>GPU{loaded > 0 ? ` · ${t("{n} loaded", { n: loaded })}` : ""}</span>
            <span className="tabular text-ink-2">
              {gpu?.vram_used_mb != null ? (gpu.vram_used_mb / 1024).toFixed(1) : "–"} / {gbFromMb(gpuTotal, 0)}
            </span>
          </div>
          <Meter value={gpu?.vram_used_mb != null ? gpu.vram_used_mb / gpuTotal : 0} label={t("GPU memory in use")} height={4} />
        </div>
      ) : (
        <div className="flex justify-between text-ink-3">
          <span>GPU</span>
          <span>{t("None detected")}</span>
        </div>
      )}
      <div className="grid gap-1">
        <div className="flex justify-between text-ink-3">
          <span>RAM</span>
          <span className="tabular text-ink-2">
            {(ramUsed / 1024 ** 3).toFixed(0)} / {(ramTotal / 1024 ** 3).toFixed(0)} GB
          </span>
        </div>
        <Meter value={ramTotal ? ramUsed / ramTotal : 0} tone="off" label={t("System memory in use")} height={4} />
      </div>
    </div>
  );
}

interface Command {
  id: string;
  label: string;
  hint?: string;
  run: () => void;
}

function CommandPalette() {
  const open = useApp((s) => s.paletteOpen);
  const setPalette = useApp((s) => s.setPalette);
  const navigate = useApp((s) => s.navigate);
  const loadHardware = useApp((s) => s.loadHardware);
  const toast = useApp((s) => s.toast);
  const [text, setText] = useState("");
  const [index, setIndex] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  const commands = useMemo<Command[]>(
    () => [
      { id: "explore", label: t("Explore Models"), hint: t("Models"), run: () => navigate("models") },
      { id: "uncensored", label: t("Explore Uncensored / Abliterated Models"), hint: t("Models"), run: () => navigate("models", { alignment: "uncensored" }) },
      { id: "installed", label: t("Show Installed Models"), hint: t("Models"), run: () => navigate("models", { installed: "1" }) },
      { id: "team", label: t("Build AI Team"), hint: t("AI Teams"), run: () => navigate("teams") },
      { id: "hardware", label: t("Open Hardware"), hint: t("Hardware"), run: () => navigate("hardware") },
      {
        id: "rescan",
        label: t("Rescan Hardware"),
        hint: t("Hardware"),
        run: () => {
          setPalette(false);
          void loadHardware(true).then(() => toast({ tone: "ok", title: t("Hardware rescanned") }));
        },
      },
      { id: "benchmark", label: t("Benchmark Model"), hint: t("Benchmarks"), run: () => navigate("benchmarks") },
      { id: "arena", label: t("Open the Arena"), hint: t("Arena"), run: () => navigate("arena") },
      { id: "arena-new", label: t("Start an Arena challenge"), hint: t("Arena"), run: () => navigate("arena", { new: "1" }) },
      { id: "run", label: t("Run Workflow"), hint: t("Workflows"), run: () => navigate("workflows") },
      { id: "chat", label: t("Chat with one model"), hint: t("Chat"), run: () => navigate("chat") },
      { id: "sessions", label: t("Open Sessions"), hint: t("Sessions"), run: () => navigate("sessions") },
      { id: "brain", label: t("Open the project memory"), hint: t("Memory"), run: () => navigate("brain") },
      { id: "downloads", label: t("Open Downloads"), hint: t("Downloads"), run: () => navigate("downloads") },
      { id: "doctor", label: t("Run Doctor"), hint: t("Settings"), run: () => navigate("settings", { tab: "doctor" }) },
      { id: "logs", label: t("Open Logs"), hint: t("Logs"), run: () => navigate("logs") },
      { id: "settings", label: t("Settings"), hint: t("Settings"), run: () => navigate("settings") },
      { id: "guide", label: t("Open the guide"), hint: t("Guide"), run: () => navigate("guide") },
      { id: "quant", label: t("Quantization and accuracy"), hint: t("Guide"), run: () => navigate("guide", { term: "quant" }) },
      {
        id: "language",
        label: getLang() === "it" ? "Switch to English" : "Passa all'italiano",
        hint: t("Settings"),
        run: () => void useApp.getState().setLanguage(getLang() === "it" ? "en" : "it"),
      },
      { id: "zoom-in", label: t("Zoom in"), hint: "Ctrl +", run: () => useApp.getState().setZoom((useApp.getState().settings?.ui.zoom ?? 1) + ZOOM_STEP) },
      { id: "zoom-out", label: t("Zoom out"), hint: "Ctrl −", run: () => useApp.getState().setZoom((useApp.getState().settings?.ui.zoom ?? 1) - ZOOM_STEP) },
      { id: "zoom-reset", label: t("Normal size"), hint: "Ctrl 0", run: () => useApp.getState().setZoom(1) },
    ],
    [navigate, loadHardware, setPalette, toast],
  );

  const matches = useMemo(() => {
    const needle = text.trim().toLowerCase();
    if (!needle) return commands;
    return commands.filter((c) => needle.split(/\s+/).every((part) => c.label.toLowerCase().includes(part)));
  }, [commands, text]);

  useEffect(() => {
    if (open) {
      setText("");
      setIndex(0);
      // Focus after the dialog is painted.
      requestAnimationFrame(() => input.current?.focus());
    }
  }, [open]);

  useEffect(() => setIndex(0), [text]);

  if (!open) return null;
  return (
    <div className="fade-in fixed inset-0 z-[40] flex justify-center bg-black/55 pt-[14vh]" onMouseDown={() => setPalette(false)}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={t("Command palette")}
        onMouseDown={(e) => e.stopPropagation()}
        className="rise-in z-[50] h-fit w-[540px] overflow-hidden rounded-lg border border-line-strong bg-panel"
        style={{ boxShadow: "var(--shadow)" }}
      >
        <div className="flex items-center gap-2.5 border-b border-line px-3.5">
          <Search size={15} className="text-ink-3" aria-hidden />
          <input
            ref={input}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setIndex((i) => Math.min(i + 1, matches.length - 1));
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setIndex((i) => Math.max(i - 1, 0));
              } else if (e.key === "Enter") {
                e.preventDefault();
                matches[index]?.run();
              } else if (e.key === "Escape") {
                setPalette(false);
              }
            }}
            placeholder={t("Search commands...")}
            aria-label={t("Search commands")}
            className="h-11 flex-1 bg-transparent text-sm text-ink placeholder:text-ink-3 focus:outline-none"
          />
          <Kbd>{t("Esc")}</Kbd>
        </div>
        <ul role="listbox" className="max-h-[320px] overflow-y-auto p-1.5">
          {matches.length === 0 && <li className="px-3 py-6 text-center text-sm text-ink-3">{t("No command matches \"{text}\"", { text })}</li>}
          {matches.map((command, i) => (
            <li key={command.id} role="option" aria-selected={i === index}>
              <button
                type="button"
                onMouseEnter={() => setIndex(i)}
                onClick={command.run}
                className={cx(
                  "flex h-9 w-full items-center justify-between rounded-md px-3 text-left text-sm",
                  i === index ? "bg-raised text-ink" : "text-ink-2",
                )}
              >
                {command.label}
                {command.hint && <span className="text-xs text-ink-3">{command.hint}</span>}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function Toasts() {
  const toasts = useApp((s) => s.toasts);
  const dismiss = useApp((s) => s.dismissToast);
  return (
    <div className="pointer-events-none fixed right-4 bottom-4 z-[60] grid w-[340px] gap-2" aria-live="polite">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className="rise-in pointer-events-auto flex items-start gap-2.5 rounded-lg border border-line-strong bg-panel px-3.5 py-3"
          style={{ boxShadow: "var(--shadow)" }}
        >
          <span className="mt-1.5">
            <StatusDot tone={toast.tone === "ok" ? "ok" : toast.tone === "bad" ? "bad" : "accent"} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="text-sm font-medium">{toast.title}</div>
            {toast.body && <div className="selectable mt-0.5 text-xs break-words text-ink-2">{toast.body}</div>}
          </div>
          <button type="button" aria-label={t("Dismiss")} onClick={() => dismiss(toast.id)} className="text-ink-3 hover:text-ink">
            <X size={14} />
          </button>
        </div>
      ))}
    </div>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const setPalette = useApp((s) => s.setPalette);
  const paletteOpen = useApp((s) => s.paletteOpen);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey)) return;
      const state = useApp.getState();
      const zoom = state.settings?.ui.zoom ?? 1;
      if (event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPalette(!paletteOpen);
      } else if (event.key === "+" || event.key === "=") {
        event.preventDefault();
        state.setZoom(zoom + ZOOM_STEP);
      } else if (event.key === "-") {
        event.preventDefault();
        state.setZoom(zoom - ZOOM_STEP);
      } else if (event.key === "0") {
        event.preventDefault();
        state.setZoom(1);
      }
    };
    // Ctrl + wheel zooms the interface, as it does in a browser.
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey) return;
      event.preventDefault();
      const state = useApp.getState();
      state.setZoom((state.settings?.ui.zoom ?? 1) + (event.deltaY < 0 ? ZOOM_STEP : -ZOOM_STEP));
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("wheel", onWheel);
    };
  }, [paletteOpen, setPalette]);

  return (
    <div className="flex h-full">
      <Sidebar />
      <main className="flex min-w-0 flex-1 flex-col overflow-hidden bg-bg">{children}</main>
      <CommandPalette />
      <Toasts />
    </div>
  );
}
