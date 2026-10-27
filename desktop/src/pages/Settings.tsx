import { useEffect, useState } from "react";

import { CloudKeys } from "../components/CloudKeys";
import { Badge, Button, Note, PageHeader, Segmented, Select, SkeletonRows, Tabs, TextInput, Toggle, toneText } from "../components/ui";
import { api } from "../lib/api";
import { canBrowseFolders, pickFolder } from "../lib/dialog";
import { errorMessage, useLoad } from "../lib/hooks";
import { useApp } from "../lib/store";
import type { DoctorCheck, EmbedStatus, InstalledModel, Settings } from "../lib/types";
import { ZOOM_MAX, ZOOM_MIN, ZOOM_STEP } from "../lib/zoom";
import { LANGUAGES, t } from "../lib/i18n";

type Tab = "general" | "models" | "cloud" | "images" | "hardware" | "storage" | "runtime" | "workflows" | "scoring" | "doctor";

const ACCENTS = ["#4aa8e0", "#5b8def", "#35b8a6", "#8a7cf0", "#e0913a", "#d9668f"];

const SCORE_LABEL: Record<string, string> = {
  hardware_fit: t("Hardware fit"),
  task_quality: t("Task quality"),
  specialization: t("Specialization"),
  speed: t("Speed"),
  context: t("Context"),
  tool_use: t("Tool use"),
  reliability: t("Reliability"),
  local_benchmark: t("Local benchmark"),
};

function Row({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-8 border-b border-line py-3.5">
      <div>
        <div className="text-sm">{title}</div>
        {hint && <div className="mt-0.5 max-w-[60ch] text-xs text-ink-3">{hint}</div>}
      </div>
      <div className="flex items-center gap-2">{children}</div>
    </div>
  );
}

function NumberSetting({ value, onCommit, min, max, step = 1, suffix }: { value: number; onCommit: (value: number) => void; min: number; max: number; step?: number; suffix?: string }) {  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  const commit = () => {
    const parsed = Number(text);
    if (Number.isFinite(parsed) && parsed >= min && parsed <= max) {
      if (parsed !== value) onCommit(parsed);
    } else {
      setText(String(value));
    }
  };
  return (
    <div className="flex items-center gap-2">
      <TextInput
        type="number"
        value={text}
        min={min}
        max={max}
        step={step}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === "Enter" && commit()}
        className="tabular w-24 text-right"
      />
      {suffix && <span className="w-10 text-xs text-ink-3">{suffix}</span>}
    </div>
  );
}

/** A free-text setting: it is saved on Enter or when the field is left, never on every keystroke. */
function TextSetting({ value, onCommit, placeholder, label }: { value: string; onCommit: (value: string) => void; placeholder?: string; label: string }) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  const commit = () => {
    const trimmed = text.trim();
    if (trimmed !== value) onCommit(trimmed);
  };
  return (
    <TextInput
      value={text}
      onChange={(e) => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => e.key === "Enter" && commit()}
      placeholder={placeholder}
      aria-label={label}
      className="w-64"
    />
  );
}

/** Which embedding model turns memories into vectors, and whether one is there at all. */
function EmbeddingSetting({ value, onChange }: { value: string; onChange: (key: string) => void }) {
  const installed = useLoad(() => api.get<{ models: InstalledModel[] }>("/api/models/installed"));
  const brain = useLoad(() => api.get<{ semantic: EmbedStatus }>("/api/brain"), [value]);
  const models = (installed.data?.models ?? []).filter((m) => m.type === "embedding");
  const status = brain.data?.semantic;
  return (
    <div className="grid justify-items-end gap-1">
      <Select value={models.some((m) => m.model_key === value) ? value : ""} onChange={(event) => onChange(event.target.value)} className="w-[300px]" disabled={models.length === 0} aria-label={t("Embedding model")}>
        <option value="">{status?.model && !value ? t("Automatic ({model})", { model: status.model }) : t("Automatic")}</option>
        {models.map((m) => (
          <option key={m.model_key} value={m.model_key}>
            {m.display_name}
          </option>
        ))}
      </Select>
      {status && !status.available && (
        <span className="max-w-[300px] text-right text-2xs text-warn">
          {status.code === "no_model" ? t("No embedding model in LM Studio: search by words only.") : t("Not reachable right now: search by words only.")}
        </span>
      )}
    </div>
  );
}

