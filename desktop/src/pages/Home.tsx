import { ArrowRight, X } from "lucide-react";
import type { CSSProperties } from "react";

import { HelpTip } from "../components/Guide";
import { Button, FitBadge, IconButton, Meter, Note, PageHeader, SkeletonRows, StatusDot, cx, toneBg, toneText } from "../components/ui";
import { api, openExternal } from "../lib/api";
import { fitLabel, fitTone, gbFromMb, placementShort, readinessWord, sentenceCase, sizeLabel } from "../lib/format";
import { useBus, useLoad } from "../lib/hooks";
import { t } from "../lib/i18n";
import { useApp } from "../lib/store";
import type { FitCategory, ModelCard, Placement, StorageLocation } from "../lib/types";

interface LadderStep {
  label: string;
  params_b: number;
  quant: string;
  category: FitCategory;
  runnable: boolean;
  placement: Placement;
  tokens_per_second: number;
  memory_gb: number;
}

interface Featured {
  repo: string;
  name: string;
  quant: string;
  category: FitCategory;
  placement: Placement;
  vram_required_gb: number;
  ram_required_gb: number;
  tokens_per_second: number;
  installed: boolean;
}

interface Readiness {
  rows: { label: string; category: FitCategory | null; model: string | null; quant: string | null; tokens_per_second: number | null; params_b: number | null }[];
  overall: FitCategory | null;
  target_tps: number;
  ladder: LadderStep[];
  featured: Featured | null;
}

const HATCH = "repeating-linear-gradient(135deg, var(--line-strong) 0 4px, var(--raised) 4px 8px)";
// Room kept above the tallest bar for its value label.
const LABEL_ROOM = 22;

function shortGpu(name: string): string {
  return name.replace(/^NVIDIA GeForce |^AMD Radeon |^Intel\(R\) /, "").replace(/\(TM\)|\(R\)/g, "");
}

function shortCpu(name: string): string {
  return name
    .replace(/\(R\)|\(TM\)|®|™/g, "")
    .replace(/\b(Intel|AMD|CPU|Processor)\b/g, "")
    .replace(/@.*$/, "")
    .replace(/\d+(th|nd|rd|st) Gen/i, "")
    .replace(/\s+/g, " ")
    .trim();
}

function speedLabel(value: number): string {
  return value >= 10 ? String(Math.round(value)) : value.toFixed(1);
}

interface Reserve {
  label: string;
  gb: number;
  style: CSSProperties;
}

/** One bar that shows where the memory goes: the recommended model, what stays free, and what is held back. */
function MemoryMap({ title, help, total, usable, model, reserves }: { title: string; help: "vram" | "ram"; total: number; usable: number; model: { name: string; gb: number } | null; reserves: Reserve[] }) {
  const used = model ? Math.min(model.gb, usable) : 0;
  const free = Math.max(0, usable - used);
  const width = (gb: number) => `${(gb / total) * 100}%`;
  const summary = [
    model ? `${model.name}: ${used.toFixed(1)} GB` : null,
    t("free: {n} GB", { n: free.toFixed(1) }),
    ...reserves.map((r) => `${r.label}: ${r.gb.toFixed(1)} GB`),
  ]
    .filter(Boolean)
    .join(", ");
  return (
    <div>
      <div className="mb-1.5 flex items-baseline justify-between gap-4 text-sm">
        <span className="text-ink-2">
          {title} <HelpTip id={help} />
        </span>
        <span className="tabular font-medium">{t("{usable} GB usable of {total}", { usable: usable.toFixed(1), total: total.toFixed(0) })}</span>
      </div>
      <div role="img" aria-label={`${title}. ${summary}`} className="tabular flex h-[34px] overflow-hidden rounded-md bg-raised text-xs">
        {model && used > 0 && (
          <div className="flex items-center overflow-hidden bg-accent px-2.5 font-semibold whitespace-nowrap text-accent-ink" style={{ width: width(used) }} title={`${model.name} · ${used.toFixed(1)} GB`}>
            <span className="truncate">
              {model.name} · {used.toFixed(1)} GB
            </span>
          </div>
        )}
        {free > 0.05 && (
          <div className="flex items-center justify-center overflow-hidden text-ok" style={{ width: width(free), boxShadow: "inset 0 0 0 1.5px var(--ok)" }} title={t("free: {n} GB", { n: free.toFixed(1) })}>
            {free / total > 0.05 ? free.toFixed(1) : ""}
          </div>
        )}
        {reserves.map((reserve) => (
          <div key={reserve.label} style={{ width: width(reserve.gb), ...reserve.style }} title={`${reserve.label}: ${reserve.gb.toFixed(1)} GB`} />
        ))}
      </div>
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-3">
        {model && (
          <li className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-[2px] bg-accent" aria-hidden /> {t("recommended model")}
          </li>
        )}
        <li className="inline-flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-[2px]" style={{ boxShadow: "inset 0 0 0 1.5px var(--ok)" }} aria-hidden /> {t("free")}
        </li>
        {reserves.map((reserve) => (
          <li key={reserve.label} className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-[2px]" style={reserve.style} aria-hidden /> {reserve.label} {reserve.gb.toFixed(1)} GB
          </li>
        ))}
      </ul>
    </div>
  );
}

