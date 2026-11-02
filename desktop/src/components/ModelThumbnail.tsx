import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { Box } from "lucide-react";
import { t } from "../lib/i18n";

const ModelPreview = lazy(() => import("./ModelPreview").then((m) => ({ default: m.ModelPreview })));
const cache = new Map<string, string>();
let queue = Promise.resolve();

/** Render visible thumbnails one at a time, then release the GLB and GPU context. */
export function ModelThumbnail({ id, load }: { id: string; load: (id: string, variant: "original") => Promise<ArrayBuffer> }) {
  const host = useRef<HTMLSpanElement>(null);
  const [image, setImage] = useState(cache.get(id) || "");
  const [data, setData] = useState<ArrayBuffer | null>(null);
  const done = useRef<() => void>(() => {});
  const capture = useCallback((value: string) => {
    if (value) { cache.set(id, value); setImage(value); }
    setData(null); done.current();
  }, [id]);
  useEffect(() => {
    if (cache.has(id)) return;
    let active = true;
    const observer = new IntersectionObserver(([entry]) => {
      if (!entry?.isIntersecting) return;
      observer.disconnect();
      queue = queue.then(async () => {
        if (!active) return;
        await new Promise<void>((resolve) => {
          const timer = window.setTimeout(() => { if (active) setData(null); resolve(); }, 30000);
          done.current = () => { clearTimeout(timer); resolve(); };
          void load(id, "original").then((bytes) => { if (active) setData(bytes); else done.current(); }).catch(() => done.current());
        });
      });
    });
    observer.observe(host.current!);
    return () => { active = false; observer.disconnect(); done.current(); };
  }, [id, load]);
  return <span ref={host} className="relative block h-20 w-24 shrink-0 overflow-hidden rounded bg-bg">
    {image ? <img src={image} alt={t("Model preview")} className="h-full w-full object-contain" /> : <Box className="absolute inset-0 m-auto text-ink-3" size={24} />}
    {data && <span className="pointer-events-none absolute left-0 top-0 block w-24" aria-hidden="true"><Suspense fallback={null}><ModelPreview data={data} height={80} onSnapshot={capture} /></Suspense></span>}
  </span>;
}
