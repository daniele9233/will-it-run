import { useEffect, useRef, useState } from "react";
import { Button } from "./ui";
import { api, inTauri } from "../lib/api";
import { pickSavePath } from "../lib/dialog";
import { errorMessage, useInterval, useLoad } from "../lib/hooks";
import { t } from "../lib/i18n";

interface Scene { id: string; prompt: string; title: string; complete: boolean; parent_id?: string }
interface CodeJob { id: string; status: string }

export function ThreeDCode({ running, refresh, job }: { running: boolean; refresh: () => void; job?: CodeJob | null }) {
  const models = useLoad(() => api.get<{ models: { model_key: string; name: string }[] }>("/api/3d/coder/models"));
  const scenes = useLoad(() => api.get<{ scenes: Scene[] }>("/api/3d/coder/scenes"));
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState("");
  const [preview, setPreview] = useState("");
  const [previewError, setPreviewError] = useState("");
  const [pending, setPending] = useState("");
  const [height, setHeight] = useState(560);
  const [expanded, setExpanded] = useState(false);
  const frame = useRef<HTMLIFrameElement>(null);
  const expandButton = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const previewRequest = useRef(0);
  useInterval(scenes.reload, 4000);
  useEffect(() => {
    const receive = (event: MessageEvent) => {
      if (event.source !== frame.current?.contentWindow || event.data?.type !== "wir-scene-preview") return;
      if (event.data.status === "error") setPreviewError(String(event.data.message || t("The scene could not be displayed.")).slice(0, 1000));
    };
    window.addEventListener("message", receive);
    return () => window.removeEventListener("message", receive);
  }, []);
  useEffect(() => {
    if (!expanded) return;
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    expandButton.current?.focus();
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") { event.preventDefault(); setExpanded(false); } };
    const containFocus = (event: FocusEvent) => { if (event.target instanceof Node && !panel.current?.contains(event.target)) expandButton.current?.focus(); };
    document.addEventListener("focusin", containFocus);
    window.addEventListener("keydown", close, true);
    return () => { document.body.style.overflow = overflow; document.removeEventListener("focusin", containFocus); window.removeEventListener("keydown", close, true); previous?.focus(); };
  }, [expanded]);
  useEffect(() => {
    if (!pending || job?.id !== pending || job.status === "running") return;
    const done = job.status === "completed";
    queueMicrotask(() => { if (done) { scenes.reload(); void show(pending); } setPending(""); });
    // Match the submitted job before opening its result.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job, pending]);
  async function start(id?: string, repairError = "") {
    setBusy(true); setError(""); setPreview(""); setPreviewError(""); ++previewRequest.current;
    try {
      const started = await api.post<CodeJob>(id ? `/api/3d/coder/${id}/repair` : "/api/3d/coder/generate", id ? { error: repairError } : { model_key: models.data?.models[0]?.model_key, prompt });
      setPending(started.id); refresh();
    } catch (err) { setError(errorMessage(err)); }
    finally { setBusy(false); }
  }
  async function show(id: string) {
    const request = ++previewRequest.current;
    setSelected(id); setError(""); setPreview(""); setPreviewError("");
    try { const response = await api.post<{ url: string }>(`/api/3d/coder/${id}/preview`); if (request === previewRequest.current) setPreview(response.url); }
    catch (err) { if (request === previewRequest.current) setError(errorMessage(err)); }
  }
  async function remove(id: string) {
    try {
      await api.del(`/api/3d/coder/${id}`);
      if (selected === id) { ++previewRequest.current; setSelected(""); setPreview(""); setPreviewError(""); setExpanded(false); }
      scenes.reload();
    } catch (err) { setError(errorMessage(err)); }
  }
  async function save(id: string) {
    try {
      if (inTauri) {
        const path = await pickSavePath(`scene-${id.slice(0, 8)}.html`, "html", t("Export HTML"));
        if (path) await api.post(`/api/3d/coder/${id}/save`, { path });
      }
    } catch (err) { setError(errorMessage(err)); }
  }
  return <section className="grid gap-3 border-t border-line pt-5">
    <h2 className="font-semibold">Qwen3.5 · 3D Coder</h2>
    <p className="text-sm text-ink-3">{t("Create a Three.js scene from a description. This coder generates HTML and JavaScript; use TRELLIS.2 above to reconstruct a photo as a GLB.")}</p>
    <p className="text-xs text-ink-3">{t("Runs locally through LM Studio with MTP disabled. The model starts only when you press Generate scene.")}</p>
    <div className="grid min-w-0 items-start gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
    <div className="grid min-w-0 content-start gap-3 rounded-lg border border-line bg-panel p-3 lg:max-h-[760px] lg:overflow-y-auto">
    {models.data?.models[0] ? <p className="text-sm">{models.data.models[0].name}</p> : <p className="text-sm">{models.loading ? t("Loading…") : t("Qwen 3D Coder was not found in LM Studio. Refresh the installed model list.")}</p>}
    <textarea aria-label={t("Describe your 3D scene")} placeholder={t("Describe your 3D scene")} value={prompt} onChange={(e) => setPrompt(e.target.value)} maxLength={12000} rows={4} className="rounded-md border border-line bg-bg p-3 text-sm" />
    <div className="flex gap-2"><Button disabled={running || busy || !prompt.trim() || !models.data?.models.length} onClick={() => void start()}>{t("Generate scene")}</Button><Button onClick={models.reload}>{t("Refresh models")}</Button></div>
    {(error || models.error || scenes.error) && <p role="alert" className="text-sm text-bad">{error || models.error || scenes.error}</p>}
    {scenes.data?.scenes.map((scene) => <div key={scene.id} className={`flex flex-wrap items-center gap-3 rounded border p-3 text-sm ${selected === scene.id ? "border-accent bg-panel" : "border-line"}`}><span className="max-w-md truncate" title={scene.prompt}>{scene.title || scene.prompt}</span>{scene.complete ? <><Button onClick={() => void show(scene.id)}>{t("Preview scene")}</Button>{inTauri && <Button onClick={() => void save(scene.id)}>{t("Export HTML")}</Button>}</> : <><span className="text-warn">{t("Incomplete code")}</span><Button disabled={running || busy} onClick={() => void start(scene.id)}>{t("Complete scene")}</Button></>}<Button disabled={running || busy} onClick={() => void remove(scene.id)}>{t("Delete scene")}</Button>{scene.parent_id && <span className="text-xs text-ink-3">{t("Recovered copy")}</span>}</div>)}
    </div>
    {preview ? <div ref={panel} role={expanded ? "dialog" : undefined} aria-modal={expanded || undefined} aria-label={t("Generated Three.js scene")} className={expanded ? "fixed inset-0 z-[90] flex flex-col gap-3 bg-bg p-4" : "grid gap-3"}>
      <div className="flex flex-wrap items-center gap-3"><h3 className="mr-auto text-sm font-semibold">{t("Scene studio")}</h3>{!expanded && <label className="text-xs">{t("Preview height")} <input aria-label={t("Scene preview height")} type="range" min={300} max={900} step={10} value={height} onChange={(event) => setHeight(Number(event.target.value))} /></label>}<button ref={expandButton} className="rounded-md border border-line px-3 py-2 text-sm" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? t("Reduce preview") : t("Expand preview")}</button></div>
      {previewError && <p role="alert" className="text-sm text-bad">{t("Scene preview error")}: {previewError}</p>}
      {previewError && <Button disabled={running || busy} onClick={() => void start(selected, previewError)}>{t("Repair scene")}</Button>}
      <iframe key={preview} ref={frame} title={t("Generated Three.js scene")} src={preview} sandbox="allow-scripts" referrerPolicy="no-referrer" className="min-h-0 w-full rounded-md border border-line bg-panel" style={{ height: expanded ? "100%" : height, flex: expanded ? 1 : undefined }} />
    </div> : <div className="grid h-[400px] place-items-center rounded-lg border border-line bg-panel p-4 text-center text-sm text-ink-3">{t("Select a scene to preview it.")}</div>}
    </div>
  </section>;
}
