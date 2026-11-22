import { useRef, useState } from "react";
import { ArrowDown, ArrowUp, Bookmark, Film, ImagePlus, Library, Play, RefreshCw, X } from "lucide-react";
import { Button, Select } from "../components/ui";
import { SavedVideoPrompts, SaveVideoPrompts, VideoPromptTexts, type SavedVideoPrompt, type VideoPromptPair } from "../components/VideoPrompts";
import { VideoCollections } from "../components/VideoCollections";
import { api, mediaBlob } from "../lib/api";
import { errorMessage, useInterval, useLoad } from "../lib/hooks";
import { t } from "../lib/i18n";
import { VideoJobProgress, type VideoJob } from "../components/VideoJobProgress";
import { VideoPlayer } from "../components/VideoPlayer";
import { VideoUpscalePanel } from "../components/VideoUpscalePanel";
import type { VideoCollection, VideoCollectionEntry, VideoInputMode, VideoModel, VideoSetupStatus } from "../lib/types";
import { defaultKeyframeTimes, galleryClipLabel, keyframeTimesProblem, videoFrameCount, videoInputDescription, videoInputLabel } from "../lib/video";

interface Recipe { duration_seconds?: number | null; model_key: string; input_mode?: VideoInputMode; image: string; images?: string[]; keyframe_times?: number[]; prompt: string; negative?: string; width: number; height: number; frames: number; fps: number; steps: number; cfg: number; seed: number; turbo?: boolean }
interface Job extends VideoJob { kind?: string }
interface Status { available: boolean; models: VideoModel[]; jobs: Job[]; finishing: { ready: boolean } }
interface Clip { id: string; request: Recipe; elapsed: number; model: string; created_at?: number; output?: { width: number; height: number; fps: number; frames: number; seconds: number } | null }
interface Keyframe { name: string; data: string; file: string }
const H3 = "minimax-h3-fl2va";
const INITIAL: Recipe = { model_key: H3, image: "", images: [], prompt: "", duration_seconds: 10, width: 1344, height: 768, frames: 243, fps: 24, steps: 20, cfg: 1, seed: -1, turbo: false };
const SIZES: Record<string, [number, number][]> = { [H3]: [[960, 544], [1152, 640], [1344, 768]], "ltx25-keyframes": [[1024, 576], [1280, 704]] };
const inputStyle = "w-full rounded-md border border-line bg-bg p-2 text-sm text-ink";

function SetupPanel({ title, setup, endpoint, busy, job, ready, onAction }: {
  title: string; setup: VideoSetupStatus; endpoint: string; busy: boolean; job: boolean; ready: boolean;
  onAction: (run: () => Promise<unknown>) => void;
}) {
  const running = setup.job.status === "running";
  return <section className="my-3 grid gap-3 border-y border-line py-3 text-sm" aria-label={t("{model} setup", { model: title })}>
    <p className="text-ink-2">{t("The upstream recipe recommends {gb} GB VRAM. Smaller GPUs are experimental.", { gb: setup.recommended_vram_gb })}{setup.environment.vram_gb != null && <> {t("Detected VRAM: {gb} GB", { gb: setup.environment.vram_gb.toFixed(1) })}</>}</p>
    <p className="break-all text-xs text-ink-3">{t("Verified model files")}: {setup.models_dir}</p>
    {setup.environment.error && <p role="alert" className="text-warn">{setup.environment.error}</p>}
    {!setup.weights_ready && <Button className="justify-self-start" disabled={busy || running || job || !setup.environment.ready} onClick={() => onAction(() => api.post(endpoint))}>{t("Set up {model} locally", { model: title })}</Button>}
    {running && <><p role="status">{t("Downloading and verifying {model}", { model: title })} · {setup.job.percent}% · {(setup.job.received / 1e9).toFixed(1)} / {(setup.job.total / 1e9).toFixed(1)} GB</p><progress max={100} value={setup.job.percent} aria-label={t("{model} setup progress", { model: title })} className="h-2 w-full accent-accent" /><p className="break-all text-xs text-ink-3">{setup.job.file}</p><Button className="justify-self-start" onClick={() => onAction(() => api.post(`${endpoint}/cancel`))}>{t("Cancel setup")}</Button></>}
    {setup.job.error && <p role="alert" className="text-bad">{setup.job.error}</p>}
    {setup.weights_ready && !ready && <p className="text-ink-2">{t("Setup is verified. Start or restart the shared engine to load the node and model folders.")}</p>}
  </section>;
}

