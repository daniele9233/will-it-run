import { Check, Loader2, Minus } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Mark } from "../components/Shell";
import { Button, Meter, Note, Segmented, cx } from "../components/ui";
import { api, openExternal } from "../lib/api";
import { gbFromMb } from "../lib/format";
import { errorMessage, useBus } from "../lib/hooks";
import { useApp } from "../lib/store";
import type { HardwareResponse, LMStudioStatus } from "../lib/types";
import { LANGUAGES, getLang, t } from "../lib/i18n";

type StepState = "pending" | "running" | "done" | "missing";

const STEPS: { id: string; label: string }[] = [
  { id: "windows", label: t("Detecting Windows") },
  { id: "cpu", label: t("Detecting CPU") },
  { id: "ram", label: t("Detecting RAM") },
  { id: "gpu", label: t("Detecting GPU") },
  { id: "cuda", label: t("Detecting CUDA") },
  { id: "storage", label: t("Checking storage") },
  { id: "lmstudio", label: t("Finding LM Studio") },
  { id: "models", label: t("Finding installed models") },
];

interface ScanResult extends HardwareResponse {
  lmstudio: LMStudioStatus;
  installed_count: number;
}

export function Wizard() {
  const saveSettings = useApp((s) => s.saveSettings);
  const [states, setStates] = useState<Record<string, StepState>>({});
  const [result, setResult] = useState<ScanResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  useBus("scan.step", (event) => {
    const step = String(event.data.step);
    const state = String(event.data.state) as StepState;
    setStates((current) => ({ ...current, [step]: state }));
  });

  const scan = () => {
    setError(null);
    setResult(null);
    setStates({});
    api
      .post<ScanResult>("/api/scan")
      .then((data) => {
        // Make sure every step reads as finished even if an event was missed.
        setStates((current) => Object.fromEntries(STEPS.map((s) => [s.id, current[s.id] === "missing" ? "missing" : "done"])));
        setResult(data);
        useApp.setState({ hardware: data, lmstudio: data.lmstudio });
      })
      .catch((err: unknown) => setError(errorMessage(err)));
  };

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    scan();
  }, []);

  const finish = () => void saveSettings({ ui: { first_run_complete: true } });
  const gpu = result?.profile.gpus.find((g) => !g.is_integrated) ?? null;
  const budget = result?.budget;

  return (
    <div className="flex h-full items-center justify-center bg-bg">
      <div className="grid w-[880px] grid-cols-[1fr_1fr] gap-12 px-10">
        <div className="self-center">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-2.5">
              <Mark size={22} />
              <span className="font-semibold tracking-tight">Will It Run</span>
            </div>
            <Segmented label="Language / Lingua" value={getLang()} onChange={(lang) => void useApp.getState().setLanguage(lang)} options={LANGUAGES} />
          </div>
          <h1 className="mt-7 text-2xl font-semibold tracking-tight">{t("Welcome to Will It Run")}</h1>
          <p className="mt-2 max-w-[38ch] text-ink-2">
            {t("Let's understand your computer. Will It Run reads your hardware and LM Studio setup locally. Nothing leaves this PC.")}
          </p>
          <ol className="mt-7 grid gap-2">
            {STEPS.map((step) => {
              const state = states[step.id] ?? "pending";
              return (
                <li key={step.id} className={cx("flex items-center gap-2.5 text-sm", state === "pending" ? "text-ink-3" : "text-ink-2")}>
                  <span className="flex h-4 w-4 items-center justify-center">
                    {state === "done" && <Check size={14} className="text-ok" aria-label={t("done")} />}
                    {state === "running" && <Loader2 size={13} className="animate-spin text-accent" aria-label={t("in progress")} />}
                    {state === "missing" && <Minus size={14} className="text-warn" aria-label={t("not found")} />}
                    {state === "pending" && <span className="h-1 w-1 rounded-full bg-line-strong" />}
                  </span>
                  {step.label}
                  {step.id === "lmstudio" && state === "missing" && <span className="text-warn">{t("not found")}</span>}
                </li>
              );
            })}
          </ol>
        </div>

        <div className="self-center">
          {error && (
            <Note tone="bad" action={<Button size="sm" onClick={scan}>{t("Retry")}</Button>}>
              {t("The scan failed: {error}", { error })}
            </Note>
          )}
          {!result && !error && (
            <div className="grid gap-3">
              <div className="skeleton h-24" />
              <div className="skeleton h-16" />
              <div className="skeleton h-16" />
            </div>
          )}
          {result && budget && (
            <div className="rise-in grid gap-6">
              <div>
                <div className="text-xs text-ink-3">{t("Your local AI computer")}</div>
                <div className="mt-1 text-xl font-semibold tracking-tight">{gpu ? gpu.name.replace(/^NVIDIA GeForce |^AMD Radeon |^Intel\(R\) /, "") : result.profile.cpu.model}</div>
                <div className="text-sm text-ink-2">
                  {gpu ? `${gbFromMb(gpu.vram_total_mb, 0)} VRAM` : t("No dedicated GPU: models will run on the CPU")}
                  {" · "}
                  {Math.round((result.profile.ram.installed_bytes ?? result.profile.ram.total_bytes) / 1024 ** 3)} GB RAM
                </div>
              </div>
              {gpu && (
                <div className="grid gap-1.5">
                  <div className="flex justify-between text-sm">
                    <span className="text-ink-2">{t("Recommended usable VRAM")}</span>
                    <span className="tabular font-medium">{budget.single_gpu_usable_gb.toFixed(1)} of {(gpu.vram_total_mb / 1024).toFixed(0)} GB</span>
                  </div>
                  <Meter value={budget.single_gpu_usable_gb / (gpu.vram_total_mb / 1024)} tone="ok" label={t("Recommended usable VRAM")} />
                  <p className="text-xs text-ink-3">{t("The rest is left for Windows, your desktop and a safety margin.")}</p>
                </div>
              )}
              <div className="grid gap-1.5">
                <div className="flex justify-between text-sm">
                  <span className="text-ink-2">{t("Recommended usable RAM")}</span>
                  <span className="tabular font-medium">{budget.ram_usable_gb.toFixed(0)} of {budget.ram_total_gb.toFixed(0)} GB</span>
                </div>
                <Meter value={budget.ram_usable_gb / budget.ram_total_gb} tone="ok" label={t("Recommended usable RAM")} />
              </div>

              {result.lmstudio.installed ? (
                <p className="text-sm text-ink-2">
                  {t("LM Studio found with")} <span className="font-medium text-ink">{t("{n} installed models", { n: result.installed_count })}</span>
                  {result.lmstudio.api_reachable ? "." : `. ${t("Its local server is not running yet; Will It Run can start it when needed.")}`}
                </p>
              ) : (
                <Note tone="warn">
                  <div className="font-medium text-ink">{t("LM Studio is required for local model execution.")}</div>
                  <div className="mt-1">{t("Will It Run does not install it for you. You can still explore hardware and models without it.")}</div>
                </Note>
              )}

              <div className="flex gap-2">
                {!result.lmstudio.installed && (
                  <Button variant="primary" onClick={() => void openExternal(result.lmstudio.download_url ?? "https://lmstudio.ai/download")}>
                    {t("Open LM Studio Download")}
                  </Button>
                )}
                <Button variant={result.lmstudio.installed ? "primary" : "secondary"} onClick={finish}>
                  {result.lmstudio.installed ? t("Continue") : t("Continue without runtime")}
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
