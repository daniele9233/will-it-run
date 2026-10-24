import { FolderOpen, Pause, Play, RotateCcw, Square, Trash2 } from "lucide-react";
import { useState } from "react";

import { Badge, Button, FitBadge, IconButton, Note, PageHeader, ProgressBar, SkeletonRows } from "../components/ui";
import { api } from "../lib/api";
import { contextLabel, sizeLabel, timeAgo, tps, type Tone } from "../lib/format";
import { errorMessage, useBus, useLoad } from "../lib/hooks";
import { t } from "../lib/i18n";
import { useApp } from "../lib/store";
import type { DownloadRecord, InstalledModel, ModelCard } from "../lib/types";

const STATUS: Record<string, { label: string; tone: Tone }> = {
  queued: { label: t("QUEUED"), tone: "off" },
  downloading: { label: t("DOWNLOADING"), tone: "accent" },
  verifying: { label: t("VERIFYING"), tone: "accent" },
  completed: { label: t("COMPLETED"), tone: "ok" },
  paused: { label: t("PAUSED"), tone: "warn" },
  failed: { label: t("FAILED"), tone: "bad" },
  cancelled: { label: t("STOPPED"), tone: "off" },
};

type Action = "pause" | "resume" | "retry" | "cancel" | "remove";

function eta(record: DownloadRecord): string {
  if (!record.bytes_per_second || !record.size_bytes) return "";
  const seconds = (record.size_bytes - record.downloaded_bytes) / record.bytes_per_second;
  if (seconds < 60) return t("{n} s left", { n: Math.max(1, Math.round(seconds)) });
  if (seconds < 3600) return t("{n} min left", { n: Math.round(seconds / 60) });
  return t("{n} h left", { n: (seconds / 3600).toFixed(1) });
}

function DownloadRow({ record: d, onAction }: { record: DownloadRecord; onAction: (id: string, action: Action) => void }) {
  // Stopping throws away what was downloaded, so it asks first.
  const [confirmStop, setConfirmStop] = useState(false);
  const status = STATUS[d.status] ?? { label: d.status.toUpperCase(), tone: "off" as Tone };
  const running = d.status === "downloading" || d.status === "verifying";
  const open = running || d.status === "paused" || d.status === "queued";
  const viaLmStudio = d.backend === "lmstudio";
  return (
    <li className="grid gap-2 border-b border-line py-3.5">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="truncate font-medium">{d.repo.split("/")[1] ?? d.repo}</span>
            {d.quant && <span className="font-mono text-xs text-ink-2">{d.quant}</span>}
            <Badge tone={status.tone}>{status.label}</Badge>
          </div>
          <div className="selectable mt-0.5 truncate font-mono text-xs text-ink-3" title={`${d.target_dir}\\${d.filename}`}>
            {d.target_dir}\{d.filename}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {d.can_pause && (
            <Button size="sm" icon={<Pause size={12} />} onClick={() => onAction(d.id, "pause")}>
              {t("Pause")}
            </Button>
          )}
          {d.status === "paused" && (
            <Button size="sm" variant="primary" icon={<Play size={12} />} onClick={() => onAction(d.id, "resume")}>
              {t("Resume")}
            </Button>
          )}
          {d.status === "failed" && (
            <Button size="sm" icon={<RotateCcw size={12} />} onClick={() => onAction(d.id, "retry")}>
              {t("Retry")}
            </Button>
          )}
          {open && d.can_cancel && (
            <Button size="sm" variant="danger" icon={<Square size={11} />} onClick={() => setConfirmStop(true)}>
              {t("Stop")}
            </Button>
          )}
          {["completed", "failed", "cancelled"].includes(d.status) && (
            <IconButton label={t("Remove from list")} onClick={() => onAction(d.id, "remove")}>
              <Trash2 size={14} />
            </IconButton>
          )}
        </div>
      </div>

      {confirmStop && (
        <div className="rise-in flex flex-wrap items-center justify-between gap-3 rounded-md border border-line-strong bg-surface px-3 py-2.5 text-sm">
          <span className="text-ink-2">
            {t("Stop this download and delete the {size} already downloaded? Pause keeps them for later.", { size: sizeLabel(d.downloaded_bytes) })}
          </span>
          <span className="flex gap-2">
            <Button size="sm" onClick={() => setConfirmStop(false)}>
              {t("Keep downloading")}
            </Button>
            <Button
              size="sm"
              variant="danger"
              onClick={() => {
                setConfirmStop(false);
                onAction(d.id, "cancel");
              }}
            >
              {t("Stop and delete")}
            </Button>
          </span>
        </div>
      )}

      {open && <ProgressBar value={d.progress} indeterminate={d.status === "verifying" || (running && !d.size_bytes)} tone={d.status === "paused" ? "warn" : "accent"} />}
      <div className="tabular flex flex-wrap items-center gap-x-4 gap-y-0.5 text-xs text-ink-3">
        {d.status !== "completed" && d.status !== "cancelled" && <span className="font-medium text-ink-2">{Math.round(d.progress * 100)}%</span>}
        <span>{d.status === "completed" ? sizeLabel(d.size_bytes) : `${sizeLabel(d.downloaded_bytes)} / ${sizeLabel(d.size_bytes)}`}</span>
        {running && d.bytes_per_second ? <span>{(d.bytes_per_second / 1e6).toFixed(0)} MB/s</span> : null}
        {running && <span>{eta(d)}</span>}
        <span>{viaLmStudio ? t("LM Studio's downloader") : t("Will It Run's downloader")}</span>
        <span>{timeAgo(d.created_at)}</span>
      </div>
      {open && viaLmStudio && (
        <p className="text-xs text-ink-3">{t("This download was handed to LM Studio, which offers no way to pause or stop it from outside. Stop it in LM Studio itself.")}</p>
      )}
      {d.note && <p className="selectable text-xs text-ink-2">{d.note}</p>}
      {d.error && <div className="selectable text-sm text-bad">{d.error}</div>}
    </li>
  );
}

