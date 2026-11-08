import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { Box, Download, ImagePlus, Play, Trash2 } from "lucide-react";
import { Button } from "../components/ui";
import { ModelThumbnail } from "../components/ModelThumbnail";
import { Hi3DPanel } from "../components/Hi3DPanel";
import { ThreeDLab } from "../components/ThreeDLab";
import { api, getBackend, inTauri } from "../lib/api";
import { pickSavePath } from "../lib/dialog";
import { errorMessage, useInterval, useLoad } from "../lib/hooks";
import { t } from "../lib/i18n";

const ModelStudio = lazy(() => import("../components/ModelStudio").then((m) => ({ default: m.ModelStudio })));
interface Job { id: string; kind: string; status: string; error: string | null; percent: number; phase: string; elapsed: number; step?: number; total?: number }
interface Status { ready: boolean; distribution: string; distributions: string[]; job: Job | null; error?: string | null }
interface Model { id: string; elapsed: number | null; bytes: number; model?: string; source_id?: string; geometry_only?: boolean; appearance?: string; request: { resolution?: number; steps?: number | null }; web?: { bytes: number; triangles: number; texture_size: number } }
type Variant = "original" | "web";
const input = "w-full rounded-md border border-line bg-bg p-2 text-sm";

async function modelBytes(id: string, variant: Variant): Promise<ArrayBuffer> {
  const backend = await getBackend();
  const response = await fetch(`${backend.url}/api/3d/gallery/${id}/file?variant=${variant}`, { cache: "no-store", headers: backend.token ? { Authorization: `Bearer ${backend.token}` } : {} });
  if (!response.ok) throw new Error(t("Could not load the 3D model."));
  return response.arrayBuffer();
}

function phaseText(phase: string): string {
  const names: Record<string, string> = {
    starting: t("Starting…"), loading: t("Loading models…"), background: t("Removing background…"), conditioning: t("Reading reference…"),
    structure: t("Generating structure…"), shape_coarse: t("Generating shape…"), shape_detail: t("Refining geometry…"), materials: t("Generating materials…"),
    geometry_decode: t("Decoding geometry…"), texture_decode: t("Decoding textures…"), export_original: t("Exporting original…"), export_uv: t("Preparing texture coordinates…"), export_textures: t("Baking model textures…"), validating: t("Validating model…"),
    web_setup: t("Preparing web exporter…"), web_import: t("Reading original…"), web_simplify: t("Simplifying mesh…"), web_normal: t("Baking Normal Map…"),
    web_color: t("Baking color…"), web_roughness: t("Baking roughness…"), web_metallic: t("Baking metallic…"), web_materials: t("Preparing PBR textures…"),
    web_compress: t("Compressing Web GLB…"), web_validate: t("Validating Web GLB…"),
    hi3d_setup: t("Preparing Hi3D runtime…"), hi3d_download: t("Downloading Hi3D weights…"), hi3d_verify: t("Verifying Hi3D weights…"), hi3d_input: t("Preparing Hi3D input mesh…"), hi3d_loading: t("Loading Hi3D…"), hi3d_sampling: t("Refining geometry with Hi3D…"), hi3d_export: t("Exporting Hi3D geometry…"),
  };
  return names[phase] || t("Working…");
}

