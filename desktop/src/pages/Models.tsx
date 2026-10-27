import { Check, Download, ExternalLink, FolderOpen, Search, WifiOff, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { AccuracyMeter, HelpTip, pplLabel } from "../components/Guide";
import {
  AlignmentTag,
  Badge,
  Button,
  Chip,
  ConfidenceTag,
  EmptyState,
  FitBadge,
  IconButton,
  KeyValue,
  Meter,
  Note,
  PageHeader,
  Section,
  Segmented,
  Select,
  SkeletonRows,
  Stars,
  Tabs,
  TextInput,
  WhyList,
  cx,
} from "../components/ui";
import { ApiError, api, openExternal, query } from "../lib/api";
import { canBrowseFolders, pickFolder } from "../lib/dialog";
import { UNKNOWN, categoryLabel, compactNumber, contextLabel, fitLabel, fitTone, gb, params, placementLabel, sizeLabel, speedRange, tps } from "../lib/format";
import { errorMessage, useBus, useDebounced, useLoad } from "../lib/hooks";
import { filterModels, nominalParamsB, paramBandBounds, parameterSizes, quantizationOptions, type ModelSort, type ParamBand } from "../lib/modelFilters";
import { useApp } from "../lib/store";
import type { DownloadPlan, FitResult, ModelCard, ModelDetails, ModelFile, ModelFileRow, SearchResponse, StorageLocation } from "../lib/types";
import { t } from "../lib/i18n";

type AlignmentFilter = "any" | "standard" | "uncensored";

const TASK_LABEL = { coding: t("Coding"), reasoning: t("Reasoning"), writing: t("Writing") } as const;

const FILTERS: { id: string; label: string }[] = [
  { id: "recommended", label: t("Recommended") },
  { id: "Coding", label: t("Coding") },
  { id: "Reasoning", label: t("Reasoning") },
  { id: "Vision", label: t("Vision") },
  { id: "tool_use", label: t("Tool Use") },
  { id: "Writing", label: t("Writing") },
  { id: "Computer Use", label: t("Computer Use") },
  { id: "installed", label: t("Installed") },
];

// Tokens per second offered as a minimum: a model slower than this is hidden.
const SPEED_OPTIONS = [0, 5, 10, 20, 30, 50] as const;

// A size can span hundreds of builds in a full cache: 27B alone passes 190. Ask for more
// cards than the usual page so choosing a size shows all of it, not just the first page.
const SIZE_FILTER_LIMIT = 240;

const PARAM_BANDS: { id: ParamBand; label: string }[] = [
  { id: "any", label: t("Any size") },
  { id: "le4", label: t("Up to 4B") },
  { id: "4to8", label: t("4–8B") },
  { id: "8to15", label: t("8–15B") },
  { id: "15to30", label: t("15–30B") },
  { id: "30to70", label: t("30–70B") },
  { id: "gt70", label: t("Over 70B") },
];

function ModelRow({ card, selected, onSelect }: { card: ModelCard; selected: boolean; onSelect: () => void }) {
  const { entry, fit } = card;
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-pressed={selected}
        className={cx(
          "grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-x-5 gap-y-1 border-b border-line px-4 py-3 text-left transition-colors duration-150",
          selected ? "bg-raised" : "hover:bg-surface",
        )}
      >
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="truncate font-medium text-ink">{entry.name}</span>
            <AlignmentTag alignment={entry.alignment} />
            {card.installed && (
              <Badge tone="accent">
                <Check size={10} aria-hidden /> {t("Installed")}
              </Badge>
            )}
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-ink-3">
            <span>{entry.publisher}</span>
            <span>{params(nominalParamsB(card))}</span>
            <span className="font-mono">{card.quant}</span>
            <span>{sizeLabel(card.download_size_bytes, card.size_is_estimate)}</span>
            <span className="inline-flex items-center gap-1">
              {t("Coding")} <Stars value={card.stars.coding ?? 0} label={t("Coding")} />
            </span>
            <span className="inline-flex items-center gap-1">
              {t("Reasoning")} <Stars value={card.stars.reasoning ?? 0} label={t("Reasoning")} />
            </span>
            {entry.capabilities.includes("tool_use") && <span>{t("Tool use")}</span>}
            {entry.capabilities.includes("vision") && <span>{t("Vision")}</span>}
          </div>
        </div>
        <div className="flex items-center gap-4">
          <div className="text-right">
            <div className="tabular text-sm text-ink">{fit.speed.tokens_per_second > 0 ? tps(fit.speed.tokens_per_second) : "–"}</div>
            <div className="text-2xs text-ink-3">{fit.speed.measured ? t("measured") : t("estimated")}</div>
          </div>
          <div className="w-[118px] text-right">
            <FitBadge category={fit.category} />
          </div>
        </div>
      </button>
    </li>
  );
}

function MemoryBars({ fit }: { fit: FitResult }) {
  const usable = fit.vram_usable_gb || 1;
  const tone = fitTone(fit.category);
  return (
    <div className="grid gap-3">
      {fit.placement !== "cpu" && fit.placement !== "none" && (
        <div className="grid gap-1.5">
          <div className="flex justify-between text-sm">
            <span className="text-ink-2">VRAM</span>
            <span className="tabular">
              {t("{used} / {usable} GB recommended", { used: fit.vram_required_gb.toFixed(1), usable: fit.vram_usable_gb.toFixed(1) })}
            </span>
          </div>
          <Meter value={fit.vram_required_gb / usable} tone={tone} height={8} label={t("VRAM required")} />
        </div>
      )}
      <div className="flex justify-between text-sm">
        <span className="text-ink-2">{t("System RAM")}</span>
        <span className="tabular">{fit.ram_required_gb > 0 ? gb(fit.ram_required_gb) : t("none beyond LM Studio itself")}</span>
      </div>
      <div className="flex justify-between text-sm">
        <span className="text-ink-2">{fit.speed.measured ? t("Measured generation") : t("Estimated generation")}</span>
        <span className="tabular">
          {fit.speed.tokens_per_second > 0 ? (fit.speed.measured ? tps(fit.speed.tokens_per_second) : speedRange(fit.speed.low, fit.speed.high)) : "–"}
        </span>
      </div>
    </div>
  );
}