function Doctor() {
  const doctor = useLoad(() => api.get<{ overall: string; checks: DoctorCheck[] }>("/api/doctor"));
  const tone = (status: string) => (status === "PASS" ? "ok" : status === "WARNING" ? "warn" : "bad");
  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <div className="text-sm text-ink-2">
          {doctor.data ? (
            <>
              {t("Overall:")} <span className={`font-semibold ${toneText[tone(doctor.data.overall)]}`}>{doctor.data.overall}</span>
            </>
          ) : (
            t("Checking this computer...")
          )}
        </div>
        <Button size="sm" loading={doctor.loading} onClick={doctor.reload}>
          {t("Run again")}
        </Button>
      </div>
      {doctor.loading && !doctor.data && <SkeletonRows rows={10} height={30} />}
      {doctor.error && <Note tone="bad">{doctor.error}</Note>}
      <ul className="border-t border-line">
        {doctor.data?.checks.map((check) => (
          <li key={check.name} className="grid grid-cols-[84px_150px_1fr] items-baseline gap-3 border-b border-line py-2.5 text-sm">
            <Badge tone={tone(check.status)}>{check.status}</Badge>
            <span className="font-medium">{check.name}</span>
            <span className="selectable min-w-0 text-ink-2">
              {check.detail}
              {check.hint && check.status !== "PASS" && <span className="mt-0.5 block text-xs text-ink-3">{check.hint}</span>}
            </span>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-xs text-ink-3">{t("The same checks are available from a terminal: willitrun doctor")}</p>
    </div>
  );
}

export function SettingsPage() {
  const settings = useApp((s) => s.settings);
  const saveSettings = useApp((s) => s.saveSettings);
  const loadHardware = useApp((s) => s.loadHardware);
  const loadLMStudio = useApp((s) => s.loadLMStudio);
  const lmstudio = useApp((s) => s.lmstudio);
  const toast = useApp((s) => s.toast);
  const setLanguage = useApp((s) => s.setLanguage);
  const setZoom = useApp((s) => s.setZoom);
  const routeTab = useApp((s) => s.params.tab) as Tab | undefined;
  const [tab, setTab] = useState<Tab>(routeTab ?? "general");
  const [token, setToken] = useState("");

  useEffect(() => {
    if (routeTab) setTab(routeTab);
  }, [routeTab]);

  if (!settings) return <div className="p-7"><SkeletonRows rows={6} height={48} /></div>;

  const save = (patch: Partial<{ [K in keyof Settings]: Partial<Settings[K]> }>, after?: () => void) => {
    saveSettings(patch as Record<string, unknown>)
      .then(() => after?.())
      .catch((err: unknown) => toast({ tone: "bad", title: t("Setting not saved"), body: errorMessage(err) }));
  };
  const refreshBudget = () => void loadHardware();
  const zoom = settings.ui.zoom ?? 1;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader title={t("Settings")} subtitle={t("Changes are saved immediately to your Windows profile.")} />
      <div className="shrink-0 px-7 pt-3">
        <Tabs
          value={tab}
          onChange={setTab}
          tabs={[
            { value: "general", label: t("General") },
            { value: "models", label: t("Models") },
            { value: "cloud", label: t("Cloud models") },
            { value: "images", label: t("Images") },
            { value: "hardware", label: t("Hardware") },
            { value: "storage", label: t("Storage") },
            { value: "runtime", label: t("Runtime") },
            { value: "workflows", label: t("Workflows") },
            { value: "scoring", label: t("Scoring") },
            { value: "doctor", label: t("Doctor") },
          ]}
        />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-7 py-3">
        <div className="max-w-[860px]">
          {tab === "general" && (
            <>
              <Row title={t("Language")} hint={t("The window reloads to apply it. Text written by the models themselves stays in the language they answer in.")}>
                <Segmented
                  label={t("Language")}
                  value={settings.ui.language}
                  onChange={(language) => void setLanguage(language).catch((err: unknown) => toast({ tone: "bad", title: t("Setting not saved"), body: errorMessage(err) }))}
                  options={[{ value: "system" as const, label: t("Windows language") }, ...LANGUAGES]}
                />
              </Row>
              <Row title={t("Interface size")} hint={t("Makes everything larger or smaller. Also Ctrl + and Ctrl −, or Ctrl with the mouse wheel; Ctrl 0 goes back to normal.")}>
                <Button size="sm" aria-label={t("Zoom out")} disabled={zoom <= ZOOM_MIN} onClick={() => setZoom(zoom - ZOOM_STEP)}>
                  −
                </Button>
                <span className="tabular w-12 text-center text-sm">{Math.round(zoom * 100)}%</span>
                <Button size="sm" aria-label={t("Zoom in")} disabled={zoom >= ZOOM_MAX} onClick={() => setZoom(zoom + ZOOM_STEP)}>
                  +
                </Button>
                <Button size="sm" variant="ghost" disabled={zoom === 1} onClick={() => setZoom(1)}>
                  {t("Normal size")}
                </Button>
              </Row>
              <Row title={t("Experience mode")} hint={t("Automatic: Will It Run decides everything. Guided: Will It Run recommends, you approve. Expert: every file, quantization and memory figure is exposed.")}>
                <Segmented
                  label={t("Experience mode")}
                  value={settings.ui.mode}
                  onChange={(mode) => save({ ui: { mode } })}
                  options={[
                    { value: "automatic", label: t("Automatic") },
                    { value: "guided", label: t("Guided") },
                    { value: "expert", label: t("Expert") },
                  ]}
                />
              </Row>
              <Row title={t("Theme")}>
                <Segmented
                  label={t("Theme")}
                  value={settings.ui.theme}
                  onChange={(theme) => save({ ui: { theme } })}
                  options={[
                    { value: "dark", label: t("Dark") },
                    { value: "light", label: t("Light") },
                    { value: "system", label: t("System") },
                  ]}
                />
              </Row>
              <Row title={t("Accent color")} hint={t("Used for primary actions, selection and active state.")}>
                {ACCENTS.map((color) => (
                  <button
                    key={color}
                    type="button"
                    aria-label={t("Accent {color}", { color })}
                    aria-pressed={settings.ui.accent.toLowerCase() === color}
                    onClick={() => save({ ui: { accent: color } })}
                    className="h-6 w-6 rounded-full border-2"
                    style={{ background: color, borderColor: settings.ui.accent.toLowerCase() === color ? "var(--ink)" : "transparent" }}
                  />
                ))}
                <input
                  type="color"
                  aria-label={t("Custom accent color")}
                  value={settings.ui.accent}
                  onChange={(e) => save({ ui: { accent: e.target.value } })}
                  className="h-7 w-9 cursor-pointer rounded-md border border-line bg-bg"
                />
              </Row>
              <Row title={t("Windows notifications")} hint={t("Download complete, benchmark complete, workflow needs approval, workflow complete. Shown when Will It Run is in the background.")}>
                <Toggle label={t("Windows notifications")} checked={settings.ui.notifications} onChange={(notifications) => save({ ui: { notifications } })} />
              </Row>
              <Row title={t("First-start scan")} hint={t("Run the welcome scan again the next time Will It Run starts.")}>
                <Button size="sm" onClick={() => save({ ui: { first_run_complete: false } })}>
                  {t("Show welcome scan")}
                </Button>
              </Row>
            </>
          )}

          {tab === "models" && (
            <>
              <Row
                title={t("Model preference")}
                hint={t("Influences recommendation ranking only. Models of the other kind stay visible and installable; nothing is hidden or penalized.")}
              >
                <Segmented
                  label={t("Model preference")}
                  value={settings.models.alignment_preference}
                  onChange={(alignment_preference) => save({ models: { alignment_preference } })}
                  options={[
                    { value: "any", label: t("Any model") },
                    { value: "standard", label: t("Standard preferred") },
                    { value: "uncensored", label: t("Uncensored / abliterated preferred") },
                  ]}
                />
              </Row>
              <Row title={t("Default context length")} hint={t("Used for fit calculations unless a job needs more.")}>
                <Select value={settings.models.default_context_length} onChange={(e) => save({ models: { default_context_length: Number(e.target.value) } })} className="w-40">
                  {[4096, 8192, 16384, 32768, 65536].map((c) => (
                    <option key={c} value={c}>
                      {t("{n} tokens", { n: c.toLocaleString() })}
                    </option>
                  ))}
                </Select>
              </Row>
              <Row title={t("Search Hugging Face")} hint={t("Needs Internet. When off, Will It Run works from the built-in catalog, cached metadata and your installed models.")}>
                <Toggle label={t("Search Hugging Face")} checked={settings.models.huggingface_enabled} onChange={(huggingface_enabled) => save({ models: { huggingface_enabled } })} />
              </Row>
              <Row title={t("Target speed")} hint={t("A model at or above this speed counts as fast enough.")}>
                <NumberSetting value={settings.performance.target_tokens_per_second} min={1} max={500} onCommit={(target_tokens_per_second) => save({ performance: { target_tokens_per_second } })} suffix="tok/s" />
              </Row>
            </>
          )}

          {tab === "cloud" && (
            <div className="py-3">
              <CloudKeys />
              <Row title={t("Most context for a cloud model")} hint={t("Every token a cloud model reads is paid for. Conversations longer than this are shortened, oldest tool results first.")}>
                <NumberSetting value={settings.cloud?.max_context ?? 200000} min={16000} max={2000000} step={1000} suffix={t("tokens")} onCommit={(max_context) => save({ cloud: { max_context } })} />
              </Row>
            </div>
          )}

          {tab === "images" && (
            <>
              <Row
                title={t("ComfyUI address")}
                hint={t("Where the image engine listens. Will It Run starts nothing itself: start ComfyUI on this PC, or point this at the one you run.")}
              >
                <TextSetting
                  label={t("ComfyUI address")}
                  value={settings.images.comfy_url}
                  placeholder="http://127.0.0.1:8188"
                  onCommit={(comfy_url) => save({ images: { comfy_url } })}
                />
              </Row>
              <Row
                title={t("ComfyUI folder")}
                hint={t("The folder that holds main.py. Empty: Will It Run looks in the usual places, including Comfy Desktop's installs.")}
              >
                <TextSetting
                  label={t("ComfyUI folder")}
                  value={settings.images.comfy_dir}
                  placeholder={t("Look for it")}
                  onCommit={(comfy_dir) => save({ images: { comfy_dir } })}
                />
              </Row>
              <Row
                title={t("Civitai token")}
                hint={t("Civitai gives its files only to a signed-in account: create a token on civitai.com and paste it here to download the Civitai models. Hugging Face models need none.")}
              >
                <TextSetting
                  label={t("Civitai token")}
                  value={settings.images.civitai_token}
                  placeholder={t("Optional")}
                  onCommit={(civitai_token) => save({ images: { civitai_token } })}
                />
              </Row>
              <Row
                title={t("Hugging Face token")}
                hint={t("Only needed for gated repositories. The public ones download without a token.")}
              >
                <TextSetting
                  label={t("Hugging Face token")}
                  value={settings.images.hf_token}
                  placeholder={t("Optional")}
                  onCommit={(hf_token) => save({ images: { hf_token } })}
                />
              </Row>
              <Row
                title={t("Gallery folder")}
                hint={t("Every picture generated is copied here, with the settings that made it. Empty: the app's own data folder, which you can open from the Images page.")}
              >
                <TextSetting
                  label={t("Gallery folder")}
                  value={settings.images.gallery_dir}
                  placeholder={t("Empty: the app's own data folder")}
                  onCommit={(gallery_dir) => save({ images: { gallery_dir } })}
                />
              </Row>
              <Row
                title={t("Time limit per picture")}
                hint={t("Seconds. The first picture after starting the engine loads the model into memory and takes far longer than the ones after it.")}
              >
                <NumberSetting
                  value={settings.images.timeout_seconds}
                  min={60}
                  max={7200}
                  step={60}
                  suffix="s"
                  onCommit={(timeout_seconds) => save({ images: { timeout_seconds } })}
                />
              </Row>
              <Row
                title={t("Hand the graphics card back")}
                hint={t("Unload ComfyUI's models when you leave the Images page, so a language model in LM Studio can use the whole card. The next picture then has to load them again. The Free VRAM button does the same on demand.")}
              >
                <Toggle
                  label={t("Hand the graphics card back")}
                  checked={settings.images.free_vram_on_leave}
                  onChange={(free_vram_on_leave) => save({ images: { free_vram_on_leave } })}
                />
              </Row>
            </>
          )}

          {tab === "hardware" && (
            <>
              <Note>{t("Will It Run never plans with all of your VRAM or RAM. These reserves decide how much is left out.")}</Note>
              <Row title={t("Windows / desktop VRAM reserve")} hint={t("VRAM that Windows, the compositor and open apps keep on the GPU that drives your display.")}>
                <NumberSetting value={settings.hardware.vram_os_reserve_gb} min={0} max={8} step={0.1} onCommit={(vram_os_reserve_gb) => save({ hardware: { vram_os_reserve_gb } }, refreshBudget)} suffix="GB" />
              </Row>
              <Row title={t("VRAM safety reserve")} hint={t("Extra margin as a share of each GPU's VRAM.")}>
                <NumberSetting value={settings.hardware.vram_reserve_percent} min={0} max={40} onCommit={(vram_reserve_percent) => save({ hardware: { vram_reserve_percent } }, refreshBudget)} suffix="%" />
              </Row>
              <Row title={t("RAM reserve")} hint={t("System memory left for Windows and your applications.")}>
                <NumberSetting value={settings.hardware.ram_reserve_percent} min={0} max={60} onCommit={(ram_reserve_percent) => save({ hardware: { ram_reserve_percent } }, refreshBudget)} suffix="%" />
              </Row>
              <Row title={t("Speed estimate efficiency")} hint={t("Share of peak memory bandwidth inference sustains. Lower it if estimates run high for your setup. Default 0.55.")}>
                <NumberSetting value={settings.hardware.tps_efficiency} min={0.1} max={1} step={0.05} onCommit={(tps_efficiency) => save({ hardware: { tps_efficiency } })} />
              </Row>
            </>
          )}

          {tab === "storage" && (
            <>
              <Row title={t("Disk safety reserve")} hint={t("Share of a drive's capacity that downloads must leave free (at most 30 GB).")}>
                <NumberSetting value={settings.storage.reserve_percent} min={0} max={50} onCommit={(reserve_percent) => save({ storage: { reserve_percent } })} suffix="%" />
              </Row>
              <Row title={t("Minimum free space")} hint={t("Never let a download leave less than this, whatever the percentage says.")}>
                <NumberSetting value={settings.storage.min_free_gb} min={1} max={500} onCommit={(min_free_gb) => save({ storage: { min_free_gb } })} suffix="GB" />
              </Row>
              <Row title={t("Model directory")} hint={t("Leave empty to use LM Studio's folder{dir}. Models are saved here in LM Studio's publisher/repository layout.", { dir: lmstudio?.models_dir ? ` (${lmstudio.models_dir})` : "" })}>
                <TextInput
                  defaultValue={settings.storage.custom_model_dir}
                  placeholder={lmstudio?.models_dir ?? "D:\\AI\\Models"}
                  onBlur={(e) => e.target.value !== settings.storage.custom_model_dir && save({ storage: { custom_model_dir: e.target.value.trim() } })}
                  className="w-72 font-mono text-xs"
                  aria-label={t("Model directory")}
                  key={settings.storage.custom_model_dir}
                />
                {canBrowseFolders && (
                  <Button
                    size="sm"
                    onClick={() =>
                      void pickFolder(settings.storage.custom_model_dir || lmstudio?.models_dir || undefined).then((picked) => {
                        if (picked) save({ storage: { custom_model_dir: picked } });
                      })
                    }
                  >
                    {t("Browse")}
                  </Button>
                )}
                {settings.storage.custom_model_dir && (
                  <Button size="sm" variant="ghost" onClick={() => save({ storage: { custom_model_dir: "" } })}>
                    {t("Use LM Studio's folder")}
                  </Button>
                )}
              </Row>
              <Row title={t("Verify downloads")} hint={t("Check the SHA-256 hash of direct downloads against the one published by the repository.")}>
                <Toggle label={t("Verify downloads")} checked={settings.downloads.verify_hash} onChange={(verify_hash) => save({ downloads: { verify_hash } })} />
              </Row>
            </>
          )}

          {tab === "runtime" && (
            <>
              <Row title="LM Studio" hint={lmstudio?.app_path ?? lmstudio?.home ?? t("Not found on this computer.")}>
                <Badge tone={lmstudio?.api_reachable ? "ok" : lmstudio?.installed ? "warn" : "off"}>
                  {lmstudio?.api_reachable ? t("CONNECTED") : lmstudio?.installed ? t("SERVER OFFLINE") : t("NOT INSTALLED")}
                </Badge>
                {lmstudio?.installed && !lmstudio.api_reachable && (
                  <Button size="sm" onClick={() => void api.post("/api/lmstudio/server/start").then(loadLMStudio).catch((err: unknown) => toast({ tone: "bad", title: t("Could not start the server"), body: errorMessage(err) }))}>
                    {t("Start server")}
                  </Button>
                )}
              </Row>
              {lmstudio?.notes.map((note) => (
                <div key={note} className="py-2">
                  <Note tone="warn">{note}</Note>
                </div>
              ))}
              <Row title={t("Server port")} hint={t("LM Studio's local server port (Developer tab in LM Studio). Default 1234.")}>
                <NumberSetting value={settings.runtime.lmstudio_port} min={1} max={65535} onCommit={(lmstudio_port) => save({ runtime: { lmstudio_port } }, () => void loadLMStudio())} />
              </Row>
              <Row title={t("API token")} hint={`${t("Only needed if you enabled authentication in LM Studio.")} ${settings.runtime.lmstudio_api_token_set ? t("A token is stored.") : t("No token stored.")} ${t("It is never shown again or written to logs.")}`}>
                <TextInput type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder={t("Paste token")} className="w-52" aria-label={t("LM Studio API token")} />
                <Button size="sm" disabled={!token && !settings.runtime.lmstudio_api_token_set} onClick={() => save({ runtime: { lmstudio_api_token: token } }, () => setToken(""))}>
                  {token ? t("Save") : t("Clear")}
                </Button>
              </Row>
              <Row title={t("Download method")} hint={t("Will It Run's own downloader can be paused, resumed and stopped, verifies the file and lets you choose the folder. LM Studio's downloader cannot be stopped from here and always saves to LM Studio's folder.")}>
                <Segmented
                  label={t("Download method")}
                  value={settings.downloads.backend as "auto" | "lmstudio" | "direct"}
                  onChange={(backend) => save({ downloads: { backend } })}
                  options={[
                    { value: "auto", label: t("Automatic") },
                    { value: "direct", label: "Will It Run" },
                    { value: "lmstudio", label: "LM Studio" },
                  ]}
                />
              </Row>
              <Row
                title={t("Faster generation (MTP)")}
                hint={t("Some models carry a small extra head that guesses the next words (the \"-mtp\" files of Qwen3.8, for example). LM Studio checks the guesses and keeps the right ones, so the answers keep their quality and come 1.4 to 1.6 times faster. Will It Run turns it on for those models, loads them with one slot and sends no repeat penalty, which would lower the number of right guesses. Turn it off if a model misbehaves.")}
              >
                <Toggle label={t("Faster generation (MTP)")} checked={settings.runtime.speculative_mtp} onChange={(speculative_mtp) => save({ runtime: { speculative_mtp } })} />
              </Row>
              {lmstudio?.engines && lmstudio.engines.length > 0 && (
                <Row title={t("Selected engines")} hint={t("Inference engines selected in LM Studio.")}>
                  <span className="selectable max-w-[360px] text-right font-mono text-xs text-ink-2">{lmstudio.engines.join(", ")}</span>
                </Row>
              )}
            </>
          )}

          {tab === "workflows" && (
            <>
              <Row
                title={t("Require approval for high-risk actions")}
                hint={t("Deleting files, installing software, privilege escalation, registry changes, destructive PowerShell, changing remote infrastructure, publishing, and mouse or keyboard control always ask first.")}
              >
                <Toggle
                  label={t("Require approval for high-risk actions")}
                  checked={settings.workflow.require_approval_for_high_risk_actions}
                  onChange={(require_approval_for_high_risk_actions) => save({ workflow: { require_approval_for_high_risk_actions } })}
                />
              </Row>
              {!settings.workflow.require_approval_for_high_risk_actions && (
                <div className="py-2">
                  <Note tone="bad">{t("High-risk tool calls will run without asking you. Agents are local models and can make mistakes; turn this back on unless you are working in a disposable environment.")}</Note>
                </div>
              )}
              <Row title={t("Unload idle models after")} hint={t("A model left idle this long is unloaded by LM Studio itself, so nothing stays in VRAM if a run is abandoned. A run reloads what it needs. 0 turns it off.")}>
                <NumberSetting
                  value={Math.round(settings.workflow.model_idle_ttl_seconds / 60)}
                  min={0}
                  max={240}
                  onCommit={(minutes) => save({ workflow: { model_idle_ttl_seconds: minutes * 60 } })}
                  suffix="min"
                />
              </Row>
              <Row title={t("VRAM left free when loading")} hint={t("When a workflow loads a model, Will It Run measures the free VRAM and picks the largest context that still leaves this much. If almost nothing is left after the load, it reloads with less context instead of letting Windows spill into system RAM.")}>
                <NumberSetting value={settings.workflow.vram_margin_mib} min={300} max={4000} step={50} onCommit={(vram_margin_mib) => save({ workflow: { vram_margin_mib } })} suffix="MiB" />
              </Row>
              <Row title={t("Default retries per agent")} hint={t("How many times an agent may retry an invalid answer before its escalation policy applies.")}>
                <NumberSetting value={settings.workflow.max_retries} min={0} max={10} onCommit={(max_retries) => save({ workflow: { max_retries } })} />
              </Row>
              <Row title={t("Default step limit")} hint={t("A run stops after this many agent steps, so no workflow can loop forever.")}>
                <NumberSetting value={settings.workflow.max_steps} min={2} max={500} onCommit={(max_steps) => save({ workflow: { max_steps } })} />
              </Row>
              <Row
                title={t("Project memory")}
                hint={t("Every folder or GitHub repository a team or a chat works in keeps a permanent memory: its architecture, the decisions taken, the files changed, the errors met and what fixed them. Each agent is given the few entries its task needs. Turned off, runs neither read nor write it; what is already remembered stays.")}
              >
                <Toggle label={t("Project memory")} checked={settings.memory.enabled} onChange={(enabled) => save({ memory: { enabled } })} />
              </Row>
              {settings.memory.enabled && (
                <Row
                  title={t("Embedding model")}
                  hint={t("The small model that lets the memory be searched by meaning, not only by words. It is loaded on the CPU, so the graphics card stays whole for the model that is working. LM Studio ships with one.")}
                >
                  <EmbeddingSetting value={settings.memory.embedding_model} onChange={(embedding_model) => save({ memory: { embedding_model } })} />
                </Row>
              )}
            </>
          )}

          {tab === "scoring" && (
            <>
              <Note>{t("The Will It Run score is a weighted sum of these factors. Weights are normalized, so only their proportions matter. Alignment (standard or uncensored) is not a factor.")}</Note>
              {Object.entries(settings.scoring).map(([key, value]) => {
                const total = Object.values(settings.scoring).reduce((a, b) => a + b, 0) || 1;
                return (
                  <Row key={key} title={SCORE_LABEL[key] ?? key} hint={t("{n}% of the score", { n: Math.round((value / total) * 100) })}>
                    <NumberSetting value={value} min={0} max={1} step={0.01} onCommit={(next) => save({ scoring: { [key]: next } })} />
                  </Row>
                );
              })}
            </>
          )}

          {tab === "doctor" && <Doctor />}
        </div>
      </div>
    </div>
  );
}
