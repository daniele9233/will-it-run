import { describe, expect, it } from "vitest";

import type { AgentDef, InstalledModel, WorkflowDef } from "./types";
import { agentModelKey, agentsWithoutModel, assignModel, distinctModels, emptySlot } from "./workflow";

function agent(id: string, model: string): AgentDef {
  return { id, role: id, goal: "", responsibilities: [], model, system_prompt: "", tools: [], input: "", output_schema: "GenericOutput", loading_policy: "shared_instance", max_retries: 2, escalation: "fail", temperature: 0.2, max_tool_rounds: 6 };
}

function installed(key: string): InstalledModel {
  return { model_key: key, display_name: key.toUpperCase(), type: "llm", repo: `org/${key}`, filename: `${key}.gguf`, quant: "Q4_K_M", size_bytes: 8e9, params_string: null, vision: false, tool_use: true, max_context_length: 32768, loaded_instances: [] };
}

function workflow(): WorkflowDef {
  return {
    workflow: { id: "w", name: "W", description: "", version: 1, max_steps: 40, specializations: [] },
    models: { reasoning: { ...emptySlot("Reasoning"), context_length: 32768 }, coding: emptySlot("Coding") },
    agents: [agent("lead", "reasoning"), agent("planner", "reasoning"), agent("dev", "coding"), agent("tester", "coding")],
    human_approval: [],
    edges: [],
    conditions: {},
    shared_state: {},
    resource_plan: {},
    layout: {},
  };
}

describe("assignModel", () => {
  it("gives one agent its own model without touching the agents that shared its slot", () => {
    const next = assignModel(workflow(), ["lead"], installed("big"));
    expect(agentModelKey(next, "lead")).toBe("big");
    expect(agentModelKey(next, "planner")).toBe("");
    expect(next.agents.find((a) => a.id === "planner")?.model).toBe("reasoning");
    // The new slot keeps the settings of the one the agent came from.
    expect(next.models[next.agents[0]!.model]?.context_length).toBe(32768);
  });

  it("reuses the slot when every agent on it moves", () => {
    const next = assignModel(workflow(), ["dev", "tester"], installed("coder"));
    expect(Object.keys(next.models).sort()).toEqual(["coding", "reasoning"]);
    expect(next.models.coding?.model_key).toBe("coder");
    expect(next.models.coding?.repo).toBe("org/coder");
    // A model picked by hand starts on automatic context; untouched slots stay as they were.
    expect(next.models.coding?.auto_tune).toBe(true);
    expect(next.models.reasoning?.auto_tune).toBe(false);
  });

  it("puts agents with the same model on one slot and drops slots nobody uses", () => {
    let next = assignModel(workflow(), ["lead", "planner", "dev", "tester"], installed("one"));
    expect(distinctModels(next)).toEqual(["one"]);
    expect(Object.keys(next.models)).toHaveLength(1);
    expect(agentsWithoutModel(next)).toEqual([]);
    // Then a different model for each of the four: as many models as agents.
    for (const [id, key] of [["planner", "b"], ["dev", "c"], ["tester", "d"]] as const) next = assignModel(next, [id], installed(key));
    expect(distinctModels(next).sort()).toEqual(["b", "c", "d", "one"]);
    expect(Object.keys(next.models)).toHaveLength(4);
    // Going back to a model already in use joins its slot instead of making a fifth.
    next = assignModel(next, ["tester"], installed("c"));
    expect(Object.keys(next.models)).toHaveLength(3);
    expect(next.agents.find((a) => a.id === "tester")?.model).toBe(next.agents.find((a) => a.id === "dev")?.model);
  });

  it("reports the agents that still have no model", () => {
    const next = assignModel(workflow(), ["dev"], installed("coder"));
    expect(agentsWithoutModel(next)).toEqual(["lead", "planner", "tester"]);
  });
});
