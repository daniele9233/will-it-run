// The Arena: the shapes the core returns, the names the screen gives them, and the arithmetic the charts need.
// Everything here is plain functions, so the pages stay about layout and the tests stay about numbers.

import type { Tone } from "./format";
import { getLang, t } from "./i18n";

// -- what the core returns --------------------------------------------------------------------

export type Status = "passed" | "partial" | "failed" | "unscored" | "skipped" | "error";
export type Preset = "quick" | "standard" | "full";
export type RunStatus = "queued" | "running" | "stopping" | "stopped" | "done" | "failed";

export interface CategoryInfo {
  id: string;
  group: string;
  area: string;
  scored: boolean;
  needs: string[];
  counts: [number, number, number];
}

export interface ToolInfo {
  available: boolean;
  version: string | null;
  path: string | null;
}

export interface Overview {
  suite: { id: string; version: string; seed: number; canonical_seed: number; bank_hash: string };
  presets: Record<Preset, number>;
  categories: CategoryInfo[];
  groups: string[];
  areas: string[];
  tools: Record<string, ToolInfo>;
  busy: string | null;
}

export interface LocalChoice {
  key: string;
  label: string;
  params: string | null;
  quant: string | null;
  size_bytes: number | null;
  vision: boolean;
  tool_use: boolean;
  reasoning: boolean;
  max_context: number | null;
  architecture: string | null;
  loaded: boolean;
  /** What the quantizations of one model share (name, size, architecture): equal for the models the Arena takes as one. */
  family?: string | null;
}

export interface CloudChoice {
  key: string;
  label: string;
  vision: boolean;
  provider: string;
  context: number | null;
}

export interface ModelsResponse {
  local: LocalChoice[];
  cloud: CloudChoice[];
  loaded: { instance_id: string; model_key: string; type: string }[];
  busy: string | null;
}

export interface RunRow {
  id: string;
  title: string;
  status: RunStatus;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  suite_version: string;
  suite_hash: string;
  seed: number;
  preset: Preset | null;
  total: number;
  done: number;
  /** A comparison made from other challenges: it has its own copies of the answers and nothing left to ask. */
  merged: boolean;
  models: { key: string; label: string; status: string; cloud: boolean }[];
  note: string;
}

export interface Settings {
  temperature: number;
  top_p: number | null;
  top_k: number | null;
  repeat_penalty: number;
  seed: number;
  repeats: number;
  max_tokens: number;
  context: number;
  timeout_s: number;
  cloud_sampling: string | null;
  /** Present when the challenge was made by putting other challenges side by side. */
  merged?: Merged;
}

export interface Merged {
  from: string[];
  differences: string[];
  replaced: { model: string; kept: string; dropped: string }[];
}

export interface RunMeta {
  id: string;
  title: string;
  status: RunStatus;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  suite_id: string;
  suite_version: string;
  suite_hash: string;
  bank_hash: string;
  seed: number;
  preset: Preset | null;
  categories: string[];
  settings: Settings;
  environment: {
    app?: { name: string; version: string };
    os?: string;
    cpu?: string;
    gpu?: string[];
    vram_gb?: number;
    ram_gb?: number;
    lmstudio?: { installed: boolean; version: string | null; port: number | null };
    tools?: Record<string, ToolInfo>;
  };
  total: number;
  done: number;
  note: string;
}

export interface Counts {
  passed: number;
  partial: number;
  failed: number;
  unscored: number;
  skipped: number;
  error: number;
}

export interface ModelSummary {
  counts: Counts;
  answers: number;
  questions: { total: number; answered: number; scored: number };
  fail_kinds: Record<string, number>;
  speed: { tps_mean: number | null; tps_median: number | null; ttft_median_s: number | null; output_tokens: number; time_s: number; timed: number };
  consistency: number | null;
  coverage: number;
}

