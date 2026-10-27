import { Download, FolderOpen, Image as ImageIcon, RefreshCw, Snowflake, Sparkles, Trash2, Upload, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { EngineCard } from "../components/images/EngineCard";
import { ImportPanel } from "../components/images/ImportPanel";
import { ModelManager, sizeLabel } from "../components/images/ModelManager";
import { hintText, suggestionText } from "../components/images/planText";
import {
  Badge,
  Button,
  Collapsible,
  Field,
  IconButton,
  Note,
  PageHeader,
  Panel,
  ProgressBar,
  Section,
  Segmented,
  Select,
  Tabs,
  TextArea,
  TextInput,
  Toggle,
  cx,
} from "../components/ui";
import { api, getBackend } from "../lib/api";
import { errorMessage, useBus, useInterval, useLoad } from "../lib/hooks";
import { t } from "../lib/i18n";
import { useApp } from "../lib/store";
import type {
  GalleryImage,
  ImageCheck,
  ImageDownload,
  ImageJob,
  ImageLora,
  ImageModel,
  ImagePlan,
  ImagePreset,
  ImageStatus,
  ImageTemplate,
} from "../lib/types";

type Mode = "txt2img" | "edit" | "upscale";
type Tab = "generate" | "models";

const ASPECTS: { value: string; ratio: [number, number] }[] = [
  { value: "1:1", ratio: [1, 1] },
  { value: "3:4", ratio: [3, 4] },
  { value: "2:3", ratio: [2, 3] },
  { value: "9:16", ratio: [9, 16] },
  { value: "4:3", ratio: [4, 3] },
  { value: "3:2", ratio: [3, 2] },
  { value: "16:9", ratio: [16, 9] },
];

const SAMPLERS = ["euler", "euler_ancestral", "dpmpp_2m", "dpmpp_2m_sde", "dpmpp_3m_sde", "ddim", "heun"];
const SCHEDULERS = ["simple", "normal", "karras", "exponential", "sgm_uniform", "beta"];

/** SDXL is happy with multiples of 8; Qwen-Image and Krea 2 want multiples of 32. */
function sizeStep(family: string | undefined): number {
  return family === "sdxl" || !family ? 8 : 32;
}

function sizeFor(aspect: string, megapixels: number, step = 8): { width: number; height: number } {
  const ratio = ASPECTS.find((item) => item.value === aspect)?.ratio ?? [1, 1];
  // A "megapixel" here is 1024 × 1024, the size the models are trained around: 1:1 gives 1024², 2:3 at 1.5 gives 1024 × 1536.
  const height = Math.sqrt((megapixels * 1024 * 1024) / (ratio[0] / ratio[1]));
  const round = (value: number) => Math.max(256, Math.round(value / step) * step);
  return { width: round(height * (ratio[0] / ratio[1])), height: round(height) };
}

function bytesLabel(bytes: number): string {
  if (!bytes) return "";
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Wan and its accelerators are video LoRAs: they belong to the other studio, not here. */
function isVideoLora(name: string): boolean {
  const low = name.toLowerCase();
  return /wan|lightx2v|i2v|t2v|\d+steps/.test(low);
}

function secondsLabel(seconds: number | null | undefined): string {
  if (!seconds) return "";
  return seconds < 60 ? t("{n} s", { n: Math.round(seconds) }) : t("{n} min", { n: (seconds / 60).toFixed(1) });
}

/** A picture on disk is served by the core, which wants the token whenever one is set. */
function useFileBase(): { url: string; token: string } | null {
  const [base, setBase] = useState<{ url: string; token: string } | null>(null);
  useEffect(() => {
    let alive = true;
    void getBackend().then((value) => {
      if (alive) setBase(value);
    });
    return () => {
      alive = false;
    };
  }, []);
  return base;
}

function fileUrl(base: { url: string; token: string } | null, id: string | null): string {
  if (!base || !id) return "";
  const token = base.token ? `?token=${encodeURIComponent(base.token)}` : "";
  return `${base.url}/api/images/gallery/${id}/file${token}`;
}

export function ImagesPage() {
  const status = useLoad<ImageStatus>(() => api.get("/api/images/status"), []);
  const models = useLoad<{ models: ImageModel[] }>(() => api.get("/api/images/models"), []);
  const templates = useLoad<{ templates: ImageTemplate[] }>(() => api.get("/api/images/templates"), []);
  const lorasAvailable = useLoad<{ loras: string[] }>(() => api.get("/api/images/loras"), []);
  const gallery = useLoad<{ images: GalleryImage[] }>(() => api.get("/api/images/gallery?limit=60"), []);
  const downloadsLoad = useLoad<{ downloads: ImageDownload[] }>(() => api.get("/api/images/downloads"), []);
  const base = useFileBase();
  const toast = useApp((state) => state.toast);

  const [tab, setTab] = useState<Tab>("generate");
  const [importing, setImporting] = useState(false);
  const [engineBusy, setEngineBusy] = useState(false);
  const [modelKey, setModelKey] = useState("");
  const [mode, setMode] = useState<Mode>("txt2img");
  const [templateKey, setTemplateKey] = useState("");
  const [prompt, setPrompt] = useState("");
  const [negative, setNegative] = useState("");
  const [aspect, setAspect] = useState("2:3");
  const [megapixels, setMegapixels] = useState(1);
  const [steps, setSteps] = useState<number | "">("");
  const [cfg, setCfg] = useState<number | "">("");
  const [sampler, setSampler] = useState("");
  const [scheduler, setScheduler] = useState("");
  const [seed, setSeed] = useState<number | "">("");
  const [randomSeed, setRandomSeed] = useState(true);
  const [denoise, setDenoise] = useState<number | "">("");
  const [batch, setBatch] = useState(1);
  const [loras, setLoras] = useState<ImageLora[]>([]);
  const [loraPick, setLoraPick] = useState("");
  const [loraWeight, setLoraWeight] = useState(0.8);
  const [upload, setUpload] = useState<{ name: string; preview: string } | null>(null);
  const [job, setJob] = useState<ImageJob | null>(null);
  const [current, setCurrent] = useState<GalleryImage | null>(null);
  const [check, setCheck] = useState<ImageCheck | null>(null);
  const [plan, setPlan] = useState<ImagePlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const freeOnLeave = useApp((state) => state.settings?.images.free_vram_on_leave ?? false);

  const engines = status.data?.engine ?? null;
  const downloads = useMemo(() => {
    const map: Record<string, ImageDownload> = {};
    for (const item of downloadsLoad.data?.downloads ?? []) map[item.component_id] = item;
    return map;
  }, [downloadsLoad.data]);
  const activeDownloads = (downloadsLoad.data?.downloads ?? []).filter((item) =>
    ["queued", "downloading", "verifying"].includes(item.status),
  );

  const model = useMemo(() => (models.data?.models ?? []).find((item) => item.key === modelKey) ?? null, [models.data, modelKey]);
  const availableTemplates = useMemo(
    () =>
      (templates.data?.templates ?? []).filter(
        (item) => (item.family === "any" || item.family === model?.family) && item.kind === mode,
      ),
    [templates.data, model, mode],
  );
  const template = useMemo(
    () => availableTemplates.find((item) => item.key === templateKey) ?? availableTemplates[0] ?? null,
    [availableTemplates, templateKey],
  );
  const size = useMemo(() => sizeFor(aspect, megapixels, sizeStep(model?.family)), [aspect, megapixels, model?.family]);
  // The negative prompt the last model came with, so a change of model replaces it only if the person left it alone.
  const modelNegative = useRef("");
  // A preset with LoRAs belongs with the LoRAs; one that only changes steps and CFG belongs with the sampling.
  const loraPresets = useMemo(() => (model?.presets ?? []).filter((preset) => preset.loras.length > 0), [model]);
  const samplingPresets = useMemo(() => (model?.presets ?? []).filter((preset) => preset.loras.length === 0), [model]);

  function applyPreset(preset: ImagePreset) {
    const options = model?.lora_options ?? [];
    setLoras(
      preset.loras
        .map((entry) => {
          const option = options.find((item) => item.id === entry.component);
          return option?.file ? { name: option.file, weight: entry.weight } : null;
        })
        .filter((entry): entry is ImageLora => entry !== null),
    );
    if (preset.sampling?.steps) setSteps(preset.sampling.steps);
    if (preset.sampling?.cfg) setCfg(preset.sampling.cfg);
  }

  const reloadAll = useCallback(() => {
    status.reload();
    models.reload();
    gallery.reload();
    downloadsLoad.reload();
  }, [status, models, gallery, downloadsLoad]);

  // First model: what is installed, else the first one listed.
  useEffect(() => {
    const rows = models.data?.models ?? [];
    if (modelKey || !rows.length) return;
    const first = rows.find((row) => row.installed) ?? rows[0];
    if (first) setModelKey(first.key);
  }, [models.data, modelKey]);

  // A model that names its own workflow gets it, when the workflow still fits this family.
  useEffect(() => {
    if (!model?.template) return;
    if (availableTemplates.some((item) => item.key === model.template)) setTemplateKey(model.template);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [model?.key]);

  useEffect(() => {
    if (!template) return;
    if (template.key !== templateKey) setTemplateKey(template.key);
    const defaults = template.defaults as Record<string, number | string | undefined>;
    setSteps((value) => (value === "" && typeof defaults.steps === "number" ? defaults.steps : value));
    setCfg((value) => (value === "" && typeof defaults.cfg === "number" ? defaults.cfg : value));
    setSampler((value) => value || (typeof defaults.sampler === "string" ? defaults.sampler : ""));
    setScheduler((value) => value || (typeof defaults.scheduler === "string" ? defaults.scheduler : ""));
    setDenoise((value) => value || (typeof defaults.denoise === "number" ? defaults.denoise : ""));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [template?.key]);

  // The settings of a model chosen for the first time: steps, CFG, sampler, resolution.
  useEffect(() => {
    if (!model) return;
    const sampling = model.sampling as Record<string, number | string | undefined>;
    if (typeof sampling.steps === "number") setSteps(sampling.steps);
    if (typeof sampling.cfg === "number") setCfg(sampling.cfg);
    if (typeof sampling.sampler === "string") setSampler(sampling.sampler);
    if (typeof sampling.scheduler === "string") setScheduler(sampling.scheduler);
    // The shape and size the model's author recommends, and the negative prompt it was tuned with.
    if (model.aspect && ASPECTS.some((item) => item.value === model.aspect)) setAspect(model.aspect);
    if (model.megapixels) setMegapixels(model.megapixels);
    const wanted = model.negative ?? "";
    setNegative((current) => (current === "" || current === modelNegative.current ? wanted : current));
    modelNegative.current = wanted;
  }, [model?.key]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (model && !model.kinds.includes(mode)) setMode(model.kinds.includes("txt2img") ? "txt2img" : (model.kinds[0] as Mode));
  }, [model, mode]);

  // What this model and this workflow still need, asked of the engine itself.
  useEffect(() => {
    if (!modelKey || !template) return;
    let alive = true;
    const timer = window.setTimeout(() => {
      api
        .post<ImageCheck>("/api/images/check", {
          model_key: modelKey,
          template: template.key,
          width: size.width,
          height: size.height,
          image: upload?.name ?? "",
        })
        .then((value) => {
          if (alive) setCheck(value);
        })
        .catch(() => {
          if (alive) setCheck(null);
        });
      api
        .get<ImagePlan>(`/api/images/models/${encodeURIComponent(modelKey)}/plan`)
        .then((value) => {
          if (alive) setPlan(value);
        })
        .catch(() => {
          if (alive) setPlan(null);
        });
    }, 350);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [modelKey, template, size.width, size.height, upload?.name, models.data]);

  // Opening the page shows the last picture made here, not an empty frame.
  useEffect(() => {
    if (current) return;
    const newest = gallery.data?.images?.[0];
    if (newest) setCurrent(newest);
  }, [gallery.data, current]);

  const running = job !== null && (job.status === "queued" || job.status === "running");

  const follow = useCallback(
    async (id: string) => {
      const { job: latest } = await api.get<{ job: ImageJob }>(`/api/images/jobs/${id}`);
      setJob(latest);
      if (latest.status === "done") {
        setRandomSeed(true);
        gallery.reload();
        status.reload();
        if (latest.image_id) {
          const { images } = await api.get<{ images: GalleryImage[] }>("/api/images/gallery?limit=60");
          setCurrent(images.find((image) => image.id === latest.image_id) ?? null);
        }
        setBusy(false);
        toast({ tone: "ok", title: t("Picture ready"), body: secondsLabel(latest.elapsed) });
      } else if (latest.status === "failed" || latest.status === "cancelled") {
        setBusy(false);
        if (latest.status === "failed") toast({ tone: "bad", title: t("The picture was not generated"), body: latest.error ?? "" });
      }
    },
    [gallery, status, toast],
  );

  useInterval(() => {
    if (job && (job.status === "queued" || job.status === "running")) void follow(job.id);
  }, running ? 900 : null);

  useInterval(() => downloadsLoad.reload(), activeDownloads.length > 0 ? 900 : null);

  useBus("image.", () => {
    if (job && (job.status === "queued" || job.status === "running")) void follow(job.id);
  });

  // Leaving the page can hand the card back, when that is what was asked for in Settings.
  useEffect(() => {
    if (!freeOnLeave) return;
    return () => {
      void api.post("/api/images/free").catch(() => undefined);
    };
  }, [freeOnLeave]);

  async function startEngine() {
    setEngineBusy(true);
    try {
      const result = await api.post<{ version: string | null }>("/api/images/engine/start");
      toast({ tone: "ok", title: t("Engine started"), body: result.version ? `ComfyUI ${result.version}` : "" });
      reloadAll();
    } catch (err) {
      toast({ tone: "bad", title: t("The engine did not start"), body: errorMessage(err) });
      status.reload();
    } finally {
      setEngineBusy(false);
    }
  }

  async function stopEngine() {
    setEngineBusy(true);
    try {
      await api.post("/api/images/engine/stop");
      toast({ tone: "info", title: t("Engine stopped") });
      reloadAll();
    } catch (err) {
      toast({ tone: "bad", title: t("The engine did not stop"), body: errorMessage(err) });
    } finally {
      setEngineBusy(false);
    }
  }

  async function downloadModel(key: string) {
    try {
      const result = await api.post<{ started: unknown[]; skipped: { name: string }[] }>(
        `/api/images/models/${encodeURIComponent(key)}/download`,
      );
      downloadsLoad.reload();
      models.reload();
      if (result.skipped.length) {
        toast({
          tone: "info",
          title: t("Some parts have to be placed by hand"),
          body: result.skipped.map((item) => item.name).join(", "),
        });
      } else {
        toast({ tone: "info", title: t("Download started") });
      }
    } catch (err) {
      toast({ tone: "bad", title: t("The download did not start"), body: errorMessage(err) });
    }
  }

  async function downloadComponent(id: string) {
    try {
      await api.post("/api/images/components/download", { components: [id] });
      downloadsLoad.reload();
    } catch (err) {
      toast({ tone: "bad", title: t("The download did not start"), body: errorMessage(err) });
    }
  }

  async function removeComponent(id: string) {
    try {
      await api.del(`/api/images/components/${id}`);
      models.reload();
      toast({ tone: "info", title: t("File removed") });
    } catch (err) {
      toast({ tone: "bad", title: t("The file was not removed"), body: errorMessage(err) });
    }
  }

  async function uploadFile(file: File | Blob, filename = "upload.png") {
    const data = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = () => reject(new Error(t("The picture could not be read.")));
      reader.readAsDataURL(file);
    });
    const { name } = await api.post<{ name: string }>("/api/images/upload", { filename, data });
    setUpload({ name, preview: data });
    if (mode !== "edit" && mode !== "upscale") setMode("edit");
  }

  async function attachFromGallery(image: GalleryImage) {
    if (!base) return;
    const token = base.token ? `?token=${encodeURIComponent(base.token)}` : "";
    const response = await fetch(`${base.url}/api/images/gallery/${image.id}/file${token}`);
    if (!response.ok) {
      toast({ tone: "bad", title: t("Could not load that picture") });
      return;
    }
    await uploadFile(await response.blob(), `${image.id}.png`);
  }

  async function generate() {
    if (!modelKey || !template) return;
    setBusy(true);
    try {
      const { job: created } = await api.post<{ job: ImageJob }>("/api/images/generate", {
        model_key: modelKey,
        template: template.key,
        prompt,
        negative,
        width: size.width,
        height: size.height,
        steps: steps === "" ? null : Number(steps),
        cfg: cfg === "" ? null : Number(cfg),
        sampler: sampler || null,
        scheduler: scheduler || null,
        seed: randomSeed ? -1 : Number(seed || 0),
        denoise: denoise === "" ? null : Number(denoise),
        loras,
        batch,
        image: upload?.name ?? "",
      });
      setJob(created);
    } catch (err) {
      setBusy(false);
      toast({ tone: "bad", title: t("The picture was not generated"), body: errorMessage(err) });
    }
  }

  async function cancel() {
    if (!job) return;
    try {
      await api.post(`/api/images/jobs/${job.id}/cancel`);
    } catch {
      // The job may have finished between the click and the call.
    }
    void follow(job.id);
  }

  async function forget(image: GalleryImage) {
    await api.del(`/api/images/gallery/${image.id}`);
    if (current?.id === image.id) setCurrent(null);
    gallery.reload();
    status.reload();
  }

  const installed = (models.data?.models ?? []).filter((row) => row.installed);
  const others = (models.data?.models ?? []).filter((row) => !row.installed);
  const ready = check?.ready ?? false;

  return (
    <>
      <PageHeader
        title={t("Images")}
        subtitle={t("ComfyUI draws on this PC. Nothing leaves it: the prompt, the picture and the model stay here.")}
        actions={
          <>
            <Button icon={<FolderOpen size={14} />} onClick={() => void api.post("/api/images/open-folder")}>
              {t("Folder")}
            </Button>
            <Button
              icon={<Snowflake size={14} />}
              onClick={() => {
                void api.post("/api/images/free").then(() => status.reload());
              }}
              title={t("Unload the models from the graphics card, so a language model can use it")}
            >
              {t("Free VRAM")}
            </Button>
            <IconButton label={t("Refresh")} onClick={reloadAll}>
              <RefreshCw size={14} />
            </IconButton>
          </>
        }
      />

      <div className="grid min-h-0 flex-1 grid-cols-[minmax(360px,430px)_1fr] gap-5 overflow-auto px-7 py-5">
        {/* -- the left column: the engine, then what to draw or what to install -- */}
        <div className="grid content-start gap-4">
          <EngineCard
            status={status.data}
            busy={engineBusy}
            onStart={() => void startEngine()}
            onStop={() => void stopEngine()}
            onRefresh={() => {
              void api.get("/api/images/engine?refresh=1").then(() => status.reload());
            }}
          />

          <Tabs
            value={tab}
            onChange={(value) => setTab(value as Tab)}
            tabs={[
              { value: "generate", label: t("Generate") },
              { value: "models", label: t("Models") },
            ]}
          />

          {tab === "models" && !importing && (
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs text-ink-3">{t("Models this PC can draw with")}</span>
              <Button size="sm" icon={<Download size={12} />} onClick={() => setImporting(true)}>
                {t("Import local models")}
              </Button>
            </div>
          )}

          {tab === "models" && importing && (
            <ImportPanel
              onClose={() => setImporting(false)}
              onDone={() => {
                setImporting(false);
                reloadAll();
              }}
            />
          )}

          {tab === "models" && !importing && (
            <ModelManager
              models={models.data?.models ?? []}
              downloads={downloads}
              selected={modelKey}
              onUse={(key) => {
                setModelKey(key);
                setTab("generate");
              }}
              onDownloadModel={(key) => void downloadModel(key)}
              onDownloadComponent={(id) => void downloadComponent(id)}
              onRemoveComponent={(id) => void removeComponent(id)}
              onRefresh={reloadAll}
            />
          )}

          {tab === "generate" && (
            <Panel className="grid gap-3.5">
              {!engines?.installed && (
                <Note tone="warn">{t("Install ComfyUI first: the card above explains how.")}</Note>
              )}
              {models.error && (
                <Note tone="bad" action={<Button size="sm" onClick={models.reload}>{t("Try again")}</Button>}>
                  {models.error}
                </Note>
              )}
              {models.loading && !models.data && <p className="text-xs text-ink-3">{t("Loading the models…")}</p>}

              <Field label={t("Model")} hint={model?.notes}>
                <Select value={modelKey} onChange={(event) => setModelKey(event.target.value)}>
                  {installed.length > 0 && (
                    <optgroup label={t("Installed")}>
                      {installed.map((row) => (
                        <option key={row.key} value={row.key}>
                          {row.name}
                        </option>
                      ))}
                    </optgroup>
                  )}
                  {others.length > 0 && (
                    <optgroup label={t("Not installed")}>
                      {others.map((row) => (
                        <option key={row.key} value={row.key}>
                          {row.name}
                        </option>
                      ))}
                    </optgroup>
                  )}
                </Select>
              </Field>

              {model?.blocked && (
                <Note tone="bad">
                  <div className="grid gap-1">
                    <span>{model.blocked.reason}</span>
                    {(model.blocked.missing ?? []).length > 0 && (
                      <span className="text-xs text-ink-3">
                        {t("Missing: {names}.", { names: (model.blocked.missing ?? []).join(", ") })}
                      </span>
                    )}
                  </div>
                </Note>
              )}

              {model && !model.installed && (
                <Note
                  tone="warn"
                  action={
                    <Button size="sm" icon={<Download size={12} />} onClick={() => void downloadModel(model.key)}>
                      {t("Download")}
                    </Button>
                  }
                >
                  {t("This model is not complete. {names}", {
                    names: model.missing.map((item) => item.name).join(", "),
                  })}
                </Note>
              )}

              <Field label={t("What to do")}>
                <Segmented
                  value={mode}
                  label={t("What to do")}
                  onChange={(value) => setMode(value as Mode)}
                  options={[
                    { value: "txt2img", label: t("Create") },
                    { value: "edit", label: t("Edit a photo") },
                    { value: "upscale", label: t("Upscale") },
                  ]}
                />
              </Field>

              <Field label={t("Workflow")} hint={template?.description}>
                <Select value={template?.key ?? ""} onChange={(event) => setTemplateKey(event.target.value)}>
                  {availableTemplates.map((item) => (
                    <option key={item.key} value={item.key}>
                      {item.name}
                    </option>
                  ))}
                </Select>
              </Field>

              {(mode === "edit" || mode === "upscale") && (
                <div
                  onDragOver={(event) => {
                    event.preventDefault();
                    setDragging(true);
                  }}
                  onDragLeave={() => setDragging(false)}
                  onDrop={(event) => {
                    event.preventDefault();
                    setDragging(false);
                    const file = event.dataTransfer.files[0];
                    if (file) void uploadFile(file, file.name);
                  }}
                  className={cx(
                    "grid place-items-center gap-1.5 rounded-md border border-dashed px-3 py-4 text-center text-xs transition-colors",
                    dragging ? "border-accent bg-raised" : "border-line-strong",
                  )}
                >
                  {upload ? (
                    <div className="grid justify-items-center gap-2">
                      <img src={upload.preview} alt="" className="max-h-40 rounded-sm border border-line object-contain" />
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-2xs text-ink-3">{upload.name.split("/").pop()}</span>
                        <IconButton label={t("Remove the picture")} onClick={() => setUpload(null)}>
                          <X size={13} />
                        </IconButton>
                      </div>
                    </div>
                  ) : (
                    <>
                      <Upload size={16} className="text-ink-3" />
                      <span className="text-ink-2">{t("Drop a picture here, click to choose one, or press Ctrl+V")}</span>
                      <Button size="sm" onClick={() => fileInput.current?.click()}>
                        {t("Choose a picture")}
                      </Button>
                    </>
                  )}
                  <input
                    ref={fileInput}
                    type="file"
                    accept="image/png,image/jpeg,image/webp"
                    className="hidden"
                    onChange={(event) => {
                      const file = event.target.files?.[0];
                      if (file) void uploadFile(file, file.name);
                      event.target.value = "";
                    }}
                  />
                </div>
              )}

              <Field label={mode === "upscale" ? t("What the picture should gain") : t("Describe the picture")}>
                <TextArea
                  rows={4}
                  value={prompt}
                  placeholder={t("Write it like the caption of a photo that already exists: who, what they do, where, the light. English works better.")}
                  onChange={(event) => setPrompt(event.target.value)}
                />
              </Field>

              <Collapsible title={t("What to avoid")}>
                <TextArea rows={2} value={negative} onChange={(event) => setNegative(event.target.value)} />
              </Collapsible>

              <div className="grid grid-cols-2 gap-3">
                <Field label={t("Shape")}>
                  <Select value={aspect} onChange={(event) => setAspect(event.target.value)}>
                    {ASPECTS.map((item) => (
                      <option key={item.value} value={item.value}>
                        {item.value}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label={t("Quality")} hint={`${size.width} × ${size.height}`}>
                  <Select value={String(megapixels)} onChange={(event) => setMegapixels(Number(event.target.value))}>
                    <option value="0.6">{t("0.6 megapixels")}</option>
                    <option value="1">{t("1 megapixel")}</option>
                    <option value="1.5">{t("1.5 megapixels")}</option>
                    <option value="2">{t("2 megapixels")}</option>
                  </Select>
                </Field>
              </div>

              {plan?.estimate.warnings.length ? (
                <Note tone={plan.estimate.fits === false ? "bad" : "warn"}>
                  <div className="grid gap-1">
                    <span>
                      {t("{needed} GB of the {usable} GB this PC can give the card.", {
                        needed: plan.estimate.needed_gb ?? 0,
                        usable: plan.estimate.usable_gb ?? 0,
                      })}
                    </span>
                    {plan.estimate.suggestions.slice(0, 2).map((suggestion, index) => (
                      <span key={`${suggestion.code}-${index}`} className="text-xs text-ink-3">
                        + {suggestionText(suggestion)}
                      </span>
                    ))}
                  </div>
                </Note>
              ) : null}

              <Collapsible title={t("Sampling")}>
                <div className="grid gap-2 pt-1">
                  <p className="text-xs text-ink-3">
                    {model?.sampling?.steps
                      ? t("This model was trained for {steps} steps, CFG {cfg}, {sampler} / {scheduler}. Change them only if the picture says so.", {
                          steps: Number(model.sampling.steps),
                          cfg: String(model.sampling.cfg ?? "1"),
                          sampler: String(model.sampling.sampler ?? "euler"),
                          scheduler: String(model.sampling.scheduler ?? "simple"),
                        })
                      : t("Leave these as they are until the pictures give you a reason.")}
                  </p>
                  {samplingPresets.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      {samplingPresets.map((preset) => (
                        <Button key={preset.key} size="sm" onClick={() => applyPreset(preset)}>
                          {t("Preset: {name}", { name: preset.name })}
                        </Button>
                      ))}
                    </div>
                  )}
                  <div className="grid grid-cols-2 gap-3">
                    <Field
                      label={t("Steps")}
                      hint={t("How many times the noise is removed. More steps, more detail and more time: distilled models are trained for eight or ten.")}
                    >
                      <TextInput
                        type="number"
                        min={1}
                        max={200}
                        value={steps}
                        onChange={(event) => setSteps(event.target.value === "" ? "" : Number(event.target.value))}
                      />
                    </Field>
                    <Field
                      label={t("CFG")}
                      hint={t("How strictly the words are followed. 1 leaves the model free (turbo and distilled models), 4 to 7 stick to the prompt (SDXL).")}
                    >
                      <TextInput
                        type="number"
                        step="0.1"
                        min={0}
                        value={cfg}
                        onChange={(event) => setCfg(event.target.value === "" ? "" : Number(event.target.value))}
                      />
                    </Field>
                    <Field
                      label={t("Sampler")}
                      hint={t("The rule that removes the noise. euler for distilled models, dpmpp_2m for SDXL.")}
                    >
                      <Select value={sampler} onChange={(event) => setSampler(event.target.value)}>
                        {SAMPLERS.map((name) => (
                          <option key={name} value={name}>
                            {name}
                          </option>
                        ))}
                      </Select>
                    </Field>
                    <Field
                      label={t("Scheduler")}
                      hint={t("How the steps are spaced. simple for Krea and Qwen-Image, karras for SDXL.")}
                    >
                      <Select value={scheduler} onChange={(event) => setScheduler(event.target.value)}>
                        {SCHEDULERS.map((name) => (
                          <option key={name} value={name}>
                            {name}
                          </option>
                        ))}
                      </Select>
                    </Field>
                    {(mode === "edit" || mode === "upscale") && (
                      <Field label={t("How much to repaint")} hint={t("Low keeps the picture, high follows the text")}>
                        <TextInput
                          type="number"
                          step="0.05"
                          min={0}
                          max={1}
                          value={denoise}
                          onChange={(event) => setDenoise(event.target.value === "" ? "" : Number(event.target.value))}
                        />
                      </Field>
                    )}
                    <Field label={t("Pictures in this run")} hint={t("One batch, one wait: the card stays loaded.")}>
                      <Select value={String(batch)} onChange={(event) => setBatch(Number(event.target.value))}>
                        <option value="1">1</option>
                        <option value="2">2</option>
                        <option value="3">3</option>
                        <option value="4">4</option>
                      </Select>
                    </Field>
                  </div>
                </div>
              </Collapsible>

              <Collapsible title={t("LoRAs")}>
                <div className="grid gap-2 pt-1">
                  {loraPresets.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      {loraPresets.map((preset) => (
                        <Button key={preset.key} size="sm" onClick={() => applyPreset(preset)}>
                          {t("Preset: {name}", { name: preset.name })}
                        </Button>
                      ))}
                    </div>
                  )}

                  {(model?.lora_options ?? []).length > 0 && (
                    <ul className="grid gap-1.5">
                      {(model?.lora_options ?? []).map((option) => {
                        const attached = loras.find((entry) => entry.name === option.file);
                        const weight = attached?.weight ?? Number(option.lora?.weight ?? 0.8);
                        const check = option.lora?.format;
                        return (
                          <li key={option.id} className="grid gap-1 rounded-md border border-line px-2 py-1.5">
                            <div className="flex items-center gap-2">
                              <Toggle
                                label={option.name}
                                checked={Boolean(attached)}
                                disabled={!option.file}
                                onChange={(on) => {
                                  if (!option.file) return;
                                  setLoras(
                                    on
                                      ? [...loras, { name: option.file, weight }]
                                      : loras.filter((entry) => entry.name !== option.file),
                                  );
                                }}
                              />
                              <span className="min-w-0 flex-1 truncate text-xs">{option.name}</span>
                              {check && (
                                <span
                                  className="shrink-0 rounded-sm border border-line px-1 text-2xs text-ink-3"
                                  title={option.lora?.note}
                                >
                                  {check === "peft-transformer" ? t("PEFT format") : t("ComfyUI format")}
                                </span>
                              )}
                            </div>
                            {option.lora?.trigger ? (
                              <span className="pl-11 font-mono text-2xs text-ink-3">
                                trigger: {option.lora.trigger}
                              </span>
                            ) : null}
                            {attached && (
                              <div className="flex items-center gap-2 pl-11">
                                <TextInput
                                  type="number"
                                  step="0.05"
                                  min={0}
                                  max={2}
                                  className="w-20"
                                  value={attached.weight}
                                  onChange={(event) =>
                                    setLoras(
                                      loras.map((entry) =>
                                        entry.name === attached.name
                                          ? { ...entry, weight: Number(event.target.value) }
                                          : entry,
                                      ),
                                    )
                                  }
                                />
                                <span className="text-2xs text-ink-3">{t("strength")}</span>
                              </div>
                            )}
                          </li>
                        );
                      })}
                    </ul>
                  )}

                  <div className="flex items-end gap-2">
                    <Select className="flex-1" value={loraPick} onChange={(event) => setLoraPick(event.target.value)}>
                      <option value="">{t("Other LoRAs in ComfyUI")}</option>
                      <optgroup label={t("Images")}>
                        {(lorasAvailable.data?.loras ?? [])
                          .filter((name) => !isVideoLora(name))
                          .map((name) => (
                            <option key={name} value={name}>
                              {name}
                            </option>
                          ))}
                      </optgroup>
                      <optgroup label={t("Video (Wan) — has no effect on an image")}>
                        {(lorasAvailable.data?.loras ?? [])
                          .filter(isVideoLora)
                          .map((name) => (
                            <option key={name} value={name}>
                              {name}
                            </option>
                          ))}
                      </optgroup>
                    </Select>
                    <TextInput
                      type="number"
                      step="0.05"
                      min={0}
                      max={2}
                      className="w-20"
                      value={loraWeight}
                      onChange={(event) => setLoraWeight(Number(event.target.value))}
                    />
                    <Button
                      disabled={!loraPick}
                      onClick={() => {
                        setLoras([...loras, { name: loraPick, weight: loraWeight }]);
                        setLoraPick("");
                      }}
                    >
                      {t("Add")}
                    </Button>
                  </div>

                  <p className="text-2xs text-ink-3">
                    {t("These are the files ComfyUI sees in its loras folder. The Wan ones are for video: on an image model they do nothing, or damage the picture.")}
                  </p>

                  {loras.length === 0 ? (
                    <p className="text-xs text-ink-3">
                      {t("No LoRA. Three at a time with a total weight under 1.5 is the usual rule.")}
                    </p>
                  ) : (
                    <ul className="grid gap-1.5">
                      {loras.map((lora, index) => (
                        <li key={`${lora.name}-${index}`} className="flex items-center gap-2 text-xs">
                          <span className="min-w-0 flex-1 truncate font-mono text-ink-2">{lora.name}</span>
                          <span className="font-mono text-ink-3">{lora.weight.toFixed(2)}</span>
                          <IconButton label={t("Remove")} onClick={() => setLoras(loras.filter((_, i) => i !== index))}>
                            <X size={12} />
                          </IconButton>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </Collapsible>

              <div className="flex items-end gap-3">
                <Field label={t("Seed")} hint={t("0 gives a new picture every time")}>
                  <TextInput
                    type="number"
                    min={0}
                    disabled={randomSeed}
                    value={seed}
                    onChange={(event) => setSeed(event.target.value === "" ? "" : Number(event.target.value))}
                  />
                </Field>
                <div className="flex h-8 items-center gap-2 pb-1">
                  <Toggle checked={randomSeed} onChange={setRandomSeed} label={t("Random seed")} />
                  <span className="text-xs text-ink-2">{t("Random seed")}</span>
                </div>
              </div>

              {check && !check.ready && (
                <Note tone="warn">
                  <div className="grid gap-1">
                    {check.problems.map((problem) => (
                      <span key={problem}>{problem}</span>
                    ))}
                  </div>
                </Note>
              )}

              <div className="flex items-center gap-2">
                <Button
                  variant="primary"
                  className="flex-1"
                  icon={<Sparkles size={14} />}
                  loading={busy && running}
                  disabled={busy || !ready || !template || (mode !== "upscale" && !prompt.trim())}
                  onClick={() => void generate()}
                >
                  {mode === "edit" ? t("Edit the picture") : mode === "upscale" ? t("Upscale") : t("Generate")}
                </Button>
                {running && (
                  <Button variant="danger" onClick={() => void cancel()}>
                    {t("Cancel")}
                  </Button>
                )}
              </div>

              {running && job && (
                <div className="grid gap-1.5">
                  <ProgressBar indeterminate />
                  <div className="flex justify-between text-xs text-ink-3">
                    <span>
                      {job.phase === "queued" ? t("Waiting for the engine") : t("Drawing")} · {secondsLabel(job.elapsed)}
                    </span>
                    {status.data?.queue && <span>{t("{n} waiting", { n: status.data.queue.pending })}</span>}
                  </div>
                </div>
              )}

              {job?.status === "failed" && job.error && (
                <Note tone="bad">
                  <div className="grid gap-1">
                    <span>{job.error}</span>
                    {job.hints.map((hint) => (
                      <span key={hint.code} className="text-xs text-ink-3">
                        + {hintText(hint)}
                      </span>
                    ))}
                  </div>
                </Note>
              )}
            </Panel>
          )}
        </div>

        {/* -- the right column: the picture, what is downloading, the gallery -- */}
        <div className="grid min-w-0 content-start gap-4">
          <Panel className="grid gap-3">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold">{current ? t("The picture") : t("Nothing generated yet")}</h2>
              {current && (
                <div className="flex items-center gap-1.5">
                  <Button size="sm" icon={<Upload size={12} />} onClick={() => void attachFromGallery(current)}>
                    {t("Edit this one")}
                  </Button>
                  <a
                    className="inline-flex h-7 items-center gap-1.5 rounded-md border border-line px-2.5 text-xs text-ink-2 hover:border-line-strong hover:text-ink"
                    href={fileUrl(base, current.id)}
                    download={`${current.id}.png`}
                  >
                    <Download size={12} /> {t("Download")}
                  </a>
                  <IconButton label={t("Delete")} onClick={() => void forget(current)}>
                    <Trash2 size={14} />
                  </IconButton>
                </div>
              )}
            </div>

            <div className="grid min-h-[320px] place-items-center rounded-md border border-line bg-bg p-2">
              {current ? (
                <img src={fileUrl(base, current.id)} alt={current.prompt} className="max-h-[62vh] rounded-sm object-contain" />
              ) : (
                <div className="grid justify-items-center gap-2 py-16 text-center text-ink-3">
                  <ImageIcon size={28} />
                  <p className="max-w-sm text-sm">
                    {t("Choose a model, describe the picture and press Generate. What comes back stays on this PC.")}
                  </p>
                </div>
              )}
            </div>

            {current && (
              <div className="grid gap-1 text-xs text-ink-2">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone="accent">
                    {current.width} × {current.height}
                  </Badge>
                  <span className="font-mono">{t("seed {n}", { n: current.seed })}</span>
                  {current.sampler && (
                    <span className="font-mono text-ink-3">
                      {current.sampler} · {t("{n} steps", { n: current.steps ?? 0 })} · CFG {current.cfg}
                    </span>
                  )}
                  {current.elapsed_seconds ? <span className="text-ink-3">{secondsLabel(current.elapsed_seconds)}</span> : null}
                  <span className="text-ink-3">{bytesLabel(current.bytes)}</span>
                </div>
                <p className="selectable line-clamp-2">{current.prompt}</p>
                {current.loras.length > 0 && (
                  <p className="font-mono text-2xs text-ink-3">
                    {current.loras.map((lora) => `${lora.name}:${lora.weight}`).join("  ")}
                  </p>
                )}
              </div>
            )}
          </Panel>

          {activeDownloads.length > 0 && (
            <Section title={t("Downloading")} aside={t("{n} files", { n: activeDownloads.length })}>
              <ul className="grid gap-2">
                {activeDownloads.map((item) => (
                  <li key={item.component_id} className="grid gap-1 rounded-md border border-line bg-surface px-3 py-2">
                    <div className="flex items-center justify-between gap-2 text-xs">
                      <span className="font-mono text-ink-2">{item.component_id}</span>
                      <span className="tabular text-ink-3">
                        {item.percent != null ? `${item.percent.toFixed(0)}%` : item.status}
                        {item.total ? ` · ${sizeLabel(item.total)}` : ""}
                        {item.speed ? ` · ${sizeLabel(item.speed)}/s` : ""}
                      </span>
                    </div>
                    <ProgressBar value={item.percent != null ? item.percent / 100 : undefined} indeterminate={item.percent == null} />
                  </li>
                ))}
              </ul>
            </Section>
          )}

          <Section
            title={t("Gallery")}
            aside={
              gallery.data?.images.length
                ? gallery.data.images.length === 1
                  ? t("1 picture")
                  : t("{n} pictures", { n: gallery.data.images.length })
                : t("The pictures this PC drew")
            }
          >
            {gallery.loading ? (
              <p className="text-sm text-ink-3">{t("Loading")}</p>
            ) : (gallery.data?.images.length ?? 0) === 0 ? (
              <p className="text-sm text-ink-3">{t("Nothing yet.")}</p>
            ) : (
              <ul className="grid grid-cols-4 gap-2">
                {(gallery.data?.images ?? []).map((image) => (
                  <li key={image.id}>
                    <button
                      type="button"
                      onClick={() => setCurrent(image)}
                      title={image.prompt}
                      className={cx(
                        "group relative block aspect-square w-full overflow-hidden rounded-md border transition-colors",
                        current?.id === image.id ? "border-accent" : "border-line hover:border-line-strong",
                      )}
                    >
                      <img src={fileUrl(base, image.id)} alt={image.prompt} loading="lazy" className="h-full w-full object-cover" />
                      <span className="absolute inset-x-0 bottom-0 hidden bg-bg/85 px-1.5 py-1 text-left text-2xs text-ink-2 group-hover:block">
                        {image.width}×{image.height} · {t("seed {n}", { n: image.seed })}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Section>

          <Section title={t("How this works")}>
            <div className="grid gap-1.5 text-xs text-ink-2">
              <p>
                {t(
                  "ComfyUI is the engine. Will It Run starts it, queues a workflow to it, waits, and keeps its own copy of the picture and of the settings that made it.",
                )}
              </p>
              <p>
                {t(
                  "A model is a list of parts, and an encoder or a VAE shared by two models is downloaded once. Nothing counts as installed unless the file is in ComfyUI's folder.",
                )}
              </p>
              <p className="text-ink-3">
                {t(
                  "What you generate is your responsibility: only adults, and only people who agreed to be depicted. Licences differ per model and are shown with it.",
                )}
              </p>
            </div>
          </Section>
        </div>
      </div>

      {mode !== "txt2img" && (
        <PasteLayer
          onFile={(file) => {
            void uploadFile(file, file.name || "pasted.png");
          }}
        />
      )}
    </>
  );
}

/** Ctrl+V puts a copied picture into the edit slot, wherever the focus is. */
function PasteLayer({ onFile }: { onFile: (file: File) => void }) {
  useEffect(() => {
    function onPaste(event: ClipboardEvent) {
      const item = Array.from(event.clipboardData?.items ?? []).find((entry) => entry.type.startsWith("image/"));
      const file = item?.getAsFile();
      if (file) onFile(file);
    }
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, [onFile]);
  return null;
}


