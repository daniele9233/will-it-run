import { useState } from "react";
import { FolderOpen, Play, ScanLine } from "lucide-react";
import { Button } from "./ui";
import { VideoPlayer } from "./VideoPlayer";
import { VideoJobProgress } from "./VideoJobProgress";
import { api, inTauri } from "../lib/api";
import { errorMessage, useInterval, useLoad } from "../lib/hooks";
import { t } from "../lib/i18n";
import type { RelayVSRStatus, UpscaledVideo } from "../lib/types";

const field = "w-full rounded-md border border-line bg-bg p-2 text-sm text-ink";
const base = "/api/videos/upscale";

export function VideoUpscalePanel({ clips, locked, onChange }: {
  clips: { id: string; model: string; request: { prompt: string } }[];
  locked: boolean; onChange: () => void;
}) {
  const status = useLoad(() => api.get<RelayVSRStatus>(`${base}/status`));
  const gallery = useLoad(() => api.get<{ videos: UpscaledVideo[] }>(`${base}/gallery`));
  const [expanded, setExpanded] = useState(false);
  const [source, setSource] = useState<string | null>(null);
  const [path, setPath] = useState("");
  const [factor, setFactor] = useState<2 | 4>(2);
  const [target, setTarget] = useState("native");
  const [detail, setDetail] = useState(1);
  const [start, setStart] = useState(0);
  const [duration, setDuration] = useState(0);
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const state = status.data;
  const setup = state?.setup;
  const settingUp = setup?.job.status === "running";
  const job = state?.jobs.find(j => j.status === "queued" || j.status === "running");
  const last = state?.jobs[0];
  const model = state?.models.find(m => m.factor === factor);
  const sourceId = source ?? clips[0]?.id ?? "";
  const result = gallery.data?.videos.find(v => v.id === selected) ?? gallery.data?.videos[0];
  useInterval(() => { status.reload(); gallery.reload(); }, job || settingUp ? 2500 : expanded ? 12000 : null);

  async function act(work: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await work(); status.reload(); gallery.reload(); onChange(); }
    catch (err) { setError(errorMessage(err)); }
    finally { setBusy(false); }
  }
  async function chooseFile() {
    const { open } = await import("@tauri-apps/plugin-dialog");
    const chosen = await open({ multiple: false, title: t("Choose a video to upscale"), filters: [{ name: t("Videos"), extensions: ["mp4", "mov", "mkv", "webm", "avi"] }] });
    if (typeof chosen === "string") { setPath(chosen); setSource(""); }
  }
  return <details className="rounded-xl border border-line bg-surface" onToggle={e => setExpanded(e.currentTarget.open)}>
    <summary className="cursor-pointer p-4 text-sm font-semibold"><span className="inline-flex items-center gap-2"><ScanLine size={18} />{t("Video upscaling · RelayVSR 2x / 4x")}</span><span className="ml-3 text-xs font-normal text-ink-3">{state ? `${state.models.filter(m => m.ready).length}/2 ${t("Ready")}` : t("Checking.")}</span></summary>
    {expanded && <div className="grid gap-4 border-t border-line p-4">
      <p className="text-sm text-ink-2">{t("Restore detail in an existing video. Frame order, average frame rate and audio are retained. The source display aspect ratio is respected.")}</p>
      {(error || status.error || gallery.error) && <p role="alert" className="text-sm text-bad">{error || status.error || gallery.error}</p>}
      {state?.disk.free != null && <p className="text-xs text-ink-3">{t("Model drive: {free} GB free · {needed} GB still to download", { free: (state.disk.free / 1e9).toFixed(2), needed: (state.disk.needed / 1e9).toFixed(2) })}</p>}
      {setup && !setup.weights_ready && <div className="grid gap-2 text-sm">
        <p>{t("Both RelayVSR models and their matching Flash decoders total 10.58 GB. Downloads are verified and can resume.")}</p>
        <Button className="justify-self-start" disabled={busy || locked || settingUp || !setup.environment.ready || state?.disk.safe === false} onClick={() => void act(() => api.post(`${base}/setup`))}>{t("Install RelayVSR locally")}</Button>
      </div>}
      {setup?.environment.error && <p role="alert" className="text-sm text-warn">{setup.environment.error}</p>}
      {setup?.job.error && <p role="alert" className="text-sm text-bad">{setup.job.error}</p>}
      {settingUp && setup && <div className="grid gap-2 text-sm"><p role="status">{t("Downloading and verifying models")} · {setup.job.percent}% · {(setup.job.received / 1e9).toFixed(2)} / {(setup.job.total / 1e9).toFixed(2)} GB</p><progress max={100} value={setup.job.percent} className="h-2 w-full accent-accent" aria-label={t("RelayVSR download progress")} /><Button className="justify-self-start" onClick={() => void act(() => api.post(`${base}/setup/cancel`))}>{t("Cancel setup")}</Button></div>}
      <div className="grid gap-4 xl:grid-cols-2">
        <div className="grid content-start gap-3">
          <label className="grid gap-1 text-sm">{t("Video to upscale")}<select className={field} value={sourceId} disabled={locked || busy} onChange={e => setSource(e.target.value)}><option value="">{t("Local video file")}</option>{clips.map(clip => <option key={clip.id} value={clip.id}>{clip.model} · {clip.request.prompt.slice(0, 90) || clip.id.slice(0, 8)}</option>)}</select></label>
          {!sourceId && <label className="grid gap-1 text-sm">{t("Video file path")}<span className="flex gap-2"><input className={field} value={path} disabled={locked || busy} onChange={e => setPath(e.target.value)} />{inTauri && <Button aria-label={t("Choose a video to upscale")} icon={<FolderOpen size={16} />} disabled={locked || busy} onClick={() => void act(chooseFile)} />}</span></label>}
          <div className="grid grid-cols-2 gap-3">
            <label className="grid gap-1 text-sm">{t("Upscaling model")}<select className={field} value={factor} disabled={locked || busy} onChange={e => setFactor(Number(e.target.value) as 2 | 4)}><option value={2}>RelayVSR 2× · LightX2V</option><option value={4}>RelayVSR 4×</option></select></label>
            <label className="grid gap-1 text-sm">{t("Output size")}<select className={field} value={target} disabled={locked || busy} onChange={e => setTarget(e.target.value)}><option value="native">{t("Native model scale")}</option><option value="1080p">1080p · Full HD</option><option value="4k">4K · UHD</option></select></label>
            <label className="grid gap-1 text-sm">{t("Start time (seconds)")}<input className={field} type="number" min={0} step={0.1} value={start} disabled={locked || busy} onChange={e => setStart(Number(e.target.value))} /></label>
            <label className="grid gap-1 text-sm">{t("Duration (0 = whole video)")}<input className={field} type="number" min={0} step={0.1} value={duration} disabled={locked || busy} onChange={e => setDuration(Number(e.target.value))} /></label>
          </div>
          <p className="text-xs text-ink-2">{target === "native" ? t("2x doubles width and height; 4x quadruples them. Native outputs larger than 4K require an explicit output preset.") : t("The selected output preset prepares a lower-resolution input for the model, preserving the full image with padding when needed.")}</p>
          <label className="grid gap-1 text-sm">{t("Detail strength")}<input className={field} type="number" min={0} max={1} step={0.05} value={detail} disabled={locked || busy} onChange={e => setDetail(Number(e.target.value))} /></label>
          {!!model?.missing_nodes.length && <p className="text-xs text-warn">{t("Missing engine nodes")}: {model.missing_nodes.join(", ")}</p>}
          <Button variant="primary" className="justify-self-start" icon={<Play size={15} />} loading={busy} disabled={locked || !model?.ready || !state?.tools_ready || (!sourceId && !path.trim())} onClick={() => void act(async () => { await api.post(base, { source_id: sourceId, path: sourceId ? "" : path, factor, target, detail, start_seconds: start, duration_seconds: duration }); setSelected(""); })}>{t("Upscale video")}</Button>
          {(job || last) && <VideoJobProgress job={(job || last)!} />}
          {job && <Button className="justify-self-start" onClick={() => void act(() => api.post(`/api/videos/jobs/${job.id}/cancel`))}>{t("Cancel")}</Button>}
          {last?.error && <p role="alert" className="text-sm text-bad">{last.error}</p>}
          <p className="text-xs text-ink-3">{t("Throughput is measured for each finished job. Real-time performance depends on resolution, clip length and GPU.")}</p>
          {result?.source.sample_aspect_ratio && result.source.sample_aspect_ratio !== "1" && <p className="text-xs text-ink-3">{t("Non-square source pixels were handled to preserve the displayed aspect ratio.")}</p>}
          {result?.source.irregular_timing && <p className="text-xs text-ink-3">{t("Irregular source timestamps were normalized to the measured average frame rate.")}</p>}
        </div>
        <div className="grid content-start gap-3">
          <h2 className="font-semibold">{t("Upscaled videos")}</h2>
          {result ? <><VideoPlayer key={result.id} id={result.id} title={`${result.model} · ${result.source.name}`} prefix={`${base}/gallery`} /><p className="text-sm">{result.model} · {result.output.width} × {result.output.height} · {result.output.fps} FPS · {result.output.frames} {t("frames")} · {result.elapsed}s</p><p className="text-xs text-ink-2">{t("Measured whole-job throughput: {fps} FPS", { fps: result.processing_fps ?? t("Unknown") })}</p><p className="text-xs text-ink-3">{result.source.width} × {result.source.height} → {result.output.width} × {result.output.height} · {result.output.audio ? t("Original audio preserved") : t("No source audio")}</p><div className="grid gap-2">{gallery.data?.videos.map(item => <button className="truncate rounded border border-line p-2 text-left text-sm" key={item.id} onClick={() => setSelected(item.id)}>{item.model} · {item.source.name} · {item.output.width} × {item.output.height}</button>)}</div><Button variant="danger" className="justify-self-start" onClick={() => void act(() => api.del(`${base}/gallery/${result.id}`))}>{t("Delete")}</Button></> : <p className="text-sm text-ink-3">{t("Upscaled copies appear here. Original videos stay in their gallery.")}</p>}
        </div>
      </div>
    </div>}
  </details>;
}