const OTHER_FOLDER = "__other__";

function InstallPanel({ repo, file, fit, onClose }: { repo: string; file: ModelFile; fit: FitResult; onClose: () => void }) {
  const navigate = useApp((s) => s.navigate);
  const toast = useApp((s) => s.toast);
  const saveSettings = useApp((s) => s.saveSettings);
  const [busy, setBusy] = useState(false);
  // "" = the default folder; OTHER_FOLDER = the path typed or browsed below.
  const [choice, setChoice] = useState("");
  const [typed, setTyped] = useState("");
  const [remember, setRemember] = useState(false);
  const typedFolder = useDebounced(typed.trim(), 400);
  const folder = choice === OTHER_FOLDER ? typedFolder : choice;
  const storage = useLoad(() => api.get<{ locations: StorageLocation[] }>("/api/storage"), []);
  const body = { repo, filename: file.filename, quant: file.quant, base_dir: folder || null };
  const waitingForPath = choice === OTHER_FOLDER && !typedFolder;
  const plan = useLoad(() => (waitingForPath ? Promise.resolve(null) : api.post<DownloadPlan>("/api/downloads/plan", body)), [repo, file.filename, folder, waitingForPath]);

  const start = () => {
    setBusy(true);
    api
      .post("/api/downloads", body)
      .then(async () => {
        if (remember && folder) await saveSettings({ storage: { custom_model_dir: folder } });
        toast({ tone: "ok", title: t("Download started"), body: file.filename });
        navigate("downloads");
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.kind === "unsafe_download") plan.reload();
        toast({ tone: "bad", title: t("Download not started"), body: errorMessage(err) });
      })
      .finally(() => setBusy(false));
  };
  const browse = () => {
    void pickFolder(typed || plan.data?.default_folder || undefined).then((picked) => {
      if (picked) {
        setChoice(OTHER_FOLDER);
        setTyped(picked);
      }
    });
  };

  const info = plan.data;
  const p = info?.plan;
  const defaultFolder = info?.default_folder ?? null;
  const others = (storage.data?.locations ?? []).filter((loc) => loc.path.toLowerCase() !== (defaultFolder ?? "").toLowerCase());
  const stoppable = info?.backend !== "lmstudio";
  return (
    <div className="rise-in rounded-lg border border-line-strong bg-surface p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-sm font-semibold">{t("Install {quant}", { quant: file.quant ?? "" })}</div>
          <div className="selectable mt-0.5 font-mono text-xs break-all text-ink-3">{file.filename}</div>
        </div>
        <IconButton label={t("Close")} onClick={onClose}>
          <X size={14} />
        </IconButton>
      </div>

      <div className="mt-3 grid gap-1.5">
        <label htmlFor="install-folder" className="text-xs font-medium text-ink-2">
          {t("Save to")}
        </label>
        <div className="flex gap-2">
          <Select id="install-folder" value={choice} onChange={(e) => setChoice(e.target.value)} className="min-w-0 flex-1 font-mono text-xs">
            <option value="">{defaultFolder ? `${defaultFolder}${defaultFolder === info?.lmstudio_folder ? ` (${t("LM Studio's folder")})` : ` (${t("default")})`}` : t("Default folder")}</option>
            {others.map((loc) => (
              <option key={loc.path} value={loc.path}>
                {loc.path} · {t("{n} GB safely usable", { n: loc.safe_available_gb.toFixed(0) })}
              </option>
            ))}
            <option value={OTHER_FOLDER}>{t("Another folder...")}</option>
          </Select>
          {canBrowseFolders && (
            <Button icon={<FolderOpen size={13} />} onClick={browse}>
              {t("Browse")}
            </Button>
          )}
        </div>
        {choice === OTHER_FOLDER && (
          <TextInput value={typed} onChange={(e) => setTyped(e.target.value)} placeholder="D:\AI\Models" aria-label={t("Folder path")} className="font-mono text-xs" autoFocus />
        )}
        {info?.outside_lmstudio && (
          <p className="text-xs text-ink-3">
            {t("LM Studio only looks in its own folder ({folder}). The file stays where you choose and Will It Run adds a link there, so LM Studio still lists the model.", { folder: info.lmstudio_folder ?? "" })}
          </p>
        )}
        {folder && (
          <label className="mt-0.5 inline-flex items-center gap-2 text-xs text-ink-2">
            <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} className="accent-[var(--accent)]" />
            {t("Use this folder for future downloads too")}
          </label>
        )}
      </div>

      {plan.loading && !p && <div className="mt-3"><SkeletonRows rows={3} height={18} /></div>}
      {plan.error && <div className="mt-3"><Note tone="bad">{plan.error}</Note></div>}
      {waitingForPath && <p className="mt-3 text-sm text-ink-2">{t("Type a folder path, for example D:\\AI\\Models.")}</p>}
      {p && !waitingForPath && (
        <>
          <dl className={cx("mt-3 grid grid-cols-[1fr_auto] gap-y-1.5 text-sm", plan.loading && "opacity-60")}>
            <dt className="text-ink-2">
              {t("Download size")}
              {file.size_bytes != null && p.download_gb * 1024 ** 3 > file.size_bytes + 50 * 1024 ** 2 && <span className="text-ink-3"> {t("(model + vision projector)")}</span>}
            </dt>
            <dd className="tabular text-right">{p.download_gb.toFixed(1)} GB</dd>
            <dt className="text-ink-2">{t("Temporary storage recommended")}</dt>
            <dd className="tabular text-right">{p.temp_gb.toFixed(1)} GB</dd>
            <dt className="text-ink-2">{t("Available on {drive}", { drive: p.drive })}</dt>
            <dd className="tabular text-right">
              {p.free_gb.toFixed(0)} GB <span className="text-ink-3">({t("{n} GB safely usable", { n: p.safe_available_gb.toFixed(0) })})</span>
            </dd>
            <dt className="text-ink-2">{t("After installation")}</dt>
            <dd className="tabular text-right">{t("{n} GB free", { n: p.after_install_free_gb.toFixed(0) })}</dd>
            <dt className="text-ink-2">{t("File location")}</dt>
            <dd className="selectable max-w-[280px] truncate text-right font-mono text-xs text-ink-3" title={p.target_dir}>{p.target_dir}</dd>
          </dl>
          <div className="mt-3">
            {p.safe ? (
              <Note tone="ok">
                <span className="font-medium text-ink">{t("Safe to install.")}</span> {t("Fit on this PC: {category}.", { category: fitLabel(fit.category) })} <HelpTip id="safety" />
              </Note>
            ) : (
              <Note tone="bad">
                <div className="font-medium text-ink">{t("Do not download.")}</div>
                <div className="mt-0.5">{p.reason || p.verdict}</div>
                <ul className="mt-2 grid gap-0.5">
                  {p.options.map((option) => (
                    <li key={option}>· {option}</li>
                  ))}
                </ul>
              </Note>
            )}
          </div>
          <div className="mt-3 flex items-center justify-between gap-3">
            <span className="text-xs text-ink-3">
              {stoppable ? t("You can pause or stop it at any time from Downloads.") : t("Uses LM Studio's own downloader: it cannot be paused or stopped from Will It Run.")}
            </span>
            <Button variant="primary" icon={<Download size={13} />} disabled={!p.safe || plan.loading} loading={busy} onClick={start}>
              {t("Download")}
            </Button>
          </div>
        </>
      )}
    </div>
  );
}

