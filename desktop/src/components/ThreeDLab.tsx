import { useRef, useState } from "react";
import { Download, ExternalLink, Play } from "lucide-react";
import { Button } from "./ui";
import { api, mediaBlob, openExternal } from "../lib/api";
import { errorMessage, useInterval, useLoad } from "../lib/hooks";
import { t } from "../lib/i18n";
import type { SplatRecording, ThreeDLabStatus } from "../lib/types";

const input = "w-full rounded-md border border-line bg-bg p-2 text-sm";

export function ThreeDLab({ image, running, act }: {
  image: string; running: boolean; act: (path: string, body: unknown) => Promise<void>;
}) {
  const status = useLoad(() => api.get<ThreeDLabStatus>("/api/3d/lab/status"));
  const splats = useLoad(() => api.get<{ items: SplatRecording[] }>("/api/3d/lab/splats"));
  const [selected, setSelected] = useState("pixal3d");
  const [resolution, setResolution] = useState(1024);
  const [seed, setSeed] = useState(42);
  const [steps, setSteps] = useState(50);
  const [count, setCount] = useState(262144);
  const [error, setError] = useState("");
  const [importing, setImporting] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  useInterval(() => { status.reload(); splats.reload(); }, 5000);
  const engine = status.data?.engines.find(item => item.id === selected);
  const purposes: Record<string, string> = {
    pixal3d: t("Create a textured object from a photo"),
    "supra-tex": t("Create a textured object for research"),
    idol: t("Create a 3D point representation from a photo"),
    triposplat: t("Create a 3D point representation from a photo"),
    effigy: t("Open a studio for meshes, rigging and animation"),
    opencartridge: t("Open a studio for avatars and 3D workflows"),
    scenario: t("Instructions for working with external 3D applications"),
  };
  async function importModel(file: File) {
    setImporting(true); setError("");
    try {
      if (file.size > 100 * 1048576) throw new Error(t("Choose a GLB smaller than 100 MB."));
      const reader = new FileReader();
      const data = await new Promise<string>((resolve, reject) => { reader.onload = () => resolve(String(reader.result)); reader.onerror = reject; reader.readAsDataURL(file); });
      await api.post("/api/3d/lab/import", { engine: selected, data });
    } catch (err) { setError(errorMessage(err)); }
    finally { setImporting(false); }
  }
  async function download(item: SplatRecording) {
    try {
      const blob = await mediaBlob(`/api/3d/lab/splats/${item.id}/file`);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a"); link.href = url; link.download = `${item.model}-${item.id.slice(0, 8)}.ply`; link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) { setError(errorMessage(err)); }
  }
  return <section className="grid gap-4 rounded-lg border border-line bg-panel p-4">
    <h2 className="font-semibold">{t("3D comparison lab")}</h2>
    <p className="text-sm text-ink-3">{t("Additional engines and local studios. TRELLIS.2 and Hi3D remain available above.")}</p>
    {(error || status.error || status.data?.error) && <p role="alert" className="text-sm text-warn">{error || status.error || status.data?.error}</p>}
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{status.data?.engines.map(item => <button key={item.id} type="button" aria-pressed={selected === item.id} onClick={() => setSelected(item.id)} className="grid content-start gap-2 rounded-lg border border-line bg-bg p-4 text-left text-sm aria-pressed:border-accent aria-pressed:bg-raised">
      <strong>{item.name}</strong>
      <span className="text-xs text-ink-3">{item.kind === "skills" ? t("Expert tool instructions") : item.kind === "studio" ? t("Local companion studio") : t("Image to 3D")}</span>
      <span className="text-xs text-ink-2">{purposes[item.id] || t("Unknown")}</span>
      <span className="text-xs text-ink-3">{item.kind === "skills" ? t("Instructions") : item.kind === "studio" ? t("Local application") : item.format === "ply" ? "PLY" : "GLB"}</span>
      <span className={`text-xs ${item.ready ? "text-ok" : "text-ink-3"}`}>{status.error || status.data?.error ? t("Could not check setup") : item.ready ? t("Runtime prepared") : t("Not set up")}</span>
    </button>)}</div>
    {engine && <div className="grid gap-3 rounded border border-line p-4">
      <input hidden ref={fileRef} type="file" accept=".glb" onChange={event => { const file = event.target.files?.[0]; if (file) void importModel(file); event.target.value = ""; }} />
      <div className="flex flex-wrap items-center gap-2"><strong>{engine.name}</strong><Button size="sm" icon={<ExternalLink size={13} />} onClick={() => void openExternal(`https://github.com/${engine.repository}`)}>{t("Source")}</Button></div>
      <p className="text-xs text-ink-3">{engine.license}</p><p className="text-xs text-ink-3">{t("Each runtime is isolated. First use may download additional upstream weights. A prepared runtime is not a successful generation test.")}</p>
      {engine.id === "supra-tex" && <p className="text-xs text-warn">{t("SUPRA-Tex material-adapter weights are for non-commercial research and evaluation.")}</p>}
      {engine.kind === "skills" && <p className="text-xs text-ink-3">{t("These are expert instructions for installed 3D applications, not an image-to-3D model. Scenario-hosted generation is not configured.")}</p>}
      {engine.format === "glb" && engine.kind !== "skills" && <Button disabled={running || importing} onClick={() => fileRef.current?.click()}>{t("Import a result into the 3D gallery")}</Button>}
      {!engine.ready && <Button disabled={running} icon={<Download size={15} />} onClick={() => void act("/api/3d/lab/action", { engine: engine.id, action: "install" })}>{t("Prepare selected 3D runtime")}</Button>}
      {engine.kind === "studio" && <><p className="text-xs text-ink-3">{engine.id === "effigy" ? t("Effigy includes Pixal3D, mesh finishing, rigging and animation.") : t("OpenCartridge includes Idol, SUPRA-Tex, SkinTokens and avatar workflows. Docker is required.")}</p><p className="text-xs text-ink-3">{t("Companion studios manage their own GPU jobs. Stop the studio before generating with another model.")}</p><Button disabled={running || !engine.ready} icon={<Play size={15} />} onClick={() => void act("/api/3d/lab/action", { engine: engine.id, action: "launch" })}>{t("Start companion studio")}</Button><Button disabled={running} onClick={() => void act("/api/3d/lab/action", { engine: engine.id, action: "stop" })}>{t("Stop companion studio")}</Button><Button icon={<ExternalLink size={15} />} onClick={() => void openExternal(engine.id === "effigy" ? "http://127.0.0.1:8000" : "http://127.0.0.1:3000")}>{t("Open local studio")}</Button></>}
      {engine.id === "idol" && <p className="text-xs text-ink-3">{t("Experimental: public Comfy-Org/Idol weights with the compatible TripoSplat pipeline. This differs from the original Idol SDK preprocessing.")}</p>}
      {engine.kind === "generation" && <>
        {engine.id === "pixal3d" && <label className="text-sm">{t("Resolution")}<select className={input} value={resolution} onChange={event => setResolution(Number(event.target.value))}><option value={1024}>1024³</option><option value={1536}>1536³</option></select></label>}
        {engine.format === "ply" && <div className="grid gap-3 sm:grid-cols-3"><label className="text-sm">{t("Seed")}<input className={input} type="number" min={0} max={2147483647} value={seed} onChange={event => setSeed(Number(event.target.value))} /></label><label className="text-sm">{t("Steps")}<input className={input} type="number" min={1} max={50} value={steps} onChange={event => setSteps(Number(event.target.value))} /></label><label className="text-sm">{t("Gaussians")}<select className={input} value={count} onChange={event => setCount(Number(event.target.value))}>{[32768, 65536, 131072, 262144].map(value => <option key={value} value={value}>{value.toLocaleString()}</option>)}</select></label></div>}
        <p className="text-xs text-ink-3">{engine.format === "glb" ? t("Uses the uploaded reference photo. New GLBs appear in the existing gallery and support Export for Web.") : t("Gaussian splats are saved separately as PLY files. Their detail cannot be judged with the ordinary mesh viewer.")}</p>
        <Button variant="primary" disabled={running || !engine.ready || !image || steps < 1 || steps > 50 || seed < 0 || seed > 2147483647} onClick={() => void act("/api/3d/lab/generate", { engine: engine.id, image, resolution, seed, steps, num_gaussians: count })}>{t("Generate comparison")}</Button>
      </>}
    </div>}
    {!!splats.data?.items.length && <div className="grid gap-2"><h3 className="text-sm font-semibold">{t("Gaussian splat results")}</h3>{splats.data.items.map(item => <div key={item.id} className="flex flex-wrap items-center justify-between gap-2 rounded border border-line p-3 text-sm"><span>{item.model} · {(item.bytes / 1048576).toFixed(1)} MB</span><Button icon={<Download size={14} />} onClick={() => void download(item)}>{t("Save PLY")}</Button></div>)}</div>}
  </section>;
}
