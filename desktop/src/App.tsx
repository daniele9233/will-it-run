import { useEffect, useState } from "react";

import { Mark, Shell } from "./components/Shell";
import { Button, ProgressBar } from "./components/ui";
import { notify, onEvent, waitForCore } from "./lib/api";
import { errorMessage } from "./lib/hooks";
import { applyAppearance, useApp } from "./lib/store";
import { ArenaPage } from "./pages/Arena";
import { BenchmarksPage } from "./pages/Benchmarks";
import { BrainPage } from "./pages/Brain";
import { ChatPage } from "./pages/Chat";
import { DownloadsPage } from "./pages/Downloads";
import { GuidePage } from "./pages/Guide";
import { HardwarePage } from "./pages/Hardware";
import { HomePage } from "./pages/Home";
import { ImagesPage } from "./pages/Images";
import { LogsPage } from "./pages/Logs";
import { ModelsPage } from "./pages/Models";
import { CloneSite } from "./pages/CloneSite";
import { SessionsPage } from "./pages/Sessions";
import { SettingsPage } from "./pages/Settings";
import { TeamsPage } from "./pages/Teams";
import { Wizard } from "./pages/Wizard";
import { WorkflowsPage } from "./pages/Workflows";
import { resolveLang, storeLang, t } from "./lib/i18n";

function Boot({ error, onRetry }: { error: string | null; onRetry: () => void }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 bg-bg">
      <Mark size={28} />
      {error ? (
        <div className="grid max-w-sm justify-items-center gap-3 text-center">
          <p className="text-sm text-ink-2">{error}</p>
          <Button onClick={onRetry}>{t("Try again")}</Button>
        </div>
      ) : (
        <div className="grid w-44 gap-2.5 text-center">
          <ProgressBar indeterminate />
          <p className="text-xs text-ink-3">{t("Starting Will It Run")}</p>
        </div>
      )}
    </div>
  );
}

export function App() {
  const page = useApp((s) => s.page);
  const settings = useApp((s) => s.settings);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    (async () => {
      await waitForCore();
      const state = useApp.getState();
      await state.loadSettings();
      // The interface language is fixed per page load; follow the saved setting.
      if (storeLang(resolveLang(useApp.getState().settings?.ui.language))) {
        window.location.reload();
        return;
      }
      // On first run the wizard performs the scan itself, with visible progress.
      if (useApp.getState().settings?.ui.first_run_complete) {
        await Promise.all([state.loadHardware(), state.loadLMStudio()]);
      }
      if (!cancelled) setReady(true);
    })().catch((err: unknown) => {
      if (!cancelled) setError(errorMessage(err));
    });
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  useEffect(() => applyAppearance(settings), [settings]);

  // Native Windows notifications for long-running work, when enabled.
  useEffect(
    () =>
      onEvent((event) => {
        if (event.topic !== "notify") return;
        const state = useApp.getState();
        const title = String(event.data.title ?? "Will It Run");
        const body = String(event.data.body ?? "");
        state.toast({ tone: /fail|reject|non riuscit|fallit|rifiut/i.test(title) ? "bad" : "info", title, body });
        if (state.settings?.ui.notifications && document.hidden) void notify(title, body);
      }),
    [],
  );

  if (!ready) return <Boot error={error} onRetry={() => setAttempt((n) => n + 1)} />;
  if (!settings?.ui.first_run_complete) return <Wizard />;

  return (
    <Shell>
      {page === "home" && <HomePage />}
      {page === "hardware" && <HardwarePage />}
      {page === "models" && <ModelsPage />}
      {page === "teams" && <TeamsPage />}
      {page === "workflows" && <WorkflowsPage />}
      {page === "benchmarks" && <BenchmarksPage />}
      {page === "arena" && <ArenaPage />}
      {page === "downloads" && <DownloadsPage />}
      {page === "images" && <ImagesPage />}
      {page === "chat" && <ChatPage />}
      {page === "clone" && <CloneSite />}
      {page === "sessions" && <SessionsPage />}
      {page === "brain" && <BrainPage />}
      {page === "logs" && <LogsPage />}
      {page === "guide" && <GuidePage />}
      {page === "settings" && <SettingsPage />}
    </Shell>
  );
}
