import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  CircleAlert,
  Copy,
  ExternalLink,
  FolderGit2,
  FolderOpen,
  Globe,
  Loader2,
  MessageCircleQuestion,
  Network,
  RotateCcw,
  Send,
  Square,
  Trash2,
  Wrench,
  X,
} from "lucide-react";
import { Fragment, useEffect, useMemo, useState } from "react";

import { Markdown } from "../components/Markdown";
import { CloudModelsLink, ModelOptions } from "../components/ModelOptions";
import { PlacePicker, type Place } from "../components/PlacePicker";
import { Button, IconButton, Note, ProgressBar, Select, Tabs, cx } from "../components/ui";
import { api, getBackend, openExternal, query } from "../lib/api";
import {
  STAGES,
  TERMINAL,
  activity,
  answerOf,
  buildOf,
  cleanUrl,
  modelName,
  nodeStates,
  prepSteps,
  prepText,
  questions,
  stageText,
  waitingForApproval,
  type CloneAsk,
  type CloneDetail,
  type CloneInfo,
  type CloneSetup,
  type InstallJob,
  type KitInfo,
  type NodeState,
} from "../lib/cloneSite";
import { errorMessage, useBus, useInterval, useLoad } from "../lib/hooks";
import { getLang, t } from "../lib/i18n";
import { useModelChoices } from "../lib/models";
import { repoName, sourcePayload, sourceProblem, type RunSource } from "../lib/runSource";
import { useApp } from "../lib/store";
import type { InstalledModel, PendingApproval } from "../lib/types";
import { useAnswerStream } from "./Chat";
import { ApprovalCard, LiveBlock } from "./Sessions";

const MODEL_KEY = "agentfit.clone.model";
const VISION_KEY = "agentfit.clone.vision";
const NO_VISION = "none";

function remembered(key: string, fallback = ""): string {
  try {
    return window.localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}
function remember(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // not remembered this time
  }
}

// -- small pieces ------------------------------------------------------------------

function useFileUrl(cloneId: string | null): (path: string | null | undefined) => string | null {
  const [base, setBase] = useState<{ url: string; token: string } | null>(null);
  useEffect(() => {
    void getBackend().then(setBase);
  }, []);
  return (path) => (base && cloneId && path ? `${base.url}/api/clone/${cloneId}/file${query({ path, token: base.token || null })}` : null);
}

function Dot({ state }: { state: NodeState | "running" | "done" | "failed" }) {
  if (state === "working" || state === "running") return <Loader2 size={13} className="animate-spin text-warn" aria-hidden />;
  if (state === "done") return <CheckCircle2 size={13} className="text-ok" aria-hidden />;
  if (state === "failed" || state === "stopped") return <CircleAlert size={13} className="text-bad" aria-hidden />;
  return <span className="mx-[3px] inline-block h-[7px] w-[7px] rounded-full border border-line-strong" aria-hidden />;
}

