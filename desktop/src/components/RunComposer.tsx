import { ArrowUp, Brain, Clock, FolderGit2, FolderOpen, Loader2, MessageSquare, Plus, ShieldCheck, ShieldOff, Square, Users, X } from "lucide-react";
import type { ReactNode } from "react";
import { useEffect, useRef, useState } from "react";

import { api, query } from "../lib/api";
import { t } from "../lib/i18n";
import { loadApproval, loadSource, looksLikeQuestion, repoName, saveApproval, saveSource, sourceProblem, type ApprovalMode, type FollowUpMode, type RunSource } from "../lib/runSource";
import { useApp } from "../lib/store";
import type { BrainProject } from "../lib/types";
import { PlacePicker, type Place } from "./PlacePicker";
import { cx } from "./ui";

const MAX_HEIGHT = 200;

interface Props {
  /** The workflow answers only from evidence: the folder is read, never written. */
  takesEvidence?: boolean;
  /** Shown instead of the prompt when the run cannot start yet. */
  blocked?: ReactNode;
  busy?: boolean;
  placeholder?: string;
  /** Start from this source instead of the remembered one (a follow-up continues where the run worked). */
  initialSource?: RunSource;
  /** Start in this mode instead of the remembered one (a follow-up keeps the mode of its run). */
  initialApproval?: ApprovalMode;
  autoFocus?: boolean;
  /** A run of this session is working: what is written waits for it to end, and the button stops it. */
  running?: boolean;
  /** The request that already waits for the run to end. */
  queued?: string | null;
  onUnqueue?: () => void;
  onStop?: () => void;
  /** The field stays after a send (a chat): empty it, as a sent message leaves the box. */
  clearOnSend?: boolean;
  /**
   * Under a session that has ended: a message can be a question one model answers ("ask") or new
   * work for the whole team ("team"). It is chosen from the words, shown, and can be switched.
   */
  askable?: boolean;
  onStart: (task: string, source: RunSource, approval: ApprovalMode, mode: FollowUpMode) => void;
  onClose?: () => void;
}

/**
 * The prompt of a run: a rounded field docked at the bottom, as in a chat. "+" connects a
 * folder or a GitHub repository, which then shows as a label above the field. Enter starts,
 * Shift+Enter makes a new line. It stays while a run works: then the button stops the run,
 * and a request written in the meantime waits for the run to end. Below the field is how
 * the run treats permissions: it asks, or it ignores them. Shift+Tab switches.
 */
