import { useRef, useState } from "react";
import { Bookmark, Film, ImagePlus, Play, RefreshCw } from "lucide-react";
import { Button } from "../components/ui";
import { SavedVideoPrompts, SaveVideoPrompts, VideoPromptTexts, type SavedVideoPrompt, type VideoPromptPair } from "../components/VideoPrompts";
import { api } from "../lib/api";
import { errorMessage, useInterval, useLoad } from "../lib/hooks";
import { t } from "../lib/i18n";
import { VideoJobProgress, type VideoJob } from "../components/VideoJobProgress";
import { VideoPlayer } from "../components/VideoPlayer";
import { VideoUpscalePanel } from "../components/VideoUpscalePanel";
import type { Ref2VAStatus, VideoModel } from "../lib/types";
import { videoFrameCount, videoInputDescription, videoInputLabel } from "../lib/video";

interface Recipe { duration_seconds?: number | null; motion_frames?: number | null; model_key: string; end_image: string; image: string; prompt: string; negative: string; width: number; height: number; frames: number; fps: number; steps: number; cfg: number; seed: number; vsa_sparsity?: number }
interface Job extends VideoJob { kind?: string }
interface Status { available: boolean; models: VideoModel[]; jobs: Job[]; finishing: { ready: boolean }; ref2va: Ref2VAStatus }
interface Clip { id: string; request: Recipe; elapsed: number; model: string; output?: { width: number; height: number; fps: number; frames: number; seconds: number } | null }
const INITIAL: Recipe = { model_key: "wan22-i2v-q6", end_image: "", image: "", prompt: "", negative: "", duration_seconds: 8, width: 832, height: 480, frames: 129, fps: 16, steps: 20, cfg: 3.5, seed: -1 };
const inputStyle = "w-full rounded-md border border-line bg-bg p-2 text-sm text-ink";

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
  const last = status.data?.jobs.find(j => j.kind !== "upscale");
  const generationJob = job?.kind === "upscale" ? undefined : job;
  const model = status.data?.models.find(m => m.key === recipe.model_key);
  const referenceMode = model?.input_mode === "reference";
  const setup = status.data?.ref2va;
  const settingUp = setup?.job.status === "running";
  const multiple = model?.size_multiple ?? 16;
  const sizes: [number, number][] = referenceMode ? [[832, 480], [1024, 576], [1344, 768]] : model?.required_end ? [[1280, 720]] : [[832, 480], [1024, 576], [1280, 720]];
  const resolutions = sizes.map(([w, h]) => (portrait ? [h, w] : [w, h]).map(n => Math.ceil(n / multiple) * multiple));
  const duration = recipe.duration_seconds ?? 6;
  const nativeFrames = videoFrameCount(model, duration, recipe.motion_frames);

  function chooseModel(key: string) {
    const chosen = status.data?.models.find(m => m.key === key);
    if (!chosen) return;
    const reference = chosen.input_mode === "reference";
    setRecipe(r => ({ ...r, model_key: key, ...chosen.defaults,
      ...(reference ? { end_image: "", negative: "", duration_seconds: 5, vsa_sparsity: 0.75 } : { duration_seconds: Math.max(6, r.duration_seconds ?? 8) }),
      motion_frames: reference ? null : chosen.required_end ? 33 : r.end_image || r.motion_frames ? 81 : null,
      width: Math.ceil((portrait ? chosen.defaults.height : chosen.defaults.width) / chosen.size_multiple) * chosen.size_multiple,
      height: Math.ceil((portrait ? chosen.defaults.width : chosen.defaults.height) / chosen.size_multiple) * chosen.size_multiple,
    }));
    if (reference) { setEndPicture(""); setEndPictureName(""); }
  }
  const clips = gallery.data?.videos ?? [];
  const clip = clips.find(c => c.id === selected) ?? clips[0];
  useInterval(() => { status.reload(); gallery.reload(); }, job || settingUp ? 2500 : 12000);

  function reuseSettings(saved: Clip) {
    const chosen = status.data?.models.find(m => m.key === saved.request.model_key);
    if (!chosen) { setError(t("This saved video used a removed model. Choose an available model before generating again.")); return; }
    const minimum = chosen?.required_end ? 1280 * 720 : 832 * 480;
    const tooSmall = saved.request.width * saved.request.height < minimum;
    const w = tooSmall ? (chosen?.defaults.width ?? 832) : Math.max(saved.request.width, saved.request.height);
    const h = tooSmall ? (chosen?.defaults.height ?? 480) : Math.min(saved.request.width, saved.request.height);
    setRecipe({ ...INITIAL, ...saved.request, duration_seconds: saved.request.duration_seconds ?? 8,
      motion_frames: saved.request.motion_frames ?? (chosen?.required_end ? 33 : null),
      width: portrait ? h : w, height: portrait ? w : h, image: recipe.image, end_image: recipe.end_image });
  }

  function useSavedPrompts(pair: VideoPromptPair) {
    setRecipe(r => ({ ...r, prompt: pair.prompt, negative: referenceMode ? "" : pair.negative }));
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
      <div><h1 className="flex items-center gap-2 text-2xl font-semibold"><Film size={24} />{t("Videos")}</h1><p className="mt-1 text-sm text-ink-2">{t("Generate locally from a single image, first/last frames or a visual reference.")}</p></div>
      <div className="flex flex-wrap gap-2"><Button icon={<Bookmark size={14} />} onClick={() => librarySection.current?.scrollIntoView({ block: "start" })}>{t("Saved video prompts")}</Button><Button icon={<RefreshCw size={14} />} onClick={() => { status.reload(); gallery.reload(); }}>{t("Refresh")}</Button><Button loading={busy} onClick={() => void action(() => api.post("/api/images/engine/start"))}>{t("Start engine")}</Button><Button disabled={!!job || busy || !status.data?.available} onClick={() => void action(async () => {
        const result = await api.post<{ stopped: boolean }>("/api/images/engine/stop");
        if (!result.stopped) throw new Error(t("The engine was started outside Will It Run. Stop it there before restarting here."));
        await api.post("/api/images/engine/start");
      })}>{t("Restart engine")}</Button><Button disabled={!!job || busy} onClick={() => void action(() => api.post("/api/images/free"))}>{t("Free VRAM")}</Button></div>
    </header>
    {(error || status.error || gallery.error) && <p role="alert" className="rounded-lg border border-bad p-3 text-bad">{error || status.error || gallery.error}</p>}
    <VideoUpscalePanel clips={clips} locked={busy || !!job} onChange={status.reload} />
    <section className="rounded-xl border border-line bg-surface p-4">
      <label className="grid max-w-4xl gap-2 text-sm font-semibold">{t("Video model")}
        <select className={inputStyle} value={recipe.model_key} disabled={busy || !!job} onChange={e => chooseModel(e.target.value)}>
          {(status.data?.models ?? [{ key: INITIAL.model_key, name: "Wan 2.2 I2V A14B · Q6_K", ready: false, input_mode: "single_or_first_last" as const }]).map(m => <option key={m.key} value={m.key}>{m.name} · {videoInputLabel(m.input_mode)}{m.ready ? ` · ${t("Ready")}` : ""}</option>)}
        </select>
      </label>
      {model && <div className="my-3 grid gap-1 text-sm"><strong className="text-accent">{videoInputLabel(model.input_mode)}</strong><p className="text-ink-2">{videoInputDescription(model.input_mode)}</p></div>}
      {referenceMode && setup && <section className="my-3 grid gap-3 border-y border-line py-3 text-sm" aria-label={t("Ref2VA-VSA setup")}>
        <p className="text-ink-2">{t("The upstream recipe recommends 24 GB VRAM. Smaller GPUs are experimental; setup checks CUDA BF16 and sol_attn support.")}{setup.environment.vram_gb != null && <> {t("Detected VRAM: {gb} GB", { gb: setup.environment.vram_gb.toFixed(1) })}</>}</p>
        <p className="break-all text-xs text-ink-3">{t("Verified model files")}: {setup.models_dir}</p>
        {setup.environment.error && <p role="alert" className="text-warn">{setup.environment.error}</p>}
        {(!setup.weights_ready || !setup.source_ready) && <Button className="justify-self-start" disabled={busy || settingUp || !!job || !setup.environment.ready} onClick={() => void action(() => api.post("/api/videos/ref2va/setup"))}>{t("Set up Ref2VA-VSA locally")}</Button>}
        {settingUp && <><p role="status">{t("Downloading and verifying Ref2VA-VSA")} · {setup.job.percent}% · {(setup.job.received / 1e9).toFixed(1)} / {(setup.job.total / 1e9).toFixed(1)} GB</p><progress max={100} value={setup.job.percent} aria-label={t("Ref2VA-VSA setup progress")} className="h-2 w-full accent-accent" /><p className="break-all text-xs text-ink-3">{setup.job.file}</p><Button className="justify-self-start" onClick={() => void action(() => api.post("/api/videos/ref2va/setup/cancel"))}>{t("Cancel setup")}</Button></>}
        {setup.job.error && <p role="alert" className="text-bad">{setup.job.error}</p>}
        {setup.weights_ready && setup.source_ready && !model?.ready && <p className="text-ink-2">{t("Setup is verified. Start or restart the shared engine to load the node and model folders.")}</p>}
      </section>}
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
        <h2 className="font-semibold">{referenceMode ? t("Reference image") : t("Start and end frames")}</h2>
        <div className="grid gap-2 text-sm">
          <p className="font-medium">{referenceMode ? t("Reference image") : t("Starting picture")}</p>
          <input ref={startInput} className="hidden" type="file" accept="image/png,image/jpeg,image/webp" aria-label={referenceMode ? t("Reference image") : t("Starting picture")} onChange={e => { const f = e.target.files?.[0]; if (f) void upload(f); }} />
          <Button className="min-h-11 w-full justify-start border-line-strong px-4" icon={<ImagePlus size={20} />} loading={uploading === "start"} disabled={!status.data?.available || busy || !!job} onClick={() => startInput.current?.click()}>{referenceMode ? (picture ? t("Change reference image") : t("Choose reference image")) : picture ? t("Change starting picture") : t("Choose starting picture")}</Button>
          {pictureName && <p className="break-all text-xs text-ink-2">{pictureName}</p>}
        </div>
        {picture && <img src={picture} alt={referenceMode ? t("Reference image") : t("Starting picture")} className="max-h-52 w-full rounded-md object-contain" />}
        {!referenceMode && <div className="grid gap-2 text-sm">
          <p className="font-medium">{t("Ending picture")}{model?.required_end ? ` · ${t("Required")}` : ` · ${t("Optional")}`}</p>
          <input ref={endInput} className="hidden" type="file" accept="image/png,image/jpeg,image/webp" aria-label={t("Ending picture")} onChange={e => { const f = e.target.files?.[0]; if (f) void upload(f, true); }} />
          <Button className="min-h-11 w-full justify-start border-line-strong px-4" icon={<ImagePlus size={20} />} loading={uploading === "end"} disabled={!status.data?.available || busy || !!job} onClick={() => endInput.current?.click()}>{endPicture ? t("Change ending picture") : t("Choose ending picture")}</Button>
          {endPictureName && <p className="break-all text-xs text-ink-2">{endPictureName}</p>}
        </div>}
        <p className="text-xs text-ink-3">{t("PNG, JPG or WebP · up to 40 MB")}</p>
        {endPicture && <><img src={endPicture} alt={t("Ending picture")} className="max-h-52 w-full rounded-md object-contain" /><Button disabled={busy || !!job} onClick={() => { setRecipe(r => ({ ...r, end_image: "", motion_frames: null })); setEndPicture(""); setEndPictureName(""); if (endInput.current) endInput.current.value = ""; }}>{t("Remove ending picture")}</Button></>}
        {model?.required_end && !recipe.end_image && <p className="text-sm text-warn">{t("This model needs both pictures.")}</p>}

        <label className="grid gap-1 text-sm">{t("Describe the movement")}<textarea ref={promptInput} className={inputStyle} rows={4} value={recipe.prompt} onChange={e => setRecipe({ ...recipe, prompt: e.target.value })} /></label>
        {!referenceMode && <label className="grid gap-1 text-sm">{t("Negative prompt")}<textarea className={inputStyle} rows={2} value={recipe.negative} onChange={e => setRecipe({ ...recipe, negative: e.target.value })} /></label>}
        {referenceMode && <p className="text-xs text-ink-2">{t("This recipe uses only the positive prompt, 4 steps and CFG 1. Generated audio is preserved in the MP4.")}</p>}
        <SaveVideoPrompts prompt={recipe.prompt} negative={recipe.negative} onSaved={promptLibrary.reload} />
        <p className="text-xs text-ink-2">{t("Higher generation resolutions and longer clips need more memory and time. The final file is always 1920 × 1080 at 30 FPS.")}</p>
        <div className="grid grid-cols-2 gap-3">
          <label className="grid gap-1 text-sm">{t("Generation resolution")}<select className={inputStyle} value={`${recipe.width}x${recipe.height}`} onChange={e => { const [width = 512, height = 512] = e.target.value.split("x").map(Number); setRecipe({ ...recipe, width, height }); }}>{resolutions.map(([w, h]) => <option key={`${w}x${h}`} value={`${w}x${h}`}>{w} × {h}</option>)}</select></label>
          <label className="grid gap-1 text-sm">{t("Duration")}<select className={inputStyle} value={duration} onChange={e => setRecipe({ ...recipe, duration_seconds: Number(e.target.value) })}>{Array.from({ length: 16 - (model?.min_seconds ?? 6) }, (_, i) => i + (model?.min_seconds ?? 6)).map(n => <option key={n} value={n}>{n} s · {n * 30} {t("frames")}</option>)}</select></label>
          <label className="grid gap-1 text-sm">{t("Steps")}<input className={inputStyle} type="number" min={4} max={60} value={recipe.steps} disabled={referenceMode} onChange={e => setRecipe({ ...recipe, steps: Number(e.target.value) })} /></label>
          <label className="grid gap-1 text-sm">CFG<input className={inputStyle} type="number" min={1} max={10} step={0.5} value={recipe.cfg} disabled={referenceMode} onChange={e => setRecipe({ ...recipe, cfg: Number(e.target.value) })} /></label>
        </div>
        {!referenceMode && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!recipe.motion_frames} onChange={e => setRecipe({ ...recipe, motion_frames: e.target.checked ? (model?.required_end ? 33 : 81) : null })} />{t("Slow motion")}</label>}
        {referenceMode && <label className="grid gap-1 text-sm">{t("VSA sparsity")}<input className={inputStyle} type="number" min={0} max={0.98} step={0.01} value={recipe.vsa_sparsity ?? 0.75} onChange={e => setRecipe({ ...recipe, vsa_sparsity: Number(e.target.value) })} /><span className="text-xs text-ink-3">{t("Default 0.75 follows the upstream example. Reference and text tokens remain dense.")}</span></label>}
        <p className="text-xs text-ink-2">{recipe.motion_frames ? t("A shorter movement is slowed to {seconds} seconds before Full HD export. This can improve first/last-frame transitions.", { seconds: duration }) : t("Generated at {fps} FPS · {frames} frames, then motion interpolation to 30 FPS.", { fps: model?.defaults.fps ?? 16, frames: nativeFrames })}</p>
        <label className="grid gap-1 text-sm">{t("Seed (-1 = random)")}<input className={inputStyle} type="number" min={-1} max={2147483647} value={recipe.seed} onChange={e => setRecipe({ ...recipe, seed: Number(e.target.value) })} /></label>
        <Button variant="primary" icon={<Play size={14} />} loading={busy} disabled={!model?.ready || !status.data?.finishing.ready || !recipe.image || !recipe.prompt.trim() || !!job || (!!model?.required_end && !recipe.end_image)} onClick={() => void action(async () => { await api.post("/api/videos/generate", { ...recipe, duration_seconds: duration }); setSelected(null); })}>{t("Generate video")}</Button>
        {(generationJob || last) && <VideoJobProgress job={(generationJob || last)!} />}
        {generationJob && <Button onClick={() => void action(() => api.post(`/api/videos/jobs/${generationJob.id}/cancel`))}>{t("Cancel")}</Button>}
        {last?.status === "failed" && <p role="alert" className="text-sm text-bad">{last.error}</p>}
      </section>
      <section className="grid gap-4 rounded-xl border border-line bg-surface p-4">
        <h2 className="font-semibold">{t("Video gallery")}</h2>
        {clip ? <><VideoPlayer key={clip.id} id={clip.id} title={clip.request.prompt} /><VideoPromptTexts key={`texts-${clip.id}`} prompt={clip.request.prompt} negative={clip.request.negative || ""} /><SaveVideoPrompts key={`save-${clip.id}`} prompt={clip.request.prompt} negative={clip.request.negative || ""} onSaved={promptLibrary.reload} /><p className="text-xs text-ink-3">{clip.model} · {clip.output?.width ?? clip.request.width} × {clip.output?.height ?? clip.request.height} · {clip.output?.fps ?? clip.request.fps} FPS · {clip.output?.frames ?? clip.request.frames} {t("frames")}{clip.output ? ` · ${clip.output.seconds} s` : ""} · {clip.elapsed} s · Seed {clip.request.seed}</p><div className="flex gap-2"><Button onClick={() => reuseSettings(clip)}>{t("Reuse settings")}</Button><Button variant="danger" onClick={() => void action(() => api.del(`/api/videos/gallery/${clip.id}`))}>{t("Delete")}</Button></div><div className="grid gap-2">{clips.map(c => <button key={c.id} className="truncate rounded-md border border-line p-2 text-left text-sm hover:bg-raised" onClick={() => setSelected(c.id)}>{c.request.prompt}</button>)}</div></> : <div className="grid min-h-72 place-content-center gap-3 text-center text-ink-3"><Film size={48} className="mx-auto" /><p>{t("Your videos will appear here.")}</p></div>}
      </section>
    </div>
    <section ref={librarySection} className="rounded-xl border border-line bg-surface p-4">
      <SavedVideoPrompts items={promptLibrary.data?.prompts ?? []} loading={promptLibrary.loading} error={promptLibrary.error} onReload={promptLibrary.reload} onUse={useSavedPrompts} disabled={busy || !!job} />
    </section>
  </div>;
}