export function VideosPage() {
  const status = useLoad(() => api.get<Status>("/api/videos/status"));
  const gallery = useLoad(() => api.get<{ videos: Clip[] }>("/api/videos/gallery"));
  const promptLibrary = useLoad(() => api.get<{ prompts: SavedVideoPrompt[] }>("/api/videos/prompts"));
  const collections = useLoad(() => api.get<{ collections: VideoCollection[] }>("/api/videos/collections"));
  const [recipe, setRecipe] = useState(INITIAL);
  const librarySection = useRef<HTMLElement>(null);
  const collectionsSection = useRef<HTMLElement>(null);
  const generatorSection = useRef<HTMLElement>(null);
  const promptInput = useRef<HTMLTextAreaElement>(null);
  const [keyframes, setKeyframes] = useState<Keyframe[]>([]);
  const [manualTimes, setManualTimes] = useState<number[] | null>(null);
  const [portrait, setPortrait] = useState(false);
  const [uploading, setUploading] = useState(false);
  const pictureInput = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const job = status.data?.jobs.find(j => j.status === "queued" || j.status === "running");
  const last = status.data?.jobs.find(j => j.kind !== "upscale");
  const generationJob = job?.kind === "upscale" ? undefined : job;
  const model = status.data?.models.find(m => m.key === recipe.model_key);
  const limit = model?.max_images ?? 9;
  const isH3 = recipe.model_key === H3;
  const textToVideo = recipe.input_mode === "text-to-video";
  const settingUp = status.data?.models.some(m => m.setup.job.status === "running");
  const multiple = model?.size_multiple ?? 32;
  const resolutions = (SIZES[recipe.model_key] ?? [[1280, 704]]).map(([w, h]) => (portrait ? [h, w] : [w, h]).map(n => Math.ceil(n / multiple) * multiple));
  const duration = recipe.duration_seconds ?? 10;
  const nativeFrames = videoFrameCount(model, duration);
  const times = manualTimes && manualTimes.length === keyframes.length ? manualTimes : defaultKeyframeTimes(keyframes.length, duration);
  const timesProblem = keyframeTimesProblem(times, duration);

  function chooseModel(key: string) {
    const chosen = status.data?.models.find(m => m.key === key);
    if (!chosen) return;
    const kept = keyframes.slice(0, chosen.max_images);
    setKeyframes(kept);
    const [w, h] = SIZES[key]?.at(-1) ?? [chosen.defaults.width, chosen.defaults.height];
    setRecipe(r => ({ ...r, model_key: key, input_mode: chosen.input_mode, ...chosen.defaults, turbo: false,
      images: kept.map(item => item.name), image: kept[0]?.name ?? "",
      width: portrait ? h : w, height: portrait ? w : h }));
  }
  const clips = gallery.data?.videos ?? [];
  const clip = clips.find(c => c.id === selected) ?? clips[0];
  useInterval(() => { status.reload(); gallery.reload(); }, job || settingUp ? 2500 : 12000);

  function reuseSettings(saved: Clip) {
    const chosen = status.data?.models.find(m => m.key === saved.request.model_key);
    if (!chosen) { setError(t("This saved video used a removed model. Choose an available model before generating again.")); return; }
    const w = Math.max(saved.request.width, saved.request.height), h = Math.min(saved.request.width, saved.request.height);
    setRecipe({ ...INITIAL, ...chosen.defaults, model_key: chosen.key, input_mode: saved.request.input_mode ?? chosen.input_mode,
      prompt: saved.request.prompt,
      duration_seconds: saved.request.duration_seconds ?? 10, seed: saved.request.seed, turbo: !!saved.request.turbo,
      width: portrait ? h : w, height: portrait ? w : h, image: recipe.image, images: recipe.images });
    const savedTimes = saved.request.keyframe_times ?? [];
    setManualTimes(savedTimes.length && savedTimes.length === keyframes.length ? savedTimes : null);
  }

  /** Load a collection video into the generator: its keyframe pictures (handed to the engine again), times, prompt and settings. */
  async function loadCollectionEntry(collectionId: string, entry: VideoCollectionEntry) {
    const chosen = status.data?.models.find(m => m.key === entry.model_key);
    if (!chosen) throw new Error(t("This saved video used a removed model. Choose an available model before generating again."));
    const base = `/api/videos/collections/${collectionId}/entries/${entry.id}`;
    const { images } = await api.post<{ images: string[] }>(`${base}/use`);
    const previews = await Promise.all(entry.keyframes.map(async (_, index) => URL.createObjectURL(await mediaBlob(`${base}/keyframes/${index}/file`))));
    setKeyframes(images.map((name, index) => ({ name, data: previews[index] ?? "", file: `${entry.title} · ${entry.keyframes[index]?.label ?? index + 1}` })));
    const width = entry.width ?? chosen.defaults.width, height = entry.height ?? chosen.defaults.height;
    setPortrait(height > width);
    setRecipe({ ...INITIAL, ...chosen.defaults, model_key: chosen.key, prompt: entry.prompt, duration_seconds: entry.duration_seconds,
      seed: entry.seed ?? -1, turbo: entry.turbo && chosen.turbo_steps != null, width, height, images, image: images[0] ?? "" });
    setManualTimes(entry.keyframe_times.length === images.length ? entry.keyframe_times : null);
    setError("");
    generatorSection.current?.scrollIntoView({ block: "start" });
  }

  function useSavedPrompts(pair: VideoPromptPair) {
    setRecipe(r => ({ ...r, prompt: pair.prompt }));
    promptInput.current?.focus();
    promptInput.current?.scrollIntoView({ block: "center" });
  }

  async function action(run: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await run(); status.reload(); gallery.reload(); }
    catch (err) { setError(errorMessage(err)); }
    finally { setBusy(false); }
  }

  function updateKeyframes(next: Keyframe[]) {
    setKeyframes(next);
    setManualTimes(null);
    setRecipe(r => ({ ...r, images: next.map(item => item.name), image: next[0]?.name ?? "" }));
  }

  async function upload(file: File) {
    if (file.size > 40 * 1024 * 1024) { setError(t("The picture is larger than 40 MB.")); return; }
    if (keyframes.length >= limit) { setError(t("You can add up to {count} keyframe pictures.", { count: limit })); return; }
    setUploading(true);
    try {
      await action(async () => {
        const data = await new Promise<string>((resolve, reject) => {
          const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = reject; reader.readAsDataURL(file);
        });
        // ComfyUI overwrites equal names: two photos may have the same original basename.
        const filename = `video-${crypto.randomUUID()}-${file.name.slice(-80)}`;
        const result = await api.post<{ name: string }>("/api/images/upload", { filename, data });
        if (keyframes.length === 0) {
          const preview = new Image();
          preview.src = data;
          await preview.decode();
          const isPortrait = preview.naturalHeight > preview.naturalWidth;
          setPortrait(isPortrait);
          setRecipe(r => ({ ...r,
            width: isPortrait ? Math.min(r.width, r.height) : Math.max(r.width, r.height),
            height: isPortrait ? Math.max(r.width, r.height) : Math.min(r.width, r.height) }));
        }
        updateKeyframes([...keyframes, { name: result.name, data, file: file.name }]);
      });
    } finally {
      setUploading(false);
      if (pictureInput.current) pictureInput.current.value = "";
    }
  }

  function move(index: number, by: number) {
    const target = index + by;
    if (target < 0 || target >= keyframes.length) return;
    const next = [...keyframes];
    const [item] = next.splice(index, 1);
    if (item) next.splice(target, 0, item);
    updateKeyframes(next);
  }

  function setTime(index: number, value: number) {
    setManualTimes(times.map((time, item) => item === index ? value : time));
  }

  function generate() {
    return action(async () => {
      await api.post("/api/videos/generate", { model_key: recipe.model_key, input_mode: recipe.input_mode ?? "keyframes", prompt: recipe.prompt,
        width: recipe.width, height: recipe.height, duration_seconds: duration, seed: recipe.seed, turbo: !!recipe.turbo,
        ...(!textToVideo && { images: recipe.images, ...(manualTimes && { keyframe_times: times }) }) });
      setSelected(null);
    });
  }

  return <div className="mx-auto grid min-h-0 w-full max-w-[1500px] flex-1 content-start gap-5 overflow-y-auto p-6">
    <header className="flex flex-wrap items-center justify-between gap-3">
      <div><h1 className="flex items-center gap-2 text-2xl font-semibold"><Film size={24} />{t("Videos")}</h1><p className="mt-1 text-sm text-ink-2">{t("Generate video and audio locally from a prompt or keyframe pictures.")}</p></div>
      <div className="flex flex-wrap gap-2"><Button icon={<Library size={14} />} onClick={() => collectionsSection.current?.scrollIntoView({ block: "start" })}>{t("Prompt collections")}</Button><Button icon={<Bookmark size={14} />} onClick={() => librarySection.current?.scrollIntoView({ block: "start" })}>{t("Saved video prompts")}</Button><Button icon={<RefreshCw size={14} />} onClick={() => { status.reload(); gallery.reload(); }}>{t("Refresh")}</Button><Button loading={busy} onClick={() => void action(() => api.post("/api/images/engine/start"))}>{t("Start engine")}</Button><Button disabled={!!job || busy || !status.data?.available} onClick={() => void action(async () => {
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
          {!status.data && <option value={recipe.model_key}>{t("Loading")}</option>}
          {(status.data?.models ?? []).map(m =><option key={m.key} value={m.key}>{m.name}{m.ready ? ` · ${t("Ready")}` : ""}</option>)}
        </select>
      </label>
      {model?.supports_text_to_video && <label className="my-3 grid max-w-xs gap-2 text-sm font-semibold">{t("Generation mode")}
        <Select value={recipe.input_mode ?? model.input_mode} disabled={busy || !!job} onChange={e => setRecipe(r => ({ ...r, input_mode: e.target.value as VideoInputMode }))}>
          <option value="keyframes">{t("Keyframes")}</option>
          <option value="text-to-video">{t("Text-to-Video")}</option>
        </Select>
      </label>}
      {model && <div className="my-3 grid gap-1 text-sm">
        {textToVideo ? <p className="text-ink-2">{t("The model generates the entire scene, including the first frame, from your prompt.")}</p> : <><strong className="text-accent">{videoInputLabel(model.max_images)}</strong><p className="text-ink-2">{videoInputDescription()}</p><p className="text-ink-2">{isH3 ? t("MiniMax H3 FL2VA: the highest quality. The first and last pictures are its trained first/last-frame task; pictures in between are pinned to their frames. 20 steps, or an 8-step Turbo draft.") : t("LTX-2.5: fast drafts and iterations. Half-resolution pass, x2 latent upscaler and a refinement pass, with every picture pinned to its frame.")}</p></>}
      </div>}
      {model && <SetupPanel title={model.name} setup={model.setup} endpoint={`/api/videos/models/${model.key}/setup`} busy={busy} job={!!job} ready={model.ready} onAction={action} />}
      <div className="my-3 rounded-lg border border-line bg-bg p-3 text-sm"><strong>1920 × 1080 · 16:9 · 30 FPS · {duration} s</strong><p className="mt-1 text-ink-2">{t("Final MP4 for a desktop hero. Motion interpolation adds new frames; Lanczos scaling produces Full HD. This is upscaled output.")}</p></div>
      {status.data && !status.data.finishing.ready && <p role="alert" className="text-warn">{t("Local FFmpeg and FFprobe are needed to finish Full HD videos.")}</p>}
      <div className="grid gap-1 text-xs">{model?.components.map(c => <div key={c.name} className="flex gap-2"><span className={c.visible ? "text-ok" : "text-warn"}>{c.visible ? t("Ready") : !c.size_valid ? t("Incomplete model file") : c.source_present ? t("Restart engine to detect the local file") : t("Not available to the engine")}</span><span className="break-all text-ink-2">{c.name}</span></div>)}</div>
      {!status.data?.available && <p className="mt-2 text-sm text-warn">{t("Start the shared Images and Videos engine here.")}</p>}
      {!!model?.missing_nodes.length && <p className="mt-2 text-sm text-warn">{t("Missing engine nodes")}: {model?.missing_nodes.join(", ")}</p>}
    </section>
    <div className="grid items-start gap-5 xl:grid-cols-[420px_1fr]">
      <section ref={generatorSection} className="grid gap-4 rounded-xl border border-line bg-surface p-4">
        <h2 className="font-semibold">{textToVideo ? t("Text-to-Video") : t("Keyframes")}</h2>
        {!textToVideo && <>
        <div className="grid gap-2 text-sm">
          <div className="flex items-baseline justify-between gap-2"><p className="font-medium">{t("Keyframe pictures, in order")}</p><span className="text-xs text-ink-3">{keyframes.length} / {limit}</span></div>
          <input ref={pictureInput} className="hidden" type="file" accept="image/png,image/jpeg,image/webp" aria-label={t("Keyframe pictures, in order")} onChange={e => { const f = e.target.files?.[0]; if (f) void upload(f); }} />
          <Button className="min-h-11 w-full justify-start border-line-strong px-4" icon={<ImagePlus size={20} />} loading={uploading} disabled={!status.data?.available || busy || !!job || keyframes.length >= limit} onClick={() => pictureInput.current?.click()}>{keyframes.length ? t("Add keyframe picture") : t("Choose the first keyframe picture")}</Button>
          <p className="text-xs text-ink-3">{t("Up to {count} pictures with the same framing ratio. PNG, JPG or WebP · 40 MB each.", { count: limit })}</p>
        </div>
        {keyframes.length > 0 && <ol className="grid gap-2">{keyframes.map((item, index) => <li key={item.name} className="grid grid-cols-[96px_1fr] gap-2 rounded-md border border-line bg-bg p-2">
          <img src={item.data} alt={t("Keyframe picture {number}", { number: index + 1 })} className="h-16 w-24 rounded object-cover" />
          <div className="grid content-start gap-1">
            <div className="flex items-center justify-between gap-1"><span className="truncate text-xs text-ink-2">{index + 1}. {item.file}</span><span className="flex gap-1">
              <button type="button" aria-label={t("Move keyframe {number} earlier", { number: index + 1 })} className="rounded p-1 text-ink-2 hover:text-ink disabled:opacity-40" disabled={busy || !!job || index === 0} onClick={() => move(index, -1)}><ArrowUp size={14} /></button>
              <button type="button" aria-label={t("Move keyframe {number} later", { number: index + 1 })} className="rounded p-1 text-ink-2 hover:text-ink disabled:opacity-40" disabled={busy || !!job || index === keyframes.length - 1} onClick={() => move(index, 1)}><ArrowDown size={14} /></button>
              <button type="button" aria-label={t("Remove keyframe {number}", { number: index + 1 })} className="rounded p-1 text-ink-2 hover:text-bad" disabled={busy || !!job} onClick={() => updateKeyframes(keyframes.filter((_, item) => item !== index))}><X size={14} /></button>
            </span></div>
            <label className="flex items-center gap-2 text-xs text-ink-2">{t("At second")}<input className="w-20 rounded border border-line bg-surface px-1 py-0.5 text-sm text-ink" type="number" min={0} max={duration} step={0.25} value={times[index] ?? 0} disabled={busy || !!job} onChange={e => setTime(index, Number(e.target.value))} /></label>
          </div>
        </li>)}</ol>}
        {keyframes.length > 1 && <div className="flex flex-wrap items-center gap-2 text-xs text-ink-2"><span>{manualTimes ? t("Times chosen by you.") : t("Times spread evenly from the first to the last frame.")}</span>{manualTimes && <Button onClick={() => setManualTimes(null)}>{t("Spread evenly")}</Button>}</div>}
        {timesProblem && keyframes.length > 0 && <p role="alert" className="text-sm text-warn">{timesProblem}</p>}
        </>}

        <label className="grid gap-1 text-sm">{textToVideo ? t("Describe the scene and movement") : t("Describe the movement")}<textarea ref={promptInput} className={inputStyle} rows={7} value={recipe.prompt} onChange={e => setRecipe({ ...recipe, prompt: e.target.value })} /></label>
        <div className="grid gap-1 rounded-md border border-line bg-bg p-2 text-xs text-ink-2">
          {textToVideo ? <p>{t("Describe the subject, setting and light, then the subject's action and the camera movement in order. Include the sounds you want to hear.")}</p> : <>
            <p>{isH3 ? t("Describe the path between the pictures: camera movement, speed and what changes. The official MiniMax H3 layout (frame alignment, soundscape, music) is added for you. Optional last lines: \"Sound: ...\" and \"Music: ...\".") : t("Describe the path between the pictures in one paragraph: camera movement, speed and what changes. The keyframe instruction is added for you.")}</p>
            <p>{t("For one continuous shot, write it as a single shot: name the camera moves in order (push in, dive, arc, glide), say \"no cuts\", and leave enough seconds between two very different pictures.")}</p>
          </>}
        </div>
        <SaveVideoPrompts prompt={recipe.prompt} negative="" onSaved={promptLibrary.reload} />
        <p className="text-xs text-ink-2">{t("Higher generation resolutions and longer clips need more memory and time. The final file is always 1920 × 1080 at 30 FPS.")}</p>
        <div className="grid grid-cols-2 gap-3">
          <label className="grid gap-1 text-sm">{t("Generation resolution")}<select className={inputStyle} value={`${recipe.width}x${recipe.height}`} onChange={e => { const [width = 1280, height = 704] = e.target.value.split("x").map(Number); setRecipe({ ...recipe, width, height }); }}>{resolutions.map(([w, h]) => <option key={`${w}x${h}`} value={`${w}x${h}`}>{w} × {h}</option>)}</select></label>
          <label className="grid gap-1 text-sm">{t("Duration")}<select className={inputStyle} value={duration} onChange={e => { setRecipe({ ...recipe, duration_seconds: Number(e.target.value) }); setManualTimes(null); }}>{Array.from({ length: 16 - (model?.min_seconds ?? 5) }, (_, i) => i + (model?.min_seconds ?? 5)).map(n => <option key={n} value={n}>{n} s · {n * 30} {t("frames")}</option>)}</select></label>
        </div>
        {model?.turbo_steps != null && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!recipe.turbo} disabled={busy || !!job} onChange={e => setRecipe({ ...recipe, turbo: e.target.checked })} />{t("Turbo draft · {steps} steps (faster, slightly softer motion)", { steps: model.turbo_steps })}</label>}
        <p className="text-xs text-ink-2">{t("Generated at {fps} FPS · {frames} frames, then motion interpolation to 30 FPS. Generated audio is preserved in the MP4.", { fps: model?.defaults.fps ?? 24, frames: nativeFrames })}</p>
        <label className="grid gap-1 text-sm">{t("Seed (-1 = random)")}<input className={inputStyle} type="number" min={-1} max={2147483647} value={recipe.seed} onChange={e => setRecipe({ ...recipe, seed: Number(e.target.value) })} /></label>
        <Button variant="primary" icon={<Play size={14} />} loading={busy} disabled={!model?.ready || !status.data?.finishing.ready || (!textToVideo && (keyframes.length === 0 || !!timesProblem)) || !recipe.prompt.trim() || !!job} onClick={() => void generate()}>{t("Generate video")}</Button>
        {(generationJob || last) && <VideoJobProgress job={(generationJob || last)!} />}
        {generationJob && <Button onClick={() => void action(() => api.post(`/api/videos/jobs/${generationJob.id}/cancel`))}>{t("Cancel")}</Button>}
        {last?.status === "failed" && <p role="alert" className="text-sm text-bad">{last.error}</p>}
      </section>
      <section className="grid content-start gap-4 rounded-xl border border-line bg-surface p-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <h2 className="font-semibold">{t("Video gallery")} {clips.length > 0 && <span className="font-normal text-ink-3">({clips.length})</span>}</h2>
          {clip && <label className="grid min-w-0 flex-1 gap-1 text-xs font-medium text-ink-2 sm:max-w-xl">{t("Video to show")}
            <Select value={clip.id} onChange={e => setSelected(e.target.value)}>
              {clips.map((c, index) => <option key={c.id} value={c.id}>{galleryClipLabel(index, c.request.prompt, c.output?.seconds ?? c.request.duration_seconds, c.created_at)}</option>)}
            </Select></label>}
        </div>
        {clip ? <><VideoPlayer key={clip.id} id={clip.id} title={clip.request.prompt} /><VideoPromptTexts key={`texts-${clip.id}`} prompt={clip.request.prompt} negative={clip.request.negative || ""} /><SaveVideoPrompts key={`save-${clip.id}`} prompt={clip.request.prompt} negative={clip.request.negative || ""} onSaved={promptLibrary.reload} /><p className="text-xs text-ink-3">{clip.model} · {clip.output?.width ?? clip.request.width} × {clip.output?.height ?? clip.request.height} · {clip.output?.fps ?? clip.request.fps} FPS · {clip.output?.frames ?? clip.request.frames} {t("frames")}{clip.output ? ` · ${clip.output.seconds} s` : ""} · {clip.elapsed} s · Seed {clip.request.seed}</p><div className="flex gap-2"><Button onClick={() => reuseSettings(clip)}>{t("Reuse settings")}</Button><Button variant="danger" onClick={() => void action(() => api.del(`/api/videos/gallery/${clip.id}`))}>{t("Delete")}</Button></div></> : <div className="grid min-h-72 place-content-center gap-3 text-center text-ink-3"><Film size={48} className="mx-auto" /><p>{t("Your videos will appear here.")}</p></div>}
      </section>
    </div>
    <section ref={collectionsSection} className="rounded-xl border border-line bg-surface p-4">
      <VideoCollections collections={collections.data?.collections ?? []} loading={collections.loading} error={collections.error} disabled={busy || !!job} engineReady={!!status.data?.available} onUse={loadCollectionEntry} />
    </section>
    <section ref={librarySection} className="rounded-xl border border-line bg-surface p-4">
      <SavedVideoPrompts items={promptLibrary.data?.prompts ?? []} loading={promptLibrary.loading} error={promptLibrary.error} onReload={promptLibrary.reload} onUse={useSavedPrompts} disabled={busy || !!job} />
    </section>
  </div>;
}