interface Installed {
  cards: ModelCard[];
  models: InstalledModel[];
  total_bytes: number;
}

/** Every model LM Studio has on this PC, whoever downloaded it. */
function InstalledModels({ data, loading, error, onRetry }: { data: Installed | null; loading: boolean; error: string | null; onRetry: () => void }) {
  const navigate = useApp((s) => s.navigate);
  const lmstudio = useApp((s) => s.lmstudio);
  const models = [...(data?.models ?? [])].sort((a, b) => Number(b.type === "llm") - Number(a.type === "llm") || a.display_name.localeCompare(b.display_name));
  const cardOf = (model: InstalledModel) => data?.cards.find((c) => c.installed_key === model.model_key);
  return (
    <section className="mb-8">
      <div className="mb-2 flex items-baseline justify-between gap-4">
        <h2 className="text-sm font-semibold">{t("Downloaded models")}</h2>
        {models.length > 0 && (
          <span className="tabular text-xs text-ink-3">
            {models.length === 1 ? t("1 model") : t("{n} models", { n: models.length })} · {sizeLabel(data?.total_bytes)}
          </span>
        )}
      </div>
      {loading && !data && <SkeletonRows rows={3} height={52} />}
      {error && !data && <Note tone="bad" action={<Button size="sm" onClick={onRetry}>{t("Retry")}</Button>}>{error}</Note>}
      {data && models.length === 0 && (
        <div className="border-t border-line pt-3 text-sm text-ink-2">
          <p className="max-w-[60ch]">
            {lmstudio?.installed === false
              ? t("LM Studio is not installed, so there is no model list to read.")
              : t("No model is on this PC yet. Pick one in Explore Models: it shows the exact file, its size and whether it is safe to install before anything is downloaded.")}
          </p>
          <Button variant="primary" className="mt-3" onClick={() => navigate("models")}>
            {t("Explore Models")}
          </Button>
        </div>
      )}
      {models.length > 0 && (
        <ul className="border-t border-line">
          {models.map((model) => {
            const card = cardOf(model);
            const loaded = model.loaded_instances.length > 0;
            return (
              <li key={model.model_key}>
                <button
                  type="button"
                  onClick={() => navigate("models", model.repo ? { installed: "1", repo: model.repo } : { installed: "1" })}
                  className="grid w-full grid-cols-[minmax(0,1fr)_88px_120px_128px] items-center gap-4 border-b border-line py-2.5 text-left transition-colors duration-150 hover:bg-surface"
                >
                  <span className="min-w-0">
                    <span className="flex items-center gap-2">
                      <span className="truncate font-medium">{model.display_name}</span>
                      {model.quant && <span className="font-mono text-xs text-ink-2">{model.quant}</span>}
                      {model.type !== "llm" && <Badge tone="off">{model.type.toUpperCase()}</Badge>}
                      {loaded && <Badge tone="ok">{t("IN MEMORY")}</Badge>}
                    </span>
                    <span className="selectable block truncate font-mono text-xs text-ink-3" title={model.filename ?? model.model_key}>
                      {model.filename ?? model.model_key}
                    </span>
                  </span>
                  <span className="tabular text-right text-sm text-ink-2">{sizeLabel(model.size_bytes)}</span>
                  <span className="tabular text-right text-xs text-ink-3">
                    {card ? (
                      <>
                        <span className="text-sm text-ink-2">
                          {card.fit.speed.measured ? "" : "~"}
                          {tps(card.fit.speed.tokens_per_second)}
                        </span>
                        <span className="block">{card.fit.speed.measured ? t("measured") : t("estimated")}</span>
                      </>
                    ) : model.max_context_length ? (
                      t("{n} context", { n: contextLabel(model.max_context_length) })
                    ) : (
                      ""
                    )}
                  </span>
                  <span className="text-right">{card && <FitBadge category={card.fit.category} />}</span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {models.length > 0 && <p className="mt-2 text-xs text-ink-3">{t("This is the list LM Studio keeps: models downloaded here, in LM Studio or copied by hand all appear. Select one to see its files and scores.")}</p>}
    </section>
  );
}

export function DownloadsPage() {
  const navigate = useApp((s) => s.navigate);
  const toast = useApp((s) => s.toast);
  const settings = useApp((s) => s.settings);
  const lmstudio = useApp((s) => s.lmstudio);
  const list = useLoad(() => api.get<{ downloads: DownloadRecord[] }>("/api/downloads"));
  const installed = useLoad(() => api.get<Installed>("/api/models/installed"));

  useBus("download.", (event) => {
    if (event.topic === "download.removed") {
      list.reload();
      return;
    }
    const update = event.data as unknown as DownloadRecord;
    if (update.status === "completed") {
      // LM Studio needs a moment to index a new file before it lists it.
      installed.reload();
      window.setTimeout(installed.reload, 4000);
    }
    const current = list.data?.downloads ?? [];
    list.setData({ downloads: current.some((d) => d.id === update.id) ? current.map((d) => (d.id === update.id ? update : d)) : [update, ...current] });
  });

  const act = (id: string, action: Action) => {
    const call = action === "remove" ? api.del(`/api/downloads/${id}`) : api.post(`/api/downloads/${id}/${action}`);
    call.then(list.reload).catch((err: unknown) => toast({ tone: "bad", title: t("Action failed"), body: errorMessage(err) }));
  };

  const downloads = list.data?.downloads ?? [];
  const active = downloads.filter((d) => ["queued", "downloading", "verifying", "paused"].includes(d.status));
  const finished = downloads.filter((d) => !active.includes(d));
  const folder = settings?.storage.custom_model_dir || lmstudio?.models_dir || null;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader title={t("Downloads")} subtitle={t("The models on this PC and the downloads in progress. Every download is checked against the disk safety reserve before it starts; you can pause, resume or stop it here.")} />
      <div className="min-h-0 flex-1 overflow-y-auto px-7 py-6">
        <div className="max-w-[900px]">
          <div className="mb-6 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line bg-surface px-4 py-3">
            <div className="min-w-0">
              <div className="text-xs text-ink-3">{t("New downloads are saved to")}</div>
              <div className="selectable truncate font-mono text-sm">{folder ?? t("Not set")}</div>
            </div>
            <Button icon={<FolderOpen size={13} />} onClick={() => navigate("settings", { tab: "storage" })}>
              {t("Change folder")}
            </Button>
          </div>

          {list.loading && !list.data && <SkeletonRows rows={3} height={70} />}
          {list.error && <Note tone="bad" action={<Button size="sm" onClick={list.reload}>{t("Retry")}</Button>}>{list.error}</Note>}
          {active.length > 0 && (
            <section className="mb-8">
              <h2 className="mb-2 text-sm font-semibold">{t("In progress")}</h2>
              <ul className="border-t border-line">
                {active.map((d) => (
                  <DownloadRow key={d.id} record={d} onAction={act} />
                ))}
              </ul>
            </section>
          )}
          <InstalledModels data={installed.data} loading={installed.loading} error={installed.error} onRetry={installed.reload} />
          {finished.length > 0 && (
            <section className="mb-8">
              <h2 className="mb-2 text-sm font-semibold">{t("Download history")}</h2>
              <ul className="border-t border-line">
                {finished.map((d) => (
                  <DownloadRow key={d.id} record={d} onAction={act} />
                ))}
              </ul>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
