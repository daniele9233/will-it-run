import { CheckCircle2, ImagePlus, Loader2, Sparkles, X } from "lucide-react";
import { useMemo, useState } from "react";

import { CloudModelsLink, ModelOptions } from "../../components/ModelOptions";
import { Button, IconButton, Note, Select, cx } from "../../components/ui";
import { api, openExternal } from "../../lib/api";
import type { CloneSetup, SkillsStatus } from "../../lib/cloneSite";
import { errorMessage, useInterval, useLoad } from "../../lib/hooks";
import { getLang, t } from "../../lib/i18n";
import { useModelChoices } from "../../lib/models";
import { useApp } from "../../lib/store";
import type { InstalledModel } from "../../lib/types";

const MODEL_KEY = "agentfit.clone.picture.model";

function remembered(key: string): string {
  try {
    return window.localStorage.getItem(key) ?? "";
  } catch {
    return "";
  }
}
function remember(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // not remembered this time
  }
}

/** The agent skills (GSAP, frontend design, Vercel, Three.js, Playwright): which are on this PC, and a button to fetch them. */
function SkillsCard() {
  const toast = useApp((s) => s.toast);
  const status = useLoad(() => api.get<SkillsStatus>("/api/clone/skills"));
  const running = status.data?.job?.state === "running";
  useInterval(status.reload, running ? 2000 : null);
  const [busy, setBusy] = useState(false);
  const install = () => {
    setBusy(true);
    api
      .post("/api/clone/skills/install", {})
      .then(status.reload)
      .catch((err: unknown) => toast({ tone: "bad", title: t("The skills could not be installed"), body: errorMessage(err) }))
      .finally(() => setBusy(false));
  };
  const data = status.data;
  const count = data?.packs.reduce((n, p) => n + (p.ready ? p.skills.length : 0), 0) ?? 0;
  return (
    <div className="clone-card grid content-start gap-2 p-4" aria-label={t("Skills")}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="clone-label">{t("Skills the model can read")}</span>
        {data && !data.ready && !running && (
          <Button size="sm" variant="primary" loading={busy} onClick={install}>{t("Install the skills")}</Button>
        )}
        {running && <span className="flex items-center gap-1.5 text-2xs text-warn"><Loader2 size={12} className="animate-spin" aria-hidden />{t("Fetching from GitHub")}</span>}
      </div>
      <p className="text-xs leading-relaxed text-ink-2">
        {t("The skills of a coding agent, fetched from GitHub at one exact version each and copied into the project, where the model reads the ones it needs. {n} installed.", { n: count })}
      </p>
      {data && (
        <ul className="grid gap-1 sm:grid-cols-2">
          {data.packs.map((p) => (
            <li key={p.id} className="grid grid-cols-[14px_1fr] items-start gap-x-2 text-xs" title={p.summary}>
              <span className="pt-[1px]">{p.ready ? <CheckCircle2 size={13} className="text-ok" aria-hidden /> : <span className="mx-[3px] inline-block h-[7px] w-[7px] rounded-full border border-line-strong" aria-hidden />}</span>
              <span className="min-w-0">
                <button type="button" onClick={() => void openExternal(p.repo)} className="font-medium text-ink hover:text-accent">{p.id}</button>
                <span className="ml-1.5 font-mono text-2xs text-ink-3">{p.skills.length} · {p.license} · {p.commit}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
      {data?.job?.state === "failed" && <Note tone="bad">{data.job.error}</Note>}
    </div>
  );
}

/** Start a site from a picture: the reference image, what the site should be, a model that sees. */
export function PictureStart({ setup, onStarted }: { setup: CloneSetup | null; onStarted: (id: string) => void }) {
  const toast = useApp((s) => s.toast);
  const { all, providers, installed, cloud } = useModelChoices();
  const seeing = useMemo(() => all.filter((m) => m.type === "llm" && m.vision), [all]);
  const [model, setModel] = useState(() => remembered(MODEL_KEY));
  const chosen = seeing.some((m) => m.model_key === model) ? model : (seeing[0]?.model_key ?? "");
  const [file, setFile] = useState<{ name: string; data: string; preview: string } | null>(null);
  const [brief, setBrief] = useState("");
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const ready = Boolean(setup?.ready);

  const read = (picked: File | undefined) => {
    if (!picked) return;
    if (!picked.type.startsWith("image/")) {
      toast({ tone: "bad", title: t("That file is not a picture"), body: t("Choose a PNG, JPEG, WebP or GIF file.") });
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const url = String(reader.result);
      setFile({ name: picked.name, data: url.slice(url.indexOf(",") + 1), preview: url });
    };
    reader.readAsDataURL(picked);
  };
  const start = () => {
    if (!file || !chosen || busy) return;
    setBusy(true);
    api
      .post<{ study: string; run: string }>("/api/clone/picture", { reference: { name: file.name, data: file.data }, brief, model_key: chosen, language: getLang() })
      .then((data) => onStarted(data.study))
      .catch((err: unknown) => toast({ tone: "bad", title: t("The site could not start"), body: errorMessage(err) }))
      .finally(() => setBusy(false));
  };

  return (
    <div className="mx-auto grid w-full max-w-[880px] gap-4">
      <div className="grid gap-2">
        <span className="clone-label">1 · {t("The reference picture")}</span>
        <label
          onDragOver={(e) => {
            e.preventDefault();
            setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setOver(false);
            read(e.dataTransfer.files[0]);
          }}
          className={cx("clone-card relative grid cursor-pointer place-items-center gap-2 p-4 text-center transition-colors duration-150 hover:border-accent", over && "border-accent")}
        >
          <input type="file" accept="image/png,image/jpeg,image/webp,image/gif" className="sr-only" onChange={(e) => read(e.target.files?.[0])} />
          {file ? (
            <span className="flex max-w-full items-center gap-3">
              <img src={file.preview} alt="" className="max-h-[180px] max-w-[60%] rounded-md border border-line object-contain" />
              <span className="grid min-w-0 gap-1 text-left">
                <span className="truncate font-mono text-xs text-ink">{file.name}</span>
                <span className="text-2xs text-ink-3">{t("Click to choose another")}</span>
              </span>
              <IconButton
                label={t("Remove")}
                onClick={(e) => {
                  e.preventDefault();
                  setFile(null);
                }}
              >
                <X size={14} />
              </IconButton>
            </span>
          ) : (
            <span className="grid justify-items-center gap-1.5 py-3 text-ink-2">
              <ImagePlus size={26} className="text-accent" aria-hidden />
              <span className="text-[13px] font-medium text-ink">{t("Drop the picture here, or click to choose it")}</span>
              <span className="text-2xs text-ink-3">{t("PNG, JPEG, WebP or GIF, up to 25 MB. The site is built to look like it.")}</span>
            </span>
          )}
        </label>
      </div>

      <div className="clone-card grid gap-3 p-4">
        <label className="grid gap-1 text-xs text-ink-2">
          <span className="clone-label">2 · {t("What should the site be?")}</span>
          <textarea
            value={brief}
            onChange={(e) => setBrief(e.target.value)}
            rows={4}
            placeholder={t("For example: the homepage of a menswear brand, in Italian; an intro of two seconds, a gentle parallax with the mouse, a lift when the pointer is over a card. The last frame must be the picture.")}
            className="resize-y rounded-md border border-line bg-bg px-2.5 py-1.5 text-xs text-ink outline-none placeholder:text-ink-3 focus:border-accent"
          />
        </label>
        <label className="grid gap-1 text-xs text-ink-2">
          <span className="clone-label">3 · {t("Model that sees")}</span>
          <Select
            value={chosen}
            onChange={(e) => {
              setModel(e.target.value);
              remember(MODEL_KEY, e.target.value);
            }}
          >
            {seeing.length === 0 && <option value="">{t("No model that sees yet")}</option>}
            <ModelOptions models={seeing} providers={providers} label={(m: InstalledModel) => m.display_name} />
          </Select>
          {installed.data && cloud.data && <CloudModelsLink models={seeing} />}
        </label>
        <p className="text-2xs leading-relaxed text-ink-3">
          {t("The model looks at the picture, writes its composition, then one component for each region; the page is measured against the picture and the weak regions are written again. A smaller model is faster and has more room; a larger one writes better code.")}
        </p>
      </div>

      <SkillsCard />

      <div className="flex flex-wrap items-center gap-3">
        <Button variant="primary" icon={<Sparkles size={14} />} loading={busy} disabled={!file || !chosen || !ready} onClick={start}>
          {t("Build the site")}
        </Button>
        {!ready && <span className="text-2xs text-ink-3">{t("Finish the preparation above first.")}</span>}
      </div>
    </div>
  );
}