export function ThreeDPage() {
  const status = useLoad(() => api.get<Status>("/api/3d/status"));
  const gallery = useLoad(() => api.get<{ models: Model[] }>("/api/3d/gallery"));
  const [distribution, setDistribution] = useState("");
  const [image, setImage] = useState("");
  const [reference, setReference] = useState("");
  const [seed, setSeed] = useState(1);
  const [steps, setSteps] = useState(50);
  const [resolution, setResolution] = useState(1536);
  const [textureSize, setTextureSize] = useState(4096);
  const [triangles, setTriangles] = useState(30000);
  const [webSize, setWebSize] = useState(2048);
  const photoInput = useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [selection, setSelection] = useState<{ id: string; variant: Variant } | null>(null);
  const [data, setData] = useState<{ original: ArrayBuffer; web: ArrayBuffer | null; source: ArrayBuffer | null } | null>(null);
  const [deleted, setDeleted] = useState("");
  const [dismissedJob, setDismissedJob] = useState("");
  const [pending, setPending] = useState<{ id: string; source?: string } | null>(null);
  const job = status.data?.job;
  const running = busy || job?.status === "running";
  const selectedWebBytes = gallery.data?.models.find((model) => model.id === selection?.id)?.web?.bytes;
  const selectedModel = gallery.data?.models.find((model) => model.id === selection?.id);
  const sourceId = gallery.data?.models.some(model => model.id === selectedModel?.source_id) ? selectedModel?.source_id : undefined;
  useInterval(() => { status.reload(); gallery.reload(); }, 2000);
  useEffect(() => {
    let active = true;
    if (selection) void Promise.allSettled([modelBytes(selection.id, "original"), selectedWebBytes ? modelBytes(selection.id, "web") : Promise.resolve(null), sourceId ? modelBytes(sourceId, "original") : Promise.resolve(null)]).then(([original, web, source]) => {
      if (!active) return;
      if (original.status === "rejected") { setError(errorMessage(original.reason)); return; }
      setData({ original: original.value, web: web.status === "fulfilled" ? web.value : null, source: source.status === "fulfilled" ? source.value : null });
      if (web.status === "rejected") setError(errorMessage(web.reason));
    });
    return () => { active = false; };
  }, [selection, selectedWebBytes, sourceId]);
  useEffect(() => {
    if (!pending || job?.id !== pending.id || job.status === "running") return;
    if (job.status === "completed") {
      const chosen = { id: pending.source || pending.id, variant: pending.source ? "web" as const : "original" as const };
      queueMicrotask(() => { setData(null); setSelection(chosen); setPending(null); });
    } else queueMicrotask(() => { setData(null); setSelection(null); setPending(null); });
  }, [job, pending]);

  function clearPreview() { setSelection(null); setData(null); }
  async function upload(file: File) {
    setBusy(true); setError(""); setImage(""); setReference(""); clearPreview(); setPending(null); setDismissedJob(job?.id || "");
    const url = URL.createObjectURL(file);
    try {
      const media = document.createElement(file.type.startsWith("video/") ? "video" : "img");
      media.src = url;
      if (media instanceof HTMLVideoElement) {
        media.muted = true;
        await new Promise<void>((resolve, reject) => { media.onloadeddata = () => resolve(); media.onerror = () => reject(new Error(t("Could not read the reference."))); });
        if (media.duration > 0.1) await new Promise<void>((resolve) => { media.onseeked = () => resolve(); media.currentTime = Math.min(0.3, media.duration / 2); });
      } else await media.decode();
      const canvas = document.createElement("canvas");
      const w = media instanceof HTMLVideoElement ? media.videoWidth : media.naturalWidth;
      const h = media instanceof HTMLVideoElement ? media.videoHeight : media.naturalHeight;
      const scale = Math.min(1, 2048 / Math.max(w, h)); canvas.width = Math.round(w * scale); canvas.height = Math.round(h * scale);
      canvas.getContext("2d")!.drawImage(media, 0, 0, canvas.width, canvas.height);
      const png = canvas.toDataURL("image/png");
      const result = await api.post<{ image: string }>("/api/3d/upload", { data: png });
      setImage(result.image); setReference(png); setFileName(file.name);
    } catch (err) { setError(errorMessage(err)); }
    finally { URL.revokeObjectURL(url); setBusy(false); }
  }
  async function act(path: string, body: unknown, source?: string) {
    setBusy(true); setError(""); setDismissedJob("");
    const labMesh = path === "/api/3d/lab/generate" && !!body && typeof body === "object" && "engine" in body && (body.engine === "pixal3d" || body.engine === "supra-tex");
    const expectsModel = path === "/api/3d/generate" || path.endsWith("/refine") || !!source || labMesh;
    if (expectsModel) clearPreview();
    try { const started = await api.post<Job>(path, body); status.reload(); if (expectsModel) setPending({ id: started.id, source }); }
    catch (err) { setError(errorMessage(err)); }
    finally { setBusy(false); }
  }
  async function download(id: string, variant: Variant) {
    try {
      const name = `model-${id.slice(0, 8)}-${variant}.glb`;
      if (inTauri) {
        const path = await pickSavePath(name, "glb", variant === "original" ? t("Original detailed") : t("Export for Web"));
        if (path) await api.post(`/api/3d/gallery/${id}/save`, { path, variant });
      } else {
        const bytes = await modelBytes(id, variant);
        const url = URL.createObjectURL(new Blob([bytes], { type: "model/gltf-binary" }));
        const a = document.createElement("a"); a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
    } catch (err) { setError(errorMessage(err)); }
  }
  async function remove(id: string) {
    try { await api.del(`/api/3d/gallery/${id}`); if (selection?.id === id) clearPreview(); setDeleted(id); gallery.reload(); }
    catch (err) { setError(errorMessage(err)); }
  }
  async function restore() {
    try { await api.post(`/api/3d/gallery/${deleted}/restore`); setDeleted(""); gallery.reload(); }
    catch (err) { setError(errorMessage(err)); }
  }
  return <div className="grid min-h-0 flex-1 content-start gap-5 overflow-y-auto p-6">
    <header><h1 className="flex items-center gap-2 text-xl font-semibold"><Box size={22} />{t("3D")}</h1><p className="mt-1 text-sm text-ink-3">{t("Turn a reference image into a textured 3D model with Microsoft TRELLIS.2.")}</p></header>
    {(error || status.error || gallery.error || (job?.id !== dismissedJob && job?.error)) && <p role="alert" className="text-sm text-bad">{error || status.error || gallery.error || job?.error}</p>}
    <section className="grid gap-3 border-y border-line py-4">
      <p className="text-sm">{status.loading && !status.data ? t("Loading…") : status.error || status.data?.error ? t("Could not check setup") : status.data?.ready ? t("TRELLIS.2 is ready locally.") : t("TRELLIS.2 requires WSL2 and officially 24 GB VRAM. On 16 GB, 1536³ generation is experimental.")}</p>
      {status.data && !status.data.ready && !status.data.error && <><p className="text-xs text-ink-3">{t("Setup downloads the official code and dependencies. Model weights are downloaded and verified during setup. No account or API key is required.")}</p><label className="max-w-sm text-sm">{t("Linux distribution")}<select className={input} value={distribution || status.data?.distribution || ""} onChange={(e) => setDistribution(e.target.value)}><option value="">{t("Choose a distribution")}</option>{status.data?.distributions.map((d) => <option key={d}>{d}</option>)}</select></label><Button className="justify-self-start" disabled={running || !(distribution || status.data?.distribution)} onClick={() => void act("/api/3d/install", { distribution: distribution || status.data?.distribution })}>{t("Install TRELLIS.2 locally")}</Button></>}
      {status.data?.error && <p role="alert" className="text-sm text-warn">{status.data.error}</p>}
      {job && job.id !== dismissedJob && <div role="status" className="grid gap-2 text-sm">
        <p>{job.status === "running" ? phaseText(job.phase) : job.status === "completed" ? t("Completed") : t("Failed")} · {job.percent}% · {Math.round(job.elapsed)}s {job.status === "running" && job.step !== undefined && job.total && <span> · {t("Step")} {job.step}/{job.total}</span>}</p>
        <progress aria-label={t("3D task progress")} max={100} value={job.percent} className="h-2 w-full accent-accent" />
        {job.status === "running" && <p className="text-xs text-ink-3">{t("Progress follows generation stages and sampling steps. Final decoding and export may take longer; 100% means the file is ready.")}</p>}
      </div>}
    </section>

    <div className="grid min-w-0 items-start gap-5 lg:grid-cols-[230px_minmax(0,1fr)_250px] xl:grid-cols-[300px_minmax(280px,1fr)_300px]">
      <section className="grid min-w-0 content-start gap-3 rounded-lg border border-line bg-panel p-3 lg:max-h-[calc(100vh-240px)] lg:overflow-y-auto">

        <h2 className="text-sm font-semibold">{t("3. Preview and export")}</h2>
        <p className="text-xs text-ink-3">{t("Original detailed keeps the generated model. Export for Web creates a separate GLB with a simplified mesh, baked Normal Map and compressed PBR textures.")}</p>
        <p className="text-xs text-ink-3">{t("For maximum fidelity, save Original detailed. Web triangle and texture settings apply when you create or rebuild the Web copy.")}</p>
        <div className="flex flex-wrap gap-3"><label className="text-xs">{t("Web triangles")}<input className={input} type="number" min={25000} max={30000} step={1000} value={triangles} onChange={(e) => setTriangles(Number(e.target.value))} /></label><label className="text-xs">{t("Web textures")}<select className={input} value={webSize} onChange={(e) => setWebSize(Number(e.target.value))}><option value={1024}>1K</option><option value={2048}>2K</option></select></label></div>
        {deleted && <p role="status" className="flex items-center gap-3 text-sm">{t("Preview removed. You can undo this deletion.")}<Button disabled={running} onClick={() => void restore()}>{t("Undo deletion")}</Button></p>}
        {gallery.data?.models.length === 0 && <p className="text-sm text-ink-3">{t("Generated models will appear here.")}</p>}
        {gallery.data?.models.map((model) => <div key={model.id} className="grid gap-2 border-b border-line py-3 text-sm">
          <div className="flex flex-wrap items-center gap-3"><button aria-label={`${t("Model preview")} ${model.id.slice(0, 8)}`} aria-pressed={selection?.id === model.id} className="grid gap-1 rounded border border-line p-1 text-xs aria-pressed:border-accent" onClick={() => { setData(null); setSelection({ id: model.id, variant: "original" }); }}><ModelThumbnail id={model.id} load={modelBytes} /><span>{model.id.slice(0, 8)}</span></button><span className="text-ink-3">{model.model === "hi3d-official/Hi3Dv1.0" ? "Hi3D" : model.model?.startsWith("microsoft/TRELLIS") ? "TRELLIS.2" : model.model ?? t("Unknown")}{model.request.resolution ? ` · ${model.request.resolution}³` : ""} · {model.model === "hi3d-official/Hi3Dv1.0" ? t("Hi3D steps per pass") : t("Steps")}: {model.request.steps ?? t("Unknown")} · {(model.bytes / 1048576).toFixed(1)} MB · {model.elapsed == null ? t("Unknown") : `${model.elapsed}s`}</span></div>
          <div className="flex flex-wrap gap-2"><Button icon={<Download size={15} />} onClick={() => void download(model.id, "original")}>{t("Original detailed")}</Button>{!model.geometry_only && model.appearance !== "source_vertex_colors" && <Button disabled={running} onClick={() => void act(`/api/3d/gallery/${model.id}/web`, { triangles, texture_size: webSize }, model.id)}>{model.web ? t("Rebuild Web copy") : t("Export for Web")}</Button>}{model.web && <><Button onClick={() => { setData(null); setSelection({ id: model.id, variant: "web" }); }}>{t("View Web copy")}</Button><Button icon={<Download size={15} />} onClick={() => void download(model.id, "web")}>{t("Save Web GLB")}</Button></>}<Button icon={<Trash2 size={15} />} disabled={running} onClick={() => void remove(model.id)}>{t("Delete preview")}</Button></div>
          {model.geometry_only && <p className="text-xs text-ink-3">{t("Geometry only · no textures or UVs")}</p>}
          {model.appearance === "source_vertex_colors" && <p className="text-xs text-ink-3">{t("Approximate vertex colors transferred from source · no PBR textures or UVs")}</p>}
          {model.web && <p className="text-xs text-ink-3">{model.web.triangles.toLocaleString()} {t("triangles")} · {model.web.texture_size}px · {(model.web.bytes / 1048576).toFixed(2)} MB · Normal Map · Draco / WebP</p>}
        </div>)}
      </section>
      <section className="min-w-0 lg:sticky lg:top-0">
        {selection && data ? <Suspense fallback={<p>{t("Loading…")}</p>}><ModelStudio key={selection.id} id={selection.id} data={selection.variant === "web" && data.web ? data.web : data.original} source={data.source} variant={selection.variant} hasWeb={!!data.web} onVariant={(variant) => setSelection({ ...selection, variant })} /></Suspense> : <div className="grid h-[400px] place-items-center rounded-md bg-panel text-sm text-ink-3">{selection ? t("Loading…") : t("Select a generated model to preview it.")}</div>}
      </section>
      <aside className="grid min-w-0 gap-4"><details open className="rounded-lg border border-line bg-panel">
      <summary className="cursor-pointer p-4 text-sm font-semibold">{t("Photo and generation settings")} <span className="ml-2 text-xs font-normal text-ink-3">{fileName || `${resolution}³ · ${textureSize / 1024}K · ${steps} ${t("Steps")}`}</span></summary>
      <section className="grid content-start gap-5 border-t border-line p-4 ">
      <div className="grid content-start gap-3">
        <h2 className="text-sm font-semibold">{t("1. Upload a photo")}</h2>
        <input ref={photoInput} className="sr-only" aria-label={t("Reference image or video")} type="file" accept="image/*,video/mp4,video/webm" disabled={running} onChange={(e) => { const file = e.target.files?.[0]; if (file) void upload(file); e.target.value = ""; }} />
        <Button icon={<ImagePlus size={16} />} disabled={running} onClick={() => photoInput.current?.click()}>{t("Upload photo")}</Button>
        {fileName && <p className="truncate text-xs text-ink-3">{fileName}</p>}
        <p className="text-xs text-ink-3">{t("For videos, a frame near the start becomes the reference. The result is a static 3D model.")}</p>
        {reference && <img src={reference} alt={t("3D reference")} className="max-h-48 w-full rounded-md object-contain bg-bg" />}
        </div><div className="grid content-start gap-4 sm:grid-cols-2">
        <h2 className="text-sm font-semibold sm:col-span-2">{t("2. Generate the model")}</h2>
        <label className="text-sm sm:col-span-2">{t("Resolution")}<select className={input} value={resolution} disabled={running} onChange={(e) => setResolution(Number(e.target.value))}><option value={1024}>{t("1024³ · recommended for 16 GB")}</option><option value={1536}>{t("1536³ · experimental on 16 GB")}</option></select></label>
        <p className="text-xs text-ink-3 sm:col-span-2">{t("Uses CPU offloading to save GPU memory. The resolution is never reduced automatically; insufficient memory produces an error.")}</p>
        <label className="text-sm">{t("Original textures")}<select className={input} value={textureSize} disabled={running} onChange={(e) => setTextureSize(Number(e.target.value))}><option value={2048}>2K</option><option value={4096}>4K</option></select></label>
        <label className="text-sm">{t("Seed")}<input className={input} type="number" min={0} max={2147483647} value={seed} onChange={(e) => setSeed(Number(e.target.value))} /></label>
        <label className="text-sm">{t("Steps")}<input className={input} type="number" min={1} max={50} value={steps} onChange={(e) => setSteps(Number(e.target.value))} /></label>
        <p className="text-xs text-ink-3 sm:col-span-2">{t("50 steps is the maximum available, not a guarantee of better quality. A seed changes the variation, not the quality level.")}</p>
        <Button className="sm:col-span-2" variant="primary" icon={<Play size={15} />} disabled={!image || !status.data?.ready || running} onClick={() => void act("/api/3d/generate", { image, seed, steps, resolution, texture_size: textureSize })}>{t("Generate 3D model")}</Button>
      </div></section>
    </details><Hi3DPanel modelId={selection?.id} image={image} running={!!running} trellisReady={!!status.data?.ready} act={act} /></aside>
    </div>
    <ThreeDLab image={image} running={!!running} act={act} />
  </div>;
}
