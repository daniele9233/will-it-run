import { Download, ExternalLink, Play, RefreshCw, Square } from "lucide-react";

import { Badge, Button, Meter, Panel, StatusDot, cx } from "../ui";
import { openExternal } from "../../lib/api";
import { t } from "../../lib/i18n";
import type { ImageEngine, ImageStatus } from "../../lib/types";

interface Props {
  status: ImageStatus | null;
  busy: boolean;
  onStart: () => void;
  onStop: () => void;
  onRefresh: () => void;
}

/** The engine card: is ComfyUI here, is it running, and the one button that changes it. */
export function EngineCard({ status, busy, onStart, onStop, onRefresh }: Props) {
  const engine: ImageEngine | null = status?.engine ?? null;
  const vram = status?.vram;
  const tone = !engine ? "off" : engine.running ? "ok" : engine.installed ? "warn" : "bad";

  return (
    <Panel className="grid gap-3">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <StatusDot tone={tone} pulse={busy || Boolean(engine?.running)} />
          <div>
            <div className="text-sm font-semibold">{t("Image engine")}</div>
            <div className="text-xs text-ink-3">
              {engine?.version ? `ComfyUI ${engine.version}` : t("ComfyUI")}
              {engine?.running ? ` · ${engine.url.replace("http://", "")}` : ""}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          {engine?.running ? (
            <Button
              size="sm"
              icon={<Square size={11} />}
              disabled={busy || !engine.started_by_us}
              title={engine.started_by_us ? undefined : t("ComfyUI was started somewhere else, so it is not closed from here.")}
              onClick={onStop}
            >
              {t("Stop engine")}
            </Button>
          ) : (
            <Button
              size="sm"
              variant="primary"
              icon={<Play size={12} />}
              loading={busy}
              disabled={busy || !engine?.installed}
              onClick={onStart}
            >
              {t("Start engine")}
            </Button>
          )}
          <Button size="sm" variant="ghost" icon={<RefreshCw size={12} />} onClick={onRefresh} aria-label={t("Look again")} />
        </div>
      </div>

      {engine && !engine.installed && (
        <div className="grid gap-2 rounded-md border border-line bg-bg/40 p-3">
          <p className="text-sm text-ink-2">{t("ComfyUI is not on this PC yet.")}</p>
          <p className="text-xs text-ink-3">
            {t("Will It Run does not install it: ComfyUI is GPL-3.0 and is not part of this app. Install it, then press Look again.")}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              variant="primary"
              icon={<Download size={12} />}
              onClick={() => void openExternal(engine.download_url)}
            >
              {t("Download ComfyUI")}
            </Button>
            <Button size="sm" icon={<ExternalLink size={12} />} onClick={() => void openExternal(engine.portable_url)}>
              {t("Portable version")}
            </Button>
            <Button size="sm" variant="ghost" icon={<RefreshCw size={12} />} onClick={onRefresh}>
              {t("Look again")}
            </Button>
          </div>
        </div>
      )}

      {engine?.installed && (
        <div className="grid gap-1 text-xs text-ink-3">
          <div className="flex items-center justify-between gap-3">
            <span className="truncate font-mono" title={engine.install?.root}>
              {engine.install?.root}
            </span>
            {engine.install?.kind === "portable" && <Badge tone="off">{t("Portable")}</Badge>}
          </div>
          {!engine.running && <span>{t("Stopped. Press Start engine: the first start takes up to a minute.")}</span>}
        </div>
      )}

      {engine?.running && vram && (
        <div className="grid gap-1">
          <div className="flex items-center justify-between text-xs text-ink-3">
            <span className="truncate">{vram.name ?? t("Graphics card")}</span>
            <span className="tabular">
              {vram.free_gb.toFixed(1)} / {vram.total_gb.toFixed(1)} GB {t("free")}
            </span>
          </div>
          <Meter
            value={vram.total_gb > 0 ? 1 - vram.free_gb / vram.total_gb : 0}
            label={t("Graphics memory in use by the engine")}
            height={4}
            tone={vram.free_gb / Math.max(1, vram.total_gb) < 0.15 ? "warn" : "accent"}
          />
        </div>
      )}

      {engine?.running && status && !status.available && (
        <p className="text-xs text-bad">{status.error}</p>
      )}

      {engine && !engine.running && engine.installed && busy && (
        <p className={cx("text-xs", "text-ink-3")}>{t("Starting ComfyUI…")}</p>
      )}
    </Panel>
  );
}