export interface ModelReport {
  key: string;
  label: string;
  cloud: boolean;
  vision: boolean;
  status: string;
  error: string | null;
  context_asked: number | null;
  context_granted: number | null;
  load_seconds: number | null;
  info: { params?: string | null; quant?: string | null; size_bytes?: number | null; tool_use?: boolean; reasoning?: boolean; provider?: string };
  position: number;
  rank: number | null;
  tied: boolean;
  score: number | null;
  ci: [number, number] | null;
  score_all: number | null;
  ci_all: [number, number] | null;
  categories_scored: number;
  areas: Record<string, number | null>;
  summary: ModelSummary;
}

export interface CategoryCell {
  total: number;
  scored: number;
  passed: number;
  partial: number;
  failed: number;
  unscored: number;
  skipped: number;
  error: number;
  score: number | null;
  ci: [number, number] | null;
}

export interface CategoryReport {
  id: string;
  group: string;
  area: string;
  scored: boolean;
  needs: string[];
  total: number;
  models: Record<string, CategoryCell>;
}

export interface Cell {
  status: Status;
  score: number | null;
  kind: string | null;
  repeats: number;
  statuses: Status[];
  tps: number | null;
  tokens: number;
  cut: boolean;
  manual: string | null;
  method: string | null;
}

export interface MatrixRow {
  id: string;
  position: number;
  category: string;
  title: string;
  tier: number;
  difficulty: number;
  cells: Record<string, Cell>;
}

export interface Report {
  run: RunMeta;
  models: ModelReport[];
  common: string[];
  categories: CategoryReport[];
  matrix: MatrixRow[];
  areas: string[];
  groups: string[];
  provisional: boolean;
  /** Models that are one model in different quantizations (two or more). */
  families?: Family[];
}

// -- quantizations ------------------------------------------------------------------------------

export interface Family {
  id: string;
  label: string;
  /** From the most precise quantization to the least. */
  members: string[];
  reference: string;
}

export interface QuantCandidate {
  key: string;
  label: string;
  quant: string | null;
  bits: number | null;
  size_bytes: number | null;
  params: string | null;
  architecture: string | null;
  vram_mib: number | null;
  vram_total_mib: number | null;
  spilled: boolean;
  family: string;
  position: number;
}

export type QuantVerdict = "worse" | "better" | "noise";

export interface QuantMember {
  key: string;
  label: string;
  quant: string | null;
  bits: number | null;
  size_bytes: number | null;
  vram_mib: number | null;
  vram_total_mib: number | null;
  spilled: boolean;
  is_reference: boolean;
  score: number;
  ci: [number, number] | null;
  tps_mean: number | null;
  ttft_median_s: number | null;
  load_seconds: number | null;
  context_granted: number | null;
  // Against the reference (absent on the reference itself).
  delta?: number;
  delta_ci?: [number, number] | null;
  better?: number;
  worse?: number;
  same?: number;
  p?: number;
  p_adjusted?: number;
  detectable?: number;
  identical?: boolean;
  verdict?: QuantVerdict;
}

export interface QuantCategory {
  id: string;
  n: number;
  models: Record<string, { score: number; delta: number | null; better: number | null; worse: number | null }>;
}

export interface QuantComparison {
  order: string[];
  reference: string;
  models: Record<string, QuantMember>;
  categories: QuantCategory[];
  questions: number;
  dropped: number;
  settings: { repeats: number | null; temperature: number | null; seed: number | null };
  suite_seed: number;
  alpha: number;
  draws: number;
  speed_comparable: boolean;
}

export interface QuantsResponse {
  families: Family[];
  candidates: QuantCandidate[];
  members: string[];
  reference: string | null;
  comparison: QuantComparison | null;
  problem: "needs_two" | "no_common" | null;
}

export interface Check {
  ok: boolean;
  text: string | null;
}

export interface Answer {
  repeat: number;
  status: Status;
  score: number | null;
  kind: string | null;
  method: string | null;
  reason: string | null;
  checks: Check[];
  extracted: string | null;
  expected: string | null;
  format_ok: boolean;
  answer: string;
  reasoning: string;
  tool_calls: { name: string; arguments: Record<string, unknown> | null; raw: string }[];
  finish_reason: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  reasoning_tokens: number | null;
  ttft_s: number | null;
  duration_s: number | null;
  tps: number | null;
  error: string | null;
  manual: string | null;
  manual_note: string | null;
}

