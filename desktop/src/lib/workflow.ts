// Which model each agent of a workflow uses.
//
// The definition stores models in slots and points agents at a slot, so agents that use
// the same model share one loaded instance. The editor hides that: the user picks a
// model for an agent, and these helpers keep the slots consistent underneath.

import { sizeLabel } from "./format";
import type { InstalledModel, ModelSlot, WorkflowDef } from "./types";

export function emptySlot(label = ""): ModelSlot {
  return {
    label,
    kind: "",
    model_key: "",
    repo: null,
    quant: null,
    context_length: 16384,
    loading_policy: "load_on_demand",
    auto_tune: false,
    vram_gb: null,
    purpose: "",
    wide_model_key: "",
    wide_context_length: null,
  };
}

export function modelLabel(model: InstalledModel): string {
  return [model.display_name, model.quant, model.size_bytes ? sizeLabel(model.size_bytes) : null].filter(Boolean).join(" · ");
}

/** Installed model key the agent runs on, or "" when none is chosen yet. */
export function agentModelKey(def: WorkflowDef, agentId: string): string {
  const agent = def.agents.find((a) => a.id === agentId);
  return agent ? (def.models[agent.model]?.model_key ?? "") : "";
}

/** Distinct installed models the workflow uses. */
export function distinctModels(def: WorkflowDef): string[] {
  return [...new Set(def.agents.map((a) => def.models[a.model]?.model_key ?? "").filter(Boolean))];
}

/** Agents whose slot has no installed model yet. */
export function agentsWithoutModel(def: WorkflowDef): string[] {
  return def.agents.filter((a) => !def.models[a.model]?.model_key).map((a) => a.id);
}

function freshSlotId(models: Record<string, ModelSlot>): string {
  let n = Object.keys(models).length + 1;
  while (models[`model_${n}`]) n++;
  return `model_${n}`;
}

/**
 * Give the listed agents the installed model `model`.
 *
 * Agents end up on the slot that already holds that model when there is one. Otherwise
 * the slot they are on takes the model if nobody else uses it, and a new slot is made
 * if somebody does, so no other agent's model changes. Slots left empty are removed.
 */
export function assignModel(def: WorkflowDef, agentIds: string[], model: InstalledModel): WorkflowDef {
  const moving = new Set(agentIds);
  const models: Record<string, ModelSlot> = { ...def.models };
  let agents = def.agents;
  let target = Object.keys(models).find((id) => models[id]?.model_key === model.model_key);
  for (const agent of def.agents) {
    if (!moving.has(agent.id) || agent.model === target) continue;
    if (!target) {
      const current = models[agent.model];
      const sharedWithOthers = def.agents.some((a) => a.model === agent.model && !moving.has(a.id));
      // A model the user picks starts on automatic: context and memory are worked out for this PC.
      const taken = { model_key: model.model_key, repo: model.repo, quant: model.quant, vram_gb: null, auto_tune: true };
      if (current && !sharedWithOthers) {
        target = agent.model;
        models[target] = { ...current, ...taken };
      } else {
        target = freshSlotId(models);
        models[target] = { ...(current ?? emptySlot()), ...taken, label: model.display_name, purpose: "", wide_model_key: "", wide_context_length: null };
      }
    }
    const slot = target;
    agents = agents.map((a) => (a.id === agent.id ? { ...a, model: slot } : a));
  }
  const used = new Set(agents.map((a) => a.model));
  return { ...def, agents, models: Object.fromEntries(Object.entries(models).filter(([id]) => used.has(id))) };
}
