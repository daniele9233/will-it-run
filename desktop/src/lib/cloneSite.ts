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
  /** What only the agent that follows the kit's skill needs (Git, to fetch the kit). */
  kit_problems: string[];
  ready: boolean;
  agent_ready: boolean;
}

/**
 * How a clone is made: an exact copy of the rendered page, measured against the original; an agent that rebuilds it with the kit's skill;
 * or a site written by a model that sees from a reference picture.
 */
export type CloneMode = "replica" | "agent" | "picture";

/** One pack of agent skills (GSAP, frontend design, Vercel, Three.js, Playwright) and whether it is on this PC. */
export interface SkillPackInfo {
  id: string;
  repo: string;
  commit: string;
  license: string;
  summary: string;
  ready: boolean;
  skills: { name: string; description: string; pack: string }[];
}

export interface SkillsStatus {
  packs: SkillPackInfo[];
  ready: boolean;
  job: { state: "running" | "done" | "failed"; lines: string[]; error: string | null } | null;
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
  host?: string;
  /** Clones made before the exact copy existed have none: they were made by the agent. */
  mode?: CloneMode;
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
  /** What the last measure of an exact copy found, 0 to 1; null before it ran. */
  accuracy?: number | null;
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

/** An exact copy: three steps that are code, and a model that is called only when the measure finds a weak section. */
export const REPLICA_STAGES: Stage[] = [{ id: "capture", check: true }, { id: "project", check: true }, { id: "measure", check: true }, { id: "polisher" }];

/** A site from a picture: code and a model take turns; the last node is an agent that writes the report. */
export const PICTURE_STAGES: Stage[] = [
  { id: "prepare", check: true },
  { id: "analyze", check: true },
  { id: "compose", check: true },
  { id: "measure", check: true },
  { id: "refine", check: true },
  { id: "motion", check: true },
  { id: "reviewer" },
];

export const stagesOf = (mode: CloneMode | undefined): Stage[] => (mode === "replica" ? REPLICA_STAGES : mode === "picture" ? PICTURE_STAGES : STAGES);

/** The words of the nodes of a site made from a picture: some ids are the ones of the other ways, with another meaning. */
const pictureText = (): Record<string, { label: string; what: string }> => ({
  prepare: { label: t("Prepare"), what: t("the project, the skills, the colours of the picture") },
  analyze: { label: t("Analyze"), what: t("the model that sees writes the composition: regions, places, depths") },
  compose: { label: t("Compose"), what: t("the model writes one component for each region from its crop") },
  measure: { label: t("Measure"), what: t("shows the page at the picture's size and compares it, region by region") },
  refine: { label: t("Refine"), what: t("the weakest regions are written again; the old code comes back if it is not better") },
  motion: { label: t("Motion"), what: t("GSAP, with numbers the model chooses: kept only if the page at rest still matches") },
  reviewer: { label: t("Reviewer"), what: t("an agent reports on the result, from the numbers") },
});

/** The words of each node, in one place so every one has its translation. */
export const stageText = (mode?: CloneMode): Record<string, { label: string; what: string }> => ({
  prepare: { label: t("Preparation"), what: t("fetches the kit, puts its project in the folder, installs the packages and the browser") },
  cloner: { label: t("Cloner"), what: t("follows the kit's /clone-website skill: asks which pages, studies the site, writes the components") },
  final: { label: t("Final check"), what: t("runs the project's check: lint, typecheck, production build") },
  capture: { label: t("Capture"), what: t("opens the page in Chrome, hides banners and pop-ups, takes screenshots and freezes the rendered page") },
  project: { label: t("Project"), what: t("writes the Next.js project, one component for each section, and builds it") },
  measure: { label: t("Measure"), what: t("compares the clone with the original, pixel by pixel and section by section") },
  polisher: { label: t("Polisher"), what: t("a model fixes the weak sections with style rules: only if the measure finds some") },
  // Last, so that a site made from a picture reads its own words for the ids it shares with the other ways.
  ...(mode === "picture" ? pictureText() : {}),
});

export type NodeState = "waiting" | "working" | "done" | "failed" | "stopped";

export interface NodeInfo {
  state: NodeState;
  /** How many times it ran: more than once when work came back to it. */
  runs: number;
  seconds: number | null;
}

/**
 * What a step of a run found: the run's own result once it has finished, and until then the latest "step completed" event, so a
 * result is on the page as soon as its step is done, not when the whole run is.
 */
export function outputOf(run: CloneRun | null, node: string): Record<string, unknown> | undefined {
  const final = run?.outputs[node];
  if (final) return final;
  for (let i = (run?.events.length ?? 0) - 1; i >= 0; i--) {
    const event = run?.events[i];
    if (event && event.type === "node.completed" && event.payload.node === node && typeof event.payload.output === "object" && event.payload.output !== null) {
      return event.payload.output as Record<string, unknown>;
    }
  }
  return undefined;
}

export function nodeStates(run: CloneRun | null, mode?: CloneMode): Record<string, NodeInfo> {
  const out: Record<string, NodeInfo> = {};
  for (const stage of stagesOf(mode)) out[stage.id] = { state: "waiting", runs: 0, seconds: null };
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
  capture: t("Page captured"),
  names: t("Components named by the model"),
  build: t("Project built (npm run build)"),
  measure: t("Clone measured against the original"),
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
  const output = outputOf(run, node);
  if (!output) return "";
  if (typeof output.text === "string") return output.text;
  if (typeof output.summary === "string") return output.summary;
  return "";
}

/** The last build check: did the project build, and the end of its log. */
export function buildOf(run: CloneRun | null): { passed: boolean; summary: string; log: string[] } | null {
  const out = outputOf(run, "final");
  if (!out) return null;
  return { passed: out.passed === true, summary: String(out.summary ?? ""), log: Array.isArray(out.log) ? out.log.map(String) : [] };
}

/** What the measure found at one width. Every number is between 0 and 1; ``null`` is "not measured". */
export interface ReplicaView {
  width: number;
  visual: number;
  pixels: number;
  ssim: number;
  layout: number;
  text: number | null;
  referenceHeight: number;
  cloneHeight: number;
  /** File names inside the report folder: the original and the clone at half size, and where they differ. */
  original: string;
  preview: string;
  diff: string;
  errors: string[];
  failed: string[];
}

export interface ReplicaReport {
  passed: boolean;
  accuracy: number | null;
  verdict: string;
  summary: string;
  target: number;
  formula?: string;
  round: number;
  viewports: Record<string, ReplicaView>;
  /** ``viewport`` is absent for a site made from a picture, which has one. */
  weak: { viewport?: string; id: number; name: string; visual: number }[];
  sections: Record<string, { id: number; name: string; visual: number; layout: number }[]>;
  /** A site made from a picture: the accuracy over the regions the model wrote, and the share of the picture that is its own crops. */
  built_accuracy?: number | null;
  copied_share?: number;
}

/** The last measure of an exact copy, or null before it ran. */
export function replicaOf(run: CloneRun | null): ReplicaReport | null {
  const out = outputOf(run, "measure");
  if (!out || typeof out.viewports !== "object" || out.viewports === null) return null;
  return out as unknown as ReplicaReport;
}

export interface CaptureInfo {
  summary: string;
  host: string;
  overlays: string[];
  warnings: string[];
  stats: { sections?: number; nodes?: number; assets?: number; seconds?: number };
}

export function captureOf(run: CloneRun | null): CaptureInfo | null {
  const out = outputOf(run, "capture");
  return out && out.passed === true ? (out as unknown as CaptureInfo) : null;
}

/** What each step of a run found, in the order of the steps: the one line each step wrote about itself. */
export function stepSummaries(run: CloneRun | null, mode: CloneMode | undefined): { id: string; summary: string; passed: boolean | null }[] {
  const out: { id: string; summary: string; passed: boolean | null }[] = [];
  for (const stage of stagesOf(mode)) {
    const result = outputOf(run, stage.id);
    const summary = typeof result?.summary === "string" ? result.summary : typeof result?.text === "string" ? result.text : "";
    if (summary) out.push({ id: stage.id, summary, passed: typeof result?.passed === "boolean" ? result.passed : null });
  }
  return out;
}

/** Why a run that stopped before its measure stopped: what the first step that did not pass says ("" when none failed). */
export function failedSummary(run: CloneRun | null): string {
  for (const id of ["prepare", "capture", "project", "analyze", "compose"]) {
    const out = outputOf(run, id);
    if (out && out.passed === false) return String(out.summary ?? "");
  }
  return "";
}

/** "99.9%" for 0.9987; "Unknown" when the number was not measured. */
export function percent(value: number | null | undefined): string {
  return typeof value === "number" ? `${(value * 100).toFixed(1)}%` : t("Unknown");
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