export interface QuestionDetail {
  question: {
    id: string;
    category: string;
    title: string;
    lang: string;
    prompt: string;
    system: string | null;
    messages: { role: string; content: unknown }[] | null;
    tools: { function: { name: string; description?: string } }[] | null;
    expected: string;
    reference: string;
    check_type: string | null;
    tier: number;
    difficulty: number;
    needs: string[];
    images: { name: string; width: number; height: number; sha256: string; data_url: string }[];
    attachments: { name: string; mime: string; pages: number; sha256: string; data_b64: string }[];
  };
  answers: Record<string, Answer[]>;
}

export interface RunRequest {
  models: string[];
  seed: number;
  preset: Preset | null;
  categories: string[] | null;
  repeats: number;
  temperature: number;
  top_p: number | null;
  top_k: number | null;
  repeat_penalty: number;
  sampling_seed: number | null;
  max_tokens: number;
  context: number | null;
  timeout_s: number;
  title: string;
  confirm_unload: boolean;
  confirm_cloud: boolean;
}

export interface PlanResponse {
  questions: number;
  models: number;
  answers: number;
  counts: Record<string, number>;
  suite: { id: string; version: string; hash: string; bank_hash: string };
  context: number;
  cloud: string[];
  blind: string[];
}

// -- names -------------------------------------------------------------------------------------

export const STATUSES: Status[] = ["passed", "partial", "failed", "unscored", "skipped", "error"];

export const STATUS_LABEL: Record<Status, string> = {
  passed: t("Right"),
  partial: t("Partly right"),
  failed: t("Wrong"),
  unscored: t("Not evaluated"),
  skipped: t("Skipped"),
  error: t("Run error"),
};

export const KIND_LABEL: Record<string, string> = {
  wrong: t("Wrong answer"),
  format: t("Format not followed"),
  fabricated: t("Made it up"),
  refused: t("Refused to answer"),
  truncated: t("Cut off"),
  manual: t("Needs a person"),
  tool: t("Tool missing"),
  tech: t("Run error"),
  capability: t("Cannot do it"),
};

export const CATEGORY_LABEL: Record<string, string> = {
  general: t("General knowledge"),
  reading: t("Reading comprehension"),
  math: t("Math"),
  logic: t("Logic"),
  coding: t("Coding"),
  sql: "PostgreSQL",
  terraform: "Terraform",
  ansible: "Ansible",
  documents: t("Business documents"),
  pdf: t("PDF analysis"),
  json: t("Structured JSON"),
  vision: t("Image analysis"),
  tools: t("Tool calling"),
  impossible: t("Impossible questions"),
  instructions: t("Following instructions"),
  injection: t("Resisting injection"),
  longctx: t("Long context"),
  writing: t("Writing"),
};

export const AREA_LABEL: Record<string, string> = {
  knowledge: t("Knowledge"),
  math: t("Math"),
  logic: t("Logic"),
  coding: t("Coding"),
  infra: t("Infrastructure"),
  documents: t("Documents"),
  agents: t("Agents"),
  honesty: t("Honesty"),
  vision: t("Vision"),
};

export const PRESET_LABEL: Record<Preset, string> = { quick: t("Quick"), standard: t("Standard"), full: t("Complete") };

export const label = (names: Record<string, string>, id: string): string => names[id] ?? id;

export function statusTone(status: Status): Tone {
  return { passed: "ok", partial: "warn", failed: "bad", unscored: "off", skipped: "off", error: "limited" }[status] as Tone;
}

/** The mark that goes in a cell. The colour is never the only thing that says what happened. */
export const STATUS_GLYPH: Record<Status, string> = { passed: "✓", partial: "½", failed: "✗", unscored: "?", skipped: "–", error: "!" };

export const RUN_STATUS_LABEL: Record<RunStatus, string> = {
  queued: t("Starting"),
  running: t("Running"),
  stopping: t("Stopping"),
  stopped: t("Stopped"),
  done: t("Finished"),
  failed: t("Failed"),
};

