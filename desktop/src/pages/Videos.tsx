import { useEffect, useRef, useState } from "react";
import { Bookmark, Download, Film, ImagePlus, Play, RefreshCw } from "lucide-react";
import { Button } from "../components/ui";
import { SavedVideoPrompts, SaveVideoPrompts, VideoPromptTexts, type SavedVideoPrompt, type VideoPromptPair } from "../components/VideoPrompts";
import { api, getBackend } from "../lib/api";
import { errorMessage, useInterval, useLoad } from "../lib/hooks";
import { t } from "../lib/i18n";
import { saveVideo } from "../lib/videoSave";
import { GoogleFlowPanel } from "../components/GoogleFlowPanel";
import { VideoJobProgress, type VideoJob } from "../components/VideoJobProgress";

interface Recipe { duration_seconds?: number | null; motion_frames?: number | null; model_key: string; end_image: string; image: string; prompt: string; negative: string; width: number; height: number; frames: number; fps: number; steps: number; cfg: number; seed: number }
interface Job extends VideoJob { request: Recipe }
interface VideoModel { key: string; name: string; ready: boolean; components: { name: string; visible: boolean; source_present: boolean | null; size_valid: boolean }[]; missing_nodes: string[]; required_end: boolean; size_multiple: number; defaults: { fps: number; steps: number; cfg: number; width: number; height: number } }
interface Status { available: boolean; models: VideoModel[]; jobs: Job[]; finishing: { ready: boolean } }
interface Clip { id: string; request: Recipe; elapsed: number; model: string; output?: { width: number; height: number; fps: number; frames: number; seconds: number } | null }
const INITIAL: Recipe = { model_key: "wan22-i2v-q6", end_image: "", image: "", prompt: "", negative: "", duration_seconds: 8, width: 832, height: 480, frames: 129, fps: 16, steps: 20, cfg: 3.5, seed: -1 };
const inputStyle = "w-full rounded-md border border-line bg-bg p-2 text-sm text-ink";

function VideoPlayer({ clip }: { clip: Clip }) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [savedPath, setSavedPath] = useState("");

  async function save() {
    setSaving(true); setError(""); setSavedPath("");
    try { setSavedPath(await saveVideo(clip.id) ?? ""); }
    catch (err) { setError(errorMessage(err)); }
    finally { setSaving(false); }
  }
  useEffect(() => {
    const controller = new AbortController();
    let objectUrl = "";
    void (async () => {
      try {
        const backend = await getBackend();
        const response = await fetch(`${backend.url}/api/videos/gallery/${clip.id}/file`, {
          headers: backend.token ? { Authorization: `Bearer ${backend.token}` } : {}, signal: controller.signal,
        });
        if (!response.ok) throw new Error(t("Could not load the video."));
        const blob = await response.blob();
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      } catch (err) { if (!controller.signal.aborted) setError(errorMessage(err)); }
    })();
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [clip.id]);
  return <div className="grid gap-2">
    {error && <p role="alert" className="text-bad">{error}</p>}
    {url ? <video className="max-h-[520px] w-full rounded-lg bg-black" src={url} controls controlsList="nodownload" preload="metadata" aria-label={clip.request.prompt} /> : <p>{t("Loading…")}</p>}
    <Button variant="primary" className="justify-self-start" icon={<Download size={16} />} loading={saving} onClick={() => void save()}>{t("Save video")}</Button>
    {savedPath && <p role="status" className="break-all text-sm text-ok">{t("Video saved to {path}", { path: savedPath })}</p>}
  </div>;
}

