import { CheckCircle2, CircleAlert } from "lucide-react";
import { useState } from "react";

import { Tabs, cx } from "../../components/ui";
import { percent, type ReplicaReport, type ReplicaView } from "../../lib/cloneSite";
import { t } from "../../lib/i18n";

/** The words for a width the capture was made at. */
const widthLabel = (name: string, view: ReplicaView): string =>
  `${name === "mobile" ? t("Phone") : name === "desktop" ? t("Desktop") : name === "reference" ? t("The picture") : name} · ${view.width}px`;

function Figure({ label, src, onOpen }: { label: string; src: string | null; onOpen: (src: string) => void }) {
  return (
    <figure className="grid min-w-0 content-start gap-1">
      <figcaption className="clone-label">{label}</figcaption>
      {src ? (
        <button type="button" onClick={() => onOpen(src)} className="block overflow-hidden rounded-md border border-line hover:border-accent" title={t("Open it larger")}>
          <img src={src} alt={label} loading="lazy" className="block w-full" />
        </button>
      ) : (
        <span className="rounded-md border border-line bg-bg p-3 text-2xs text-ink-3">{t("Not measured")}</span>
      )}
    </figure>
  );
}

/** What the measure of an exact copy found: the accuracy, how it breaks down, the weak sections, and the pictures that show it. */
export function ReplicaResult({
  report,
  host,
  file,
  onOpen,
}: {
  report: ReplicaReport;
  host: string;
  file: (path: string | null | undefined) => string | null;
  onOpen: (src: string) => void;
}) {
  const names = Object.keys(report.viewports);
  const [shown, setShown] = useState<string>(names[0] ?? "desktop");
  const view = report.viewports[shown] ?? report.viewports[names[0] ?? ""];
  const folder = `docs/research/${host}/report`;
  const reached = report.accuracy !== null && report.accuracy >= report.target;

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-end gap-x-6 gap-y-2">
        <div className="grid gap-0.5" title={report.formula}>
          <span className="clone-label">{t("Accuracy")}</span>
          <span className={cx("font-mono text-[44px] leading-none font-bold tracking-tight", reached ? "text-ok" : "text-bad")}>{percent(report.accuracy)}</span>
        </div>
        <p className="flex max-w-[460px] items-start gap-1.5 pb-1 text-xs leading-relaxed text-ink-2">
          {reached ? <CheckCircle2 size={14} className="mt-[1px] shrink-0 text-ok" aria-hidden /> : <CircleAlert size={14} className="mt-[1px] shrink-0 text-bad" aria-hidden />}
          <span>
            {reached
              ? t("Above the target of {target}. The accuracy is the mean, over the widths, of how many pixels match the original and how well its structure matches.", { target: percent(report.target) })
              : t("Below the target of {target}.", { target: percent(report.target) })}
          </span>
        </p>
      </div>

      {typeof report.copied_share === "number" && (
        <p className="text-xs leading-relaxed text-ink-2">
          {t("Over the regions the model wrote: {built}. {share} of the picture is its own crops (photographs, and regions the model could not write), not built.", {
            built: percent(report.built_accuracy),
            share: percent(report.copied_share),
          })}
        </p>
      )}

      <div className="grid gap-1.5">
        {names.map((name) => {
          const v = report.viewports[name];
          if (!v) return null;
          return (
            <div key={name} className="grid grid-cols-[minmax(110px,150px)_repeat(5,minmax(0,1fr))] items-center gap-x-3 gap-y-0.5 text-xs">
              <span className="font-medium text-ink">{widthLabel(name, v)}</span>
              {[
                { label: t("Visual"), value: percent(v.visual), strong: true },
                { label: t("Pixels"), value: percent(v.pixels) },
                { label: t("Structure"), value: percent(v.ssim) },
                { label: t("Layout"), value: percent(v.layout) },
                { label: t("Text"), value: v.text === null ? t("Not measured") : percent(v.text) },
              ].map((cell) => (
                <span key={cell.label} className="grid">
                  <span className="text-2xs text-ink-3">{cell.label}</span>
                  <span className={cx("font-mono", cell.strong ? "font-semibold text-ink" : "text-ink-2")}>{cell.value}</span>
                </span>
              ))}
            </div>
          );
        })}
      </div>

      {report.weak.length > 0 && (
        <div className="grid gap-1">
          <span className="clone-label text-warn">{t("Weakest sections")}</span>
          <ul className="grid gap-0.5 text-xs text-ink-2">
            {report.weak.slice(0, 6).map((w) => (
              <li key={`${w.viewport ?? "picture"}-${w.id}`} className="flex items-baseline gap-2">
                <span className="font-mono text-warn">{percent(w.visual)}</span>
                <span className="truncate">{w.name}</span>
                {w.viewport && <span className="font-mono text-2xs text-ink-3">{w.viewport === "mobile" ? t("Phone") : t("Desktop")}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {view && (
        <div className="grid gap-2">
          {names.length > 1 && <Tabs value={shown} tabs={names.map((n) => ({ value: n, label: widthLabel(n, report.viewports[n] as ReplicaView) }))} onChange={setShown} />}
          <div className="grid max-h-[560px] gap-2 overflow-auto rounded-md border border-line bg-bg p-2 md:grid-cols-3">
            <Figure label={t("Original")} src={file(`${folder}/${view.original}`)} onOpen={onOpen} />
            <Figure label={t("Clone")} src={file(`${folder}/${view.preview}`)} onOpen={onOpen} />
            <Figure label={t("Where they differ")} src={file(`${folder}/${view.diff}`)} onOpen={onOpen} />
          </div>
          <p className="text-2xs text-ink-3">
            {t("Page height: original {a}px, clone {b}px. Red marks the pixels that differ.", { a: view.referenceHeight, b: view.cloneHeight })}
          </p>
          {(view.errors.length > 0 || view.failed.length > 0) && (
            <p className="text-2xs text-warn">
              {t("The clone logged {n} console messages and {m} failed requests.", { n: view.errors.length, m: view.failed.length })}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