function DriveRows({ locations }: { locations: StorageLocation[] }) {
  return (
    <ul className="grid gap-2.5">
      {locations.map((loc) => {
        const below = loc.safe_available_gb <= 0;
        const used = loc.total_gb ? (loc.total_gb - loc.free_gb) / loc.total_gb : 0;
        return (
          <li key={loc.drive} className="grid grid-cols-[26px_minmax(0,1fr)_auto] items-center gap-3">
            <span className="font-mono text-sm font-semibold">{loc.drive}</span>
            <Meter value={used} tone={below ? "bad" : "off"} height={8} label={t("Space used on {drive}", { drive: loc.drive })} />
            <span className={cx("tabular w-[196px] text-right text-xs", below ? "text-bad" : "text-ink-3")}>
              {below
                ? t("{free} GB free · below the reserve", { free: loc.free_gb.toFixed(0) })
                : t("{free} GB free · {safe} safe for models", { free: loc.free_gb.toFixed(0), safe: loc.safe_available_gb.toFixed(0) })}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

/** Estimated speed per model size, against the target speed. Bars are colored by fit, never by decoration. */
function SizeChart({ ladder, target }: { ladder: LadderStep[]; target: number }) {
  const max = Math.max(target * 1.6, ...ladder.map((s) => (s.runnable ? s.tokens_per_second : 0)));
  const columns = { gridTemplateColumns: `repeat(${ladder.length}, minmax(0, 1fr))` };
  const height = (value: number) => `calc((100% - ${LABEL_ROOM}px) * ${Math.min(1, value / max).toFixed(4)})`;
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="relative grid min-h-[150px] flex-1 gap-3.5 border-b border-line-strong" style={columns}>
        <div className="pointer-events-none absolute inset-x-0 border-t border-dashed border-ink-3" style={{ bottom: height(target) }}>
          <span className="tabular absolute -top-[17px] right-0 text-2xs text-ink-3">{t("target {n} tok/s", { n: target })}</span>
        </div>
        {ladder.map((step) => {
          const tone = fitTone(step.category);
          const hint = step.runnable
            ? `${step.label} · ${step.quant} · ${placementShort(step.placement)} · ${step.memory_gb.toFixed(1)} GB · ~${speedLabel(step.tokens_per_second)} tok/s`
            : t("{size} needs {gb} GB: more than this PC can safely give", { size: step.label, gb: step.memory_gb.toFixed(1) });
          return (
            <div key={step.label} className="relative" title={hint}>
              {step.runnable ? (
                <>
                  <span className="tabular absolute inset-x-0 text-center text-xs font-medium text-ink-2" style={{ bottom: `calc(${height(step.tokens_per_second)} + 4px)` }}>
                    {speedLabel(step.tokens_per_second)}
                  </span>
                  <div className={cx("absolute bottom-0 left-1/2 w-full max-w-[64px] -translate-x-1/2 rounded-t-sm", toneBg[tone])} style={{ height: height(step.tokens_per_second), minHeight: 3 }} />
                </>
              ) : (
                <span className="absolute inset-x-0 bottom-1 text-center text-sm font-medium text-bad">×</span>
              )}
            </div>
          );
        })}
      </div>
      <div className="tabular mt-1.5 grid gap-3.5 text-center text-xs" style={columns}>
        {ladder.map((step) => (
          <div key={step.label} className="min-w-0">
            <div className="truncate">{step.label}</div>
            <div className={cx("truncate text-2xs", toneText[fitTone(step.category)])}>{sentenceCase(fitLabel(step.category))}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function InstalledList({ cards, totalBytes }: { cards: ModelCard[]; totalBytes: number }) {
  const navigate = useApp((s) => s.navigate);
  const top = [...cards].sort((a, b) => b.score.total - a.score.total).slice(0, 5);
  return (
    <section className="flex min-h-0 flex-col">
      <div className="mb-2.5 flex items-baseline justify-between gap-4">
        <h2 className="text-sm font-semibold">{t("Installed models")}</h2>
        {cards.length > 0 && (
          <span className="tabular text-xs text-ink-3">
            {cards.length} · {sizeLabel(totalBytes)}
          </span>
        )}
      </div>
      {cards.length === 0 ? (
        <div className="border-t border-line pt-3 text-sm text-ink-2">
          <p className="max-w-[44ch]">{t("No models installed yet. Explore Models shows which ones fit this PC before you download anything.")}</p>
          <Button className="mt-3" onClick={() => navigate("models")}>
            {t("Explore Models")}
          </Button>
        </div>
      ) : (
        <>
          <ul className="border-t border-line">
            {top.map((card) => (
              <li key={card.installed_key ?? card.entry.repo}>
                <button
                  type="button"
                  onClick={() => navigate("models", { installed: "1", repo: card.entry.repo })}
                  className="grid w-full grid-cols-[minmax(0,1fr)_auto_112px] items-center gap-3 border-b border-line py-2 text-left transition-colors duration-150 hover:bg-surface"
                >
                  <span className="min-w-0">
                    <span className="block truncate text-sm">{card.entry.name}</span>
                    <span className="block truncate text-xs text-ink-3">
                      <span className="font-mono">{card.quant}</span> · {sizeLabel(card.download_size_bytes, card.size_is_estimate)}
                    </span>
                  </span>
                  <span className="tabular text-right text-sm text-ink-2">
                    {card.fit.speed.measured ? "" : "~"}
                    {speedLabel(card.fit.speed.tokens_per_second)} tok/s
                    <span className="block text-2xs text-ink-3">{card.fit.speed.measured ? t("measured") : t("estimated")}</span>
                  </span>
                  <span className="text-right">
                    <FitBadge category={card.fit.category} />
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {cards.length > top.length && (
            <button type="button" onClick={() => navigate("models", { installed: "1" })} className="mt-2.5 justify-self-start text-left text-sm text-accent hover:underline">
              {t("See all {n} models", { n: cards.length })}
            </button>
          )}
        </>
      )}
    </section>
  );
}

/** First-visit pointer for people new to local AI. Closed once, it stays closed. */
function GettingStarted() {
  const navigate = useApp((s) => s.navigate);
  const saveSettings = useApp((s) => s.saveSettings);
  return (
    <div className="col-span-2 flex flex-wrap items-center gap-x-5 gap-y-2 rounded-lg border border-line bg-surface px-4 py-3">
      <div className="min-w-0 flex-1 basis-[420px]">
        <div className="text-sm font-medium">{t("New to local AI?")}</div>
        <p className="mt-0.5 text-sm text-ink-2">
          {t("This page shows what your PC can run. Next, pick a model in Explore Models: Will It Run chooses the file that fits and checks the disk before downloading.")}
        </p>
      </div>
      <div className="flex items-center gap-2">
        <Button onClick={() => navigate("guide")}>{t("How it works")}</Button>
        <Button variant="primary" onClick={() => navigate("models", { recommended: "1" })}>
          {t("Pick a model")}
        </Button>
        <IconButton label={t("Hide this")} onClick={() => void saveSettings({ ui: { guide_dismissed: true } })}>
          <X size={14} />
        </IconButton>
      </div>
    </div>
  );
}

export function HomePage() {
  const hardware = useApp((s) => s.hardware);
  const lmstudio = useApp((s) => s.lmstudio);
  const navigate = useApp((s) => s.navigate);
  const showGuide = useApp((s) => s.settings?.ui.guide_dismissed === false);
  const readiness = useLoad(() => api.get<Readiness>("/api/readiness"), [hardware?.fingerprint]);
  const storage = useLoad(() => api.get<{ locations: StorageLocation[] }>("/api/storage"), [hardware?.fingerprint]);
  const installed = useLoad(() => api.get<{ cards: ModelCard[]; total_bytes: number }>("/api/models/installed"), [hardware?.fingerprint]);
  useBus("download.update", (event) => {
    if (event.data.status === "completed") {
      installed.reload();
      storage.reload();
    }
  });

  if (!hardware) return <div className="p-7"><SkeletonRows rows={4} height={64} /></div>;
  const { profile, budget } = hardware;
  const gpu = profile.gpus.find((g) => !g.is_integrated) ?? null;
  const gpuBudget = budget.gpus[0] ?? null;
  const dedicated = profile.gpus.filter((g) => !g.is_integrated).length;
  const overall = readinessWord(readiness.data?.overall ?? null);
  const featured = readiness.data?.featured ?? null;
  const ramInstalled = Math.round((profile.ram.installed_bytes ?? profile.ram.total_bytes) / 1024 ** 3);
  const featuredName = featured ? `${featured.name} ${featured.quant}` : "";

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title={t("Your Local AI Computer")}
        subtitle={t("What this PC can run well, based on its real hardware and what is safe to use.")}
        actions={
          <>
            <Button onClick={() => navigate("models")}>{t("Explore Models")}</Button>
            <Button variant="primary" icon={<ArrowRight size={14} />} onClick={() => navigate("teams")}>
              {t("Build AI Team")}
            </Button>
          </>
        }
      />
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className={cx("grid min-h-full max-w-[1520px] grid-cols-[minmax(0,6fr)_minmax(0,5fr)] gap-x-10 gap-y-6 px-7 pt-5 pb-6", showGuide ? "grid-rows-[auto_auto_minmax(250px,1fr)]" : "grid-rows-[auto_minmax(250px,1fr)]")}>
          {showGuide && <GettingStarted />}
          <section className="grid content-start gap-4">
            {lmstudio && !lmstudio.installed && (
              <Note tone="warn" action={<Button size="sm" onClick={() => void openExternal(lmstudio.download_url ?? "https://lmstudio.ai/download")}>{t("Open LM Studio Download")}</Button>}>
                <span className="font-medium text-ink">{t("LM Studio is required for local model execution.")}</span> {t("You can explore hardware and models without it.")}
              </Note>
            )}
            {lmstudio?.installed && !lmstudio.api_reachable && (
              <Note tone="warn" action={<Button size="sm" onClick={() => void api.post("/api/lmstudio/server/start").then(() => useApp.getState().loadLMStudio())}>{t("Start server")}</Button>}>
                {t("LM Studio's local server is not running. Models cannot be loaded until it starts.")}
              </Note>
            )}

            <div>
              <div className="text-[1.375rem] leading-tight font-semibold tracking-tight">{gpu ? shortGpu(gpu.name) : shortCpu(profile.cpu.model)}</div>
              <div className="mt-0.5 text-ink-2">
                {gpu ? `${gbFromMb(gpu.vram_total_mb, 0)} VRAM · ${shortCpu(profile.cpu.model)}` : t("No dedicated GPU. Models run on the CPU.")} · {ramInstalled} GB {profile.ram.memory_type ?? "RAM"}
                {dedicated > 1 && ` · ${t("{n} GPUs", { n: dedicated })}`}
              </div>
            </div>

            {gpuBudget ? (
              <MemoryMap
                title={t("Graphics memory map")}
                help="vram"
                total={gpuBudget.total_gb}
                usable={gpuBudget.usable_gb}
                model={featured && featured.vram_required_gb > 0 ? { name: featuredName, gb: featured.vram_required_gb } : null}
                reserves={[
                  { label: t("safety margin"), gb: gpuBudget.safety_reserve_gb, style: { background: HATCH } },
                  { label: "Windows", gb: gpuBudget.os_reserve_gb, style: { background: "var(--line-strong)" } },
                ]}
              />
            ) : (
              <MemoryMap
                title={t("System memory map")}
                help="ram"
                total={budget.ram_total_gb}
                usable={budget.ram_usable_gb}
                model={featured && featured.ram_required_gb > 0 ? { name: featuredName, gb: featured.ram_required_gb } : null}
                reserves={[{ label: t("Windows and apps"), gb: budget.ram_reserve_gb, style: { background: HATCH } }]}
              />
            )}

            {gpuBudget && (
              <div className="grid gap-1.5">
                <div className="flex items-baseline justify-between text-sm">
                  <span className="text-ink-2">{t("System memory")}</span>
                  <span className="tabular font-medium">{t("{usable} GB usable of {total}", { usable: budget.ram_usable_gb.toFixed(0), total: ramInstalled })}</span>
                </div>
                <Meter value={budget.ram_total_gb ? budget.ram_usable_gb / budget.ram_total_gb : 0} tone="ok" height={8} label={t("System memory")} />
              </div>
            )}

            {storage.data && storage.data.locations.length > 0 && <DriveRows locations={storage.data.locations.slice(0, 4)} />}

            <button type="button" onClick={() => navigate("hardware")} className="justify-self-start text-sm text-accent hover:underline">
              {t("See full hardware details")}
            </button>
          </section>

          <section className="grid content-start gap-3">
            <div>
              <div className="text-sm text-ink-2">
                {t("AI readiness")} <HelpTip id="fit" />
              </div>
              {readiness.loading && !readiness.data ? (
                <div className="skeleton mt-1.5 h-8 w-40" />
              ) : (
                <div className={cx("text-2xl leading-tight font-semibold tracking-tight", toneText[overall.tone])}>{overall.word}</div>
              )}
            </div>
            {readiness.error && <Note tone="bad">{readiness.error}</Note>}
            {readiness.loading && !readiness.data && <SkeletonRows rows={5} height={38} />}
            {readiness.data && (
              <ul className="border-t border-line">
                {readiness.data.rows.map((row) => {
                  const word = readinessWord(row.category);
                  return (
                    <li key={row.label} className="grid grid-cols-[1fr_auto] items-center gap-x-4 border-b border-line py-2">
                      <div className="min-w-0">
                        <div className="text-sm">{row.label}</div>
                        <div className="truncate text-xs text-ink-3">
                          {row.model
                            ? `${row.model} ${row.quant ?? ""} · ~${Math.round(row.tokens_per_second ?? 0)} tok/s`
                            : row.category
                              ? t("Best case {quant} at ~{tps} tok/s", { quant: row.quant ?? "", tps: (row.tokens_per_second ?? 0).toFixed(1) })
                              : t("No runnable model found yet")}
                        </div>
                      </div>
                      <div className="flex items-center gap-2 text-sm">
                        <StatusDot tone={word.tone} />
                        <span className={cx("font-medium", toneText[word.tone])}>{word.word}</span>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          <section className="flex min-h-0 flex-col">
            <div className="mb-2.5 flex items-baseline justify-between gap-4">
              <h2 className="text-sm font-semibold">
                {t("What runs on this PC")} <HelpTip id="params" />
              </h2>
              <span className="text-xs text-ink-3">{t("estimated speed by model size")}</span>
            </div>
            {readiness.data ? <SizeChart ladder={readiness.data.ladder} target={readiness.data.target_tps} /> : <div className="skeleton min-h-[190px] flex-1" />}
          </section>

          {installed.data ? (
            <InstalledList cards={installed.data.cards} totalBytes={installed.data.total_bytes} />
          ) : (
            <section>
              <h2 className="mb-2.5 text-sm font-semibold">{t("Installed models")}</h2>
              {installed.error ? <Note tone="bad">{installed.error}</Note> : <SkeletonRows rows={4} height={44} />}
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