export function VideosPage() {
  const status = useLoad(() => api.get<Status>("/api/videos/status"));
  const gallery = useLoad(() => api.get<{ videos: Clip[] }>("/api/videos/gallery"));
  const promptLibrary = useLoad(() => api.get<{ prompts: SavedVideoPrompt[] }>("/api/videos/prompts"));
  const [recipe, setRecipe] = useState(INITIAL);
  const librarySection = useRef<HTMLElement>(null);
  const promptInput = useRef<HTMLTextAreaElement>(null);
  const [picture, setPicture] = useState("");
  const [endPicture, setEndPicture] = useState("");
  const [pictureName, setPictureName] = useState("");
  const [portrait, setPortrait] = useState(false);
  const [endPictureName, setEndPictureName] = useState("");
  const [uploading, setUploading] = useState<"start" | "end" | null>(null);
  const startInput = useRef<HTMLInputElement>(null);
  const endInput = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const job = status.data?.jobs.find(j => j.status === "queued" || j.status === "running");
  const last = status.data?.jobs[0];
  const model = status.data?.models.find(m => m.key === recipe.model_key);
  const multiple = model?.size_multiple ?? 16;
  const sizes: [number, number][] = model?.required_end ? [[1280, 720]] : [[832, 480], [1024, 576], [1280, 720]];
  const resolutions = sizes.map(([w, h]) => (portrait ? [h, w] : [w, h]).map(n => Math.ceil(n / multiple) * multiple));
  const duration = recipe.duration_seconds ?? 6;
  const nativeFrames = recipe.motion_frames ?? duration * (model?.defaults.fps ?? 16) + 1;

  function chooseModel(key: string) {
    const chosen = status.data?.models.find(m => m.key === key);
    if (!chosen) return;
    setRecipe(r => ({ ...r, model_key: key, ...chosen.defaults,
      motion_frames: chosen.required_end ? 33 : r.end_image || r.motion_frames ? 81 : null,
      width: Math.ceil((portrait ? chosen.defaults.height : chosen.defaults.width) / chosen.size_multiple) * chosen.size_multiple,
      height: Math.ceil((portrait ? chosen.defaults.width : chosen.defaults.height) / chosen.size_multiple) * chosen.size_multiple,
    }));
  }
  const clips = gallery.data?.videos ?? [];
  const clip = clips.find(c => c.id === selected) ?? clips[0];
  useInterval(() => { status.reload(); gallery.reload(); }, job ? 2500 : 12000);

  function reuseSettings(saved: Clip) {
    const chosen = status.data?.models.find(m => m.key === saved.request.model_key);
    const minimum = chosen?.required_end ? 1280 * 720 : 832 * 480;
    const tooSmall = saved.request.width * saved.request.height < minimum;
    const w = tooSmall ? (chosen?.defaults.width ?? 832) : Math.max(saved.request.width, saved.request.height);
    const h = tooSmall ? (chosen?.defaults.height ?? 480) : Math.min(saved.request.width, saved.request.height);
    setRecipe({ ...INITIAL, ...saved.request, duration_seconds: saved.request.duration_seconds ?? 8,
      motion_frames: saved.request.motion_frames ?? (chosen?.required_end ? 33 : null),
      width: portrait ? h : w, height: portrait ? w : h, image: recipe.image, end_image: recipe.end_image });
  }

  function useSavedPrompts(pair: VideoPromptPair) {
    setRecipe(r => ({ ...r, prompt: pair.prompt, negative: pair.negative }));
    promptInput.current?.focus();
    promptInput.current?.scrollIntoView({ block: "center" });
  }

  async function action(run: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await run(); status.reload(); gallery.reload(); }
    catch (err) { setError(errorMessage(err)); }
    finally { setBusy(false); }
  }

  async function upload(file: File, ending = false) {
    if (file.size > 40 * 1024 * 1024) { setError(t("The picture is larger than 40 MB.")); return; }
    setUploading(ending ? "end" : "start");
    try {
    await action(async () => {
      const data = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = reject; reader.readAsDataURL(file);
      });
      // ComfyUI overwrites equal names: two photos may have the same original basename.
      const filename = `video-${crypto.randomUUID()}-${file.name.slice(-80)}`;
      const result = await api.post<{ name: string }>("/api/images/upload", { filename, data });
      const preview = new Image();
      preview.src = data;
      await preview.decode();
      const isPortrait = preview.naturalHeight > preview.naturalWidth;
      setRecipe(r => ({ ...r, ...(ending ? { end_image: result.name, motion_frames: model?.required_end ? 33 : 81 } : {
        image: result.name,
        width: isPortrait ? Math.min(r.width, r.height) : Math.max(r.width, r.height),
        height: isPortrait ? Math.max(r.width, r.height) : Math.min(r.width, r.height),
      }) }));
      if (ending) { setEndPicture(data); setEndPictureName(file.name); }
      else { setPicture(data); setPictureName(file.name); setPortrait(isPortrait); }
    });
    } finally {
      setUploading(null);
      const input = ending ? endInput.current : startInput.current;
      if (input) input.value = "";
    }
  }

  return <div className="mx-auto grid min-h-0 w-full max-w-[1500px] flex-1 content-start gap-5 overflow-y-auto p-6">
    <header className="flex flex-wrap items-center justify-between gap-3">
      <div><h1 className="flex items-center gap-2 text-2xl font-semibold"><Film size={24} />{t("Videos")}</h1><p className="mt-1 text-sm text-ink-2">{t("Generate videos locally from a starting picture and an optional ending picture.")}</p></div>
      <div className="flex flex-wrap gap-2"><Button icon={<Bookmark size={14} />} onClick={() => librarySection.current?.scrollIntoView({ block: "start" })}>{t("Saved video prompts")}</Button><Button icon={<RefreshCw size={14} />} onClick={() => { status.reload(); gallery.reload(); }}>{t("Refresh")}</Button><Button loading={busy} onClick={() => void action(() => api.post("/api/images/engine/start"))}>{t("Start engine")}</Button><Button disabled={!!job || busy || !status.data?.available} onClick={() => void action(async () => {
        const result = await api.post<{ stopped: boolean }>("/api/images/engine/stop");
        if (!result.stopped) throw new Error(t("The engine was started outside Will It Run. Stop it there before restarting here."));
        await api.post("/api/images/engine/start");
      })}>{t("Restart engine")}</Button><Button disabled={!!job || busy} onClick={() => void action(() => api.post("/api/images/free"))}>{t("Free VRAM")}</Button></div>
    </header>
    <GoogleFlowPanel />
    {(error || status.error || gallery.error) && <p role="alert" className="rounded-lg border border-bad p-3 text-bad">{error || status.error || gallery.error}</p>}
    <section className="rounded-xl border border-line bg-surface p-4">
      <label className="grid max-w-xl gap-2 text-sm font-semibold">{t("Video model")}
        <select className={inputStyle} value={recipe.model_key} disabled={busy || !!job} onChange={e => chooseModel(e.target.value)}>
          {(status.data?.models ?? [{ key: INITIAL.model_key, name: "Wan 2.2 I2V A14B · Q6_K", ready: false }]).map(m => <option key={m.key} value={m.key}>{m.name}{m.ready ? ` · ${t("Ready")}` : ""}</option>)}
        </select>
      </label>
      <p className="my-2 text-sm text-ink-2">{t("Choose a local model and upload the starting picture. Add an ending picture to guide the final frame.")}</p>
      <div className="my-3 rounded-lg border border-line bg-bg p-3 text-sm"><strong>1920 × 1080 · 16:9 · 30 FPS · {duration} s</strong><p className="mt-1 text-ink-2">{t("Final MP4 for a desktop hero. Motion interpolation adds new frames; Lanczos scaling produces Full HD. This is upscaled output.")}</p></div>
      <p className="mb-3 text-sm text-ink-2">{t("The entire picture is preserved. Portrait pictures use a portrait generation size and black side borders in the Full HD video.")}</p>
      {model?.required_end && <p className="mb-3 text-sm text-ink-2">{t("This model needs native 720p generation for detail and stable motion. Small preview resolutions are not suitable.")}</p>}
      {status.data && !status.data.finishing.ready && <p role="alert" className="text-warn">{t("Local FFmpeg and FFprobe are needed to finish Full HD videos.")}</p>}
      <div className="grid gap-1 text-xs">{model?.components.map(c => <div key={c.name} className="flex gap-2"><span className={c.visible ? "text-ok" : "text-warn"}>{c.visible ? t("Ready") : !c.size_valid ? t("Incomplete model file") : c.source_present ? t("Restart engine to detect the local file") : t("Not available to the engine")}</span><span className="break-all text-ink-2">{c.name}</span></div>)}</div>
      {!status.data?.available && <p className="mt-2 text-sm text-warn">{t("Start the shared Images and Videos engine here.")}</p>}
      {!!model?.missing_nodes.length && <p className="mt-2 text-sm text-warn">{t("Missing engine nodes")}: {model?.missing_nodes.join(", ")}</p>}
    </section>
    <div className="grid items-start gap-5 xl:grid-cols-[380px_1fr]">
      <section className="grid gap-4 rounded-xl border border-line bg-surface p-4">
        <h2 className="font-semibold">{t("Start and end frames")}</h2>
        <div className="grid gap-2 text-sm">
          <p className="font-medium">{t("Starting picture")}</p>
          <input ref={startInput} className="hidden" type="file" accept="image/png,image/jpeg,image/webp" aria-label={t("Starting picture")} onChange={e => { const f = e.target.files?.[0]; if (f) void upload(f); }} />
          <Button className="min-h-11 w-full justify-start border-line-strong px-4" icon={<ImagePlus size={20} />} loading={uploading === "start"} disabled={!status.data?.available || busy || !!job} onClick={() => startInput.current?.click()}>{picture ? t("Change starting picture") : t("Choose starting picture")}</Button>
          {pictureName && <p className="break-all text-xs text-ink-2">{pictureName}</p>}
        </div>
        {picture && <img src={picture} alt={t("Starting picture")} className="max-h-52 w-full rounded-md object-contain" />}
        <div className="grid gap-2 text-sm">
          <p className="font-medium">{t("Ending picture")}{model?.required_end ? ` · ${t("Required")}` : ` · ${t("Optional")}`}</p>
          <input ref={endInput} className="hidden" type="file" accept="image/png,image/jpeg,image/webp" aria-label={t("Ending picture")} onChange={e => { const f = e.target.files?.[0]; if (f) void upload(f, true); }} />
          <Button className="min-h-11 w-full justify-start border-line-strong px-4" icon={<ImagePlus size={20} />} loading={uploading === "end"} disabled={!status.data?.available || busy || !!job} onClick={() => endInput.current?.click()}>{endPicture ? t("Change ending picture") : t("Choose ending picture")}</Button>
          {endPictureName && <p className="break-all text-xs text-ink-2">{endPictureName}</p>}
        </div>
        <p className="text-xs text-ink-3">{t("PNG, JPG or WebP · up to 40 MB")}</p>
        {endPicture && <><img src={endPicture} alt={t("Ending picture")} className="max-h-52 w-full rounded-md object-contain" /><Button disabled={busy || !!job} onClick={() => { setRecipe(r => ({ ...r, end_image: "", motion_frames: null })); setEndPicture(""); setEndPictureName(""); if (endInput.current) endInput.current.value = ""; }}>{t("Remove ending picture")}</Button></>}
        {model?.required_end && !recipe.end_image && <p className="text-sm text-warn">{t("This model needs both pictures.")}</p>}

        <label className="grid gap-1 text-sm">{t("Describe the movement")}<textarea ref={promptInput} className={inputStyle} rows={4} value={recipe.prompt} onChange={e => setRecipe({ ...recipe, prompt: e.target.value })} /></label>
        <label className="grid gap-1 text-sm">{t("Negative prompt")}<textarea className={inputStyle} rows={2} value={recipe.negative} onChange={e => setRecipe({ ...recipe, negative: e.target.value })} /></label>
        <SaveVideoPrompts prompt={recipe.prompt} negative={recipe.negative} onSaved={promptLibrary.reload} />
        <p className="text-xs text-ink-2">{t("Higher generation resolutions and longer clips need more memory and time. The final file is always 1920 × 1080 at 30 FPS.")}</p>
        <div className="grid grid-cols-2 gap-3">
          <label className="grid gap-1 text-sm">{t("Generation resolution")}<select className={inputStyle} value={`${recipe.width}x${recipe.height}`} onChange={e => { const [width = 512, height = 512] = e.target.value.split("x").map(Number); setRecipe({ ...recipe, width, height }); }}>{resolutions.map(([w, h]) => <option key={`${w}x${h}`} value={`${w}x${h}`}>{w} × {h}</option>)}</select></label>
          <label className="grid gap-1 text-sm">{t("Duration")}<select className={inputStyle} value={duration} onChange={e => setRecipe({ ...recipe, duration_seconds: Number(e.target.value) })}>{Array.from({ length: 10 }, (_, i) => i + 6).map(n => <option key={n} value={n}>{n} s · {n * 30} {t("frames")}</option>)}</select></label>
          <label className="grid gap-1 text-sm">{t("Steps")}<input className={inputStyle} type="number" min={4} max={60} value={recipe.steps} onChange={e => setRecipe({ ...recipe, steps: Number(e.target.value) })} /></label>
          <label className="grid gap-1 text-sm">CFG<input className={inputStyle} type="number" min={1} max={10} step={0.5} value={recipe.cfg} onChange={e => setRecipe({ ...recipe, cfg: Number(e.target.value) })} /></label>
        </div>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!recipe.motion_frames} onChange={e => setRecipe({ ...recipe, motion_frames: e.target.checked ? (model?.required_end ? 33 : 81) : null })} />{t("Slow motion")}</label>
        <p className="text-xs text-ink-2">{recipe.motion_frames ? t("A shorter movement is slowed to {seconds} seconds before Full HD export. This can improve first/last-frame transitions.", { seconds: duration }) : t("Generated at {fps} FPS · {frames} frames, then motion interpolation to 30 FPS.", { fps: model?.defaults.fps ?? 16, frames: nativeFrames })}</p>
        <label className="grid gap-1 text-sm">{t("Seed (-1 = random)")}<input className={inputStyle} type="number" min={-1} max={2147483647} value={recipe.seed} onChange={e => setRecipe({ ...recipe, seed: Number(e.target.value) })} /></label>
        <Button variant="primary" icon={<Play size={14} />} loading={busy} disabled={!model?.ready || !status.data?.finishing.ready || !recipe.image || !recipe.prompt.trim() || !!job || (!!model?.required_end && !recipe.end_image)} onClick={() => void action(async () => { await api.post("/api/videos/generate", { ...recipe, duration_seconds: duration }); setSelected(null); })}>{t("Generate video")}</Button>
        {(job || last) && <VideoJobProgress job={(job || last)!} />}
        {job && <Button onClick={() => void action(() => api.post(`/api/videos/jobs/${job.id}/cancel`))}>{t("Cancel")}</Button>}
        {last?.status === "failed" && <p role="alert" className="text-sm text-bad">{last.error}</p>}
      </section>
      <section className="grid gap-4 rounded-xl border border-line bg-surface p-4">
        <h2 className="font-semibold">{t("Video gallery")}</h2>
        {clip ? <><VideoPlayer key={clip.id} clip={clip} /><VideoPromptTexts key={`texts-${clip.id}`} prompt={clip.request.prompt} negative={clip.request.negative || ""} /><SaveVideoPrompts key={`save-${clip.id}`} prompt={clip.request.prompt} negative={clip.request.negative || ""} onSaved={promptLibrary.reload} /><p className="text-xs text-ink-3">{clip.model} · {clip.output?.width ?? clip.request.width} × {clip.output?.height ?? clip.request.height} · {clip.output?.fps ?? clip.request.fps} FPS · {clip.output?.frames ?? clip.request.frames} {t("frames")}{clip.output ? ` · ${clip.output.seconds} s` : ""} · {clip.elapsed} s · Seed {clip.request.seed}</p><div className="flex gap-2"><Button onClick={() => reuseSettings(clip)}>{t("Reuse settings")}</Button><Button variant="danger" onClick={() => void action(() => api.del(`/api/videos/gallery/${clip.id}`))}>{t("Delete")}</Button></div><div className="grid gap-2">{clips.map(c => <button key={c.id} className="truncate rounded-md border border-line p-2 text-left text-sm hover:bg-raised" onClick={() => setSelected(c.id)}>{c.request.prompt}</button>)}</div></> : <div className="grid min-h-72 place-content-center gap-3 text-center text-ink-3"><Film size={48} className="mx-auto" /><p>{t("Your videos will appear here.")}</p></div>}
      </section>
    </div>
    <section ref={librarySection} className="rounded-xl border border-line bg-surface p-4">
      <SavedVideoPrompts items={promptLibrary.data?.prompts ?? []} loading={promptLibrary.loading} error={promptLibrary.error} onReload={promptLibrary.reload} onUse={useSavedPrompts} disabled={busy || !!job} />
    </section>
  </div>;
}