function Lightbox({ src, onClose }: { src: string; onClose: () => void }) {
  useEffect(() => {
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [onClose]);
  return (
    <div role="dialog" aria-modal className="fixed inset-0 z-[80] grid place-items-center bg-black/70 p-6" onClick={onClose}>
      <img src={src} alt="" className="max-h-full max-w-full rounded-md border border-line-strong shadow-[var(--shadow)]" />
      <IconButton label={t("Close")} onClick={onClose} className="absolute top-4 right-4 bg-panel">
        <X size={16} />
      </IconButton>
    </div>
  );
}

// -- getting ready -----------------------------------------------------------------

function SetupCard({ setup, onInstall, onCancel, busy }: { setup: CloneSetup; onInstall: () => void; onCancel: () => void; busy: boolean }) {
  const job: InstallJob | null = setup.job;
  const installing = job?.state === "running";
  const failed = !installing && job?.state === "failed" ? job : null;
  const blocked = setup.problems.length > 0;
  return (
    <div className="clone-card mx-auto grid w-full max-w-[880px] gap-2 p-4">
      <span className={cx("clone-label", failed || blocked ? "text-bad" : "text-accent")}>
        {installing ? t("Installing the browser engine") : failed ? t("The browser engine could not be installed") : blocked ? t("Something is missing on this PC") : t("One step before the first clone")}
      </span>
      {blocked && (
        <ul className="grid gap-1">
          {setup.problems.map((p) => (
            <li key={p} className="grid grid-cols-[12px_1fr] gap-1 text-xs text-ink-2"><span className="text-bad">·</span><span>{p}</span></li>
          ))}
        </ul>
      )}
      {failed && <pre className="selectable max-h-32 overflow-auto rounded-md bg-bg p-2 font-mono text-[10.5px] whitespace-pre-wrap text-ink-2">{failed.output.slice(-14).join("\n")}</pre>}
      {!installing && !failed && !blocked && (
        <p className="text-xs leading-relaxed text-ink-2">
          {t("Will It Run drives the Chrome or Edge you already have with Playwright's library (a few MB, kept in its own folder). The cloning kit is fetched from GitHub the first time you use it, at one exact version; its own scripts also download a copy of Chromium (about 170 MB, once).")}
        </p>
      )}
      {installing ? (
        <div className="grid gap-1.5">
          <ProgressBar indeterminate />
          <Button size="sm" onClick={onCancel} className="justify-self-start">{t("Cancel")}</Button>
        </div>
      ) : (
        !setup.installed && setup.node.ok && setup.npm && (
          <Button size="sm" variant="primary" onClick={onInstall} loading={busy} className="justify-self-start">{failed ? t("Try again") : t("Prepare")}</Button>
        )
      )}
    </div>
  );
}

/** What does the cloning: the kit, its author and licence, and the way it runs here. */
function KitCard({ kit }: { kit: KitInfo | null }) {
  return (
    <div className="clone-card grid content-start gap-2 p-4">
      <span className="flex items-center gap-2.5">
        <span className="grid h-9 w-9 place-items-center rounded-lg border border-accent bg-bg text-accent">
          <Copy size={17} />
        </span>
        <span className="grid min-w-0">
          <span className="truncate text-[15px] font-semibold text-ink">{t("A Next.js project, cloned the way the kit does it")}</span>
          <span className="truncate font-mono text-2xs text-ink-3">{kit ? `${kit.name} · ${kit.author}` : "ai-site-cloner · Mahanaicoach"}</span>
        </span>
      </span>
      <p className="text-xs leading-relaxed text-ink-2">
        {t("The kit's project is put in the folder, its packages are installed, and the model runs its /clone-website skill, exactly as in a coding agent in the terminal: it opens the site, finds the pages and asks you which ones to clone (only the home page, two pages, three…), then studies and rebuilds them as Next.js components.")}
      </p>
      <span className="flex flex-wrap items-center gap-1 pt-1">
        {STAGES.map((stage, i) => (
          <Fragment key={stage.id}>
            {i > 0 && <ArrowRight size={10} className="text-ink-3" aria-hidden />}
            <span className="clone-chip">{stageText()[stage.id]?.label}</span>
          </Fragment>
        ))}
      </span>
      {kit && (
        <p className="text-2xs text-ink-3">
          {kit.license === "MIT" ? t("MIT licence") : t("no licence stated")} ·{" "}
          <button type="button" onClick={() => void openExternal(kit.repo)} className="inline-flex items-center gap-0.5 text-accent hover:underline">
            GitHub <ExternalLink size={10} aria-hidden />
          </button>{" "}
          · {kit.ready ? t("already on this PC (version {v})", { v: kit.commit }) : t("fetched from GitHub the first time (version {v})", { v: kit.commit })}
        </p>
      )}
    </div>
  );
}

// -- starting a clone ----------------------------------------------------------------

function StartPanel({ setup, ready, onStarted }: { setup: CloneSetup | null; ready: boolean; onStarted: (id: string) => void }) {
  const toast = useApp((s) => s.toast);
  const { all, providers, installed, cloud } = useModelChoices();
  const [url, setUrl] = useState("");
  const [model, setModel] = useState(() => remembered(MODEL_KEY));
  const [vision, setVision] = useState(() => remembered(VISION_KEY));
  const [extra, setExtra] = useState("");
  const [place, setPlace] = useState<RunSource>({ kind: "none" });
  const [placeMenu, setPlaceMenu] = useState<Place | null>(null);
  const [busy, setBusy] = useState(false);
  const clean = cleanUrl(url);
  const usable = useMemo(() => all.filter((m) => m.type === "llm"), [all]);
  const chosen = usable.some((m) => m.model_key === model) ? model : (usable[0]?.model_key ?? "");
  const chosenModel = usable.find((m) => m.model_key === chosen);
  const sees = Boolean(chosenModel?.vision);
  const seeing = useMemo(() => usable.filter((m) => m.vision && m.model_key !== chosen), [usable, chosen]);
  // Off unless the person chose one: on a card that holds one model at a time every look swaps models (about 40 s each, 12 times in a run).
  const visionChoice = seeing.some((m) => m.model_key === vision) ? vision : NO_VISION;
  const visionKey = sees || visionChoice === NO_VISION ? "" : visionChoice;

  const start = () => {
    if (!clean || !chosen || busy) return;
    setBusy(true);
    const body = { url: clean, model_key: chosen, vision_key: visionKey, language: getLang(), extra, ...sourcePayload(place) };
    api
      .post<{ study: string; run: string }>("/api/clone", body)
      .then((data) => onStarted(data.study))
      .catch((err: unknown) => toast({ tone: "bad", title: t("The clone could not start"), body: errorMessage(err) }))
      .finally(() => setBusy(false));
  };

  return (
    <div className="mx-auto grid w-full max-w-[880px] gap-4">
      <div className="grid gap-2">
        <label htmlFor="clone-url" className="clone-label">1 · {t("The address of the website")}</label>
        <div className={cx("flex items-center gap-2.5 rounded-xl border bg-panel px-4 py-3 transition-colors duration-150 focus-within:border-accent", url && !clean ? "border-warn" : "border-line-strong")}>
          <Globe size={18} className="shrink-0 text-ink-3" aria-hidden />
          <input
            id="clone-url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && ready && start()}
            placeholder="https://www.example.com"
            spellCheck={false}
            autoFocus
            className="min-w-0 flex-1 bg-transparent font-mono text-[15px] text-ink outline-none placeholder:text-ink-3"
          />
          {url && (
            <IconButton label={t("Clear")} onClick={() => setUrl("")}>
              <X size={14} />
            </IconButton>
          )}
        </div>
        {url && !clean && <span className="text-2xs text-warn">{t("That is not the address of a website.")}</span>}
      </div>

      <KitCard kit={setup?.kit ?? null} />

      <div className="clone-card grid gap-3 p-4 md:grid-cols-2">
        <label className="grid gap-1 text-xs text-ink-2">
          <span className="clone-label">2 · {t("Model")}</span>
          <Select
            value={chosen}
            onChange={(e) => {
              setModel(e.target.value);
              remember(MODEL_KEY, e.target.value);
            }}
          >
            {usable.length === 0 && <option value="">{t("No model yet")}</option>}
            <ModelOptions models={usable} providers={providers} label={(m: InstalledModel) => `${m.display_name}${m.vision ? ` · ${t("sees")}` : ""}`} />
          </Select>
          {installed.data && cloud.data && <CloudModelsLink models={usable} />}
        </label>
        <label className="grid gap-1 text-xs text-ink-2" title={t("A model that sees describes the screenshots to the agent: where things are, how big, which colours. Without one the agent measures the page with scripts only. Each look swaps models: about 40 seconds on a card that holds one at a time.")}>
          <span className="clone-label">{t("Model that sees (optional)")}</span>
          {sees ? (
            <span className="rounded-md border border-line bg-bg px-2.5 py-1.5 text-xs text-ink-2">{t("The model can see: it looks at the screenshots itself.")}</span>
          ) : (
            <Select
              value={visionChoice}
              onChange={(e) => {
                setVision(e.target.value);
                remember(VISION_KEY, e.target.value);
              }}
            >
              {seeing.map((m) => (
                <option key={m.model_key} value={m.model_key}>{m.display_name}</option>
              ))}
              <option value={NO_VISION}>{seeing.length ? t("None: measure with scripts only") : t("None (no model with vision available)")}</option>
            </Select>
          )}
        </label>
        <div className="relative md:col-span-2">
          {placeMenu && (
            <PlacePicker
              place={placeMenu}
              source={place}
              signInNow={placeMenu === "github"}
              label={t("Where the project is written")}
              folderHint={t("An empty folder gets the project; a folder that already is a project of the kit is built into; any other folder gets a subfolder for the clone.")}
              githubHint={t("The repository is copied to a folder of Will It Run and the agent writes in the copy. Nothing is sent to GitHub unless you approve it.")}
              className="absolute right-0 bottom-[calc(100%+8px)] left-0 z-[30]"
              onPlace={setPlaceMenu}
              onChoose={(next, done) => {
                setPlace(next);
                if (done) setPlaceMenu(null);
              }}
              onClose={() => setPlaceMenu(null)}
            />
          )}
          <div className="flex min-w-0 flex-wrap items-center gap-1.5 text-2xs text-ink-3">
            {place.kind === "github" ? <FolderGit2 size={11} aria-hidden /> : <FolderOpen size={11} aria-hidden />}
            <span>{t("The project is written in")}</span>
            <button type="button" onClick={() => setPlaceMenu(placeMenu ? null : place.kind === "github" ? "github" : "folder")} className={cx("min-w-0 truncate rounded px-1 font-mono hover:bg-raised hover:text-ink", sourceProblem(place) ? "text-warn" : "text-ink-2")}>
              {place.kind === "folder" ? place.path || t("choose a folder") : place.kind === "github" ? (place.url.trim() ? repoName(place.url) : t("choose a repository")) : t("its own folder")}
            </button>
            {place.kind !== "none" && (
              <button type="button" aria-label={t("Back to its own folder")} onClick={() => setPlace({ kind: "none" })} className="grid h-4 w-4 place-items-center rounded-full hover:bg-raised hover:text-ink">
                <X size={10} aria-hidden />
              </button>
            )}
          </div>
        </div>
        <label className="grid gap-1 text-xs text-ink-2 md:col-span-2">
          <span className="clone-label">{t("Extra instructions (optional)")}</span>
          <textarea
            value={extra}
            onChange={(e) => setExtra(e.target.value)}
            rows={2}
            placeholder={t("For example: keep the dark theme, skip the blog")}
            className="resize-y rounded-md border border-line bg-bg px-2.5 py-1.5 text-xs text-ink outline-none placeholder:text-ink-3 focus:border-accent"
          />
        </label>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Button variant="primary" icon={<Copy size={14} />} loading={busy} disabled={!clean || !chosen || !ready || sourceProblem(place) !== null} onClick={start}>
          {t("Clone the site")}
        </Button>
        {!ready && <span className="text-2xs text-ink-3">{t("Finish the preparation above first.")}</span>}
      </div>
      <p className="text-2xs leading-relaxed text-ink-3">
        {t("Meant for sites you own (a migration, lost source code) and for learning how a site is built. Check the site's terms first; its text, logos and brand belong to its owner. Not legal advice.")}
      </p>
    </div>
  );
}

// -- one clone -------------------------------------------------------------------------

type TabId = "result" | "screens" | "log";

function Pipeline({ run }: { run: Parameters<typeof nodeStates>[0] }) {
  const states = nodeStates(run);
  return (
    <div className="flex flex-wrap items-stretch gap-1.5">
      {STAGES.map((stage, i) => {
        const s = states[stage.id] ?? { state: "waiting" as NodeState, runs: 0, seconds: null };
        return (
          <Fragment key={stage.id}>
            {i > 0 && <ArrowRight size={14} className="self-center text-ink-3" aria-hidden />}
            <div className={cx("clone-card grid min-w-[130px] flex-1 gap-0.5 px-3 py-2", s.state === "working" && "border-warn", s.state === "done" && "border-[color-mix(in_oklch,var(--ok)_45%,var(--line))]", s.state === "failed" && "border-bad")}>
              <span className="flex items-center gap-1.5 text-[13px] font-semibold text-ink">
                <Dot state={s.state} />
                {stageText()[stage.id]?.label}
                {s.runs > 1 && <span className="font-mono text-2xs text-accent">×{s.runs}</span>}
                {s.seconds != null && s.state === "done" && <span className="ml-auto font-mono text-2xs font-normal text-ink-3">{s.seconds}s</span>}
              </span>
              <span className="text-2xs leading-snug text-ink-3">{stageText()[stage.id]?.what}</span>
            </div>
          </Fragment>
        );
      })}
      <span className="flex items-center gap-1 self-center font-mono text-2xs text-ink-3" title={t("A failed build goes back to the agent that wrote the code, once, with the log.")}>
        <RotateCcw size={11} aria-hidden /> {t("failed check → back to the writer")}
      </span>
    </div>
  );
}

/** The agent's question (which pages to clone, for one): the options as buttons, or an answer in the person's own words. */
function QuestionCard({ cloneId, ask, onAnswered }: { cloneId: string; ask: CloneAsk; onAnswered: () => void }) {
  const toast = useApp((s) => s.toast);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const send = (answer: string) => {
    const value = answer.trim();
    if (!value || busy) return;
    setBusy(true);
    api
      .post(`/api/clone/${cloneId}/answer`, { answer: value })
      .then(() => {
        setText("");
        onAnswered();
      })
      .catch((err: unknown) => toast({ tone: "bad", title: t("The answer was not delivered"), body: errorMessage(err) }))
      .finally(() => setBusy(false));
  };
  return (
    <div role="group" aria-label={t("The agent asks")} className="clone-card grid gap-2.5 border-accent p-4">
      <span className="clone-label flex items-center gap-1.5 text-accent">
        <MessageCircleQuestion size={13} aria-hidden /> {t("The agent is waiting for your answer")}
      </span>
      <p className="selectable text-[14px] leading-relaxed whitespace-pre-wrap text-ink">{ask.question}</p>
      {ask.options.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {ask.options.map((option) => (
            <Button key={option} size="sm" variant="primary" disabled={busy} onClick={() => send(option)}>
              {option}
            </Button>
          ))}
        </div>
      )}
      <form
        className="flex items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          send(text);
        }}
      >
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={ask.options.length > 0 ? t("Or write your own answer") : t("Write your answer")}
          aria-label={t("Your answer")}
          className="min-w-0 flex-1 rounded-md border border-line bg-bg px-2.5 py-1.5 text-xs text-ink outline-none placeholder:text-ink-3 focus:border-accent"
        />
        <Button type="submit" size="sm" icon={<Send size={12} />} disabled={!text.trim() || busy}>{t("Send")}</Button>
      </form>
    </div>
  );
}

