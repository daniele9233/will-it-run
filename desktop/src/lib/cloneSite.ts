// What the Clone a site page reads from the core: the clones, what is installed, and the pieces of a run
// (which step is running, the preparation, the questions the agent asked, what it changed) worked out from its events.

import { t } from "./i18n";
import type { PendingApproval, RunEvent } from "./types";

export interface KitInfo {
  id: string;
  name: string;
  author: string;
  repo: string;
  commit: string;
  license: string;
  summary: string;
  ready: boolean;
}

export interface CloneSetup {
  installed: boolean;
  version: string | null;
  install_dir: string;
  node: { path: string | null; version: string | null; ok: boolean; preferred: boolean; detail: string | null };
  npm: string | null;
  git: string | null;
  job: InstallJob | null;
  browser: { name: string; executable: string } | null;
  kit: KitInfo;
  problems: string[];
  ready: boolean;
}

export interface InstallJob {
  kind: string;
  label: string;
  state: "running" | "done" | "failed" | "cancelled";
  started_at: string;
  finished_at: string | null;
  exit_code: number | null;
  error: string | null;
  output: string[];
}

export interface CloneInfo {
  id: string;
  url: string;
  title: string;
  slug: string;
  model_key?: string;
  vision_key?: string;
  sees?: boolean;
  source?: { kind: string; path?: string; url?: string; branch?: string | null };
  workspace?: string;
  created_at: string;
  updated_at?: string;
  status: string;
  active: boolean;
  thumbnail: string | null;
}

export interface CloneRun {
  id: string;
  status: string;
  error: string | null;
  task: string;
  started_at: string;
  finished_at: string | null;
  events: RunEvent[];
  outputs: Record<string, Record<string, unknown>>;
  pending_approvals: PendingApproval[];
}

/** The question the agent is waiting on: which pages to clone, for one. */
export interface CloneAsk {
  question: string;
  options: string[];
}

export interface CloneDetail extends Omit<CloneInfo, "status" | "thumbnail"> {
  runs: CloneRun[];
  active: boolean;
  shots: string[];
  project: boolean;
  preview: string | null;
  ask: CloneAsk | null;
  ask_run: string | null;
}

export const TERMINAL = new Set(["completed", "failed", "cancelled", "blocked", "retry_limit", "rejected", "no_route"]);

export interface Stage {
  id: string;
  /** A deterministic step, not a model. */
  check?: boolean;
}

/** The nodes of a run, in the order they run: the agent follows the kit's own skill, with a check before and after it. */
export const STAGES: Stage[] = [{ id: "prepare", check: true }, { id: "cloner" }, { id: "final", check: true }];

/** The words of each node, in one place so every one has its translation. */
export const stageText = (): Record<string, { label: string; what: string }> => ({
  prepare: { label: t("Preparation"), what: t("fetches the kit, puts its project in the folder, installs the packages and the browser") },
  cloner: { label: t("Cloner"), what: t("follows the kit's /clone-website skill: asks which pages, studies the site, writes the components") },
  final: { label: t("Final check"), what: t("runs the project's check: lint, typecheck, production build") },
});

export type NodeState = "waiting" | "working" | "done" | "failed" | "stopped";

export interface NodeInfo {
  state: NodeState;
  /** How many times it ran: more than once when work came back to it. */
  runs: number;
  seconds: number | null;
}

export function nodeStates(run: CloneRun | null): Record<string, NodeInfo> {
  const out: Record<string, NodeInfo> = {};
  for (const stage of STAGES) out[stage.id] = { state: "waiting", runs: 0, seconds: null };
  if (!run) return out;
  for (const event of run.events) {
    const node = String(event.payload.node ?? "");
    const info = out[node];
    if (!info) continue;
    if (event.type === "node.started") {
      info.state = "working";
      info.runs += 1;
    } else if (event.type === "node.completed") {
      info.state = event.payload.output && (event.payload.output as Record<string, unknown>).passed === false ? "failed" : "done";
      info.seconds = typeof event.payload.seconds === "number" ? Math.round(event.payload.seconds) : info.seconds;
    } else if (event.type === "node.failed") {
      info.state = "failed";
    }
  }
  if (TERMINAL.has(run.status)) for (const info of Object.values(out)) if (info.state === "working") info.state = "stopped";
  return out;
}

export interface PrepStep {
  step: string;
  state: "running" | "done" | "failed";
  detail: string;
}

