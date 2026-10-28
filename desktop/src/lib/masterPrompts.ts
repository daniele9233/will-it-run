import { getLang, t } from "./i18n";

export type PromptGroup = "pages" | "gallery" | "motion" | "surfaces" | "three-d" | "process" | "after-copy";

export interface PromptProof {
  builder: string;
  date: string;
  build_ok: boolean;
  widths: number[];
  dna_match: number | null;
  notes: string;
}

export interface PromptSummary {
  id: string;
  group: PromptGroup;
  title: string;
  summary: string;
  tags: string[];
  size: "S" | "M" | "L";
  skills: string[];
  libs: Record<string, string>;
  proof: PromptProof | null;
}

export interface PromptDetail extends PromptSummary {
  text: string;
}

export const GROUPS: { id: PromptGroup; label: () => string }[] = [
  { id: "pages", label: () => t("Pages") },
  { id: "gallery", label: () => t("Galleries") },
  { id: "motion", label: () => t("Motion") },
  { id: "surfaces", label: () => t("Surfaces") },
  { id: "three-d", label: () => t("3D") },
  { id: "process", label: () => t("Process") },
  { id: "after-copy", label: () => t("After the copy") },
];

/** The prompts of a group (or all of them) whose id, title, summary or tags contain every word typed, ignoring case. */
export function filterPrompts(list: PromptSummary[], group: PromptGroup | "all", query: string): PromptSummary[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  return list.filter((p) => {
    if (group !== "all" && p.group !== group) return false;
    const haystack = [p.id, p.title, p.summary, ...p.tags].join(" ").toLowerCase();
    return words.every((w) => haystack.includes(w));
  });
}

export function proofLabel(proof: PromptProof | null): string {
  if (!proof) return t("Not tested yet");
  return t("Tested with {builder} on {date}", { builder: proof.builder, date: proof.date });
}

/** Put the text on the clipboard; false when the browser refuses. */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

/** One prompt the composer chose or was told to choose, and the rule's reason in the language of the interface. */
export interface Recommendation {
  id: string;
  why: string;
}

/** What `POST /api/clone/{id}/compose` returns: the prompt, and why each part is in it. */
export interface Composed {
  prompt: string;
  used: string[];
  reasons: Recommendation[];
  dropped: string[];
  tokens: number;
  over_budget: boolean;
  recommended_empty: boolean;
}

/** What `POST /api/clone/{id}/prompts/save` says about the skills the saved prompts cite (header names). */
export interface SkillsCopy {
  copied: string[];
  skipped: string[];
  missing: string[];
  failed: string[];
}

/** The ids picked after one click on a checkbox. `null` (nothing touched yet) starts from the recommendation. */
export function togglePick(picked: string[] | null, id: string, recommended: string[]): string[] {
  const now = picked ?? recommended;
  return now.includes(id) ? now.filter((p) => p !== id) : [...now, id];
}

export function tokenLabel(tokens: number): string {
  return t("~{n} tokens", { n: tokens.toLocaleString(getLang()) });
}

/** Why a study stopped before it wrote its Site DNA: what the study step said, else the run's own error, else a plain sentence. */
export function studyFailure(run: { error: string | null; outputs: Record<string, Record<string, unknown>> } | null): string {
  const out = run?.outputs.dna;
  if (out && out.passed === false && typeof out.summary === "string" && out.summary) return out.summary;
  return run?.error || t("The study did not finish.");
}

/** The ids of the skill packs that hold any of the skills named in `missing` (header names), each once. */
export function packsFor(missing: string[], packs: { id: string; skills: { name: string }[] }[]): string[] {
  return packs.filter((p) => p.skills.some((s) => missing.includes(s.name))).map((p) => p.id);
}
