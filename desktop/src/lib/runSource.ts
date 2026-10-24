// Where a run works: an empty workspace of its own, a folder on this PC, or a copy of a
// GitHub repository. The choice is made next to the prompt and remembered for next time.

export type RunSource =
  | { kind: "none" }
  /** `protect`: ask before overwriting a file that was already in the folder. */
  | { kind: "folder"; path: string; protect: boolean }
  | { kind: "github"; url: string; branch: string };

const STORAGE_KEY = "agentfit.run.source";
const GITHUB = /^https:\/\/github\.com\/[A-Za-z0-9][A-Za-z0-9-]{0,38}\/[A-Za-z0-9._-]{1,100}?(\.git)?\/?$/;

export function isGithubUrl(url: string): boolean {
  return GITHUB.test(url.trim());
}

/** "owner/repository" of a GitHub address, for a short label. */
export function repoName(url: string): string {
  return url.trim().replace(/^https:\/\/github\.com\//, "").replace(/(\.git)?\/?$/, "");
}

/** Why the source cannot be used yet, or null when it can. The core checks again. */
export function sourceProblem(source: RunSource): "folder" | "github" | null {
  if (source.kind === "folder" && !source.path.trim()) return "folder";
  if (source.kind === "github" && !isGithubUrl(source.url)) return "github";
  return null;
}

/** The fields of POST /api/runs that say where the run works. */
export function sourcePayload(source: RunSource): Record<string, string | boolean | null> {
  if (source.kind === "folder") return { workspace: source.path.trim(), protect_existing: source.protect };
  if (source.kind === "github") return { repo_url: source.url.trim(), repo_branch: source.branch.trim() || null };
  return {};
}

/** The source a finished run used, read from its start event, so the next request continues there. */
export function sourceFromEvent(payload: Record<string, unknown> | undefined): RunSource {
  const source = (payload?.source ?? {}) as Record<string, unknown>;
  if (source.kind === "folder" && typeof source.path === "string") return { kind: "folder", path: source.path, protect: payload?.protect_existing !== false };
  if (source.kind === "github" && typeof source.url === "string") return { kind: "github", url: source.url, branch: typeof source.branch === "string" ? source.branch : "" };
  return { kind: "none" };
}

export function loadSource(): RunSource {
  try {
    const raw = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? "null") as Partial<RunSource> | null;
    if (raw?.kind === "folder") return { kind: "folder", path: String((raw as { path?: unknown }).path ?? ""), protect: (raw as { protect?: unknown }).protect !== false };
    if (raw?.kind === "github") return { kind: "github", url: String((raw as { url?: unknown }).url ?? ""), branch: String((raw as { branch?: unknown }).branch ?? "") };
  } catch {
    // Storage is unavailable or holds something else: start without a source.
  }
  return { kind: "none" };
}

export function saveSource(source: RunSource): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(source));
  } catch {
    // Remembering the choice is a convenience; the run does not depend on it.
  }
}

// -- side panels that can be docked left or right and resized ------------------------

export interface PanelPrefs {
  side: "left" | "right";
  width: number;
}

/** Where a panel's choice is remembered and how wide it may be. */
export interface PanelRange {
  key: string;
  min: number;
  max: number;
  fallback: number;
}

export const PANEL_MIN = 300;
export const PANEL_MAX = 640;
/** The side panel of the workflow editor. */
export const EDITOR_PANEL: PanelRange = { key: "agentfit.editor.panel", min: PANEL_MIN, max: PANEL_MAX, fallback: 360 };
/** The session, step and resources panel next to the graph of a run. */
export const SESSION_PANEL: PanelRange = { key: "agentfit.sessions.panel", min: 380, max: 1400, fallback: 640 };

export function clampPanelWidth(width: number, range: PanelRange = EDITOR_PANEL): number {
  return Math.round(Math.min(range.max, Math.max(range.min, width)));
}

export function loadPanelPrefs(range: PanelRange = EDITOR_PANEL): PanelPrefs {
  try {
    const raw = JSON.parse(window.localStorage.getItem(range.key) ?? "null") as Partial<PanelPrefs> | null;
    if (raw) return { side: raw.side === "left" ? "left" : "right", width: clampPanelWidth(Number(raw.width) || range.fallback, range) };
  } catch {
    // Fall through to the default.
  }
  return { side: "right", width: range.fallback };
}

export function savePanelPrefs(prefs: PanelPrefs, range: PanelRange = EDITOR_PANEL): void {
  try {
    window.localStorage.setItem(range.key, JSON.stringify(prefs));
  } catch {
    // Not remembered this time.
  }
}

// -- what a message under a finished session does ---------------------------------------

/** "ask": one model answers, in the place the team worked. "team": the whole workflow runs again. */
export type FollowUpMode = "ask" | "team";

// Words a question starts with. Requests that only sound like questions ("puoi aggiungere...")
// count as questions only when they end with a question mark.
// Not \b: in JavaScript it ends a word only at ASCII letters, so "perché" would never match.
const QUESTION_START =
  /^(?:cos['’]|(?:come|cosa|che cos[aè]?|perch[eé]|dove|quando|quale|quali|quanto|quanta|quanti|quante|chi|spiegami|mi spieghi|dimmi|how|what|why|where|when|which|who|explain|tell me)(?![\p{L}\p{N}]))/iu;

/** True when a message reads as a question about the work rather than new work for the team. */
export function looksLikeQuestion(text: string): boolean {
  const clean = text.trim();
  if (!clean) return false;
  if (/\?\s*$/.test(clean)) return true;
  return clean.length <= 300 && QUESTION_START.test(clean);
}

// -- how a run treats permissions ------------------------------------------------------

/** "ask": plans and risky actions wait for the user. "bypass": nothing waits. */
export type ApprovalMode = "ask" | "bypass";

const APPROVAL_KEY = "agentfit.run.approval";

export function loadApproval(): ApprovalMode {
  try {
    return window.localStorage.getItem(APPROVAL_KEY) === "bypass" ? "bypass" : "ask";
  } catch {
    return "ask";
  }
}

export function saveApproval(mode: ApprovalMode): void {
  try {
    window.localStorage.setItem(APPROVAL_KEY, mode);
  } catch {
    // Not remembered this time.
  }
}
