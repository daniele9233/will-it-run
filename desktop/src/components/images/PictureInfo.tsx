import { bytesLabel, secondsLabel } from "../../lib/format";
import { t } from "../../lib/i18n";
import type { GalleryImage } from "../../lib/types";
import { Badge } from "../ui";

/** What made a picture: the model, the seed, the sampler, how long it took and how much it weighs. Shown under the picture and in the zoom. */
export function PictureInfo({ image, modelName, lines = 2 }: { image: GalleryImage; modelName: string; lines?: number }) {
  const sampling = [image.sampler && image.scheduler ? `${image.sampler} / ${image.scheduler}` : image.sampler, image.steps ? t("{n} steps", { n: image.steps }) : null, image.cfg !== null ? `CFG ${image.cfg}` : null]
    .filter(Boolean)
    .join(" · ");
  const took = secondsLabel(image.elapsed_seconds);
  return (
    <div className="grid gap-1 text-xs text-ink-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Badge tone="accent">
          {image.width} × {image.height}
        </Badge>
        <span className="selectable font-medium text-ink" title={image.model_key}>
          {t("Model: {name}", { name: modelName })}
        </span>
        <span className="selectable font-mono">{t("seed {n}", { n: image.seed })}</span>
        {sampling && <span className="font-mono text-ink-3">{sampling}</span>}
        {took && <span className="text-ink-3">{t("took {time}", { time: took })}</span>}
        {image.bytes ? <span className="text-ink-3">{bytesLabel(image.bytes)}</span> : null}
      </div>
      <p className="selectable" style={{ display: "-webkit-box", WebkitLineClamp: lines, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
        {image.prompt}
      </p>
      {image.loras.length > 0 && <p className="font-mono text-2xs text-ink-3">{image.loras.map((lora) => `${lora.name}:${lora.weight}`).join("  ")}</p>}
    </div>
  );
}
