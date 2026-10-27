import { ChevronRight, Download, ExternalLink, Star, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { Badge, Button, IconButton, Note, ProgressBar, cx } from "../ui";
import { api, openExternal } from "../../lib/api";
import { errorMessage } from "../../lib/hooks";
import { t } from "../../lib/i18n";
import type { ImageComponent, ImageDownload, ImageModel, ImagePlan } from "../../lib/types";
import { suggestionText, warningText } from "./planText";

const ROLE_LABEL: Record<string, string> = {
  diffusion_model: t("Diffusion model"),
  text_encoder: t("Text encoder"),
  vae: t("VAE"),
  checkpoint: t("Checkpoint"),
  custom_node: t("Custom node"),
};

export function sizeLabel(bytes: number | null | undefined): string {
  if (!bytes) return t("size unknown");
  if (bytes < 1e9) return `${Math.round(bytes / 1e6)} MB`;
  return `${(bytes / 1e9).toFixed(1)} GB`;
}

function statusBadge(component: ImageComponent, download: ImageDownload | undefined) {
  if (download && ["queued", "downloading", "verifying"].includes(download.status)) {
    return <Badge tone="accent">{download.percent != null ? `${download.percent.toFixed(0)}%` : t("Downloading")}</Badge>;
  }
  if (component.status === "present") return <Badge tone="ok">{t("Ready")}</Badge>;
  if (component.status === "failed") return <Badge tone="bad">{t("Failed")}</Badge>;
  if (!component.downloadable) return <Badge tone="warn">{t("To place by hand")}</Badge>;
  if (component.needs_token) return <Badge tone="warn">{t("Needs your Civitai token")}</Badge>;
  return <Badge tone="off">{t("Missing")}</Badge>;
}

function ComponentRow({
  component,
  download,
  onDownload,
  onRemove,
}: {
  component: ImageComponent;
  download?: ImageDownload;
  onDownload: (id: string) => void;
  onRemove: (id: string) => void;
}) {
  const active = download && ["queued", "downloading", "verifying"].includes(download.status);
  return (
    <li className="grid grid-cols-[1fr_auto] items-center gap-2 border-b border-line/60 py-2 last:border-0">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-2xs tracking-wide text-ink-3 uppercase">{ROLE_LABEL[component.role] ?? component.role}</span>
          {component.used_by.length > 1 && <Badge tone="off">{t("shared by {n} models", { n: component.used_by.length })}</Badge>}
        </div>
        <div className="selectable truncate font-mono text-xs text-ink-2" title={component.target}>
          {component.file || component.folder}
        </div>
        <div className="flex items-center gap-2 text-2xs text-ink-3">
          <span>{sizeLabel(component.installed_size ?? component.size_bytes)}</span>
          {component.licence && <span className="truncate">· {component.licence}</span>}
        </div>
        {active && download?.percent != null && (
          <div className="mt-1">
            <ProgressBar value={download.percent / 100} />
          </div>
        )}
        {download?.error && <p className="mt-0.5 text-2xs text-bad">{download.error}</p>}
      </div>
      <div className="flex shrink-0 items-center gap-1">
        {statusBadge(component, download)}
        {component.status !== "present" && component.downloadable && !active && (
          <IconButton label={t("Download {name}", { name: component.name })} onClick={() => onDownload(component.id)}>
            <Download size={13} />
          </IconButton>
        )}
        {component.status === "present" && component.role !== "custom_node" && (
          <IconButton
            label={component.used_by.length > 1 ? t("Used by other models: not removed from here") : t("Remove {name}", { name: component.name })}
            disabled={component.used_by.length > 1}
            onClick={() => onRemove(component.id)}
          >
            <Trash2 size={13} />
          </IconButton>
        )}
        {component.url && (
          <IconButton label={t("Open the model page")} onClick={() => void openExternal(component.url)}>
            <ExternalLink size={13} />
          </IconButton>
        )}
      </div>
    </li>
  );
}

function PlanLine({ plan }: { plan: ImagePlan | null }) {
  if (!plan) return null;
  const estimate = plan.estimate;
  return (
    <div className="grid gap-1.5 rounded-md border border-line bg-bg/40 p-2.5">
      <div className="flex items-center justify-between text-xs">
        <span className="text-ink-2">
          {estimate.needed_gb != null && estimate.usable_gb != null
            ? t("{needed} GB of the {usable} GB this PC can give the card.", {
                needed: estimate.needed_gb,
                usable: estimate.usable_gb,
              })
            : t("The memory this needs is not known yet.")}
        </span>
        <Badge tone={estimate.fits === false ? "bad" : estimate.fits ? "ok" : "off"}>
          {estimate.fits === false ? t("Does not fit") : estimate.fits ? t("Fits") : t("Not known")}
        </Badge>
      </div>
      {plan.download_bytes ? (
        <span className="text-xs text-ink-3">{t("{size} to download.", { size: sizeLabel(plan.download_bytes) })}</span>
      ) : null}
      {estimate.warnings.length > 0 && (
        <ul className="grid gap-0.5 text-xs">
          {estimate.warnings.map((warning, index) => (
            <li key={`${warning.code}-${index}`} className="text-warn">
              · {warningText(warning)}
            </li>
          ))}
        </ul>
      )}
      {estimate.suggestions.length > 0 && (
        <ul className="grid gap-0.5 text-xs text-ink-2">
          {estimate.suggestions.map((suggestion, index) => (
            <li key={`${suggestion.code}-${index}`}>+ {suggestionText(suggestion)}</li>
          ))}
        </ul>
      )}
      {plan.lighter.length > 0 && !estimate.fits && (
        <span className="text-xs text-ink-3">
          {t("Lighter in this family: {names}", { names: plan.lighter.map((item) => item.name).join(", ") })}
        </span>
      )}
      <span className="text-2xs text-ink-3">
        {t("Memory figures are estimates ({basis}).", { basis: estimate.basis })}
      </span>
    </div>
  );
}

export function ModelManager({
  models,
  downloads,
  selected,
  onUse,
  onDownloadModel,
  onDownloadComponent,
  onRemoveComponent,
  onRefresh,
}: {
  models: ImageModel[];
  downloads: Record<string, ImageDownload>;
  selected: string;
  onUse: (key: string) => void;
  onDownloadModel: (key: string) => void;
  onDownloadComponent: (id: string) => void;
  onRemoveComponent: (id: string) => void;
  onRefresh: () => void;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const [plans, setPlans] = useState<Record<string, ImagePlan>>({});
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    api
      .get<ImagePlan>(`/api/images/models/${encodeURIComponent(open)}/plan`)
      .then((value) => {
        if (alive) setPlans((current) => ({ ...current, [open]: value }));
      })
      .catch((err: unknown) => {
        if (alive) setError(errorMessage(err));
      });
    return () => {
      alive = false;
    };
  }, [open, models, downloads]);

  return (
    <div className="grid gap-2">
      {error && <Note tone="bad">{error}</Note>}
      <ul className="grid gap-2">
        {models.map((model) => {
          const expanded = open === model.key;
          const plan = plans[model.key];
          const busy = model.components_detail.some((component) => {
            const download = downloads[component.id];
            return download && ["queued", "downloading", "verifying"].includes(download.status);
          });
          return (
            <li key={model.key} className={cx("rounded-lg border bg-surface", expanded ? "border-line-strong" : "border-line")}>
              <button
                type="button"
                aria-expanded={expanded}
                onClick={() => setOpen(expanded ? null : model.key)}
                className="grid w-full grid-cols-[1fr_auto] items-center gap-2 px-3 py-2.5 text-left"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <ChevronRight size={13} className={cx("shrink-0 text-ink-3 transition-transform", expanded && "rotate-90")} />
                    <span className="truncate text-sm font-medium">{model.name}</span>
                    {model.recommended && <Star size={11} className="shrink-0 fill-accent text-accent" />}
                    {model.nsfw === "native" && <Badge tone="off">{t("unfiltered")}</Badge>}
                  </div>
                  <div className="mt-0.5 flex flex-wrap items-center gap-2 pl-5 text-2xs text-ink-3">
                    <span className="font-mono">{model.family}</span>
                    {model.vram_gb != null && <span>{model.vram_gb} GB VRAM</span>}
                    {model.download_bytes ? <span>{t("{size} to download.", { size: sizeLabel(model.download_bytes) })}</span> : null}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-1.5">
                  {model.blocked ? (
                    <Badge tone="warn">{t("Cannot run here")}</Badge>
                  ) : model.installed ? (
                    <Badge tone="ok">{t("Ready")}</Badge>
                  ) : (
                    <Badge tone="warn">{model.missing.length === 1 ? t("1 missing") : t("{n} missing", { n: model.missing.length })}</Badge>
                  )}
                  {selected === model.key && <Badge tone="accent">{t("In use")}</Badge>}
                </div>
              </button>

              {expanded && (
                <div className="grid gap-2 border-t border-line px-3 py-2.5">
                  {model.notes && <p className="text-xs text-ink-2">{model.notes}</p>}
                  {model.blocked && (
                    <Note tone="warn">
                      <div className="grid gap-1">
                        <span>{model.blocked.reason}</span>
                        {(model.blocked.missing ?? []).length > 0 && (
                          <span className="text-2xs text-ink-3">
                            {t("Missing: {names}.", { names: (model.blocked.missing ?? []).join(", ") })}
                          </span>
                        )}
                      </div>
                    </Note>
                  )}
                  <div className="flex flex-wrap items-center gap-2 text-2xs text-ink-3">
                    {model.licence && <span>{t("Licence: {licence}", { licence: model.licence })}</span>}
                    {model.source && (
                      <button type="button" className="underline hover:text-ink" onClick={() => void openExternal(model.source)}>
                        {t("Model page")}
                      </button>
                    )}
                  </div>

                  <ul className="grid">
                    {model.components_detail.map((component) => (
                      <ComponentRow
                        key={component.id}
                        component={component}
                        download={downloads[component.id]}
                        onDownload={onDownloadComponent}
                        onRemove={onRemoveComponent}
                      />
                    ))}
                  </ul>

                  <PlanLine plan={plan ?? null} />
                  {plan?.problems.map((problem) => (
                    <Note key={problem} tone="warn">
                      {problem}
                    </Note>
                  ))}

                  <div className="flex flex-wrap items-center gap-2">
                    <Button size="sm" variant="primary" icon={<Download size={12} />} disabled={busy || model.installed} onClick={() => onDownloadModel(model.key)}>
                      {model.installed ? t("All parts ready") : t("Download the missing parts")}
                    </Button>
                    <Button size="sm" disabled={!model.installed || selected === model.key} onClick={() => onUse(model.key)}>
                      {t("Use this model")}
                    </Button>
                    <Button size="sm" variant="ghost" onClick={onRefresh}>
                      {t("Refresh")}
                    </Button>
                  </div>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

