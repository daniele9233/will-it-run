import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { GripHorizontal, Maximize2, Minimize2 } from "lucide-react";
import { t } from "../lib/i18n";
import { Button } from "./ui";

const ModelPreview = lazy(() => import("./ModelPreview").then((module) => ({ default: module.ModelPreview })));
const MIN_HEIGHT = 260;
const MAX_HEIGHT = 900;
const HEIGHT_KEY = "agentfit.3d.studioHeight";

export function ModelStudio({ id, data, variant, onVariant, hasWeb }: { id: string; data: ArrayBuffer; variant: "original" | "web"; onVariant: (variant: "original" | "web") => void; hasWeb: boolean }) {
  const [height, setHeight] = useState(() => {
    try { const stored = Number(localStorage.getItem(HEIGHT_KEY)); return stored >= MIN_HEIGHT && stored <= MAX_HEIGHT ? stored : 400; }
    catch { return 400; }
  });
  const [expanded, setExpanded] = useState(false);
  const [windowHeight, setWindowHeight] = useState(window.innerHeight);
  const panel = useRef<HTMLDivElement>(null);
  const expandButton = useRef<HTMLDivElement>(null);
  const drag = useRef<{ y: number; height: number } | null>(null);
  const shownHeight = expanded ? Math.max(MIN_HEIGHT, windowHeight - 185) : height;
  function resize(value: number) {
    const next = Math.max(MIN_HEIGHT, Math.min(MAX_HEIGHT, Math.round(value)));
    setHeight(next);
    try { localStorage.setItem(HEIGHT_KEY, String(next)); } catch { /* Optional preference only. */ }
  }
  useEffect(() => {
    const onResize = () => setWindowHeight(window.innerHeight);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  useEffect(() => {
    if (!expanded) return;
    const previousFocus = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const frame = requestAnimationFrame(() => expandButton.current?.querySelector("button")?.focus());
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); setExpanded(false); }
      if (event.key !== "Tab") return;
      const elements = [...panel.current!.querySelectorAll<HTMLElement>("button:not(:disabled), input:not(:disabled), select:not(:disabled), a[href]")].filter((el) => el.getClientRects().length);
      const first = elements[0], last = elements.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    window.addEventListener("keydown", onKey, true);
    return () => {
      cancelAnimationFrame(frame); window.removeEventListener("keydown", onKey, true);
      document.body.style.overflow = previousOverflow;
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, [expanded]);
  return <div ref={panel} role={expanded ? "dialog" : undefined} aria-modal={expanded || undefined} aria-label={t("Studio")} className={expanded ? "fixed inset-0 z-[80] grid content-start gap-3 overflow-y-auto bg-bg p-4" : "grid min-w-0 gap-3 rounded-lg border border-line bg-panel p-3"}>
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div><h2 className="text-sm font-semibold">{t("Studio")}</h2><p className="text-xs text-ink-3">{id.slice(0, 8)}</p></div>
      <label className="text-xs">{t("Preview version")} <select className="rounded border border-line bg-bg p-2" value={variant} onChange={(event) => onVariant(event.target.value as "original" | "web")}><option value="original">{t("Original detailed")}</option><option value="web" disabled={!hasWeb}>{t("Web copy")}</option></select></label>
      <div ref={expandButton}><Button size="sm" aria-expanded={expanded} icon={expanded ? <Minimize2 size={15} /> : <Maximize2 size={15} />} onClick={() => setExpanded(!expanded)}>{expanded ? t("Reduce preview") : t("Expand preview")}</Button></div>
    </div>
    <Suspense fallback={<p>{t("Loading…")}</p>}><ModelPreview data={data} height={shownHeight} /></Suspense>
    {!expanded && <div className="flex touch-none flex-wrap items-center gap-3 border-y border-line py-2 text-xs text-ink-3" onPointerDown={(event) => {
      if ((event.target as HTMLElement).closest("input,label")) return;
      drag.current = { y: event.clientY, height }; event.currentTarget.setPointerCapture(event.pointerId);
    }} onPointerMove={(event) => { if (drag.current) resize(drag.current.height + event.clientY - drag.current.y); }} onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }} onLostPointerCapture={() => { drag.current = null; }}>
      <span className="flex cursor-ns-resize items-center gap-2"><GripHorizontal size={17} />{t("Drag to resize the preview")}</span>
      <label className="ml-auto flex items-center gap-2">{t("Preview height")}<input aria-label={t("Preview height")} className="w-28 accent-accent" type="range" min={MIN_HEIGHT} max={MAX_HEIGHT} step={10} value={height} onChange={(event) => resize(Number(event.target.value))} /><span className="min-w-10 tabular-nums">{height}px</span></label>
    </div>}
  </div>;
}
