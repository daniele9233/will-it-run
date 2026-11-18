// The sampling an image model wants for one kind of workflow (txt2img, edit, upscale).
//
// A model's `sampling` is tuned for its own workflow; `kind_sampling` replaces some of it for another kind.
// Qwen-Image UC generates at CFG 1, but an edit at CFG 1 ignores its instruction: its edits run at 2.5.

export interface SamplingModel {
  sampling: Record<string, unknown>;
  kind_sampling?: Record<string, Record<string, unknown>>;
}

export function samplingFor(model: SamplingModel | null | undefined, kind: string): Record<string, unknown> {
  if (!model) return {};
  return { ...model.sampling, ...(model.kind_sampling?.[kind] ?? {}) };
}

/** What a switch from one kind of workflow to another changes: only the values that differ, so the rest of what the
 * person set stays as it is. */
export function samplingChanges(model: SamplingModel | null | undefined, from: string, to: string): Record<string, unknown> {
  const before = samplingFor(model, from);
  const after = samplingFor(model, to);
  return Object.fromEntries(Object.entries(after).filter(([key, value]) => before[key] !== value));
}
