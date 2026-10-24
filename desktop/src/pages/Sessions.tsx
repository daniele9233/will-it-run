import { Brain, Check, ChevronRight, CircleAlert, ClipboardCopy, FolderGit2, FolderOpen, Loader2, LocateFixed, PanelLeft, PanelLeftClose, PanelLeftOpen, PanelRight, Plus, RefreshCw, ShieldAlert, ShieldCheck, ShieldOff, ShieldX, Square, Terminal, Trash2, UserCheck } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { LiveGraph } from "../components/LiveGraph";
import { RunComposer } from "../components/RunComposer";
import { Badge, Button, EmptyState, Field, IconButton, Meter, Note, Select, SkeletonRows, StatusDot, Tabs, TextInput, cx } from "../components/ui";
import { api } from "../lib/api";
import { duration, gbFromMb, policyLabel, retryText, timeAgo, type Tone } from "../lib/format";
import { errorMessage, useBus, useInterval, useLoad } from "../lib/hooks";
import { deriveGraph } from "../lib/liveRun";
import { SESSION_PANEL, clampPanelWidth, loadPanelPrefs, repoName, savePanelPrefs, sourceFromEvent, sourcePayload, type ApprovalMode, type FollowUpMode, type PanelPrefs, type RunSource } from "../lib/runSource";
import { useApp } from "../lib/store";
import type { InstalledModel, PendingApproval, RunEvent, RunRecord, WorkflowDef, WorkflowInfo } from "../lib/types";
import { t } from "../lib/i18n";
import { SessionQuestions } from "./Chat";

const TERMINAL = new Set(["completed", "failed", "rejected", "cancelled", "retry_limit", "no_route", "blocked"]);

const STATUS: Record<string, { label: string; tone: Tone }> = {
  running: { label: t("Running"), tone: "accent" },
  waiting_approval: { label: t("Needs approval"), tone: "warn" },
  completed: { label: t("Completed"), tone: "ok" },
  failed: { label: t("Failed"), tone: "bad" },
  rejected: { label: t("Rejected"), tone: "bad" },
  cancelled: { label: t("Cancelled"), tone: "off" },
  retry_limit: { label: t("Retry limit reached"), tone: "limited" },
  no_route: { label: t("Stopped: no route"), tone: "limited" },
  blocked: { label: t("Stopped by a check"), tone: "limited" },
};

const TOOL_STATUS: Record<string, string> = {
  success: t("SUCCESS"),
  error: t("ERROR"),
  denied: t("DENIED"),
  pending: t("PENDING"),
  running: t("RUNNING"),
};

function toolStatusLabel(status: string): string {
  return TOOL_STATUS[status] ?? status.toUpperCase();
}

function statusOf(status: string) {
  return STATUS[status] ?? { label: status, tone: "off" as Tone };
}

// -- activity feed ------------------------------------------------------------

interface Step {
  key: string;
  node: string;
  role: string;
  model: string;
  state: "working" | "done" | "failed";
  /** A check is code: no model, no tokens. */
  check: boolean;
  /** SHA-256 of this step's output, and of each earlier output it was shown. */
  sha: string | null;
  read: Record<string, string>;
  events: RunEvent[];
  output: Record<string, unknown> | null;
  /** What the model thought before answering, when it says. */
  thought: string | null;
  seconds: number | null;
  tps: number | null;
  tokens: number | null;
  reasoning: number | null;
}

type FeedItem =
  | { kind: "step"; step: Step }
  | { kind: "switch"; event: RunEvent }
  | { kind: "gate"; event: RunEvent; decided: RunEvent | null }
  /** What the run left in the memory of its project. */
  | { kind: "memory"; event: RunEvent };

function buildFeed(events: RunEvent[]): FeedItem[] {
  const items: FeedItem[] = [];
  let current: Step | null = null;
  let count = 0;
  for (const event of events) {
    const p = event.payload;
    switch (event.type) {
      case "node.started":
        current = { key: `${String(p.node)}-${count++}`, node: String(p.node), role: String(p.role ?? p.node), model: String(p.model ?? ""), state: "working", check: p.kind === "check", sha: null, read: {}, events: [], output: null, thought: null, seconds: null, tps: null, tokens: null, reasoning: null };
        items.push({ kind: "step", step: current });
        break;
      case "node.completed":
        if (current && current.node === p.node) {
          current.state = "done";
          current.output = (p.output as Record<string, unknown>) ?? null;
          current.seconds = typeof p.seconds === "number" ? p.seconds : null;
          current.tps = typeof p.tokens_per_second === "number" ? p.tokens_per_second : null;
          current.tokens = typeof p.output_tokens === "number" ? p.output_tokens : null;
          current.reasoning = typeof p.reasoning_tokens === "number" ? p.reasoning_tokens : null;
          current.sha = typeof p.sha256 === "string" ? p.sha256 : null;
          current.thought = typeof p.reasoning === "string" && p.reasoning.trim() ? p.reasoning : null;
          current.read = (p.read as Record<string, string> | undefined) ?? {};
        }
        break;
      case "node.failed":
        if (current && current.node === p.node) {
          current.state = "failed";
          current.events.push(event);
        }
        break;
      case "tool.call":
      case "node.retry":
      case "node.trimmed":
      case "node.stalled":
      case "memory.recalled":
        current?.events.push(event);
        break;
      case "memory.saved":
        items.push({ kind: "memory", event });
        break;
      case "model.switch":
        if ((p.phase === "ready" && !p.already_loaded) || p.phase === "failed" || p.phase === "spilling") {
          // The load happens before the agent speaks, so show it above the step it was for.
          const last = items[items.length - 1];
          if (last && last.kind === "step" && last.step === current && current.events.length === 0 && current.state === "working") {
            items.splice(items.length - 1, 0, { kind: "switch", event });
          } else {
            items.push({ kind: "switch", event });
          }
        }
        break;
      case "approval.requested":
        if (p.kind === "gate") items.push({ kind: "gate", event, decided: null });
        else current?.events.push(event);
        break;
      case "approval.decided":
        if (p.kind === "gate") {
          const gate = [...items].reverse().find((i) => i.kind === "gate" && i.event.payload.fingerprint === p.fingerprint);
          if (gate && gate.kind === "gate") gate.decided = event;
        } else {
          current?.events.push(event);
        }
        break;
      default:
        break;
    }
  }
  return items;
}

function JsonBlock({ value }: { value: unknown }) {
  return <pre className="selectable max-h-80 overflow-auto rounded-md border border-line bg-bg p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap text-ink-2">{JSON.stringify(value, null, 2)}</pre>;
}

function OutputSummary({ output }: { output: Record<string, unknown> }) {
  const summary = typeof output.summary === "string" ? output.summary : typeof output.objective === "string" ? output.objective : typeof output.title === "string" ? output.title : null;
  // A check names its outcome (NO_VERIFIED_CLAIMS, INSUFFICIENT_CONSENSUS, ...): show that, not a bare pass or fail.
  const verdict = typeof output.verdict === "string" ? output.verdict.replace(/_/g, " ") : "passed" in output ? (output.passed ? t("PASS") : t("FAIL")) : "approved" in output ? (output.approved ? t("APPROVED") : t("CHANGES REQUESTED")) : "needs_rewrite" in output ? (output.needs_rewrite ? t("REWRITE") : t("ACCEPTED")) : null;
  const good = "passed" in output ? Boolean(output.passed) : "approved" in output ? Boolean(output.approved) : "needs_rewrite" in output && !output.needs_rewrite;
  return (
    <div className="grid gap-1.5">
      {verdict && (
        <div>
          <Badge tone={good ? "ok" : "limited"}>{verdict}</Badge>
        </div>
      )}
      {summary && <p className="selectable max-w-[75ch] text-sm text-ink-2">{summary}</p>}
    </div>
  );
}