export const isLive = (status: RunStatus): boolean => status === "queued" || status === "running" || status === "stopping";

// -- colours ------------------------------------------------------------------------------------

/** The colour of the n-th model of a run. Fixed by position, so a model keeps its colour in every chart and every tab. */
export const seriesVar = (index: number): string => `var(--s${(index % 8) + 1})`;

// -- numbers ------------------------------------------------------------------------------------

export function fmt(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "–";
  return value.toLocaleString(getLang(), { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function interval(ci: [number, number] | null | undefined, score: number | null | undefined): string | null {
  if (!ci || score === null || score === undefined) return null;
  return `±${fmt(Math.max(score - ci[0], ci[1] - score), 1)}`;
}

/** Whole minutes to show for a time in seconds. */
export function minutes(seconds: number): string {
  if (seconds < 90) return t("{n} s", { n: Math.max(1, Math.round(seconds)) });
  return t("{n} min", { n: Math.round(seconds / 60) });
}

/** How long a running run still needs, from what it has done so far. null when it has not done enough to say. */
export function remainingSeconds(run: Pick<RunMeta, "started_at" | "done" | "total">, nowMs: number): number | null {
  if (!run.started_at || run.done < 3 || run.total <= run.done) return null;
  const elapsed = (nowMs - new Date(run.started_at).getTime()) / 1000;
  if (!(elapsed > 0)) return null;
  return (elapsed / run.done) * (run.total - run.done);
}

// -- models ---------------------------------------------------------------------------------------

export function modelLine(model: Pick<ModelReport, "info" | "cloud" | "vision" | "summary">): string {
  const bits: string[] = [];
  if (model.cloud) bits.push(t("cloud"));
  if (model.info.params) bits.push(model.info.params);
  if (model.info.quant) bits.push(model.info.quant);
  if (model.vision) bits.push(t("vision"));
  return bits.join(" · ");
}

export function speedLine(model: Pick<ModelReport, "summary" | "score" | "ci">): string {
  const parts: string[] = [];
  const speed = model.summary.speed.tps_mean;
  if (speed) parts.push(`${fmt(speed, speed >= 100 ? 0 : 1)} tok/s`);
  const spread = interval(model.ci, model.score);
  if (spread) parts.push(spread);
  return parts.join(" · ");
}

// -- the matrix -----------------------------------------------------------------------------------

export type MatrixFilter = "all" | "wrong" | "unscored" | "split";

const SCORED: Status[] = ["passed", "partial", "failed"];

export function scoredValues(row: MatrixRow, models: string[]): number[] {
  return models.flatMap((key) => {
    const cell = row.cells[key];
    return cell && SCORED.includes(cell.status) && cell.score !== null ? [cell.score] : [];
  });
}

export function inFilter(row: MatrixRow, models: string[], filter: MatrixFilter): boolean {
  if (filter === "all") return true;
  const cells = models.map((key) => row.cells[key]).filter((c): c is Cell => Boolean(c));
  if (filter === "wrong") return cells.some((c) => c.status === "failed" || c.status === "partial");
  if (filter === "unscored") return cells.some((c) => c.status === "unscored" || c.status === "skipped" || c.status === "error");
  const values = scoredValues(row, models);
  return values.length >= 2 && Math.max(...values) - Math.min(...values) >= 0.5;
}

export interface CategoryGroup {
  category: string;
  rows: MatrixRow[];
}

/** The rows in the order the bank asks them, grouped by category. */
export function groupByCategory(rows: MatrixRow[]): CategoryGroup[] {
  const groups: CategoryGroup[] = [];
  for (const row of rows) {
    const last = groups[groups.length - 1];
    if (last && last.category === row.category) last.rows.push(row);
    else groups.push({ category: row.category, rows: [row] });
  }
  return groups;
}

// -- the duel -------------------------------------------------------------------------------------

export interface AreaDuel {
  area: string;
  a: number | null;
  b: number | null;
  delta: number | null;
  winner: "a" | "b" | "tie" | null;
}

export interface Split {
  row: MatrixRow;
  a: Cell;
  b: Cell;
  /** Which of the two did better on this question. */
  better: "a" | "b";
}

export interface Duel {
  a: ModelReport;
  b: ModelReport;
  areas: AreaDuel[];
  aWins: number;
  bWins: number;
  compared: number;
  /** Points of difference between the two scores (a minus b), on what both were scored on. */
  delta: number | null;
  splits: Split[];
}

export function duel(report: Pick<Report, "models" | "areas" | "matrix">, aKey: string, bKey: string): Duel | null {
  const a = report.models.find((m) => m.key === aKey);
  const b = report.models.find((m) => m.key === bKey);
  if (!a || !b) return null;
  const areas: AreaDuel[] = report.areas.map((area) => {
    const x = a.areas[area] ?? null;
    const y = b.areas[area] ?? null;
    const delta = x !== null && y !== null ? x - y : null;
    return { area, a: x, b: y, delta, winner: delta === null ? null : Math.abs(delta) < 0.5 ? "tie" : delta > 0 ? "a" : "b" };
  });
  const compared = areas.filter((r) => r.winner !== null).length;
  const splits: Split[] = [];
  for (const row of report.matrix) {
    const x = row.cells[aKey];
    const y = row.cells[bKey];
    if (!x || !y || !SCORED.includes(x.status) || !SCORED.includes(y.status) || x.score === null || y.score === null) continue;
    if (Math.abs(x.score - y.score) < 0.5) continue;
    splits.push({ row, a: x, b: y, better: x.score > y.score ? "a" : "b" });
  }
  return {
    a,
    b,
    areas,
    aWins: areas.filter((r) => r.winner === "a").length,
    bWins: areas.filter((r) => r.winner === "b").length,
    compared,
    delta: a.score !== null && b.score !== null ? a.score - b.score : null,
    splits,
  };
}

// -- the quantizations of one model --------------------------------------------------------------

export const QUANT_VERDICT_LABEL: Record<QuantVerdict, string> = { worse: t("real loss"), better: t("real gain"), noise: t("noise") };

export function quantVerdictTone(verdict: QuantVerdict): Tone {
  return verdict === "worse" ? "bad" : verdict === "better" ? "ok" : "off";
}

/** A signed number with a real minus sign: «+1.2», «−3.4», «0.0». */
export function signed(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "–";
  const text = fmt(Math.abs(value), digits);
  return value > 0 && Number(text.replace(",", ".")) !== 0 ? `+${text}` : value < 0 && Number(text.replace(",", ".")) !== 0 ? `−${text}` : text;
}

export type QuantAxis = "size" | "memory" | "speed";

/** What a quantization is plotted against: the file, the graphics memory it took, or its speed. null where it was not measured. */
export function quantAxisValue(member: Pick<QuantMember, "size_bytes" | "vram_mib" | "tps_mean">, axis: QuantAxis): number | null {
  if (axis === "size") return member.size_bytes ? member.size_bytes / 1024 ** 3 : null;
  if (axis === "memory") return member.vram_mib ? member.vram_mib / 1024 : null;
  return member.tps_mean ?? null;
}

/** The range of the vertical axis of the accuracy chart: tight around the intervals (the quantizations differ by a few points), in steps of 5. */
export function scoreAxis(lows: number[], highs: number[]): [number, number] {
  let min = Math.max(0, Math.floor((Math.min(...lows) - 2) / 5) * 5);
  let max = Math.min(100, Math.ceil((Math.max(...highs) + 2) / 5) * 5);
  while (max - min < 10 && (min > 0 || max < 100)) {
    if (min > 0) min -= 5;
    if (max - min < 10 && max < 100) max += 5;
  }
  return [min, max];
}

/** The values to mark on an axis from ``min`` to ``max``: multiples of a round step (1, 2, 5, 10, 20 or 25), in at most six steps. */
export function niceTicks(min: number, max: number): number[] {
  const span = max - min;
  const step = [1, 2, 5, 10, 20, 25].find((candidate) => span / candidate <= 6) ?? 50;
  const first = Math.ceil(min / step) * step;
  const ticks: number[] = [];
  for (let value = first; value <= max + 1e-9; value += step) ticks.push(value);
  return ticks;
}

/** The range of the horizontal axis: a little room around the values, never below zero. */
export function spreadAxis(values: number[]): [number, number] {
  const low = Math.min(...values);
  const high = Math.max(...values);
  const room = (high - low) * 0.15 || high * 0.1 || 1;
  return [Math.max(0, low - room), high + room];
}

export interface BottomLine {
  /** The quantizations whose loss against the reference is real. */
  losses: QuantMember[];
  /** The smallest file whose loss is not real (the reference itself excluded), if any. */
  lightest: QuantMember | null;
  /** Bytes it saves against the reference. */
  saved: number | null;
}

/** What the person wants to know in one line: which quantization can they lighten to without a loss that is real. */
export function bottomLine(comparison: QuantComparison): BottomLine {
  const others = comparison.order.flatMap((k) => {
    const m = comparison.models[k];
    return m && !m.is_reference ? [m] : [];
  });
  const reference = comparison.models[comparison.reference];
  const fine = others.filter((m) => m.verdict !== "worse" && m.size_bytes !== null).sort((a, b) => (a.size_bytes ?? 0) - (b.size_bytes ?? 0));
  const lightest = fine[0] && reference?.size_bytes && fine[0].size_bytes !== null && fine[0].size_bytes < reference.size_bytes ? fine[0] : null;
  return {
    losses: others.filter((m) => m.verdict === "worse"),
    lightest,
    saved: lightest && reference?.size_bytes && lightest.size_bytes !== null ? reference.size_bytes - lightest.size_bytes : null,
  };
}

/** The chosen models that are one model in different quantizations: those that share a family, each in a quantization of its own. Empty when there are fewer than two. */
export function chosenQuantizations(chosen: string[], local: Pick<LocalChoice, "key" | "family" | "quant" | "label">[]): { family: string; label: string; keys: string[] } | null {
  const groups = new Map<string, { label: string; keys: string[]; quants: Set<string> }>();
  for (const key of chosen) {
    const model = local.find((m) => m.key === key);
    if (!model?.family) continue;
    const group = groups.get(model.family) ?? { label: model.label, keys: [], quants: new Set<string>() };
    group.keys.push(key);
    group.quants.add(model.quant ?? key);
    groups.set(model.family, group);
  }
  const best = [...groups.entries()].filter(([, g]) => g.quants.size >= 2).sort((a, b) => b[1].keys.length - a[1].keys.length)[0];
  return best ? { family: best[0], label: best[1].label, keys: best[1].keys } : null;
}

/** Whether more repeats would help, given how the run was made. null when nothing needs saying. */
export function repeatAdvice(settings: QuantComparison["settings"]): "greedy" | "few" | null {
  const temperature = settings.temperature ?? 0;
  const repeats = settings.repeats ?? 1;
  if (temperature === 0 && repeats > 1) return "greedy";
  if (temperature > 0 && repeats < 3) return "few";
  return null;
}

/** The background of a cell that holds a difference: red for a loss, green for a gain, deeper the larger (full at 25 points). */
export function deltaTint(delta: number | null): string {
  if (delta === null || Math.abs(delta) < 0.5) return "transparent";
  const share = Math.min(1, Math.abs(delta) / 25);
  return `color-mix(in oklch, var(--${delta < 0 ? "bad" : "ok"}) ${Math.round(8 + 32 * share)}%, transparent)`;
}

// -- the radar ------------------------------------------------------------------------------------

/** The point of an axis of a radar: ``index`` of ``count`` axes, ``value`` out of 100, around a centre. */
export function radarPoint(index: number, count: number, value: number, cx: number, cy: number, radius: number): [number, number] {
  const angle = -Math.PI / 2 + (2 * Math.PI * index) / count;
  const r = (radius * Math.max(0, Math.min(100, value))) / 100;
  return [cx + r * Math.cos(angle), cy + r * Math.sin(angle)];
}

/** The axes worth drawing: the ones some model has a score on. A radar with fewer than three axes is no radar, so then all of them. */
export function usedAreas(models: Pick<ModelReport, "areas">[], areas: string[]): string[] {
  const used = areas.filter((area) => models.some((m) => m.areas[area] !== null && m.areas[area] !== undefined));
  return used.length >= 3 ? used : areas;
}

/** The model's strongest area, with its score, for the line under its name. */
export function strongestArea(model: Pick<ModelReport, "areas">): { area: string; score: number } | null {
  let best: { area: string; score: number } | null = null;
  for (const [area, score] of Object.entries(model.areas)) {
    if (score !== null && (best === null || score > best.score)) best = { area, score };
  }
  return best;
}

// -- the request ------------------------------------------------------------------------------------

export const DEFAULT_REQUEST: RunRequest = {
  models: [],
  seed: 42,
  preset: "standard",
  categories: null,
  repeats: 1,
  temperature: 0,
  top_p: null,
  top_k: null,
  repeat_penalty: 1,
  sampling_seed: null,
  max_tokens: 2048,
  context: null,
  timeout_s: 600,
  title: "",
  confirm_unload: false,
  confirm_cloud: false,
};

/** Questions a pack asks in a category. */
export function perCategory(info: CategoryInfo, preset: Preset): number {
  return info.counts[preset === "quick" ? 0 : preset === "standard" ? 1 : 2];
}

/** How many questions a choice of pack and categories comes to. */
export function questionCount(categories: CategoryInfo[], preset: Preset, chosen: string[] | null): number {
  return categories.filter((c) => chosen === null || chosen.includes(c.id)).reduce((sum, c) => sum + perCategory(c, preset), 0);
}

/** A model that thinks before it answers needs room to do it: the answer is cut off if it runs out of tokens. */
export const THINKING_MAX_TOKENS = 4096;

// -- what the core says while a run works -------------------------------------------------------------

/** A line for the run's log from a model event: a model loaded, unloaded, left out. null for what is not worth a line. */
export function modelEventLine(data: Record<string, unknown>, labelOf: (key: string) => string): string | null {
  const topic = String(data.topic ?? "");
  const phase = String(data.phase ?? "");
  const model = labelOf(String(data.to ?? data.model ?? ""));
  if (topic === "arena.model") {
    if (phase === "start") return t("{model}: its turn", { model });
    if (phase === "failed") return t("{model} could not be loaded: {message}", { model, message: String(data.message ?? "") });
    return null;
  }
  const context = data.context_length ? Number(data.context_length).toLocaleString(getLang()) : "?";
  if (phase === "loading") return t("Loading {model}, context {context}", { model, context });
  if (phase === "ready" && !data.cloud) return t("{model} loaded in {seconds} s, context {context}", { model, seconds: fmt(Number(data.load_seconds ?? 0), 1), context });
  if (phase === "unloaded") return t("{model} unloaded from memory", { model });
  if (phase === "saturated" || phase === "spilling") return t("{model} does not fit in graphics memory: part of it is in system RAM", { model });
  if (phase === "failed") return t("{model} could not be loaded: {message}", { model, message: String(data.message ?? "") });
  if (phase === "warning") return String(data.message ?? "") || null;
  return null;
}

/** The squares of a lane: one per question, in the order they are asked, with what each model got. */
export function laneCells(matrix: MatrixRow[], modelKey: string): (Status | null)[] {
  return matrix.map((row) => row.cells[modelKey]?.status ?? null);
}

/** The score of a model over what it has answered so far, for the lane of a run that is still working. */
export function scoreSoFar(matrix: MatrixRow[], modelKey: string): number | null {
  const values = matrix.flatMap((row) => {
    const cell = row.cells[modelKey];
    return cell && SCORED.includes(cell.status) && cell.score !== null ? [cell.score] : [];
  });
  return values.length ? (100 * values.reduce((a, b) => a + b, 0)) / values.length : null;
}
