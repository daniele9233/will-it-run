import { Download, Maximize, X, ZoomIn, ZoomOut } from "lucide-react";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";

import { t } from "../../lib/i18n";
import { FIT, STEP, type Size, type View, actualSizeZoom, clampView, fitSize, zoomAt, zoomPercent } from "../../lib/imageView";
import { IconButton } from "../ui";

const NONE: Size = { width: 0, height: 0 };

/** A picture on the whole window: zoom with the wheel or the buttons, move it by dragging, double-click for actual size. */
export function Lightbox({ src, alt, onClose, onSave, children }: { src: string; alt: string; onClose: () => void; onSave?: () => void; children?: ReactNode }) {
  const frameRef = useRef<HTMLDivElement>(null);
  const [frame, setFrame] = useState<Size>(NONE);
  const [natural, setNatural] = useState<Size>(NONE);
  const [view, setView] = useState<View>(FIT);
  const [dragging, setDragging] = useState(false);
  const drag = useRef<{ x: number; y: number; view: View } | null>(null);
  const shown = fitSize(natural, frame);

  useEffect(() => {
    const element = frameRef.current;
    if (!element) return;
    const measure = () => setFrame({ width: element.clientWidth, height: element.clientHeight });
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  // The view is kept inside the frame when the window is resized.
  useEffect(() => setView((current) => clampView(current, frame, shown)), [frame.width, frame.height, natural.width, natural.height]); // eslint-disable-line react-hooks/exhaustive-deps

  const centre = useCallback((clientX: number, clientY: number) => {
    const box = frameRef.current?.getBoundingClientRect();
    return box ? { x: clientX - box.left - box.width / 2, y: clientY - box.top - box.height / 2 } : { x: 0, y: 0 };
  }, []);

  const zoomBy = useCallback((factor: number, point = { x: 0, y: 0 }) => setView((current) => zoomAt(current, factor, point, frame, shown)), [frame, shown.width, shown.height]); // eslint-disable-line react-hooks/exhaustive-deps

  const toActual = useCallback(
    (point = { x: 0, y: 0 }) => setView((current) => zoomAt(current, actualSizeZoom(natural, shown) / current.zoom, point, frame, shown)),
    [natural, frame, shown.width, shown.height], // eslint-disable-line react-hooks/exhaustive-deps
  );

  // A wheel listener has to be non-passive to stop the page behind from scrolling.
  useEffect(() => {
    const element = frameRef.current;
    if (!element) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      zoomBy(Math.exp(-event.deltaY * 0.002), centre(event.clientX, event.clientY));
    };
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, [zoomBy, centre]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      else if (event.key === "+" || event.key === "=") zoomBy(STEP);
      else if (event.key === "-") zoomBy(1 / STEP);
      else if (event.key === "0") setView(FIT);
      else if (event.key === "1") toActual();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, zoomBy, toActual]);

  const zoomed = view.zoom > 1.001;
  const percent = zoomPercent(natural, shown, view.zoom);

  return (
    <div role="dialog" aria-modal aria-label={t("Picture")} className="fade-in fixed inset-0 z-[80] flex flex-col bg-black/90">
      <div className="flex items-center justify-between gap-3 px-4 py-2">
        <span className="font-mono text-xs text-ink-2">{natural.width ? `${natural.width} × ${natural.height} · ${percent}%` : ""}</span>
        <div className="flex items-center gap-1">
          <IconButton label={t("Zoom out")} onClick={() => zoomBy(1 / STEP)} disabled={!zoomed}>
            <ZoomOut size={16} />
          </IconButton>
          <IconButton label={t("Zoom in")} onClick={() => zoomBy(STEP)}>
            <ZoomIn size={16} />
          </IconButton>
          <IconButton label={t("Fit to window")} onClick={() => setView(FIT)} disabled={!zoomed}>
            <Maximize size={16} />
          </IconButton>
          <button
            type="button"
            title={t("Actual size")}
            onClick={() => toActual()}
            className="inline-flex h-7 items-center rounded-md px-2 font-mono text-xs text-ink-3 hover:bg-raised hover:text-ink"
          >
            100%
          </button>
          {onSave && (
            <IconButton label={t("Download")} onClick={onSave}>
              <Download size={16} />
            </IconButton>
          )}
          <IconButton label={t("Close")} onClick={onClose}>
            <X size={16} />
          </IconButton>
        </div>
      </div>

      <div
        ref={frameRef}
        className="relative min-h-0 flex-1 touch-none select-none overflow-hidden"
        style={{ cursor: dragging ? "grabbing" : zoomed ? "grab" : "zoom-in" }}
        onPointerDown={(event) => {
          if (event.button !== 0) return;
          event.currentTarget.setPointerCapture(event.pointerId);
          drag.current = { x: event.clientX, y: event.clientY, view };
          setDragging(true);
        }}
        onPointerMove={(event) => {
          const start = drag.current;
          if (!start) return;
          setView(clampView({ zoom: start.view.zoom, x: start.view.x + event.clientX - start.x, y: start.view.y + event.clientY - start.y }, frame, shown));
        }}
        onPointerUp={() => {
          drag.current = null;
          setDragging(false);
        }}
        onDoubleClick={(event) => (zoomed ? setView(FIT) : toActual(centre(event.clientX, event.clientY)))}
      >
        <img
          src={src}
          alt={alt}
          draggable={false}
          onLoad={(event) => setNatural({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })}
          className="absolute left-1/2 top-1/2 max-w-none"
          style={{
            width: shown.width || undefined,
            height: shown.height || undefined,
            marginLeft: -shown.width / 2,
            marginTop: -shown.height / 2,
            transform: `translate(${view.x}px, ${view.y}px) scale(${view.zoom})`,
            transformOrigin: "center",
            opacity: shown.width ? 1 : 0,
          }}
        />
      </div>

      <div className="grid gap-1 px-4 py-2">
        {children}
        <p className="text-2xs text-ink-3">{t("Scroll to zoom, drag to move, double-click for actual size, Esc to close.")}</p>
      </div>
    </div>
  );
}