/** The preparation and final-check steps, each with its latest state and the line that says what it is doing. */
export function prepSteps(run: CloneRun | null): PrepStep[] {
  const order: string[] = [];
  const latest = new Map<string, PrepStep>();
  for (const event of run?.events ?? []) {
    if (event.type !== "clone.step") continue;
    const step = String(event.payload.step ?? "");
    if (!latest.has(step)) order.push(step);
    latest.set(step, { step, state: (event.payload.state as PrepStep["state"]) ?? "running", detail: String(event.payload.detail ?? "") });
  }
  return order.map((s) => latest.get(s) as PrepStep);
}

export const prepText = (): Record<string, string> => ({
  tools: t("Node.js, npm, browser engine and Chrome or Edge"),
  kit: t("Cloning kit fetched at its pinned version"),
  project: t("Project ready"),
  install: t("Packages installed (npm install)"),
  browser: t("Chromium for the kit's scripts (npm run setup)"),
  final: t("Final check"),
});

export interface Question {
  question: string;
  /** Null while the agent is still waiting for it. */
  answer: string | null;
}

/** The questions the agent asked the person, in order, each with the answer once given. */
export function questions(run: CloneRun | null): Question[] {
  const out: Question[] = [];
  for (const event of run?.events ?? []) {
    if (event.type !== "clone.ask") continue;
    const question = String(event.payload.question ?? "");
    if (event.payload.state === "answered") {
      const open = out.find((q) => q.question === question && q.answer === null);
      if (open) open.answer = String(event.payload.answer ?? "");
      else out.push({ question, answer: String(event.payload.answer ?? "") });
    } else {
      out.push({ question, answer: null });
    }
  }
  return out;
}

export interface Activity {
  reads: number;
  changes: number;
  browser: number;
  commands: number;
  last: { tool: string; path: string | null } | null;
}

const WRITING = new Set(["write_file", "edit_file", "delete_file", "download_asset"]);

/** What the agent has done so far, counted from the tool calls of a run. */
export function activity(run: CloneRun | null): Activity {
  const out: Activity = { reads: 0, changes: 0, browser: 0, commands: 0, last: null };
  for (const event of run?.events ?? []) {
    if (event.type !== "tool.call") continue;
    const tool = String(event.payload.tool ?? "");
    if (event.payload.status === "denied") continue;
    if (tool === "read_file" || tool === "list_dir") out.reads += 1;
    else if (WRITING.has(tool)) out.changes += 1;
    else if (tool.startsWith("browser_") || tool === "look") out.browser += 1;
    else if (tool === "run_command") out.commands += 1;
    out.last = { tool, path: typeof event.payload.path === "string" ? event.payload.path : null };
  }
  return out;
}

/** The text the agent ended with: its report on the clone. */
export function answerOf(run: CloneRun | null, node: string): string {
  const output = run?.outputs[node];
  if (!output) return "";
  if (typeof output.text === "string") return output.text;
  if (typeof output.summary === "string") return output.summary;
  return "";
}

/** The last build check: did the project build, and the end of its log. */
export function buildOf(run: CloneRun | null): { passed: boolean; summary: string; log: string[] } | null {
  const out = run?.outputs.final;
  if (!out) return null;
  return { passed: out.passed === true, summary: String(out.summary ?? ""), log: Array.isArray(out.log) ? out.log.map(String) : [] };
}

export function cleanUrl(raw: string): string | null {
  const text = raw.trim();
  if (!text) return null;
  const withScheme = /^[a-z]+:\/\//i.test(text) ? text : `https://${text}`;
  try {
    const url = new URL(withScheme);
    if (!/^https?:$/.test(url.protocol)) return null;
    if (!url.hostname.includes(".") && url.hostname !== "localhost") return null;
    return url.toString();
  } catch {
    return null;
  }
}

/** A model key as a person reads it: the model's own name, without the folder it sits in or the provider's prefix. */
export function modelName(key: string): string {
  const bare = key.startsWith("cloud:") ? key.slice(6) : key;
  return bare.split("/").slice(-1)[0] ?? bare;
}

/** True while the run is stopped at a question for the person (a risky tool call waits for their yes or no). */
export function waitingForApproval(run: CloneRun | null): boolean {
  return Boolean(run && !TERMINAL.has(run.status) && run.pending_approvals.length > 0);
}
