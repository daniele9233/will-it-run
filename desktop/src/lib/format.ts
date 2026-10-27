import { t } from "./i18n";
import type { FitCategory, FitResult } from "./types";

const GIB = 1024 ** 3;

export const UNKNOWN = t("Unknown");

export function gbFromBytes(bytes: number | null | undefined, digits = 1): string {
  if (bytes === null || bytes === undefined) return UNKNOWN;
  return `${(bytes / GIB).toFixed(digits)} GB`;
}

export function gbFromMb(mb: number | null | undefined, digits = 1): string {
  if (mb === null || mb === undefined) return UNKNOWN;
  return `${(mb / 1024).toFixed(digits)} GB`;
}

export function gb(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return UNKNOWN;
  return `${value.toFixed(digits)} GB`;
}

export function sizeLabel(bytes: number | null | undefined, estimate = false): string {
  if (bytes === null || bytes === undefined) return UNKNOWN;
  if (bytes < GIB) return `${estimate ? "~" : ""}${Math.round(bytes / 1024 ** 2)} MB`;
  return `${estimate ? "~" : ""}${(bytes / GIB).toFixed(1)} GB`;
}

export function compactNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return UNKNOWN;
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(value >= 10_000 ? 0 : 1)}k`;
  return String(value);
}

export function params(value: number | null | undefined): string {
  if (!value) return UNKNOWN;
  if (value < 1) return `${Math.round(value * 1000)}M`;
  return `${Number.isInteger(value) ? value : value.toFixed(1)}B`;
}

export function contextLabel(tokens: number | null | undefined): string {
  if (!tokens) return UNKNOWN;
  if (tokens >= 1_000_000) return `${(tokens / 1_048_576).toFixed(0)}M`;
  return `${Math.round(tokens / 1024)}K`;
}

export function tps(value: number | null | undefined): string {
  if (value === null || value === undefined) return t("Not measured");
  return `${value >= 100 ? value.toFixed(0) : value.toFixed(1)} tok/s`;
}

export function speedRange(low: number, high: number): string {
  const lo = Math.round(low);
  const hi = Math.round(high);
  return lo === hi ? `~${lo} tok/s` : `${lo}–${hi} tok/s`;
}

export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "";
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const seconds = Math.max(0, (Date.now() - then) / 1000);
  if (seconds < 60) return t("just now");
  if (seconds < 3600) return t("{n} min ago", { n: Math.floor(seconds / 60) });
  if (seconds < 86400) return t("{n} h ago", { n: Math.floor(seconds / 3600) });
  return new Date(iso).toLocaleDateString();
}

export type Tone = "ok" | "accent" | "warn" | "limited" | "bad" | "off";

/** Semantic tone for a fit category: green perfect, blue good, yellow warning, orange limited, red incompatible. */
export function fitTone(category: FitCategory): Tone {
  switch (category) {
    case "PERFECT":
    case "EXCELLENT":
      return "ok";
    case "GOOD":
      return "accent";
    case "ACCEPTABLE":
      return "warn";
    case "SLOW":
    case "MEMORY TIGHT":
      return "limited";
    case "NOT RECOMMENDED":
    case "IMPOSSIBLE":
      return "bad";
  }
}

/** Dashboard wording for how well a job runs on this PC. */
export function readinessWord(category: FitCategory | null): { word: string; tone: Tone } {
  if (category === null) return { word: t("Not available"), tone: "off" };
  switch (category) {
    case "PERFECT":
    case "EXCELLENT":
      return { word: t("Excellent"), tone: "ok" };
    case "GOOD":
      return { word: t("Good"), tone: "accent" };
    case "ACCEPTABLE":
      return { word: t("Usable"), tone: "warn" };
    case "SLOW":
    case "MEMORY TIGHT":
      return { word: t("Limited"), tone: "limited" };
    default:
      return { word: t("Not viable"), tone: "bad" };
  }
}

export function titleCase(text: string): string {
  return text.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/** "PERFETTO" -> "Perfetto": status codes shown inside running text. */
export function sentenceCase(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1).toLowerCase();
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

// -- labels for values the core sends as fixed English codes ---------------------

const FIT_LABELS: Record<FitCategory, string> = {
  PERFECT: t("PERFECT"),
  EXCELLENT: t("EXCELLENT"),
  GOOD: t("GOOD"),
  ACCEPTABLE: t("ACCEPTABLE"),
  SLOW: t("SLOW"),
  "MEMORY TIGHT": t("MEMORY TIGHT"),
  "NOT RECOMMENDED": t("NOT RECOMMENDED"),
  IMPOSSIBLE: t("IMPOSSIBLE"),
};

export function fitLabel(category: FitCategory): string {
  return FIT_LABELS[category] ?? category;
}

const CONFIDENCE_LABELS: Record<string, string> = {
  HIGH: t("HIGH"),
  MEDIUM: t("MEDIUM"),
  LOW: t("LOW"),
  UNKNOWN: t("UNKNOWN"),
  "NOT MEASURED": t("NOT MEASURED"),
};

export function confidenceLabel(value: string): string {
  return CONFIDENCE_LABELS[value] ?? value;
}

const CATEGORY_LABELS: Record<string, string> = {
  General: t("General"),
  Coding: t("Coding"),
  Reasoning: t("Reasoning"),
  Vision: t("Vision"),
  Writing: t("Writing"),
  Translation: t("Translation"),
  "Medical / Biomedical Research": t("Medical / Biomedical Research"),
  "Scientific Research": t("Scientific Research"),
  "Legal Analysis": t("Legal Analysis"),
  Embedding: t("Embedding"),
  Reranking: t("Reranking"),
  "Computer Use": t("Computer Use"),
  DevOps: t("DevOps"),
  Linux: t("Linux"),
};

export function categoryLabel(category: string): string {
  return CATEGORY_LABELS[category] ?? category;
}

/** Where a model's weights live, in words. */
export function placementLabel(fit: Pick<FitResult, "placement" | "gpu_offload_ratio">): string {
  switch (fit.placement) {
    case "gpu":
      return t("Fully on GPU");
    case "gpu_split":
      return t("Split across GPUs");
    case "partial_offload":
      return t("GPU + CPU ({n}% on GPU)", { n: Math.round(fit.gpu_offload_ratio * 100) });
    case "cpu":
      return t("CPU only");
    default:
      return t("Does not fit");
  }
}

/** Short placement tag for dense tables. */
export function placementShort(placement: string): string {
  switch (placement) {
    case "gpu":
    case "gpu_split":
      return "GPU";
    case "partial_offload":
      return "GPU + CPU";
    case "cpu":
      return "CPU";
    default:
      return "–";
  }
}

const POLICY_LABELS: Record<string, string> = {
  keep_loaded: t("keep loaded"),
  load_on_demand: t("load on demand"),
  unload_after_use: t("unload after use"),
  shared_instance: t("shared instance"),
  sequential: t("sequential"),
  parallel: t("parallel"),
};

/** A model slot's loading policy, in words. */
export function policyLabel(policy: string): string {
  return POLICY_LABELS[policy] ?? policy.replace(/_/g, " ");
}

/** A retry of an agent, in words. A turn LM Studio could not read is explained; anything else shows its own message. */
export function retryText(payload: Record<string, unknown>): string {
  const n = String(payload.attempt ?? "");
  if (payload.explanation) {
    return t("Attempt {n}: LM Studio could not read what the model wrote as a tool call or an answer, so the model was told what went wrong.", { n });
  }
  return t("Retry {n}: {reason}", { n, reason: String(payload.reason ?? "") });
}

/** The weight of a file on disk, the way a file manager says it. Empty when it is not known. */
export function bytesLabel(bytes: number | null | undefined): string {
  if (!bytes) return "";
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** How long something took. Empty when it is not known. */
export function secondsLabel(seconds: number | null | undefined): string {
  if (!seconds) return "";
  return seconds < 60 ? t("{n} s", { n: Math.round(seconds) }) : t("{n} min", { n: (seconds / 60).toFixed(1) });
}