function CloneView({ cloneId, onBack, onDeleted }: { cloneId: string; onBack: () => void; onDeleted: () => void }) {
  const toast = useApp((s) => s.toast);
  const navigate = useApp((s) => s.navigate);
  const { all } = useModelChoices();
  const [study, setStudy] = useState<CloneDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<TabId | null>(null);
  const [lightbox, setLightbox] = useState<string | null>(null);
  const [busy, setBusy] = useState<"again" | "preview" | null>(null);
  const [again, setAgain] = useState(false);
  const [extra, setExtra] = useState("");
  const file = useFileUrl(cloneId);

  const refresh = () => {
    api
      .get<CloneDetail>(`/api/clone/${cloneId}`)
      .then((data) => {
        setStudy(data);
        setError(null);
      })
      .catch((err: unknown) => setError(errorMessage(err)));
  };
  useEffect(refresh, [cloneId]);

  const run = study?.runs[study.runs.length - 1] ?? null;
  const working = run && !TERMINAL.has(run.status) ? run : null;
  useInterval(refresh, working ? 1500 : null);
  const stream = useAnswerStream(working?.id ?? null, (type) => {
    if (type.startsWith("node.") || type.startsWith("clone.") || type.startsWith("run.") || type === "tool.call") refresh();
  });
  const states = nodeStates(run);
  const workingNode = Object.entries(states).find(([, s]) => s.state === "working")?.[0] ?? null;
  const steps = prepSteps(run);
  const done = activity(run);
  const asked = questions(run);
  const build = buildOf(run);
  const final = answerOf(run, "cloner");
  const approving = waitingForApproval(run);

  const tabs = useMemo(() => [{ value: "result" as TabId, label: t("Result") }, { value: "screens" as TabId, label: t("Screenshots") }, { value: "log" as TabId, label: t("Build log") }], []);
  const shownTab: TabId = tab ?? "result";

  const stop = () => {
    if (working) void api.post(`/api/runs/${working.id}/cancel`).then(refresh);
  };
  const remove = () => {
    api
      .del(`/api/clone/${cloneId}`)
      .then(onDeleted)
      .catch((err: unknown) => toast({ tone: "bad", title: t("Could not delete"), body: errorMessage(err) }));
  };
  const goAgain = () => {
    if (!study) return;
    const model = all.find((m) => m.model_key === study.model_key)?.model_key ?? all.find((m) => m.type === "llm")?.model_key;
    if (!model) return;
    setBusy("again");
    api
      .post(`/api/clone/${cloneId}/runs`, { model_key: model, vision_key: study.vision_key ?? "", language: getLang(), extra })
      .then(() => {
        setAgain(false);
        setExtra("");
        setTab(null);
        refresh();
      })
      .catch((err: unknown) => toast({ tone: "bad", title: t("The clone could not start"), body: errorMessage(err) }))
      .finally(() => setBusy(null));
  };
  const decide = (a: PendingApproval, approved: boolean, comment: string) => {
    if (!run) return;
    api
      .post(`/api/runs/${run.id}/approvals/${a.id}`, { approved, comment })
      .then(refresh)
      .catch((err: unknown) => toast({ tone: "bad", title: t("Decision not recorded"), body: errorMessage(err) }));
  };
  const openClone = () => {
    setBusy("preview");
    api
      .post<{ url: string }>(`/api/clone/${cloneId}/preview`)
      .then((data) => openExternal(data.url))
      .catch((err: unknown) => toast({ tone: "bad", title: t("The clone could not be opened"), body: errorMessage(err) }))
      .finally(() => setBusy(null));
  };
  const openFolder = () => {
    if (study?.workspace) void api.post("/api/folders/open", { path: study.workspace }).catch((err: unknown) => toast({ tone: "bad", title: t("The folder could not be opened"), body: errorMessage(err) }));
  };

  if (error) return <Note tone="bad">{error}</Note>;
  if (!study) return <ProgressBar indeterminate />;
  const shotName = (p: string) => p.split("/").slice(-1)[0] ?? p;

  return (
    <div className="grid gap-4">
      {lightbox && <Lightbox src={lightbox} onClose={() => setLightbox(null)} />}

      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <IconButton label={t("All clones")} onClick={onBack} className="mt-1">
            <ArrowLeft size={16} />
          </IconButton>
          <div className="min-w-0">
            <h2 className="truncate text-[22px] leading-tight font-bold tracking-tight">{study.title}</h2>
            {/* Plain text: the app only opens a few known addresses, and the site being cloned is not one of them. */}
            <span className="selectable block truncate font-mono text-xs text-accent">{study.url}</span>
            <p className="mt-0.5 font-mono text-2xs text-ink-3">
              {study.model_key ? modelName(study.model_key) : ""}
              {study.vision_key ? ` · ${t("sees")} ${modelName(study.vision_key)}` : study.sees ? ` · ${t("sees")}` : ""}
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {working ? (
            <Button size="sm" variant="danger" icon={<Square size={11} />} onClick={stop} title={t("The files already written stay in the project.")}>{t("Stop")}</Button>
          ) : (
            <>
              {study.project && <Button size="sm" variant="primary" icon={<Globe size={13} />} loading={busy === "preview"} onClick={openClone}>{t("Open the clone")}</Button>}
              {run && <Button size="sm" icon={<RotateCcw size={13} />} onClick={() => setAgain((v) => !v)}>{t("Improve it")}</Button>}
            </>
          )}
          {run && <Button size="sm" icon={<Network size={13} />} onClick={() => navigate("sessions", { run: run.id })}>{t("Live graph")}</Button>}
          <Button size="sm" icon={<FolderOpen size={13} />} onClick={openFolder}>{t("Folder")}</Button>
          {!working && (
            <IconButton label={t("Delete")} onClick={remove}>
              <Trash2 size={14} />
            </IconButton>
          )}
        </div>
      </div>

      {again && !working && (
        <div className="clone-card grid gap-2 p-3">
          <span className="clone-label">{t("What should change?")}</span>
          <span className="text-xs text-ink-2">{t("Leave it empty to just continue where it stopped: the agent resumes from the kit's manifest and builds what is missing.")}</span>
          <textarea
            value={extra}
            onChange={(e) => setExtra(e.target.value)}
            rows={2}
            placeholder={t("For example: the hero is too tall on a phone, the footer links are missing")}
            className="resize-y rounded-md border border-line bg-bg px-2.5 py-1.5 text-xs text-ink outline-none placeholder:text-ink-3 focus:border-accent"
          />
          <div className="flex gap-2">
            <Button size="sm" variant="primary" loading={busy === "again"} onClick={goAgain}>{t("Run it again")}</Button>
            <Button size="sm" onClick={() => setAgain(false)}>{t("Cancel")}</Button>
          </div>
        </div>
      )}

      {working && study.ask && <QuestionCard key={study.ask.question} cloneId={cloneId} ask={study.ask} onAnswered={refresh} />}
      {approving && run?.pending_approvals.map((a) => <ApprovalCard key={a.id} approval={a} onDecide={(ok, comment) => decide(a, ok, comment)} />)}

      <Pipeline run={run} />

      <div className="grid gap-4 xl:grid-cols-[300px_minmax(0,1fr)]">
        <div className="grid content-start gap-4">
          <section className="clone-card grid content-start gap-2 p-4" aria-label={t("Preparation")}>
            <div className="clone-label">{t("Preparation · what it did")}</div>
            {steps.length === 0 ? (
              <p className="text-xs text-ink-3">{t("The preparation starts in a moment.")}</p>
            ) : (
              <ul className="grid gap-1.5">
                {steps.map((s) => (
                  <li key={s.step} className="grid grid-cols-[16px_1fr] gap-x-2 text-xs">
                    <span className="pt-[1px]"><Dot state={s.state} /></span>
                    <span className={cx(s.state === "running" ? "text-ink" : "text-ink-2")}>{prepText()[s.step] ?? s.step}</span>
                    {s.detail && <span className="col-start-2 truncate font-mono text-2xs text-ink-3" title={s.detail}>{s.detail}</span>}
                  </li>
                ))}
              </ul>
            )}
          </section>
          {asked.length > 0 && (
            <section className="clone-card grid content-start gap-2 p-4" aria-label={t("Your choices")}>
              <div className="clone-label">{t("Your choices")}</div>
              <ul className="grid gap-2">
                {asked.map((q, i) => (
                  <li key={i} className="grid gap-0.5 text-xs">
                    <span className="text-ink-3">{q.question}</span>
                    <span className={q.answer ? "font-medium text-ink" : "text-warn"}>{q.answer ?? t("waiting for you")}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}
          <section className="clone-card grid content-start gap-1.5 p-4" aria-label={t("Activity")}>
            <div className="clone-label">{t("Activity")}</div>
            <div className="grid grid-cols-4 gap-2 text-center">
              {[
                { n: done.browser, label: t("browser") },
                { n: done.commands, label: t("commands") },
                { n: done.reads, label: t("files read") },
                { n: done.changes, label: t("files written") },
              ].map((x) => (
                <div key={x.label} className="grid">
                  <span className="font-mono text-lg font-semibold text-ink">{x.n}</span>
                  <span className="text-2xs text-ink-3">{x.label}</span>
                </div>
              ))}
            </div>
            {done.last && (
              <span className="truncate font-mono text-2xs text-ink-3" title={done.last.path ?? undefined}>
                <Wrench size={10} className="mr-1 inline" aria-hidden />{done.last.tool}{done.last.path ? ` ${done.last.path}` : ""}
              </span>
            )}
          </section>
        </div>

        <section className="grid min-w-0 content-start gap-3">
          {working && stream && workingNode && !STAGES.find((s) => s.id === workingNode)?.check && (
            <div className="grid gap-1.5">
              <div className="clone-label text-warn">{stageText()[workingNode]?.label} · {t("working")}</div>
              <LiveBlock stream={stream} />
            </div>
          )}
          {run?.status === "failed" && run.error && <Note tone="bad">{run.error}</Note>}
          {run?.status === "blocked" && run.error && <Note tone="warn">{run.error}</Note>}

          <div className="clone-card grid min-w-0 gap-3 p-4">
            <Tabs value={shownTab} tabs={tabs} onChange={setTab} />
            {shownTab === "result" && (
              <div className="grid gap-3">
                {build && (
                  <p className={cx("text-xs", build.passed ? "text-ok" : "text-bad")}>
                    {build.passed ? t("The project builds: lint, typecheck and production build passed.") : build.summary}
                  </p>
                )}
                {final ? <Markdown text={final} /> : <p className="text-xs text-ink-3">{working ? t("The result appears here when the agent has finished.") : t("No result yet.")}</p>}
              </div>
            )}
            {shownTab === "screens" && (
              <div className="grid gap-3">
                {study.shots.length > 0 && (
                  <div className="flex flex-wrap gap-2">
                    {study.shots.map((p) => {
                      const src = file(p);
                      return src ? (
                        <button key={p} type="button" onClick={() => setLightbox(src)} className="grid shrink-0 gap-0.5 text-left" title={p}>
                          <img src={src} alt="" loading="lazy" className="block h-[180px] w-auto rounded-md border border-line object-cover object-top hover:border-accent" />
                          <span className="max-w-[240px] truncate font-mono text-2xs text-ink-3">{shotName(p)}</span>
                        </button>
                      ) : null;
                    })}
                  </div>
                )}
                {study.shots.length === 0 && <p className="text-xs text-ink-3">{t("The screenshots appear here as the agent takes them.")}</p>}
              </div>
            )}
            {shownTab === "log" &&
              (build?.log.length ? (
                <pre className="selectable max-h-72 overflow-auto rounded-md bg-bg p-2 font-mono text-[10.5px] whitespace-pre-wrap text-ink-2">{build.log.join("\n")}</pre>
              ) : (
                <p className="text-xs text-ink-3">{t("No build yet.")}</p>
              ))}
          </div>
        </section>
      </div>
    </div>
  );
}

// -- the page ----------------------------------------------------------------------------

/** Clone a site: an address in, a project on this PC out. */
export function CloneSite() {
  const routeStudy = useApp((s) => s.params.study) ?? null;
  const navigate = useApp((s) => s.navigate);
  const toast = useApp((s) => s.toast);
  const list = useLoad(() => api.get<{ clones: CloneInfo[]; setup: CloneSetup }>("/api/clone"));
  const [busy, setBusy] = useState(false);
  useInterval(list.reload, 5000);
  useBus("clone.install", (event) => {
    const job = event.data as unknown as InstallJob;
    if (list.data) list.setData({ ...list.data, setup: { ...list.data.setup, job } });
    if (job.state !== "running") list.reload();
  });

  const setup = list.data?.setup ?? null;
  const clones = list.data?.clones ?? [];
  const install = () => {
    setBusy(true);
    api
      .post("/api/clone/install")
      .then(list.reload)
      .catch((err: unknown) => toast({ tone: "bad", title: t("The browser engine could not be installed"), body: errorMessage(err) }))
      .finally(() => setBusy(false));
  };
  const cancel = () => void api.post("/api/clone/install/cancel").then(list.reload);

  return (
    <div className="clone-console relative flex min-h-0 flex-1 flex-col overflow-y-auto">
      <header className="flex flex-wrap items-start justify-between gap-4 px-6 pt-5 pb-4">
        <div className="flex min-w-0 items-start gap-4">
          <div className="grid h-[54px] w-[54px] shrink-0 place-items-center rounded-2xl border border-line-strong bg-panel">
            <Copy size={26} className="text-accent" aria-hidden />
          </div>
          <div className="min-w-0">
            <h1 className="text-[30px] leading-none font-bold tracking-[-0.03em]">{t("Clone a site")}</h1>
            <p className="mt-2 text-xs text-ink-2">{t("Give an address: the ai-site-cloner kit and the model you choose (on this PC or in the cloud) rebuild the site as a Next.js project in a folder or a GitHub repository. It asks you which pages to clone.")}</p>
          </div>
        </div>
      </header>
      <div className="mx-6 border-t border-line" />

      <div className="grid gap-5 px-6 pt-5 pb-8">
        {setup && !setup.ready && <SetupCard setup={setup} onInstall={install} onCancel={cancel} busy={busy} />}

        {routeStudy ? (
          <CloneView
            key={routeStudy}
            cloneId={routeStudy}
            onBack={() => navigate("clone")}
            onDeleted={() => {
              list.reload();
              navigate("clone");
            }}
          />
        ) : (
          <>
            <StartPanel
              setup={setup}
              ready={Boolean(setup?.ready)}
              onStarted={(id) => {
                list.reload();
                navigate("clone", { study: id });
              }}
            />
            {clones.length > 0 && (
              <div className="mx-auto grid w-full max-w-[880px] gap-2">
                <div className="clone-label">{t("Sites cloned")}</div>
                <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
                  {clones.map((c) => (
                    <CloneCard key={c.id} clone={c} onOpen={() => navigate("clone", { study: c.id })} />
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function CloneCard({ clone, onOpen }: { clone: CloneInfo; onOpen: () => void }) {
  const file = useFileUrl(clone.id);
  const thumb = file(clone.thumbnail);
  return (
    <button type="button" onClick={onOpen} className="clone-card group grid overflow-hidden text-left transition-colors duration-150 hover:border-accent">
      <span className="block aspect-[16/9] overflow-hidden border-b border-line bg-bg">
        {thumb ? <img src={thumb} alt="" loading="lazy" className="h-full w-full object-cover object-top" /> : <span className="grid h-full place-items-center text-ink-3"><Globe size={22} aria-hidden /></span>}
      </span>
      <span className="grid gap-0.5 p-2.5">
        <span className="truncate text-[13px] font-semibold text-ink">{clone.title}</span>
        <span className="flex items-center gap-1.5 font-mono text-2xs">
          <span className={cx("h-1.5 w-1.5 rounded-full", clone.active ? "pulse-dot bg-warn" : clone.status === "completed" ? "bg-ok" : clone.status === "none" ? "bg-line-strong" : "bg-bad")} />
          <span className={clone.active ? "text-warn" : clone.status === "completed" ? "text-ok" : "text-ink-3"}>
            {clone.active ? t("working") : clone.status === "completed" ? t("done") : clone.status === "cancelled" ? t("stopped") : clone.status === "failed" ? t("failed") : clone.status === "blocked" ? t("blocked") : clone.status.replace(/_/g, " ")}
          </span>
        </span>
      </span>
    </button>
  );
}