export function ToolCard({ events }: { events: RunEvent[] }) {
  const [open, setOpen] = useState(false);
  const last = events[events.length - 1];
  if (!last) return null;
  const p = last.payload;
  const status = String(p.status ?? "");
  const tone: Tone = status === "success" ? "ok" : status === "running" ? "accent" : status === "denied" ? "limited" : "bad";
  const args = (p.arguments ?? {}) as Record<string, unknown>;
  const headline = typeof args.command === "string" ? args.command : typeof args.path === "string" ? args.path : typeof args.args === "string" ? `git ${args.args}` : Object.keys(args).length ? JSON.stringify(args) : "";
  return (
    <div className="rounded-md border border-line bg-bg">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="grid w-full grid-cols-[auto_1fr_auto] items-center gap-2.5 px-3 py-2 text-left">
        <Terminal size={13} className="text-ink-3" aria-hidden />
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-xs">
            <span className="font-semibold">{String(p.label ?? p.tool)}</span>
            {p.risk === "high" && <Badge tone="bad">{t("HIGH RISK")}</Badge>}
          </div>
          <div className="selectable truncate font-mono text-xs text-ink-2">{headline}</div>
        </div>
        <div className="flex items-center gap-2 text-xs">
          {typeof p.duration === "number" && <span className="tabular text-ink-3">{duration(p.duration)}</span>}
          <Badge tone={tone}>{toolStatusLabel(status)}</Badge>
          <ChevronRight size={13} className={cx("text-ink-3 transition-transform duration-150", open && "rotate-90")} aria-hidden />
        </div>
      </button>
      {open && (
        <div className="fade-in grid gap-2 border-t border-line p-3">
          <div className="text-2xs text-ink-3">{String(p.reason ?? "")}</div>
          <JsonBlock value={args} />
          {typeof p.output === "string" && <pre className="selectable max-h-72 overflow-auto rounded-md border border-line bg-panel p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap text-ink-2">{p.output}</pre>}
        </div>
      )}
    </div>
  );
}

/** Text a model is producing right now, for one node. Not stored: it exists while the session is open. */
export interface LiveText {
  reasoning: string;
  content: string;
  tokens: number;
  tokensPerSecond: number | null;
}