type DetailTab = "overview" | "compatibility" | "files" | "benchmarks" | "capabilities" | "card" | "license";

function DetailPanel({ repo, initial, onClose }: { repo: string; initial: ModelCard | null; onClose: () => void }) {
  const mode = useApp((s) => s.settings?.ui.mode ?? "guided");
  const toast = useApp((s) => s.toast);
  const navigate = useApp((s) => s.navigate);
  const live = useApp((s) => s.live);
  const [tab, setTab] = useState<DetailTab>("overview");
  const [installing, setInstalling] = useState<ModelFile | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const details = useLoad(() => api.get<ModelDetails>(`/api/models/details${query({ repo })}`), [repo]);

  useEffect(() => {
    setTab("overview");
    setInstalling(null);
  }, [repo]);

  const card = details.data?.card ?? initial;
  const entry = details.data?.entry ?? initial?.entry;
  if (!card || !entry) {
    return (
      <aside className="w-[460px] shrink-0 border-l border-line bg-panel p-5">
        {details.error ? <Note tone="bad">{details.error}</Note> : <SkeletonRows rows={6} height={28} />}
      </aside>
    );
  }
  const fit = card.fit;
  const installedKey = details.data?.installed[0]?.model_key ?? card.installed_key;
  const loadedInstance = live?.loaded.find((l) => l.model_key === installedKey);
  // Several builds can share a quantization label, so match the exact recommended file first.
  const recommendedFile =
    details.data?.files.find((f) => f.file.filename === details.data?.recommended_file && f.file.quant === card.quant)?.file ??
    details.data?.files.find((f) => f.file.quant === card.quant)?.file ??
    card.file;

  const act = (name: string, run: () => Promise<unknown>, done: string) => {
    setBusy(name);
    run()
      .then(() => {
        toast({ tone: "ok", title: done });
        void useApp.getState().loadLive();
      })
      .catch((err: unknown) => toast({ tone: "bad", title: t("Action failed"), body: errorMessage(err) }))
      .finally(() => setBusy(null));
  };

  const tabs: { value: DetailTab; label: string }[] = [
    { value: "overview", label: t("Overview") },
    { value: "compatibility", label: t("Compatibility") },
    { value: "files", label: t("Files") },
    { value: "benchmarks", label: t("Benchmarks") },
    { value: "capabilities", label: t("Capabilities") },
    { value: "card", label: t("Model Card") },
    { value: "license", label: t("License") },
  ];

  return (
    <aside className="flex w-[560px] shrink-0 flex-col border-l border-line bg-panel">
      <div className="flex items-start justify-between gap-3 px-5 pt-5">
        <div className="min-w-0">
          <h2 className="selectable text-lg leading-snug font-semibold tracking-tight break-words">{entry.name}</h2>
          <div className="selectable mt-0.5 text-xs break-all text-ink-3">{entry.repo}</div>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <FitBadge category={fit.category} solid />
            <Badge tone={entry.alignment === "uncensored" ? "warn" : "off"}>{entry.alignment === "uncensored" ? t("Uncensored / Abliterated") : t("Standard")}</Badge>
            {card.installed && <Badge tone="accent">{t("Installed")}</Badge>}
            {loadedInstance && <Badge tone="ok">{t("Loaded")}</Badge>}
          </div>
        </div>
        <IconButton label={t("Close details")} onClick={onClose}>
          <X size={15} />
        </IconButton>
      </div>

      <div className="mt-4 flex flex-wrap gap-2 px-5">
        {installedKey ? (
          <>
            {loadedInstance ? (
              <Button loading={busy === "unload"} onClick={() => act("unload", () => api.post("/api/models/unload", { instance_id: loadedInstance.instance_id }), t("Model unloaded"))}>
                {t("Unload")}
              </Button>
            ) : (
              <Button variant="primary" loading={busy === "load"} onClick={() => act("load", () => api.post("/api/models/load", { model_key: installedKey, context_length: fit.context_length }), t("Model loaded in LM Studio"))}>
                {t("Load in LM Studio")}
              </Button>
            )}
            <Button onClick={() => navigate("benchmarks", { model: installedKey })}>{t("Benchmark")}</Button>
          </>
        ) : (
          <Button
            variant="primary"
            icon={<Download size={13} />}
            disabled={!recommendedFile?.filename || !details.data}
            onClick={() => recommendedFile && setInstalling(recommendedFile)}
          >
            {t("Download {quant}", { quant: card.quant })} · {sizeLabel(card.download_size_bytes, card.size_is_estimate)}
          </Button>
        )}
      </div>

      {installing && (
        <div className="mt-3 px-5">
          <InstallPanel repo={entry.repo} file={installing} fit={details.data?.files.find((f) => f.file.filename === installing.filename)?.fit ?? fit} onClose={() => setInstalling(null)} />
        </div>
      )}

      <div className="mt-4 overflow-x-auto px-5">
        <Tabs value={tab} tabs={tabs} onChange={setTab} />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        {details.data && !details.data.online && (
          <div className="mb-3">
            <Note tone="warn">{t("Offline: showing cached metadata. Exact file sizes and downloads need an Internet connection.")}</Note>
          </div>
        )}

        {tab === "overview" && (
          <div className="grid gap-5">
            <Section title={fit.category === "IMPOSSIBLE" || fit.category === "NOT RECOMMENDED" ? t("Why Will It Run does not recommend this model") : t("Why Will It Run recommends this model")}>
              <WhyList lines={card.why} />
            </Section>
            {(card.quant_reasoning.length > 0 || card.accuracy) && (
              <Section
                title={
                  <>
                    {t("Why {quant}?", { quant: card.quant })} <HelpTip id="quant" />
                  </>
                }
              >
                <ul className="selectable grid gap-1.5 text-sm text-ink-2">
                  {card.quant_reasoning.map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
                {card.accuracy && (
                  <div className="mt-2.5 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-sm">
                    <span className="text-ink-2">{t("Accuracy at {quant}", { quant: card.quant })}</span>
                    <AccuracyMeter accuracy={card.accuracy} />
                    <button type="button" className="text-xs text-accent hover:underline" onClick={() => setTab("files")}>
                      {t("Compare all files")}
                    </button>
                  </div>
                )}
              </Section>
            )}
            <Section
              title={
                <>
                  {t("Will It Run score")} <HelpTip id="score" />
                </>
              }
              aside={`${card.score.agentfit_score.toFixed(1)} / 10`}
            >
              <ScoreRows card={card} />
            </Section>
          </div>
        )}

        {tab === "compatibility" && (
          <div className="grid gap-5">
            <Section title={t("On your computer")} aside={`${card.quant} · ${t("{n} context", { n: contextLabel(fit.context_length) })}`}>
              <MemoryBars fit={fit} />
            </Section>
            <Section title={t("Memory breakdown")} aside={<ConfidenceTag value={fit.memory.confidence} />}>
              <dl className="grid grid-cols-[1fr_auto] gap-y-1.5 text-sm">
                <dt className="text-ink-2">{t("Model weights")} <span className="text-ink-3">({fit.memory.weights_source === "file" ? t("real file size") : t("estimated from parameters")})</span></dt>
                <dd className="tabular text-right">{gb(fit.memory.weights_gb)}</dd>
                <dt className="text-ink-2">{t("Context reservation (KV cache)")} <span className="text-ink-3">({fit.memory.kv_source === "architecture" ? t("exact") : t("approximate")})</span></dt>
                <dd className="tabular text-right">{gb(fit.memory.kv_cache_gb)}</dd>
                {fit.memory.vision_projector_gb > 0 && (
                  <>
                    <dt className="text-ink-2">{t("Vision projector")}</dt>
                    <dd className="tabular text-right">{gb(fit.memory.vision_projector_gb)}</dd>
                  </>
                )}
                <dt className="text-ink-2">{t("Runtime overhead")}</dt>
                <dd className="tabular text-right">{gb(fit.memory.runtime_overhead_gb)}</dd>
                <dt className="border-t border-line pt-1.5 font-medium">{t("Total")}</dt>
                <dd className="tabular border-t border-line pt-1.5 text-right font-medium">{gb(fit.memory.total_gb)}</dd>
              </dl>
            </Section>
            <Section title={t("Performance")} aside={<ConfidenceTag value={fit.speed.confidence} />}>
              <KeyValue
                rows={[
                  [fit.speed.measured ? t("Measured speed") : t("Estimated speed"), fit.speed.tokens_per_second > 0 ? tps(fit.speed.tokens_per_second) : "–"],
                  [t("Prompt processing"), fit.speed.prompt_tokens_per_second ? `~${Math.round(fit.speed.prompt_tokens_per_second)} tok/s (${t("LOW confidence")})` : UNKNOWN],
                  [t("Time to first token"), fit.speed.ttft_ms ? `~${Math.round(fit.speed.ttft_ms)} ms (${t("LOW confidence")})` : UNKNOWN],
                  [t("Placement"), placementLabel(fit)],
                  ...(mode === "expert" ? ([[t("Method"), fit.speed.method]] as [string, string][]) : []),
                ]}
              />
              {fit.speed.notes.map((note) => (
                <p key={note} className="mt-2 text-xs text-ink-3">{note}</p>
              ))}
            </Section>
          </div>
        )}

        {tab === "files" && (
          <FilesTab details={details.data} loading={details.loading} expert={mode === "expert"} onInstall={setInstalling} />
        )}

        {tab === "benchmarks" && (
          <div className="grid gap-3">
            {(details.data?.benchmarks.length ?? 0) === 0 ? (
              <>
                <p className="text-sm text-ink-2">{t("Not measured on this PC yet. Speed above is an estimate.")}</p>
                {installedKey && (
                  <div>
                    <Button onClick={() => navigate("benchmarks", { model: installedKey })}>{t("Benchmark this model")}</Button>
                  </div>
                )}
              </>
            ) : (
              <ul className="border-t border-line">
                {details.data?.benchmarks.map((b) => (
                  <li key={b.id} className="grid grid-cols-4 gap-3 border-b border-line py-2.5 text-sm">
                    <div>
                      <div className="tabular font-medium">{tps(b.generation_tps)}</div>
                      <div className="text-2xs text-ink-3">{t("generation")}</div>
                    </div>
                    <div>
                      <div className="tabular">{b.ttft_ms != null ? `${Math.round(b.ttft_ms)} ms` : "–"}</div>
                      <div className="text-2xs text-ink-3">{t("first token")}</div>
                    </div>
                    <div>
                      <div className="tabular">{b.vram_used_mb != null ? `${(b.vram_used_mb / 1024).toFixed(1)} GB` : "–"}</div>
                      <div className="text-2xs text-ink-3">VRAM</div>
                    </div>
                    <div className="text-right text-xs text-ink-3">{new Date(b.created_at).toLocaleString()}</div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {tab === "capabilities" && (
          <div className="grid gap-5">
            <Section title={t("Task strength")} aside={<ConfidenceTag value={entry.quality.confidence} />}>
              <ul className="grid gap-2">
                {(["coding", "reasoning", "writing"] as const).map((task) => (
                  <li key={task} className="grid grid-cols-[90px_1fr_36px] items-center gap-3 text-sm">
                    <span className="text-ink-2">{TASK_LABEL[task]}</span>
                    <Meter value={entry.quality[task] / 100} tone="off" label={t("{task} strength", { task: TASK_LABEL[task] })} />
                    <span className="tabular text-right text-ink-3">{entry.quality[task] > 0 ? Math.round(entry.quality[task]) : "?"}</span>
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-xs text-ink-3">
                {entry.quality.basis === "unknown"
                  ? t("Unknown: the parameter count is not available, so no estimate is made.")
                  : t("An estimate from model family and size, not a benchmark run. Benchmark the model locally for measured speed.")}
              </p>
            </Section>
            <Section title={t("Capabilities")}>
              <KeyValue
                rows={[
                  [t("Tool use"), entry.capabilities.includes("tool_use") ? t("Supported") : t("Not confirmed")],
                  [t("Vision"), entry.capabilities.includes("vision") ? t("Accepts images") : t("No")],
                  [t("Reasoning"), entry.capabilities.includes("reasoning") ? t("Has a thinking mode") : t("Not declared")],
                  [t("Context length"), entry.context_length ? t("{n} tokens", { n: entry.context_length.toLocaleString() }) : UNKNOWN],
                  [t("Categories"), entry.categories.map(categoryLabel).join(", ") || UNKNOWN],
                ]}
              />
            </Section>
          </div>
        )}

        {tab === "card" && (
          <div className="grid gap-4">
            <KeyValue
              rows={[
                [t("Publisher"), entry.publisher],
                [t("Repository"), entry.repo],
                [t("Model family"), entry.family ?? UNKNOWN],
                [t("Fine-tune lineage"), entry.base_models.length ? entry.base_models.join(" → ") : UNKNOWN],
                [t("Quantizer"), entry.quantizer ?? UNKNOWN],
                [t("Architecture"), entry.architecture ?? UNKNOWN],
                [t("Parameters"), entry.params_b ? `${params(entry.params_b)}${entry.active_params_b ? ` (${t("{n} active per token", { n: params(entry.active_params_b) })})` : ""}` : UNKNOWN],
                [t("Quantization"), card.quant],
                [t("Download size"), sizeLabel(card.download_size_bytes, card.size_is_estimate)],
                [t("Downloads"), compactNumber(entry.downloads)],
                [t("Revision"), entry.revision ? entry.revision.slice(0, 12) : UNKNOWN],
                [t("Source"), entry.source === "huggingface" ? "Hugging Face" : entry.source === "lmstudio" ? t("LM Studio (local)") : t("Built-in catalog")],
              ]}
            />
            {entry.alignment === "uncensored" && (
              <Note tone="warn">
                {t("Labelled uncensored / abliterated: {evidence}. Will It Run ranks it by the same criteria as any other model.", { evidence: entry.alignment_evidence.join("; ") })}
              </Note>
            )}
            <div className="flex items-center gap-3 text-sm">
              <ConfidenceTag value={entry.metadata_confidence} />
              {entry.unknown_fields.length > 0 && <span className="text-ink-3">{t("Unknown: {fields}", { fields: entry.unknown_fields.join(", ") })}</span>}
            </div>
            {!entry.repo.startsWith("lmstudio/") && (
              <div>
                <Button icon={<ExternalLink size={13} />} onClick={() => void openExternal(entry.model_card_url ?? `https://huggingface.co/${entry.repo}`)}>
                  {t("Open model card on Hugging Face")}
                </Button>
              </div>
            )}
          </div>
        )}

        {tab === "license" && (
          <div className="grid gap-3">
            <KeyValue rows={[[t("License"), entry.license ?? UNKNOWN]]} />
            <p className="max-w-[60ch] text-sm text-ink-2">
              {entry.license
                ? t("This is the license declared on the model's repository. Read the full terms on the model card before using the model commercially.")
                : t("The repository does not declare a license. Check the model card and its base model before relying on it.")}
            </p>
          </div>
        )}
      </div>
    </aside>
  );
}

type FileSort = "score" | "accuracy" | "size";

/** The part every file name of a repository starts with, cut at a "-" so no word is split. */
function sharedPrefix(names: string[]): string {
  if (names.length < 2) return "";
  let prefix = names[0] ?? "";
  for (const name of names) {
    while (prefix && !name.startsWith(prefix)) prefix = prefix.slice(0, -1);
  }
  return prefix.slice(0, prefix.lastIndexOf("-") + 1);
}

/**
 * What tells one file from the others: "UD-Q3_K_XL", "GSQ-RCO-IQ3_XXS-mtp". Repositories
 * often ship several builds of the same quantization, so the bare label is not enough.
 */
function fileLabel(file: ModelFile, prefix: string): string {
  const rest = file.filename.slice(prefix.length).replace(/\.gguf$/i, "");
  const quant = file.quant ?? "";
  if (!rest || !prefix) return quant || rest;
  return rest.toUpperCase().includes(quant.toUpperCase()) ? rest : `${rest} · ${quant}`;
}

/** Every file of the repository with its own Will It Run score and accuracy, so any of them can be chosen knowingly. */
function FilesTab({ details, loading, expert, onInstall }: { details: ModelDetails | null; loading: boolean; expert: boolean; onInstall: (file: ModelFile) => void }) {
  const [sort, setSort] = useState<FileSort>("score");
  const rows = useMemo(() => {
    const list = [...(details?.files ?? [])];
    const size = (row: ModelFileRow) => row.file.size_bytes ?? 0;
    if (sort === "score") list.sort((a, b) => b.score.total - a.score.total || size(b) - size(a));
    else if (sort === "accuracy") list.sort((a, b) => b.accuracy.level - a.accuracy.level || (a.accuracy.ppl_delta ?? 99) - (b.accuracy.ppl_delta ?? 99) || size(b) - size(a));
    else list.sort((a, b) => size(a) - size(b));
    return list;
  }, [details, sort]);

  if (loading && !details) return <SkeletonRows rows={5} height={64} />;
  if (!details) return null;
  if (rows.length === 0) {
    return <p className="text-sm text-ink-2">{details.online ? t("The exact file list is not available for this repository.") : t("The exact file list is not available while offline.")}</p>;
  }
  const best = Math.max(...rows.map((row) => row.score.total));
  const prefix = sharedPrefix(rows.map((row) => row.file.filename));
  return (
    <div>
      <p className="max-w-[60ch] text-sm text-ink-2">
        {t("Each file is the same model at a different quantization. The score says how good a choice it is on this PC; accuracy says how much of the original quality it keeps.")}
      </p>
      <div className="mt-3 mb-2 flex items-center justify-between gap-3">
        <span className="text-xs text-ink-3">
          {t("Score")} <HelpTip id="score" /> · {t("Accuracy")} <HelpTip id="accuracy" /> · {t("Quantization")} <HelpTip id="quant" />
        </span>
        <Segmented
          label={t("Sort files by")}
          value={sort}
          onChange={setSort}
          options={[
            { value: "score", label: t("Score") },
            { value: "accuracy", label: t("Accuracy") },
            { value: "size", label: t("Size") },
          ]}
        />
      </div>
      <ul className="border-t border-line">
        {rows.map(({ file, fit, score, accuracy, installed }) => (
          <li key={file.filename} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 border-b border-line py-3">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="selectable font-mono text-sm font-medium break-all" title={file.filename}>
                  {fileLabel(file, prefix)}
                </span>
                <FitBadge category={fit.category} />
                {file.filename === details.recommended_file && <Badge tone="accent">{t("RECOMMENDED")}</Badge>}
                {installed && <span className="text-2xs text-ink-3">{t("installed")}</span>}
              </div>
              <div className="tabular mt-1 text-xs text-ink-3">
                {sizeLabel(file.size_bytes)} · {gb(fit.vram_required_gb)} VRAM
                {fit.ram_required_gb > 0 ? ` + ${gb(fit.ram_required_gb)} RAM` : ""} ·{" "}
                {fit.speed.tokens_per_second > 0 ? `${fit.speed.measured ? "" : "~"}${Math.round(fit.speed.tokens_per_second)} tok/s` : t("does not run")}
              </div>
              <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-3">
                <span>{t("Accuracy")}</span>
                <AccuracyMeter accuracy={accuracy} />
                {accuracy.ppl_delta != null && accuracy.basis !== "original" && (
                  <span className="tabular" title={accuracy.note}>
                    ({pplLabel(accuracy)} ppl)
                  </span>
                )}
              </div>
              {expert && (
                <div className="selectable mt-1 font-mono text-2xs break-all text-ink-3">
                  {file.filename}
                  {file.sha256 ? ` · sha256 ${file.sha256.slice(0, 12)}…` : ""}
                  {accuracy.bits_per_weight ? ` · ${accuracy.bits_per_weight.toFixed(1)} bpw` : ""}
                </div>
              )}
            </div>
            <div className="flex items-center gap-4">
              <div className="w-[68px] text-right" title={t("Will It Run score for this file on this PC")}>
                <div className="tabular leading-none">
                  <span className={cx("text-lg font-semibold", score.total === best ? "text-ink" : "text-ink-2")}>{score.agentfit_score.toFixed(1)}</span>
                  <span className="text-xs text-ink-3"> / 10</span>
                </div>
                <div className="mt-1.5">
                  <Meter value={score.total / 100} tone={score.total === best ? "accent" : "off"} height={4} label={t("Will It Run score")} />
                </div>
              </div>
              <Button size="sm" disabled={installed} onClick={() => onInstall(file)}>
                {installed ? t("Installed") : t("Download")}
              </Button>
            </div>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-xs text-ink-3">
        {t("Accuracy is the quality loss the llama.cpp project measured for each quantization on a reference 8B model, not a test of this model.")}{" "}
        <button type="button" className="text-accent hover:underline" onClick={() => useApp.getState().navigate("guide", { term: "quant" })}>
          {t("See the full table")}
        </button>
      </p>
    </div>
  );
}

function ScoreRows({ card }: { card: ModelCard }) {
  const rows: [string, number, string?][] = [
    [t("Hardware fit"), card.score.hardware_fit],
    [t("Task quality"), card.score.task_quality],
    [t("Specialization"), card.score.specialization],
    [t("Speed"), card.score.speed],
    [t("Context"), card.score.context],
    [t("Tool use"), card.score.tool_use],
    [t("Reliability"), card.score.reliability],
    [t("Local benchmark"), card.score.local_benchmark, card.measured_tps ? undefined : t("not measured")],
  ];
  return (
    <div className="grid gap-1.5">
      {rows.map(([label, value, note]) => (
        <div key={label} className="grid grid-cols-[110px_1fr_auto] items-center gap-3 text-xs">
          <span className="text-ink-2">{label}</span>
          <Meter value={value / 100} tone="off" height={4} label={label} />
          <span className="tabular w-[84px] text-right text-ink-3">{note ?? Math.round(value)}</span>
        </div>
      ))}
      {card.score.preference_bonus > 0 && (
        <p className="mt-1 text-xs text-ink-3">{t("+{n} for matching your model preference in Settings.", { n: card.score.preference_bonus })}</p>
      )}
    </div>
  );
}

export function ModelsPage() {
  const routeParams = useApp((s) => s.params);
  const [text, setText] = useState("");
  const [alignment, setAlignment] = useState<AlignmentFilter>((routeParams.alignment as AlignmentFilter) ?? "any");
  const [active, setActive] = useState<Set<string>>(new Set(routeParams.installed ? ["installed"] : routeParams.recommended ? ["recommended"] : []));
  const [selected, setSelected] = useState<string | null>(routeParams.repo ?? null);
  const [sort, setSort] = useState<ModelSort>("recommended");
  const [minSpeed, setMinSpeed] = useState(0);
  const [quant, setQuant] = useState("");
  const [paramBand, setParamBand] = useState<ParamBand>("any");
  const [paramsExact, setParamsExact] = useState<number | null>(null);
  const debounced = useDebounced(text, 350);

  useEffect(() => {
    if (routeParams.alignment) setAlignment(routeParams.alignment as AlignmentFilter);
    if (routeParams.installed) setActive(new Set(["installed"]));
    else if (routeParams.recommended) setActive(new Set(["recommended"]));
    if (routeParams.repo) setSelected(routeParams.repo);
  }, [routeParams]);

  const filterKey = [...active].sort().join(",");
  // A size filter searches the whole catalog, so "27B" shows every 27B model, not the two
  // that happen to be in the first page. Bands travel as a range; an exact size as itself.
  const sizeFilterActive = paramsExact !== null || paramBand !== "any";
  const bounds = paramBandBounds(paramBand);
  const results = useLoad(
    () =>
      api.post<SearchResponse>("/api/models/search", {
        query: debounced,
        alignment: alignment === "any" ? null : alignment,
        categories: [...active].filter((f) => !["recommended", "installed", "tool_use"].includes(f)),
        installed_only: active.has("installed"),
        recommended_only: active.has("recommended"),
        tool_use: active.has("tool_use"),
        params_exact_b: paramsExact,
        params_min_b: paramsExact === null ? bounds.min : null,
        params_max_b: paramsExact === null ? bounds.max : null,
        limit: sizeFilterActive ? SIZE_FILTER_LIMIT : 60,
      }),
    [debounced, alignment, filterKey, paramBand, paramsExact],
  );
  useBus("download.update", (event) => {
    if (event.data.status === "completed") results.reload();
  });

  const toggle = (id: string) => {
    const next = new Set(active);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setActive(next);
  };

  const cards = useMemo(() => results.data?.cards ?? [], [results.data]);
  const selectedCard = useMemo(() => cards.find((c) => c.entry.repo === selected) ?? null, [cards, selected]);
  const quantOptions = useMemo(() => quantizationOptions(cards), [cards]);
  useEffect(() => {
    if (quant && !quantOptions.includes(quant)) setQuant("");
  }, [quant, quantOptions]);
  const sizeOptions = useMemo(() => {
    // The core lists every named size in the catalog; without it (an older core), fall back
    // to the sizes of the cards on screen so the menu still works.
    const facets = results.data?.sizes?.map((facet) => facet.params_b) ?? [];
    return facets.length > 0 ? facets : parameterSizes(cards);
  }, [results.data, cards]);
  useEffect(() => {
    if (paramsExact !== null && !sizeOptions.includes(paramsExact)) {
      setParamsExact(null);
      setParamBand("any");
    }
  }, [paramsExact, sizeOptions]);
  const visibleCards = useMemo(
    () => filterModels(cards, { sort, minTokensPerSecond: minSpeed, quant: quant || null, params: paramBand, paramsExact }),
    [cards, sort, minSpeed, quant, paramBand, paramsExact],
  );
  const filtering = sort !== "recommended" || minSpeed > 0 || quant !== "" || paramBand !== "any" || paramsExact !== null;
  const chooseSize = (value: string) => {
    if (value.startsWith("exact:")) {
      const size = Number(value.slice("exact:".length));
      setParamsExact(Number.isFinite(size) ? size : null);
      setParamBand("any");
    } else {
      setParamsExact(null);
      setParamBand(value as ParamBand);
    }
  };
  const clearFilters = () => {
    setSort("recommended");
    setMinSpeed(0);
    setQuant("");
    setParamBand("any");
    setParamsExact(null);
  };

  return (
    <div className="flex min-h-0 flex-1">
      <div className="flex min-w-0 flex-1 flex-col">
        <PageHeader title={t("Explore Models")} subtitle={t("Real models from Hugging Face, the built-in catalog and LM Studio, ranked by how well they run on this PC.")} />
        <div className="grid shrink-0 gap-3 border-b border-line px-7 py-4">
          <div className="relative">
            <Search size={14} className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-ink-3" aria-hidden />
            <TextInput
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder={t('Search models, e.g. "Qwen uncensored 27B" or "best coding model for 16GB VRAM"')}
              aria-label={t("Search models")}
              className="h-9 pl-9"
            />
          </div>
          <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
            <div className="flex flex-wrap gap-1.5">
              {FILTERS.map((filter) => (
                <Chip key={filter.id} active={active.has(filter.id)} onClick={() => toggle(filter.id)}>
                  {filter.label}
                </Chip>
              ))}
              <Chip active={alignment === "uncensored"} onClick={() => setAlignment(alignment === "uncensored" ? "any" : "uncensored")}>
                {t("Uncensored")}
              </Chip>
            </div>
            <div className="flex items-center gap-2 text-xs text-ink-3">
              {t("Safety alignment")}
              <Segmented
                label={t("Safety alignment")}
                value={alignment}
                onChange={setAlignment}
                options={[
                  { value: "any", label: t("Any") },
                  { value: "standard", label: t("Standard") },
                  { value: "uncensored", label: t("Uncensored / Abliterated") },
                ]}
              />
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-line pt-3">
            <div className="flex items-center gap-2 text-xs text-ink-3">
              <span>{t("Sort by")}</span>
              <Segmented
                label={t("Sort by")}
                value={sort}
                onChange={setSort}
                options={[
                  { value: "recommended", label: t("Recommended") },
                  { value: "fastest", label: t("Fastest") },
                ]}
              />
            </div>
            <label className="flex items-center gap-2 whitespace-nowrap text-xs text-ink-3">
              {t("Minimum speed")}
              <Select
                value={minSpeed}
                onChange={(e) => setMinSpeed(Number(e.target.value))}
                aria-label={t("Minimum speed")}
                className="h-7 w-[112px] text-xs"
              >
                {SPEED_OPTIONS.map((value) => (
                  <option key={value} value={value}>
                    {value === 0 ? t("Any speed") : t("{n}+ tok/s", { n: value })}
                  </option>
                ))}
              </Select>
            </label>
            <label className="flex items-center gap-2 whitespace-nowrap text-xs text-ink-3">
              {t("Quantization")}
              <Select
                value={quant}
                onChange={(e) => setQuant(e.target.value)}
                aria-label={t("Quantization")}
                className="h-7 w-[136px] text-xs"
                disabled={quantOptions.length === 0}
              >
                <option value="">{t("Any quantization")}</option>
                {quantOptions.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </Select>
            </label>
            <label className="flex items-center gap-2 whitespace-nowrap text-xs text-ink-3">
              {t("Parameters")}
              <Select
                value={paramsExact !== null ? `exact:${paramsExact}` : paramBand}
                onChange={(e) => chooseSize(e.target.value)}
                aria-label={t("Parameters")}
                className="h-7 w-[150px] text-xs"
              >
                <option value="any">{t("Any size")}</option>
                {sizeOptions.length > 0 && (
                  <optgroup label={t("Exact size")}>
                    {sizeOptions.map((size) => (
                      <option key={size} value={`exact:${size}`}>
                        {params(size)}
                      </option>
                    ))}
                  </optgroup>
                )}
                <optgroup label={t("Size range")}>
                  {PARAM_BANDS.filter((band) => band.id !== "any").map((band) => (
                    <option key={band.id} value={band.id}>
                      {band.label}
                    </option>
                  ))}
                </optgroup>
              </Select>
            </label>
            {filtering && (
              <Button size="sm" variant="ghost" onClick={clearFilters}>
                {t("Clear filters")}
              </Button>
            )}
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {results.data && (
            <div className="flex items-center justify-between px-7 pt-3 pb-1 text-xs text-ink-3">
              <span>
                {t("{shown} shown of {total} matching", { shown: visibleCards.length, total: results.data.total_candidates })}
                {filtering && cards.length !== visibleCards.length && (
                  <span className="text-ink-3"> · {t("{n} hidden by filters", { n: cards.length - visibleCards.length })}</span>
                )}
              </span>
              <span className="inline-flex items-center gap-1.5">
                {results.data.online ? (
                  t("Live results from Hugging Face")
                ) : results.data.hub_unreachable ? (
                  <>
                    <WifiOff size={12} aria-hidden /> {t("Offline: cached and built-in models")}
                  </>
                ) : (
                  t("Built-in catalog, cache and installed models. Type to search Hugging Face live.")
                )}
              </span>
            </div>
          )}
          {results.data?.notes.map((note) => (
            <div key={note} className="px-7 pt-2">
              <Note tone="warn">{note}</Note>
            </div>
          ))}
          {results.error && (
            <div className="px-7 pt-4">
              <Note tone="bad" action={<Button size="sm" onClick={results.reload}>{t("Retry")}</Button>}>{results.error}</Note>
            </div>
          )}
          {results.loading && !results.data && <div className="px-7 pt-4"><SkeletonRows rows={8} height={56} /></div>}
          {results.data && visibleCards.length === 0 && !results.loading && (
            <div className="px-7">
              {filtering ? (
                <EmptyState
                  title={t("No models match these filters")}
                  action={
                    <Button size="sm" onClick={clearFilters}>
                      {t("Clear filters")}
                    </Button>
                  }
                >
                  {t("Try a lower minimum speed, another quantization or a different size.")}
                </EmptyState>
              ) : (
                <EmptyState title={t("No models match")}>
                  Try fewer words or clear a filter. Model names come from repository names, so "abliterated" and "uncensored" find different builds; the Uncensored filter searches for both.
                </EmptyState>
              )}
            </div>
          )}
          <ul className={cx("mt-1 border-t border-line", results.loading && "opacity-60 transition-opacity duration-150")}>
            {visibleCards.map((card) => (
              <ModelRow key={`${card.entry.repo}|${card.installed_key ?? ""}`} card={card} selected={card.entry.repo === selected} onSelect={() => setSelected(card.entry.repo)} />
            ))}
          </ul>
        </div>
      </div>
      {selected && <DetailPanel repo={selected} initial={selectedCard} onClose={() => setSelected(null)} />}
    </div>
  );
}