export function RunComposer({
  takesEvidence = false, blocked, busy = false, placeholder, initialSource, initialApproval, autoFocus = true,
  running = false, queued = null, onUnqueue, onStop, clearOnSend = false, askable = false, onStart, onClose,
}: Props) {
  const [task, setTask] = useState("");
  const [approval, setApproval] = useState<ApprovalMode>(() => initialApproval ?? loadApproval());
  const [source, setSource] = useState<RunSource>(() => initialSource ?? loadSource());
  const [menu, setMenu] = useState<Place | null>(null);
  // True once the user has opened GitHub here, as opposed to a choice restored from last time.
  const [askedGithub, setAskedGithub] = useState(false);
  const area = useRef<HTMLTextAreaElement>(null);
  const root = useRef<HTMLDivElement>(null);

  // Grow with the text up to a limit, then scroll.
  useEffect(() => {
    const element = area.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, MAX_HEIGHT)}px`;
  }, [task]);

  // The menu closes on a click outside it and on Escape.
  useEffect(() => {
    if (!menu) return;
    const outside = (event: MouseEvent) => {
      if (root.current && !root.current.contains(event.target as globalThis.Node)) setMenu(null);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenu(null);
    };
    document.addEventListener("mousedown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [menu]);

  const choose = (next: RunSource) => {
    setSource(next);
    saveSource(next);
  };
  const open = (place: Place) => {
    if (place === "github") setAskedGithub(true);
    setMenu(place);
  };

  // Picked by hand for this message, or null: read from what is written.
  const [pickedMode, setPickedMode] = useState<FollowUpMode | null>(null);
  const mode: FollowUpMode = !askable ? "team" : (pickedMode ?? (looksLikeQuestion(task) ? "ask" : "team"));

  const problem = sourceProblem(source);
  const ready = task.trim().length > 0 && problem === null && !busy;

  // The permanent memory of the place the team is about to work in, when that place is a project.
  const navigate = useApp((s) => s.navigate);
  const [memory, setMemory] = useState<{ enabled: boolean; project: BrainProject | null } | null>(null);
  const place = source.kind === "folder" ? source.path.trim() : source.kind === "github" ? source.url.trim() : "";
  const placeKind = source.kind;
  useEffect(() => {
    if (takesEvidence || problem !== null || placeKind === "none" || !place) {
      setMemory(null);
      return;
    }
    let stale = false;
    const timer = window.setTimeout(() => {
      api
        .get<{ enabled: boolean; project: BrainProject | null }>(`/api/brain/lookup${query(placeKind === "folder" ? { workspace: place } : { repo_url: place })}`)
        .then((data) => {
          if (!stale) setMemory(data);
        })
        .catch(() => {
          if (!stale) setMemory(null);
        });
    }, 250);
    return () => {
      stale = true;
      window.clearTimeout(timer);
    };
  }, [placeKind, place, takesEvidence, problem, running]);
  const submit = () => {
    if (!ready) return;
    saveSource(source);
    onStart(task.trim(), source, approval, mode);
    setPickedMode(null);
    // While a run works this field stays on screen: what was sent now shows as waiting, above it.
    // A question is answered under the session, and the field stays for the next one.
    if (running || clearOnSend || mode === "ask") setTask("");
  };
  const switchApproval = () => {
    const next: ApprovalMode = approval === "ask" ? "bypass" : "ask";
    setApproval(next);
    saveApproval(next);
  };
  // With nothing written, the button of a working run is Stop.
  const stopping = running && task.trim().length === 0;

  if (blocked) return <div className="mx-auto w-full max-w-[760px]">{blocked}</div>;

  return (
    <div ref={root} className="mx-auto w-full max-w-[760px]">
      {source.kind !== "none" && (
        <div className="mb-2 ml-[18px] flex flex-wrap gap-1.5">
          <span className={cx("inline-flex h-[26px] max-w-full items-center gap-1.5 rounded-full bg-raised pr-1 pl-2.5 text-xs", problem ? "text-warn" : "text-ink-2")}>
            {source.kind === "github" ? <FolderGit2 size={13} className="shrink-0" aria-hidden /> : <FolderOpen size={13} className="shrink-0" aria-hidden />}
            <button type="button" onClick={() => open(source.kind === "github" ? "github" : "folder")} className="selectable min-w-0 truncate font-mono hover:text-ink" title={t("Change")}>
              {source.kind === "github"
                ? source.url.trim()
                  ? `${repoName(source.url)}${source.branch ? ` · ${source.branch}` : ""}`
                  : t("choose a repository")
                : source.path || t("choose a folder")}
            </button>
            <button type="button" aria-label={t("Disconnect")} onClick={() => choose({ kind: "none" })} className="grid h-5 w-5 shrink-0 place-items-center rounded-full text-ink-3 hover:bg-surface hover:text-ink">
              <X size={12} aria-hidden />
            </button>
          </span>
          {memory?.enabled &&
            (memory.project ? (
              <button
                type="button"
                onClick={() => navigate("brain", { project: memory.project?.id ?? "" })}
                title={t("This project has a permanent memory. Each agent is given the entries its task needs, and what this run finds out is added. Click to open it.")}
                className="inline-flex h-[26px] items-center gap-1.5 rounded-full bg-raised px-2.5 text-xs text-ink-2 hover:text-ink"
              >
                <Brain size={13} className="shrink-0 text-accent" aria-hidden />
                {memory.project.memories === 1 ? t("1 memory") : t("{n} memories", { n: memory.project.memories })}
              </button>
            ) : (
              <span
                title={t("This project has no memory yet. This run starts it: what it changes and finds out is written down for the next sessions.")}
                className="inline-flex h-[26px] items-center gap-1.5 rounded-full bg-raised px-2.5 text-xs text-ink-3"
              >
                <Brain size={13} className="shrink-0" aria-hidden />
                {t("Memory starts here")}
              </span>
            ))}
        </div>
      )}

      {queued && (
        <div className="mb-2 ml-[18px] flex max-w-full items-center gap-1.5 text-xs text-ink-2">
          <Clock size={13} className="shrink-0 text-ink-3" aria-hidden />
          <span className="shrink-0 text-ink-3">{t("Waiting for this run to end:")}</span>
          <span className="selectable min-w-0 truncate" title={queued}>
            {queued}
          </span>
          {onUnqueue && (
            <button type="button" aria-label={t("Remove the waiting request")} title={t("Remove the waiting request")} onClick={onUnqueue} className="grid h-5 w-5 shrink-0 place-items-center rounded-full text-ink-3 hover:bg-surface hover:text-ink">
              <X size={12} aria-hidden />
            </button>
          )}
        </div>
      )}

      <div className="relative">
        {menu && (
          <PlacePicker
            place={menu}
            source={source}
            signInNow={askedGithub}
            label={t("Where the team works")}
            protectable={!takesEvidence}
            folderHint={
              takesEvidence
                ? t("Text files in this folder are added to the evidence. They are only read, never changed. PDF and Word files are not read.")
                : t("The agents read and write in this folder. Deleting a file always asks you first; code they run (Python, tests) runs without asking, as in every run.")
            }
            githubHint={
              takesEvidence
                ? t("The repository is copied to a folder of Will It Run and its text files are added to the evidence.")
                : t("The repository is copied to a folder of Will It Run and the team works on the copy. Nothing is sent to GitHub unless you approve it.")
            }
            className="absolute bottom-[calc(100%+10px)] left-0 z-[30] w-[min(460px,100%)]"
            onPlace={open}
            onChoose={(next, done) => {
              choose(next);
              if (done) {
                setMenu(null);
                area.current?.focus();
              }
            }}
            onClose={() => setMenu(null)}
          />
        )}

        <form
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
          className="grid grid-cols-[auto_1fr_auto] items-end gap-2 rounded-[25px] border border-line-strong bg-surface p-[7px] transition-colors duration-150 focus-within:border-accent"
          style={{ boxShadow: "0 6px 22px oklch(0 0 0 / 0.28)" }}
        >
          <button
            type="button"
            aria-label={t("Connect a folder or GitHub")}
            title={t("Connect a folder or GitHub")}
            aria-expanded={menu !== null}
            onClick={() => (menu ? setMenu(null) : open(source.kind === "github" ? "github" : "folder"))}
            className={cx(
              "grid h-[34px] w-[34px] place-items-center rounded-full border transition-colors duration-150",
              menu ? "border-line-strong bg-raised text-ink" : "border-line text-ink-2 hover:border-line-strong hover:text-ink",
            )}
          >
            <Plus size={16} className={cx("transition-transform duration-150", menu && "rotate-45")} aria-hidden />
          </button>
          <textarea
            ref={area}
            value={task}
            rows={1}
            autoFocus={autoFocus}
            onChange={(event) => setTask(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                submit();
              } else if (event.key === "Tab" && event.shiftKey) {
                event.preventDefault();
                switchApproval();
              } else if (event.key === "Escape" && onClose && !task && !menu) {
                onClose();
              }
            }}
            placeholder={
              running
                ? t("Write the next request")
                : (placeholder ?? (takesEvidence ? t("Ask the question, then paste the text the answer must come from") : t("Tell the team what to do")))
            }
            aria-label={t("Request for the team")}
            className="selectable block max-h-[200px] min-h-[34px] w-full resize-none bg-transparent py-[7px] text-sm leading-[20px] text-ink outline-none placeholder:text-ink-3"
          />
          {stopping ? (
            <button
              type="button"
              aria-label={t("Stop the run")}
              title={t("Stop the run")}
              onClick={onStop}
              className="grid h-[34px] w-[34px] place-items-center rounded-full border border-bad text-bad transition-colors duration-150 hover:bg-bad hover:text-bg"
            >
              <Square size={12} fill="currentColor" aria-hidden />
            </button>
          ) : (
            <button
              type="submit"
              aria-label={running ? t("Send when this run ends") : t("Start")}
              title={running ? t("Send when this run ends") : t("Start")}
              disabled={!ready}
              className="grid h-[34px] w-[34px] place-items-center rounded-full bg-accent text-accent-ink transition-[filter,opacity] duration-150 hover:brightness-110 disabled:opacity-40 disabled:hover:brightness-100"
            >
              {busy ? <Loader2 size={15} className="animate-spin" aria-hidden /> : <ArrowUp size={16} strokeWidth={2.4} aria-hidden />}
            </button>
          )}
        </form>
      </div>

      <div className="mt-2 flex flex-wrap items-center justify-between gap-x-3 gap-y-1 pr-1 pl-[10px] text-xs text-ink-3">
        <span className="flex flex-wrap items-center gap-1">
        {askable && (
          <button
            type="button"
            role="switch"
            aria-checked={mode === "team"}
            onClick={() => setPickedMode(mode === "ask" ? "team" : "ask")}
            title={
              mode === "ask"
                ? t("One model answers, in the folder the team worked in and knowing what it delivered. The team does not run again. Click to give this to the whole team instead.")
                : t("The whole team works on this, step by step, as for the first request. Click to have one model answer instead.")
            }
            className={cx(
              "inline-flex items-center gap-1.5 rounded-full py-0.5 pr-2.5 pl-2 transition-colors duration-150 hover:bg-raised",
              mode === "ask" ? "text-accent" : "text-ink-2 hover:text-ink",
            )}
          >
            {mode === "ask" ? <MessageSquare size={13} aria-hidden /> : <Users size={13} aria-hidden />}
            {mode === "ask" ? t("One model answers") : t("The whole team works")}
            {pickedMode === null && task.trim() && <span className="text-ink-3">· {t("chosen from your words")}</span>}
          </button>
        )}
        <button
          type="button"
          role="switch"
          aria-checked={approval === "bypass"}
          onClick={switchApproval}
          title={
            approval === "bypass"
              ? t("Nothing waits for you: plans are approved as they come, files are overwritten or deleted and commands run without asking. Click, or press Shift+Tab in the field, to be asked again.")
              : t("Plans wait for your approval, and so does anything risky: deleting a file, overwriting one of yours, a command that changes the system. Click, or press Shift+Tab in the field, to ignore permissions.")
          }
          className={cx(
            "inline-flex items-center gap-1.5 rounded-full py-0.5 pr-2.5 pl-2 transition-colors duration-150 hover:bg-raised",
            approval === "bypass" ? "text-warn" : "text-ink-2 hover:text-ink",
          )}
        >
          {approval === "bypass" ? <ShieldOff size={13} aria-hidden /> : <ShieldCheck size={13} aria-hidden />}
          {approval === "bypass" ? t("Ignore permissions") : t("Ask for approval")}
        </button>
        </span>
        <span>
          {busy && source.kind === "github"
            ? t("Copying the repository...")
            : problem === "folder"
              ? t("Choose the folder, or remove the label above.")
              : problem === "github"
                ? t("Choose the repository, or remove the label above.")
                : running
                  ? t("Enter to queue · the square stops the run")
                  : mode === "ask"
                    ? t("Enter to ask · Shift+Enter for a new line")
                    : t("Enter to start · Shift+Enter for a new line")}
        </span>
        {onClose && (
          <button type="button" onClick={onClose} className="text-ink-3 underline-offset-2 hover:text-ink hover:underline">
            {t("Close")}
          </button>
        )}
      </div>
    </div>
  );
}