/** The end of a text that keeps growing, scrolled to its last line. */
function Stream({ text, tall = false }: { text: string; tall?: boolean }) {
  const box = useRef<HTMLPreElement>(null);
  useEffect(() => {
    if (box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [text]);
  return (
    <pre ref={box} className={cx("selectable overflow-y-auto px-3 pb-2.5 font-sans text-xs leading-relaxed whitespace-pre-wrap text-ink-2", tall ? "max-h-[46vh]" : "max-h-36")}>
      {text}
    </pre>
  );
}

export function LiveBlock({ stream, tall = false }: { stream: LiveText; tall?: boolean }) {
  const writing = stream.content.trim().length > 0;
  return (
    <div className="rounded-md border border-line bg-bg">
      <div className="flex items-center justify-between gap-3 px-3 py-1.5 text-2xs text-ink-3">
        <span className="flex items-center gap-1.5">
          <span className="pulse-dot h-1.5 w-1.5 rounded-full bg-accent" aria-hidden />
          {writing ? t("Writing the answer") : t("Reasoning")}
        </span>
        <span className="tabular">
          {stream.tokensPerSecond != null ? `${Math.round(stream.tokensPerSecond)} tok/s · ` : ""}
          {t("{n} tokens", { n: stream.tokens.toLocaleString() })}
        </span>
      </div>
      <Stream text={writing ? stream.content : stream.reasoning} tall={tall} />
    </div>
  );
}

function StepCard({ step, live, stream }: { step: Step; live: boolean; stream?: LiveText }) {
  const [open, setOpen] = useState(false);
  const [thinking, setThinking] = useState(false);
  // Fold the running/finished pair of each tool call into one card.
  const grouped = useMemo(() => {
    const groups: { key: string; kind: "tool" | "retry" | "failed" | "approval" | "trimmed" | "stalled" | "memory"; events: RunEvent[] }[] = [];
    for (const event of step.events) {
      if (event.type === "tool.call") {
        const last = groups[groups.length - 1];
        if (last && last.kind === "tool" && last.events[0]?.payload.tool === event.payload.tool && last.events[last.events.length - 1]?.payload.status === "running") {
          last.events.push(event);
        } else {
          groups.push({ key: `t${event.seq}`, kind: "tool", events: [event] });
        }
      } else if (event.type === "node.retry") {
        groups.push({ key: `r${event.seq}`, kind: "retry", events: [event] });
      } else if (event.type === "node.failed") {
        groups.push({ key: `f${event.seq}`, kind: "failed", events: [event] });
      } else if (event.type === "node.trimmed") {
        groups.push({ key: `m${event.seq}`, kind: "trimmed", events: [event] });
      } else if (event.type === "node.stalled") {
        groups.push({ key: `s${event.seq}`, kind: "stalled", events: [event] });
      } else if (event.type === "memory.recalled") {
        groups.push({ key: `b${event.seq}`, kind: "memory", events: [event] });
      }
    }
    return groups;
  }, [step.events]);

  return (
    <li className="rise-in grid grid-cols-[22px_1fr] gap-x-3">
      <div className="flex flex-col items-center pt-1">
        {step.state === "done" && !step.check && <Check size={15} className="text-ok" aria-label={t("completed")} />}
        {step.state === "done" && step.check && (step.output?.passed === false ? <ShieldX size={15} className="text-limited" aria-label={t("did not pass")} /> : <ShieldCheck size={15} className="text-ok" aria-label={t("passed")} />)}
        {step.state === "working" && (live ? <Loader2 size={15} className="animate-spin text-accent" aria-label={t("working")} /> : <Square size={12} className="text-ink-3" />)}
        {step.state === "failed" && <CircleAlert size={15} className="text-bad" aria-label={t("failed")} />}
        <div className="mt-1.5 w-px flex-1 bg-line" />
      </div>
      <div className="min-w-0 pb-5">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
          <span className="text-sm font-semibold tracking-wide uppercase">{step.role}</span>
          <span className={cx("text-xs", step.state === "done" ? "text-ok" : step.state === "failed" ? "text-bad" : "text-accent")}>
            {step.state === "done" ? t("Completed") : step.state === "failed" ? t("Failed") : live ? t("Working") : t("Stopped")}
          </span>
          {step.check && <span className="text-xs text-ink-3">{t("check in code, no model")}</span>}
          <span className="tabular text-xs text-ink-3">
            {[step.model, step.seconds != null ? duration(step.seconds) : null, step.tps != null ? `${step.tps} tok/s` : null, step.tokens ? `${step.tokens} tokens${step.reasoning ? ` (${step.reasoning} reasoning)` : ""}` : null].filter(Boolean).join(" · ")}
          </span>
        </div>
        <div className="mt-2 grid gap-2">
          {grouped.map((group) =>
            group.kind === "tool" ? (
              <ToolCard key={group.key} events={group.events} />
            ) : group.kind === "retry" ? (
              <div key={group.key} className="text-xs text-warn" title={String(group.events[0]?.payload.reason ?? "")}>
                {retryText(group.events[0]?.payload ?? {})}
              </div>
            ) : group.kind === "trimmed" ? (
              <div key={group.key} className="text-xs text-ink-3">
                {t("Older tool results were shortened so the conversation fits the model's context ({n} tokens).", { n: Number(group.events[0]?.payload.context_length ?? 0).toLocaleString() })}
              </div>
            ) : group.kind === "stalled" ? (
              <div key={group.key} className="text-xs text-warn">
                {group.events[0]?.payload.stopped
                  ? t("{n} rounds in a row without changing a file: the tools were taken away and the agent concludes.", { n: Number(group.events[0]?.payload.rounds ?? 0) })
                  : t("{n} rounds in a row without changing a file: the agent was told to write now.", { n: Number(group.events[0]?.payload.rounds ?? 0) })}
              </div>
            ) : group.kind === "memory" ? (
              <RecalledLine key={group.key} event={group.events[0] as RunEvent} />
            ) : (
              <div key={group.key} className="selectable text-sm text-bad">
                {String(group.events[0]?.payload.error)}
              </div>
            ),
          )}
          {live && step.state === "working" && stream && (stream.reasoning || stream.content) && <LiveBlock stream={stream} />}
          {step.thought && step.state !== "working" && (
            <>
              <button type="button" onClick={() => setThinking(!thinking)} aria-expanded={thinking} className="inline-flex items-center gap-1 justify-self-start text-xs text-ink-3 hover:text-ink">
                <ChevronRight size={12} className={cx("transition-transform duration-150", thinking && "rotate-90")} aria-hidden />
                {thinking ? t("Hide the reasoning") : t("Show the reasoning")}
              </button>
              {thinking && <pre className="selectable max-h-72 overflow-y-auto rounded-md border border-line bg-bg p-3 font-sans text-xs leading-relaxed whitespace-pre-wrap text-ink-2">{step.thought}</pre>}
            </>
          )}
          {step.output && (
            <>
              <OutputSummary output={step.output} />
              <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="inline-flex items-center gap-1 justify-self-start text-xs text-ink-3 hover:text-ink">
                <ChevronRight size={12} className={cx("transition-transform duration-150", open && "rotate-90")} aria-hidden />
                {open ? t("Hide structured output") : t("Show structured output")}
              </button>
              {open && <JsonBlock value={step.output} />}
            </>
          )}
          {step.sha && (
            <div className="selectable font-mono text-2xs text-ink-3" title={`SHA-256 ${step.sha}`}>
              sha256 {step.sha.slice(0, 16)}
              {Object.keys(step.read).length > 0 && (
                <span className="font-sans"> · {t("read: {items}", { items: Object.keys(step.read).map((key) => (key === "project_memory" ? t("project memory") : key)).join(", ") })}</span>
              )}
            </div>
          )}
        </div>
      </div>
    </li>
  );
}

/** The memories of the project that were put in a step's prompt, chosen for its task. */
export function RecalledLine({ event }: { event: RunEvent }) {
  const p = event.payload;
  const items = (p.items as { id: number; kind: string; title: string }[] | undefined) ?? [];
  if (typeof p.error === "string") return <div className="text-xs text-warn">{t("The project memory could not be read, so this step works without it: {error}", { error: p.error })}</div>;
  if (items.length === 0) return null;
  const shown = items.slice(0, 4).map((item) => item.title).join(" · ");
  return (
    <div className="flex items-baseline gap-1.5 text-xs text-ink-3" title={items.map((item) => `#${item.id} ${item.title}`).join("\n")}>
      <Brain size={12} className="shrink-0 translate-y-0.5" aria-hidden />
      <span className="min-w-0">
        {items.length === 1 ? t("1 memory of the project was given to this step") : t("{n} memories of the project were given to this step", { n: items.length })}
        <span className="text-ink-2">: {shown}</span>
        {items.length > 4 ? ` +${items.length - 4}` : ""}
      </span>
    </div>
  );
}

/** What the run wrote in the memory of its project when it ended. */
export function SavedLine({ event }: { event: RunEvent }) {
  const navigate = useApp((s) => s.navigate);
  const p = event.payload;
  const saved = (p.saved as Record<string, number> | undefined) ?? {};
  const count = Object.values(saved).reduce((sum, n) => sum + n, 0) + Number(p.by_agents ?? 0);
  return (
    <li className="grid grid-cols-[22px_1fr] gap-x-3">
      <div className="flex flex-col items-center pt-1">
        <Brain size={14} className="text-accent" aria-hidden />
      </div>
      <div className="pb-4 text-xs text-ink-3">
        {count === 1 ? t("1 memory written to the memory of {name}.", { name: String(p.name) }) : t("{n} memories written to the memory of {name}.", { n: count, name: String(p.name) })}{" "}
        <button type="button" onClick={() => navigate("brain", { project: String(p.project) })} className="text-accent hover:underline">
          {t("Open the project memory")}
        </button>
      </div>
    </li>
  );
}

export function SwitchLine({ event }: { event: RunEvent }) {
  const p = event.payload;
  const failed = p.phase === "failed";
  const unloaded = (p.unloaded as string[] | undefined) ?? [];
  const context = typeof p.context_length === "number" ? p.context_length : null;
  const requested = typeof p.requested_context === "number" ? p.requested_context : null;
  const free = typeof p.vram_free_mib === "number" ? p.vram_free_mib : null;
  const attempts = typeof p.attempts === "number" ? p.attempts : 1;
  const reduced = context !== null && requested !== null && context < requested;
  const planned = typeof p.planned_context === "number" ? p.planned_context : null;
  const speed = typeof p.tokens_per_second === "number" ? p.tokens_per_second : null;
  // Loaded with more context than the free VRAM allowed on paper, and kept because the speed held.
  const stretched = context !== null && planned !== null && context > planned && !p.saturated;
  if (p.phase === "spilling") {
    // Noticed while the model was working: part of it is in system RAM, which is why it got slow.
    const next = typeof p.next_context === "number" ? p.next_context : null;
    const numbers = { n: Number(p.spilled_mib ?? 0).toLocaleString(), from: (context ?? 0).toLocaleString(), to: (next ?? 0).toLocaleString() };
    return (
      <li className="grid grid-cols-[22px_1fr] gap-x-3">
        <div className="flex flex-col items-center pt-1.5">
          <RefreshCw size={12} className="text-limited" aria-hidden />
          <div className="mt-1.5 w-px flex-1 bg-line" />
        </div>
        <div className="pb-4 text-xs text-limited">
          {next !== null
            ? t("{n} MiB of the model are in system RAM, so it answers slowly: other programs have taken graphics memory. It is reloaded with less context, from {from} to {to}.", numbers)
            : t("{n} MiB of the model are in system RAM even with the smallest context, so it answers slowly. With what is open now this model is too large for the graphics card: close programs that use the GPU, or use a smaller quantization of it.", numbers)}
        </div>
      </li>
    );
  }
  return (
    <li className="grid grid-cols-[22px_1fr] gap-x-3">
      <div className="flex flex-col items-center pt-1.5">
        <RefreshCw size={12} className={failed ? "text-bad" : p.saturated ? "text-limited" : "text-ink-3"} aria-hidden />
        <div className="mt-1.5 w-px flex-1 bg-line" />
      </div>
      <div className="pb-4 text-xs text-ink-3">
        {failed ? (
          <span className="text-bad">{t("Could not load {model}: {message}", { model: String(p.to), message: String(p.message) })}</span>
        ) : (
          <>
            {unloaded.length > 0 ? t("Switching model") : t("Loading model")}
            {unloaded.length > 0 && <span> · {t("unloaded")} {unloaded.join(", ")} ({duration(Number(p.unload_seconds))})</span>} · {t("loaded")} <span className="text-ink-2">{String(p.to)}</span> ({duration(Number(p.load_seconds))}) ·{" "}
            {t("ready in")} <span className="tabular text-ink-2">{duration(Number(p.total_seconds))}</span>
            {(context !== null || free !== null) && (
              <div className="tabular mt-0.5">
                {context !== null && <span>{t("context {n}", { n: context.toLocaleString() })}</span>}
                {context !== null && free !== null && " · "}
                {free !== null && <span>{t("{n} MiB of VRAM left free", { n: free.toLocaleString() })}</span>}
              </div>
            )}
            {Boolean(p.auto) && !reduced && <div className="mt-0.5">{t("Context chosen automatically for this request.")}</div>}
            {Boolean(p.wide) && <div className="mt-0.5 text-ink-2">{t("Wide-context variant: the request did not fit the slot's usual model.")}</div>}
            {reduced && !p.saturated && (
              <div className="mt-0.5 text-ink-2">
                {t("Context lowered from {requested} to {context}: that is what the free VRAM allowed.", { requested: (requested ?? 0).toLocaleString(), context: (context ?? 0).toLocaleString() })}
                {attempts > 1 ? ` ${t("Found on attempt {n}, after a load that filled the VRAM.", { n: attempts })}` : ""}
              </div>
            )}
            {stretched && (
              <div className="mt-0.5 text-ink-2">
                {t("On paper the free VRAM allowed {planned} tokens. {context} were loaded and the speed measured: {speed} tok/s, the model's usual speed, so it fits.", {
                  planned: (planned ?? 0).toLocaleString(),
                  context: (context ?? 0).toLocaleString(),
                  speed: speed !== null ? Math.round(speed) : "?",
                })}
              </div>
            )}
            {typeof p.shrunk_from === "number" && (
              <div className="mt-0.5 text-ink-2">
                {t("Reloaded with less context because it had overflowed into system RAM: from {from} to {to}.", { from: p.shrunk_from.toLocaleString(), to: (context ?? 0).toLocaleString() })}
              </div>
            )}
            {Boolean(p.partial_offload) && <div className="mt-0.5 text-ink-2">{t("This model is larger than the graphics memory: part of it runs from system RAM, as planned.")}</div>}
            {Boolean(p.saturated) && (
              <div className="mt-0.5 text-limited">{t("VRAM is full even at the smallest context. Windows moves the overflow to system RAM, so this model will answer slowly. Close other apps that use the GPU.")}</div>
            )}
          </>
        )}
      </div>
    </li>
  );
}

export function ApprovalCard({ approval, onDecide }: { approval: PendingApproval; onDecide: (approved: boolean, comment: string) => void }) {
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const tool = approval.kind === "tool";
  const decide = (approved: boolean) => {
    setBusy(true);
    onDecide(approved, comment);
  };
  return (
    <li className="rise-in grid grid-cols-[22px_1fr] gap-x-3">
      <div className="flex flex-col items-center pt-1">
        {tool ? <ShieldAlert size={15} className="text-warn" aria-hidden /> : <UserCheck size={15} className="text-warn" aria-hidden />}
        <div className="mt-1.5 w-px flex-1 bg-line" />
      </div>
      <div className="mb-5 rounded-lg border border-warn bg-surface p-4">
        <div className="text-sm font-semibold">{tool ? t("Approve this action?") : approval.kind === "escalation" ? t("An agent failed") : t("Your approval is needed")}</div>
        <p className="selectable mt-1 max-w-[75ch] text-sm text-ink-2">{approval.prompt}</p>
        {tool && approval.payload.arguments !== undefined && (
          <div className="mt-2.5">
            <JsonBlock value={approval.payload.arguments} />
          </div>
        )}
        {!tool && approval.payload.output !== undefined && approval.payload.output !== null && (
          <div className="mt-2.5">
            <JsonBlock value={approval.payload.output} />
          </div>
        )}
        <div className="mt-3 grid grid-cols-[1fr_auto_auto] items-center gap-2">
          <TextInput value={comment} onChange={(e) => setComment(e.target.value)} placeholder={tool ? t("Reason (sent to the agent if you deny)") : t("Feedback for the team (optional)")} aria-label={t("Comment")} />
          <Button variant="danger" disabled={busy} onClick={() => decide(false)}>
            {tool ? t("Deny") : t("Reject")}
          </Button>
          <Button variant="primary" loading={busy} onClick={() => decide(true)}>
            {t("Approve")}
          </Button>
        </div>
      </div>
    </li>
  );
}

function GateLine({ event, decided }: { event: RunEvent; decided: RunEvent | null }) {
  if (!decided) return null;
  const approved = Boolean(decided.payload.approved);
  return (
    <li className="grid grid-cols-[22px_1fr] gap-x-3">
      <div className="flex flex-col items-center pt-1">
        <UserCheck size={14} className={approved ? "text-ok" : "text-bad"} aria-hidden />
        <div className="mt-1.5 w-px flex-1 bg-line" />
      </div>
      <div className="pb-4 text-xs text-ink-3">
        {decided.payload.automatic ? t("Approved without asking, permissions are ignored in this run:") : t("Human approval:")}{" "}
        <span className={approved ? "text-ok" : "text-bad"}>{approved ? t("approved") : t("rejected")}</span>
        {decided.payload.comment ? ` · "${String(decided.payload.comment)}"` : ""} <span className="text-ink-3">({String(event.payload.prompt)})</span>
      </div>
    </li>
  );
}

// -- right panel --------------------------------------------------------------

/** Models in memory, the GPU and RAM right now, and the files the run wrote. */
function Resources({ events, definition }: { events: RunEvent[]; definition: WorkflowDef | null }) {
  const live = useApp((s) => s.live);
  const tools = events.filter((e) => e.type === "tool.call" && e.payload.status !== "running");
  const switches = events.filter((e) => e.type === "model.switch" && e.payload.phase === "ready" && !e.payload.already_loaded);
  const navigate = useApp((s) => s.navigate);
  const started = events.find((e) => e.type === "run.started")?.payload;
  const workspace = String(started?.workspace ?? "");
  const memory = (started?.memory ?? null) as { project: string; name: string; memories: number } | null;
  const takesEvidence = (definition?.checks ?? []).some((c) => c.kind === "evidence_dossier") || definition?.workflow.category === "verification";
  const artifacts = [...new Set(tools.filter((e) => e.payload.tool === "write_file" && e.payload.status === "success").map((e) => String((e.payload.arguments as Record<string, unknown>).path)))];
  const gpu = live?.gpus[0];
  const heading = "mb-2 text-xs font-semibold text-ink-2";

  return (
    <div className="grid gap-6 text-sm">
      <div>
        <div className={heading}>{t("Models")}</div>
          <div className="grid gap-4">
            {Object.entries(definition?.models ?? {}).map(([id, slot]) => {
              const loaded = live?.loaded.some((l) => l.model_key === slot.model_key);
              return (
                <div key={id}>
                  <div className="flex items-start justify-between gap-2">
                    <span className="selectable min-w-0 font-medium break-all">{slot.model_key || t("no model")}</span>
                    <Badge tone={loaded ? "ok" : "off"}>{loaded ? t("IN MEMORY") : t("NOT LOADED")}</Badge>
                  </div>
                  <div className="mt-0.5 text-xs text-ink-2">
                    {(definition?.agents ?? []).filter((a) => a.model === id).map((a) => a.role).join(", ")}
                  </div>
                  <div className="mt-0.5 text-2xs text-ink-3">
                    {policyLabel(slot.loading_policy)} · {t("{n} context", { n: slot.context_length.toLocaleString() })}
                    {slot.vram_gb ? ` · ${slot.vram_gb} GB planned` : ""}
                  </div>
                </div>
              );
            })}
            <div className="border-t border-line pt-3 text-xs text-ink-3">
              {switches.length === 1 ? t("1 model load this run") : t("{n} model loads this run", { n: switches.length })}
              {switches.length > 0 && ` · ${t("{time} spent switching", { time: duration(switches.reduce((sum, e) => sum + Number(e.payload.total_seconds ?? 0), 0)) })}`}
            </div>
          </div>
      </div>
      <div>
        <div className={heading}>{t("Resources")}</div>
          <div className="grid gap-4">
            {gpu?.vram_total_mb ? (
              <div className="grid gap-1.5">
                <div className="flex justify-between text-xs">
                  <span className="text-ink-2">GPU</span>
                  <span className="tabular">
                    {gbFromMb(gpu.vram_used_mb)} / {gbFromMb(gpu.vram_total_mb, 0)}
                  </span>
                </div>
                <Meter value={(gpu.vram_used_mb ?? 0) / gpu.vram_total_mb} label={t("GPU memory")} />
                <div className="tabular text-2xs text-ink-3">
                  {t("{n}% utilization", { n: gpu.utilization_percent ?? 0 })}{gpu.temperature_c != null ? ` · ${gpu.temperature_c} °C` : ""}
                </div>
              </div>
            ) : (
              <div className="text-xs text-ink-3">{t("No GPU readings available.")}</div>
            )}
            {live && (
              <div className="grid gap-1.5">
                <div className="flex justify-between text-xs">
                  <span className="text-ink-2">RAM</span>
                  <span className="tabular">
                    {(live.ram_used_bytes / 1024 ** 3).toFixed(1)} / {(live.ram_total_bytes / 1024 ** 3).toFixed(0)} GB
                  </span>
                </div>
                <Meter value={live.ram_used_bytes / live.ram_total_bytes} tone="off" label={t("System memory")} />
              </div>
            )}
          </div>
      </div>
      <div>
        <div className={heading}>{t("Artifacts")}</div>
          <div className="grid gap-3">
            {artifacts.length === 0 ? <div className="text-ink-3">{t("Files the agents write appear here.")}</div> : (
              <ul className="selectable grid gap-1 font-mono text-xs">
                {artifacts.map((path) => (
                  <li key={path} className="truncate text-ink-2">
                    {path}
                  </li>
                ))}
              </ul>
            )}
            {workspace && (
              <div className="border-t border-line pt-3">
                <div className="text-2xs text-ink-3">{t("Workspace")}</div>
                <div className="selectable font-mono text-xs break-all text-ink-2">{workspace}</div>
              </div>
            )}
          </div>
      </div>
      <div>
        <div className={heading}>{t("Project memory")}</div>
        {memory ? (
          <div className="grid gap-1.5">
            <div className="text-ink-2">
              {memory.memories === 0
                ? t("{name} had no memory before this run: it starts here.", { name: memory.name })
                : memory.memories === 1
                  ? t("{name} had 1 memory when this run started.", { name: memory.name })
                  : t("{name} had {n} memories when this run started.", { name: memory.name, n: memory.memories })}
            </div>
            <div className="text-xs text-ink-3">{t("Each step is given the few that match its task. What this run changes and finds out is written down when it ends.")}</div>
            <button type="button" onClick={() => navigate("brain", { project: memory.project })} className="justify-self-start text-xs text-accent hover:underline">
              {t("Open the project memory")}
            </button>
          </div>
        ) : (
          <div className="text-xs text-ink-3">
            {takesEvidence
              ? t("A verification workflow answers from its evidence only: it reads no memory and leaves none.")
              : t("This run has no project: it works in a workspace of its own, so nothing is remembered. Connect a folder or a GitHub repository to give the team a permanent memory.")}
          </div>
        )}
      </div>
    </div>
  );
}

/** One node of the graph, in full: what the model thought, what it answered, what it ran. */
function StepDetail({ node, definition, steps, stream, live }: { node: string | null; definition: WorkflowDef | null; steps: Step[]; stream?: LiveText; live: boolean }) {
  if (!node) return <p className="text-sm text-ink-3">{t("Select a step in the graph to see what the model thought and what it answered.")}</p>;
  const runs = steps.filter((s) => s.node === node);
  const step = runs[runs.length - 1];
  const agent = definition?.agents.find((a) => a.id === node);
  const check = definition?.checks?.find((c) => c.id === node);
  const title = agent?.role ?? check?.label ?? step?.role ?? node;
  if (!step) {
    return (
      <div className="grid gap-2">
        <div className="text-lg font-semibold tracking-tight">{title}</div>
        <p className="text-sm text-ink-3">{t("This step has not run yet.")}</p>
        {agent?.goal && <p className="max-w-[70ch] text-sm text-ink-2">{agent.goal}</p>}
      </div>
    );
  }
  const working = live && step.state === "working";
  const tools = step.events.filter((e) => e.type === "tool.call" && e.payload.status !== "running");
  return (
    <div className="grid gap-4">
      <div>
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className="text-lg font-semibold tracking-tight">{title}</span>
          <span className={cx("text-xs", step.state === "done" ? "text-ok" : step.state === "failed" ? "text-bad" : "text-accent")}>
            {step.state === "done" ? t("Completed") : step.state === "failed" ? t("Failed") : live ? t("Working") : t("Stopped")}
          </span>
          {runs.length > 1 && <span className="text-xs text-ink-3">{t("run {n}", { n: runs.length })}</span>}
        </div>
        <div className="tabular mt-0.5 text-xs text-ink-3">
          {step.check
            ? t("check in code, no model")
            : [step.model, step.seconds != null ? duration(step.seconds) : null, step.tps != null ? `${step.tps} tok/s` : null, step.tokens ? `${step.tokens} tokens${step.reasoning ? ` (${step.reasoning} reasoning)` : ""}` : null].filter(Boolean).join(" · ")}
        </div>
      </div>
      {working && stream && (stream.reasoning || stream.content) && <LiveBlock stream={stream} tall />}
      {working && stream && stream.content && stream.reasoning && (
        <div>
          <div className="mb-1 text-xs font-semibold text-ink-2">{t("Reasoning")}</div>
          <pre className="selectable max-h-64 overflow-y-auto rounded-md border border-line bg-bg p-3 font-sans text-xs leading-relaxed whitespace-pre-wrap text-ink-2">{stream.reasoning}</pre>
        </div>
      )}
      {!working && !step.check && (
        <div>
          <div className="mb-1 text-xs font-semibold text-ink-2">{t("Reasoning")}</div>
          {step.thought ? (
            <pre className="selectable max-h-[42vh] overflow-y-auto rounded-md border border-line bg-bg p-3 font-sans text-xs leading-relaxed whitespace-pre-wrap text-ink-2">{step.thought}</pre>
          ) : (
            <p className="text-xs text-ink-3">{t("This model did not report any reasoning for this step.")}</p>
          )}
        </div>
      )}
      {tools.length > 0 && (
        <div>
          <div className="mb-1 text-xs font-semibold text-ink-2">{t("Tools")}</div>
          <ul className="grid gap-1 text-xs">
            {tools.map((e) => (
              <li key={e.seq} className="grid grid-cols-[1fr_auto] items-center gap-2">
                <span className="selectable truncate font-mono text-ink-2">
                  {String(e.payload.label ?? e.payload.tool)} {String((e.payload.arguments as Record<string, unknown> | undefined)?.path ?? (e.payload.arguments as Record<string, unknown> | undefined)?.command ?? "")}
                </span>
                <Badge tone={e.payload.status === "success" ? "ok" : e.payload.status === "denied" ? "limited" : "bad"}>{toolStatusLabel(String(e.payload.status))}</Badge>
              </li>
            ))}
          </ul>
        </div>
      )}
      {step.output && (
        <div>
          <div className="mb-1 text-xs font-semibold text-ink-2">{t("Answer")}</div>
          <OutputSummary output={step.output} />
          <div className="mt-2">
            <JsonBlock value={step.output} />
          </div>
        </div>
      )}
      {step.sha && (
        <div className="selectable font-mono text-2xs text-ink-3" title={`SHA-256 ${step.sha}`}>
          sha256 {step.sha.slice(0, 16)}
          {Object.keys(step.read).length > 0 && <span className="font-sans"> · {t("read: {items}", { items: Object.keys(step.read).join(", ") })}</span>}
        </div>
      )}
    </div>
  );
}

/** The session as plain text, for pasting somewhere else. */
function sessionLog(run: RunRecord, steps: Step[]): string {
  const lines = [`# ${run.title}`, run.task, `status: ${run.status}${run.error ? ` (${run.error})` : ""}`, ""];
  for (const step of steps) {
    const facts = [step.model, step.seconds != null ? duration(step.seconds) : null, step.tps != null ? `${step.tps} tok/s` : null].filter(Boolean).join(" · ");
    lines.push(`## ${step.role} — ${step.state}${facts ? ` — ${facts}` : ""}`);
    for (const event of step.events) {
      if (event.type === "tool.call" && event.payload.status !== "running") lines.push(`- tool ${String(event.payload.tool)}: ${String(event.payload.status)}`);
      if (event.type === "node.retry") lines.push(`- retry ${String(event.payload.attempt)}: ${String(event.payload.reason)}`);
      if (event.type === "node.failed") lines.push(`- failed: ${String(event.payload.error)}`);
    }
    if (step.output) lines.push(JSON.stringify(step.output, null, 2));
    lines.push("");
  }
  return lines.join("\n");
}

// -- run view -----------------------------------------------------------------

type RunTab = "session" | "step" | "resources";

function RunView({ runId, onChanged, listHidden, onToggleList }: { runId: string; onChanged: () => void; listHidden: boolean; onToggleList: () => void }) {
  const toast = useApp((s) => s.toast);
  const [tab, setTab] = useState<RunTab>("session");
  const [selected, setSelected] = useState<string | null>(null);
  // Follow: the graph keeps the working step in view, and the step tab shows it.
  const [follow, setFollow] = useState(true);
  const [streams, setStreams] = useState<Record<string, LiveText>>({});
  const installed = useLoad(() => api.get<{ models: InstalledModel[] }>("/api/models/installed"));
  const [run, setRun] = useState<RunRecord | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [definition, setDefinition] = useState<WorkflowDef | null>(null);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);
  const lastStatus = useRef<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const navigate = useApp((s) => s.navigate);

  const refresh = () => {
    api
      .get<{ run: RunRecord; events: RunEvent[] }>(`/api/runs/${runId}?after=${seq.current}`)
      .then((data) => {
        if (data.events.length > 0) {
          seq.current = data.events[data.events.length - 1]?.seq ?? seq.current;
          setEvents((current) => [...current, ...data.events.filter((e) => !current.some((c) => c.seq === e.seq))]);
        }
        // The list is told when this run ends. Not from inside the state update: that runs while rendering.
        const before = lastStatus.current;
        lastStatus.current = data.run.status;
        if (before !== null && before !== data.run.status && TERMINAL.has(data.run.status)) onChanged();
        setRun(data.run);
        setError(null);
      })
      .catch((err: unknown) => setError(errorMessage(err)));
  };

  useEffect(() => {
    seq.current = 0;
    lastStatus.current = null;
    setEvents([]);
    setRun(null);
    setDefinition(null);
    setStreams({});
    setSelected(null);
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  useEffect(() => {
    if (!run || definition) return;
    // A website study's run carries a workflow built for it; any other run, a saved one.
    api
      .get<{ workflow: WorkflowDef }>(`/api/runs/${run.id}/definition`)
      .catch(() => api.get<{ workflow: WorkflowDef }>(`/api/workflows/${run.workflow_id}`))
      .then((data) => setDefinition(data.workflow))
      .catch(() => undefined);
  }, [run, definition]);

  useBus(`run.${runId}`, (event) => {
    const data = event.data as { type?: string; payload?: Record<string, unknown> };
    if (data.type === "run.followed" && typeof data.payload?.next === "string") {
      // The request that waited for this run has started: go where the work is now.
      onChanged();
      navigate("sessions", { run: data.payload.next });
      return;
    }
    if (data.type !== "node.stream") {
      refresh();
      return;
    }
    // Text as the model generates it: shown straight away, never stored.
    const p = data.payload ?? {};
    const node = String(p.node ?? "");
    setStreams((current) => {
      const before = current[node] ?? { reasoning: "", content: "", tokens: 0, tokensPerSecond: null };
      // A count that went back means the agent started a new call: its text starts again.
      const fresh = typeof p.tokens === "number" && p.tokens < before.tokens;
      return {
        ...current,
        [node]: {
          reasoning: ((fresh ? "" : before.reasoning) + String(p.reasoning ?? "")).slice(-12000),
          content: ((fresh ? "" : before.content) + String(p.content ?? "")).slice(-12000),
          tokens: typeof p.tokens === "number" ? p.tokens : before.tokens,
          tokensPerSecond: typeof p.tokens_per_second === "number" ? p.tokens_per_second : before.tokensPerSecond,
        },
      };
    });
  });
  const active = run !== null && !TERMINAL.has(run.status);
  const graph = useMemo(() => deriveGraph(definition, events, active, run?.status ?? ""), [definition, events, active, run?.status]);
  const workingNode = graph.working[0] ?? null;
  useEffect(() => {
    if (follow && workingNode) setSelected(workingNode);
  }, [follow, workingNode]);
  // The event stream drives updates; polling covers a dropped connection.
  useInterval(refresh, active ? 2500 : null);

  const count = events.length;
  useEffect(() => {
    if (active) bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [count, active]);

  const [starting, setStarting] = useState(false);
  const startedWith = events.find((e) => e.type === "run.started")?.payload;
  const source = sourceFromEvent(startedWith);
  const ignoresPermissions = startedWith?.approval === "bypass";
  const takesEvidence = (definition?.checks ?? []).some((c) => c.kind === "evidence_dossier");
  /** The next request of the session: same team, same place, told what this run did. Written while the run works, it waits for it. */
  // Questions asked about this session's work: bumped after each, so the list under the result reloads.
  const [asked, setAsked] = useState(0);
  const followUp = (task: string, next: RunSource, approval: ApprovalMode, mode: FollowUpMode = "team") => {
    if (!run) return;
    setStarting(true);
    if (mode === "ask") {
      // One model answers under the result, where the team worked. A second question waits for the first answer.
      api
        .post(`/api/runs/${run.id}/ask`, { message: task, approval, queue: true })
        .then(() => {
          setTab("session");
          setAsked((n) => n + 1);
        })
        .catch((err: unknown) => toast({ tone: "bad", title: t("Could not ask"), body: errorMessage(err) }))
        .finally(() => setStarting(false));
      return;
    }
    api
      .post<{ id?: string; queued?: boolean }>("/api/runs", {
        workflow_id: run.workflow_id, task, project: run.project, continue_from: run.id, approval, queue: !TERMINAL.has(run.status), ...sourcePayload(next),
      })
      .then((data) => {
        if (!data.id) {
          refresh();
          return;
        }
        onChanged();
        navigate("sessions", { run: data.id });
      })
      .catch((err: unknown) => toast({ tone: "bad", title: t("Could not start the run"), body: errorMessage(err) }))
      .finally(() => setStarting(false));
  };
  const stop = () => void api.post(`/api/runs/${runId}/cancel`).then(refresh);
  const unqueue = () => void api.del(`/api/runs/${runId}/queued`).then(refresh);

  // The panel beside the graph: left or right, as wide as it is dragged, remembered.
  const [panel, setPanel] = useState<PanelPrefs>(() => loadPanelPrefs(SESSION_PANEL));
  useEffect(() => savePanelPrefs(panel, SESSION_PANEL), [panel]);
  const drag = useRef<{ x: number; width: number } | null>(null);

  if (error && !run) return <div className="p-7"><Note tone="bad">{error}</Note></div>;
  if (!run) return <div className="flex-1 p-7"><SkeletonRows rows={5} height={56} /></div>;

  const feed = buildFeed(events);
  const steps = feed.flatMap((item) => (item.kind === "step" ? [item.step] : []));
  const speed = workingNode ? (streams[workingNode]?.tokensPerSecond ?? null) : null;
  const status = statusOf(run.status);
  const decide = (approval: PendingApproval, approved: boolean, comment: string) => {
    api
      .post(`/api/runs/${runId}/approvals/${approval.id}`, { approved, comment })
      .then(refresh)
      .catch((err: unknown) => toast({ tone: "bad", title: t("Decision not recorded"), body: errorMessage(err) }));
  };
  const toolApprovals = run.pending_approvals;

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <div className="flex shrink-0 items-start justify-between gap-4 border-b border-line px-5 py-3">
        <div className="flex min-w-0 items-start gap-2.5">
          {listHidden && (
            // The list is hidden: this is the way back to it, so it says what it brings back.
            <Button size="sm" icon={<PanelLeftOpen size={13} />} onClick={onToggleList} title={`${t("Show the list of sessions")} (Ctrl+B)`}>
              {t("Sessions")}
            </Button>
          )}
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2.5">
              <h1 className="truncate text-base font-semibold tracking-tight">{run.title}</h1>
              <Badge tone={status.tone}>
                {active && run.status === "running" && <Loader2 size={10} className="animate-spin" aria-hidden />}
                {status.label.toUpperCase()}
              </Badge>
              <span className="tabular text-xs text-ink-3">{graph.stepsDone === 1 ? t("1 step so far") : t("{n} steps so far", { n: graph.stepsDone })}</span>
              {ignoresPermissions && (
                <Badge tone="warn" title={t("This run was started with permissions ignored: plans are approved as they come and nothing asks before a risky action.")}>
                  <ShieldOff size={10} aria-hidden />
                  {t("PERMISSIONS IGNORED")}
                </Badge>
              )}
              {graph.shortOnContext > 0 && (
                <Badge tone="limited" title={t("Times a model was loaded with less context than asked, or a conversation was shortened to fit. A smaller model or quantization leaves more room.")}>
                  {t("{n} SHORT ON CONTEXT", { n: graph.shortOnContext })}
                </Badge>
              )}
            </div>
            <p className="selectable mt-0.5 line-clamp-1 max-w-[90ch] text-sm text-ink-2">{run.task}</p>
            {source.kind !== "none" && (
              <p className="selectable mt-0.5 flex items-center gap-1.5 truncate font-mono text-xs text-ink-3" title={String((startedWith?.source as Record<string, unknown> | undefined)?.note ?? "")}>
                {source.kind === "github" ? <FolderGit2 size={12} aria-hidden /> : <FolderOpen size={12} aria-hidden />}
                {source.kind === "github" ? `github.com/${repoName(source.url)}${source.branch ? ` (${source.branch})` : ""}` : source.path}
              </p>
            )}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <Button size="sm" icon={<LocateFixed size={12} />} aria-pressed={follow} onClick={() => setFollow(!follow)} className={follow ? "border-accent text-ink" : undefined} title={t("Keep the working step in view")}>
            {t("Follow")}
          </Button>
          <Button
            size="sm"
            icon={<ClipboardCopy size={12} />}
            onClick={() => void navigator.clipboard.writeText(sessionLog(run, steps)).then(() => toast({ tone: "ok", title: t("Copied") }))}
          >
            {t("Copy the log")}
          </Button>
          {active && (
            <Button size="sm" variant="danger" icon={<Square size={11} />} onClick={stop}>
              {t("Stop")}
            </Button>
          )}
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-[320px] flex-1 flex-col">
          <div className="min-h-0 flex-1">
            {definition ? (
              <LiveGraph
                definition={definition}
                live={graph}
                speed={speed}
                installed={installed.data?.models ?? []}
                selected={selected}
                onSelect={(node) => {
                  setSelected(node);
                  if (node) {
                    setFollow(false);
                    setTab("step");
                  }
                }}
                follow={follow && active}
                started={events.length > 0}
                finished={run.status === "completed"}
              />
            ) : (
              <div className="p-5">
                <SkeletonRows rows={5} height={48} />
              </div>
            )}
          </div>
          {definition && (
            // Always there, also while the run works: to stop it, or to write what comes next.
            <div className="shrink-0 border-t border-line bg-panel px-4 pt-3 pb-2.5">
              <RunComposer
                key={run.id}
                takesEvidence={takesEvidence}
                busy={starting}
                initialSource={source}
                initialApproval={ignoresPermissions ? "bypass" : undefined}
                autoFocus={false}
                placeholder={TERMINAL.has(run.status) ? t("Ask about this work, or tell the team what to do next") : takesEvidence ? undefined : t("Ask this team for the next thing")}
                askable={TERMINAL.has(run.status)}
                running={active}
                queued={run.queued ?? null}
                onUnqueue={unqueue}
                onStop={stop}
                onStart={followUp}
              />
            </div>
          )}
        </div>

        <aside
          style={{ width: panel.width }}
          className={cx("relative flex max-w-[calc(100%-320px)] shrink-0 flex-col border-line", panel.side === "left" ? "order-first border-r" : "border-l")}
        >
          <div
            role="separator"
            aria-orientation="vertical"
            aria-label={t("Panel width")}
            aria-valuemin={SESSION_PANEL.min}
            aria-valuemax={SESSION_PANEL.max}
            aria-valuenow={panel.width}
            tabIndex={0}
            title={t("Drag to resize, double-click to reset")}
            onPointerDown={(e) => {
              drag.current = { x: e.clientX, width: panel.width };
              e.currentTarget.setPointerCapture(e.pointerId);
            }}
            onPointerMove={(e) => {
              const start = drag.current;
              if (!start) return;
              const delta = e.clientX - start.x;
              setPanel((p) => ({ ...p, width: clampPanelWidth(start.width + (p.side === "right" ? -delta : delta), SESSION_PANEL) }));
            }}
            onPointerUp={(e) => {
              drag.current = null;
              e.currentTarget.releasePointerCapture(e.pointerId);
            }}
            onDoubleClick={() => setPanel((p) => ({ ...p, width: SESSION_PANEL.fallback }))}
            onKeyDown={(e) => {
              if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
              e.preventDefault();
              const wider = (e.key === "ArrowLeft") === (panel.side === "right");
              setPanel((p) => ({ ...p, width: clampPanelWidth(p.width + (wider ? 24 : -24), SESSION_PANEL) }));
            }}
            className={cx("absolute top-0 bottom-0 z-[5] w-1.5 cursor-col-resize transition-colors duration-150 hover:bg-accent focus-visible:bg-accent", panel.side === "right" ? "-left-[3px]" : "-right-[3px]")}
          />
          <div className="flex shrink-0 items-end justify-between gap-3 pt-3 pr-3 pl-5">
            <Tabs
              value={tab}
              onChange={setTab}
              tabs={[
                { value: "session", label: t("Session") },
                { value: "step", label: t("Step detail") },
                { value: "resources", label: t("Resources") },
              ]}
            />
            <div role="radiogroup" aria-label={t("Panel position")} className="mb-1 flex shrink-0 gap-0.5">
              {(["left", "right"] as const).map((side) => (
                <button
                  key={side}
                  type="button"
                  role="radio"
                  aria-checked={panel.side === side}
                  aria-label={side === "left" ? t("Panel on the left") : t("Panel on the right")}
                  title={side === "left" ? t("Panel on the left") : t("Panel on the right")}
                  onClick={() => setPanel((p) => ({ ...p, side }))}
                  className={cx("grid h-6 w-7 place-items-center rounded-sm transition-colors duration-150", panel.side === side ? "bg-raised text-ink" : "text-ink-3 hover:text-ink")}
                >
                  {side === "left" ? <PanelLeft size={14} aria-hidden /> : <PanelRight size={14} aria-hidden />}
                </button>
              ))}
            </div>
          </div>
          {tab === "step" && (
            <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
              <StepDetail node={selected} definition={definition} steps={steps} stream={selected ? streams[selected] : undefined} live={active} />
            </div>
          )}
          {tab === "resources" && (
            <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
              <Resources events={events} definition={definition} />
            </div>
          )}
          <div className={cx("min-h-0 flex-1 overflow-y-auto px-5 py-4", tab !== "session" && "hidden")}>
          <ol className="max-w-[860px]">
            {feed.map((item, index) =>
              item.kind === "step" ? (
                <StepCard key={item.step.key} step={item.step} live={active} stream={item.step.state === "working" ? streams[item.step.node] : undefined} />
              ) : item.kind === "switch" ? (
                <SwitchLine key={`s${item.event.seq}`} event={item.event} />
              ) : item.kind === "memory" ? (
                <SavedLine key={`b${item.event.seq}`} event={item.event} />
              ) : (
                <GateLine key={`g${index}`} event={item.event} decided={item.decided} />
              ),
            )}
            {toolApprovals.map((approval) => (
              <ApprovalCard key={approval.id} approval={approval} onDecide={(approved, comment) => decide(approval, approved, comment)} />
            ))}
          </ol>
          {TERMINAL.has(run.status) && (
            <div className="rise-in mt-2 max-w-[860px] border-t border-line pt-5">
              <div className="flex items-center gap-2.5">
                <StatusDot tone={status.tone} />
                <span className="font-semibold">{status.label}</span>
                <span className="text-xs text-ink-3">
                  {run.finished_at ? timeAgo(run.finished_at) : ""}
                  {run.result ? ` · ${t("{n} steps", { n: run.result.history.length })} · ${run.result.model_switches === 1 ? t("1 model load") : t("{n} model loads", { n: run.result.model_switches })}` : ""}
                </span>
              </div>
              {run.error && <p className="selectable mt-2 text-sm text-ink-2">{run.error}</p>}
              {run.result?.final && (
                <div className="mt-3 grid gap-2">
                  <div className="text-sm text-ink-2">{run.status === "blocked" ? t("What the check found") : t("Final result")}</div>
                  {typeof run.result.final.markdown === "string" ? (
                    <pre className="selectable max-h-[480px] overflow-auto rounded-md border border-line bg-panel p-4 font-sans text-sm leading-relaxed whitespace-pre-wrap">{run.result.final.markdown}</pre>
                  ) : (
                    <JsonBlock value={run.result.final} />
                  )}
                </div>
              )}
              {(run.result?.ledger?.length ?? 0) > 0 && <Trail run={run} definition={definition} />}
            </div>
          )}
          {TERMINAL.has(run.status) && <SessionQuestions runId={run.id} version={asked} />}
          <div ref={bottom} />
          </div>
        </aside>
      </div>
    </div>
  );
}

/** What each step produced and what it had read, by hash: the run's audit trail. */
function Trail({ run, definition }: { run: RunRecord; definition: WorkflowDef | null }) {
  const [open, setOpen] = useState(false);
  const ledger = run.result?.ledger ?? [];
  const label = (node: string) => definition?.agents.find((a) => a.id === node)?.role ?? definition?.checks?.find((c) => c.id === node)?.label ?? node;
  return (
    <div className="mt-4">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="inline-flex items-center gap-1 text-sm text-ink-2 hover:text-ink">
        <ChevronRight size={13} className={cx("transition-transform duration-150", open && "rotate-90")} aria-hidden />
        {t("Audit trail: {n} steps, each with the hash of what it wrote and read", { n: ledger.length })}
      </button>
      {open && (
        <div className="fade-in mt-2 overflow-x-auto">
          <table className="selectable w-full min-w-[640px] text-left text-xs">
            <thead className="text-ink-3">
              <tr className="border-b border-line">
                <th className="py-1.5 pr-3 font-normal">#</th>
                <th className="py-1.5 pr-3 font-normal">{t("Step")}</th>
                <th className="py-1.5 pr-3 font-normal">{t("Model")}</th>
                <th className="py-1.5 pr-3 font-normal">SHA-256</th>
                <th className="py-1.5 font-normal">{t("Had read")}</th>
              </tr>
            </thead>
            <tbody>
              {ledger.map((entry, index) => (
                <tr key={`${entry.node}-${index}`} className="border-b border-line align-top">
                  <td className="tabular py-1.5 pr-3 text-ink-3">{index + 1}</td>
                  <td className="py-1.5 pr-3">{label(entry.node)}</td>
                  <td className="py-1.5 pr-3 text-ink-2">{entry.kind === "check" ? t("code") : entry.model || "–"}</td>
                  <td className="py-1.5 pr-3 font-mono text-ink-2" title={entry.sha256}>
                    {entry.sha256.slice(0, 16)}
                  </td>
                  <td className="py-1.5 font-mono text-ink-3">
                    {Object.entries(entry.read).length === 0
                      ? "–"
                      : Object.entries(entry.read).map(([key, sha]) => (
                          <div key={key} title={sha}>
                            {key} <span className="text-ink-3">{sha.slice(0, 8)}</span>
                          </div>
                        ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 max-w-[80ch] text-xs text-ink-3">
            {t("A hash is the SHA-256 of a step's output. A step that read another shows the same hash that step wrote: what it used is exactly what was produced. These are hashes, not signatures.")}
          </p>
        </div>
      )}
    </div>
  );
}

function NewSession({ onStarted, onCancel }: { onStarted: (id: string) => void; onCancel: (() => void) | null }) {
  const toast = useApp((s) => s.toast);
  const navigate = useApp((s) => s.navigate);
  const workflows = useLoad(() => api.get<{ workflows: WorkflowInfo[] }>("/api/workflows"));
  const [workflowId, setWorkflowId] = useState("");
  const [project, setProject] = useState("Default");
  const [busy, setBusy] = useState(false);
  const runnable = (workflows.data?.workflows ?? []).filter((w) => w.source === "user");
  const chosen = runnable.find((w) => w.id === workflowId);

  useEffect(() => {
    if (!workflowId && runnable[0]) setWorkflowId(runnable[0].id);
  }, [runnable, workflowId]);

  const start = (task: string, source: RunSource, approval: ApprovalMode) => {
    setBusy(true);
    api
      .post<{ id: string }>("/api/runs", { workflow_id: workflowId, task, project: project.trim() || "Default", approval, ...sourcePayload(source) })
      .then((data) => onStarted(data.id))
      .catch((err: unknown) => toast({ tone: "bad", title: t("Could not start the session"), body: errorMessage(err) }))
      .finally(() => setBusy(false));
  };

  if (workflows.data && runnable.length === 0) {
    return (
      <div className="px-8">
        <EmptyState title={t("Build a team first")} action={<Button variant="primary" onClick={() => navigate("teams")}>{t("Build AI Team")}</Button>}>
          {t("A session runs one of your workflows on a task. Build an AI team to create a workflow with models assigned, then start a session here.")}
        </EmptyState>
      </div>
    );
  }
  return (
    <div className="flex min-h-full flex-col">
      <div className="grid max-w-[920px] gap-4 px-8 pt-8">
        <div className="flex items-center justify-between gap-3">
          <h1 className="text-xl font-semibold tracking-tight">{t("New session")}</h1>
          {onCancel && <Button onClick={onCancel}>{t("Cancel")}</Button>}
        </div>
        {workflows.loading && !workflows.data && <SkeletonRows rows={3} height={36} />}
        <div className="grid grid-cols-[2fr_1fr] gap-3">
          <Field label={t("Workflow")}>
            <Select value={workflowId} onChange={(e) => setWorkflowId(e.target.value)}>
              {runnable.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name} · {t("{n} agents", { n: w.agents })}{w.runnable ? "" : ` · ${t("needs models")}`}
                </option>
              ))}
            </Select>
          </Field>
          <Field label={t("Project")}>
            <TextInput value={project} onChange={(e) => setProject(e.target.value)} placeholder={t("Default")} />
          </Field>
        </div>
        <p className="max-w-[75ch] text-sm text-ink-2">
          {chosen?.evidence
            ? t("This workflow answers only from evidence: what you paste here and the text files of the folder below. A statement it cannot quote from there is discarded.")
            : t("Write the request below. To work on a project of yours, add a folder on this PC or one of your GitHub repositories. The first agent turns the request into a brief; plans wait for your approval.")}
        </p>
      </div>
      <div className="mt-auto border-t border-line bg-panel px-8 pt-3 pb-2.5">
        <RunComposer key={workflowId} takesEvidence={chosen?.evidence ?? false} busy={busy} onStart={start} />
      </div>
    </div>
  );
}

export function SessionsPage() {
  const routeRun = useApp((s) => s.params.run);
  const navigate = useApp((s) => s.navigate);
  const toast = useApp((s) => s.toast);
  const runs = useLoad(() => api.get<{ runs: RunRecord[] }>("/api/runs"));
  const [creating, setCreating] = useState(false);
  const [listHidden, setListHidden] = useState(() => {
    try {
      return window.localStorage.getItem("agentfit.sessions.list") === "hidden";
    } catch {
      return false;
    }
  });
  const toggleList = () => {
    setListHidden((hidden) => {
      try {
        window.localStorage.setItem("agentfit.sessions.list", hidden ? "shown" : "hidden");
      } catch {
        // Not remembered this time.
      }
      return !hidden;
    });
  };
  const selected = creating ? null : (routeRun ?? null);

  // Ctrl+B hides and shows the list, as it does a side bar in an editor.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && !event.shiftKey && !event.altKey && event.key.toLowerCase() === "b") {
        event.preventDefault();
        toggleList();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (routeRun) {
      setCreating(false);
      runs.reload();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeRun]);
  useInterval(runs.reload, 5000);

  const list = useMemo(() => runs.data?.runs ?? [], [runs.data]);
  const projects = useMemo(() => {
    const map = new Map<string, RunRecord[]>();
    for (const run of list) map.set(run.project, [...(map.get(run.project) ?? []), run]);
    return [...map.entries()];
  }, [list]);

  const remove = (id: string) => {
    api
      .del(`/api/runs/${id}`)
      .then(() => {
        if (id === routeRun) navigate("sessions");
        runs.reload();
      })
      .catch((err: unknown) => toast({ tone: "bad", title: t("Could not delete"), body: errorMessage(err) }));
  };

  return (
    <div className="flex min-h-0 flex-1">
      <div className={cx("w-[250px] shrink-0 flex-col border-r border-line bg-panel", listHidden && selected ? "hidden" : "flex")}>
        <div className="flex h-12 shrink-0 items-center justify-between border-b border-line px-4">
          <span className="text-sm font-semibold">{t("Sessions")}</span>
          <div className="flex items-center gap-1">
            <Button size="sm" icon={<Plus size={13} />} onClick={() => setCreating(true)}>
              {t("New")}
            </Button>
            {selected && (
              <IconButton label={`${t("Hide the list of sessions")} (Ctrl+B)`} onClick={toggleList}>
                <PanelLeftClose size={15} />
              </IconButton>
            )}
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-2">
          {runs.loading && !runs.data && <SkeletonRows rows={4} height={40} />}
          {runs.data && list.length === 0 && <p className="px-2 py-3 text-xs text-ink-3">{t("No sessions yet. Each run of a workflow appears here with its full activity.")}</p>}
          {projects.map(([project, items]) => (
            <div key={project} className="mb-3">
              <div className="px-2 pb-1 text-2xs font-medium text-ink-3">{project}</div>
              <ul className="grid gap-px">
                {items.map((run) => {
                  const status = statusOf(run.status);
                  const active = run.id === selected;
                  return (
                    <li key={run.id} className="group relative">
                      <button
                        type="button"
                        onClick={() => {
                          setCreating(false);
                          navigate("sessions", { run: run.id });
                        }}
                        aria-current={active ? "true" : undefined}
                        className={cx("grid w-full grid-cols-[10px_1fr] items-baseline gap-2 rounded-md px-2 py-1.5 text-left transition-colors duration-150", active ? "bg-raised" : "hover:bg-surface")}
                      >
                        <span className="pt-1">
                          <StatusDot tone={status.tone} pulse={run.status === "running"} />
                        </span>
                        <span className="min-w-0">
                          <span className={cx("block truncate text-sm", active ? "text-ink" : "text-ink-2")}>{run.title}</span>
                          <span className="block truncate text-2xs text-ink-3">
                            {status.label} · {timeAgo(run.started_at)}
                          </span>
                        </span>
                      </button>
                      {TERMINAL.has(run.status) && (
                        <IconButton label={t("Delete {name}", { name: run.title })} onClick={() => remove(run.id)} className="absolute top-1.5 right-1 opacity-0 group-hover:opacity-100 focus:opacity-100">
                          <Trash2 size={13} />
                        </IconButton>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </div>
      </div>
      {selected ? (
        <RunView key={selected} runId={selected} onChanged={runs.reload} listHidden={listHidden} onToggleList={toggleList} />
      ) : (
        <div className="min-w-0 flex-1 overflow-y-auto">
          <NewSession
            onStarted={(id) => {
              setCreating(false);
              navigate("sessions", { run: id });
            }}
            onCancel={creating && list.length > 0 ? () => setCreating(false) : null}
          />
        </div>
      )}
    </div>
  );
}
