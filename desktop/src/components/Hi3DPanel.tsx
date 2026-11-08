import { useState } from "react";
import { Button } from "./ui";
import { api } from "../lib/api";
import { useInterval, useLoad } from "../lib/hooks";
import { t } from "../lib/i18n";

const input = "w-full rounded-md border border-line bg-bg p-2 text-sm";

export function Hi3DPanel({ modelId, image, running, trellisReady, act }: {
  modelId?: string; image: string; running: boolean; trellisReady: boolean;
  act: (path: string, body: unknown) => Promise<void>;
}) {
  const status = useLoad(() => api.get<{ ready: boolean; error?: string | null }>("/api/3d/hi3d/status"));
  const [resolution, setResolution] = useState(1024);
  const [steps, setSteps] = useState(25);
  const [seed, setSeed] = useState(1);
  const [triangles, setTriangles] = useState(60000);
  const [cpuOffload, setCpuOffload] = useState(true);
  const [useUploaded, setUseUploaded] = useState(false);
  useInterval(status.reload, 5000);
  return <section className="grid gap-3 rounded-lg border border-line bg-panel p-4">
    <h2 className="text-sm font-semibold">Hi3D · {t("Improve an existing model")}</h2>
    <p className="text-xs text-ink-3">{t("Refine a selected model using its reference photo. Hi3D creates geometry only: no textures or UVs. The original stays in the gallery.")}</p>
    {status.error && <p role="alert" className="text-sm text-bad">{status.error}</p>}
    <p className="text-xs">{status.error || status.data?.error ? t("Could not check setup") : !status.data ? t("Checking.") : status.data.ready ? t("Hi3D is ready locally.") : t("Hi3D needs a separate local setup and the TRELLIS.2 runtime.")}</p>
    {status.data && !status.data.ready && !status.data.error && <Button disabled={running || !trellisReady} onClick={() => void act("/api/3d/hi3d/install", {})}>{t("Install Hi3D locally")}</Button>}
    {!modelId && <p className="text-xs text-ink-3">{t("Select a model in the gallery to refine it with Hi3D.")}</p>}
    {modelId && <p className="text-xs text-ink-3">{t("Source model")} · {modelId.slice(0, 8)}</p>}
    <div className="grid gap-3">
    <label className="text-sm">{t("Hi3D resolution")}<select className={input} value={resolution} disabled={running} onChange={e => setResolution(Number(e.target.value))}><option value={512}>512³</option><option value={1024}>1024³</option><option value={1536}>1536³</option></select></label>
    <p className="text-xs text-ink-3">{t("1536³ is experimental on 16 GB. Resolution is never lowered automatically. Structural errors in the source mesh may remain.")}</p>
    <div className="grid grid-cols-2 gap-3">
      <label className="text-sm">{t("Hi3D steps per pass")}<input className={input} type="number" min={1} max={50} value={steps} disabled={running} onChange={e => setSteps(Number(e.target.value))} /></label>
      <label className="text-sm">{t("Seed")}<input className={input} type="number" min={0} max={2147483647} value={seed} disabled={running} onChange={e => setSeed(Number(e.target.value))} /></label>
    </div>
    <label className="text-sm">{t("Hi3D input triangles")}<input className={input} type="number" min={10000} max={200000} step={10000} value={triangles} disabled={running} onChange={e => setTriangles(Number(e.target.value))} /></label>
    <p className="text-xs text-ink-3">{t("A separate simplified copy feeds Hi3D. The source file is never overwritten.")}</p>
    <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={cpuOffload} disabled={running} onChange={e => setCpuOffload(e.target.checked)} />{t("Hi3D CPU offloading")}</label>
    <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={useUploaded && !!image} disabled={running || !image} onChange={e => setUseUploaded(e.target.checked)} />{t("Use the newly uploaded photo for Hi3D")}</label>
    <p className="text-xs text-ink-3">{useUploaded && image ? t("The uploaded photo must show the same object as the selected mesh.") : t("Uses the selected model's saved reference photo.")}</p>
    </div>
    <Button variant="primary" disabled={running || !modelId || !status.data?.ready || steps < 1 || steps > 50 || triangles < 10000 || triangles > 200000 || seed < 0 || seed > 2147483647} onClick={() => void act(`/api/3d/gallery/${modelId}/refine`, { resolution, steps, seed, triangles, cpu_offload: cpuOffload, image: useUploaded && image ? image : null })}>{t("Refine with Hi3D")}</Button>
    <a className="text-xs text-accent underline" href="https://hi3d.ai" target="_blank" rel="noreferrer">Powered by Hi3D 1.0</a>
  </section>;
}
