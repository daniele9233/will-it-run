import { describe, expect, it } from "vitest";

import { deriveGraph, edgeKey, flowSeconds } from "./liveRun";
import type { AgentDef, RunEvent, WorkflowDef } from "./types";

function agent(id: string): AgentDef {
  return { id, role: id, goal: "", responsibilities: [], model: "m", system_prompt: "", tools: [], input: "", output_schema: "GenericOutput", loading_policy: "shared_instance", max_retries: 2, escalation: "fail", temperature: 0.2, max_tool_rounds: 6 };
}

const edge = (from: string | string[], to: string, extra: Partial<WorkflowDef["edges"][number]> = {}) => ({ from, to, when: null, label: null, max_loops: null, on_exhausted: null, ...extra });

/** lead -> plan -> approve -> dev <-> test -> END, with a three-way fan-out and join on the side. */
const def: WorkflowDef = {
  workflow: { id: "w", name: "W", description: "", version: 1, max_steps: 40, specializations: [] },
  models: {},
  agents: ["lead", "plan", "dev", "test", "a", "b", "merge"].map(agent),
  checks: [{ id: "gate", kind: "verify_claims", label: "Gate", params: {} }],
  human_approval: [{ id: "approve", prompt: "ok?", show: "plan", on_reject: "plan", max_rejections: 2 }],
  edges: [
    edge("START", "lead"), edge("lead", "plan"), edge("plan", "approve"), edge("approve", "dev"), edge("dev", "test"),
    edge("test", "dev", { when: "passed == false", max_loops: 3 }), edge("test", "a"), edge("test", "b"), edge(["a", "b"], "merge"),
    edge("merge", "gate"), edge("gate", "END"),
  ],
  conditions: {},
  shared_state: {},
  resource_plan: {},
  layout: {},
};

let seq = 0;
const ev = (type: string, payload: Record<string, unknown>): RunEvent => ({ seq: ++seq, type, payload });
const started = (node: string) => ev("node.started", { node });
const completed = (node: string, extra: Record<string, unknown> = {}) => ev("node.completed", { node, seconds: 2, tokens_per_second: 40, ...extra });

describe("deriveGraph", () => {
  it("follows the arrows the run took and marks the one into the working node", () => {
    const events = [started("lead"), completed("lead"), started("plan")];
    const live = deriveGraph(def, events, true, "running");
    expect([...live.taken]).toEqual([edgeKey("START", "lead"), edgeKey("lead", "plan")]);
    expect([...live.active]).toEqual([edgeKey("lead", "plan")]);
    expect(live.working).toEqual(["plan"]);
    expect(live.nodes.lead).toMatchObject({ state: "done", runs: 1, tokensPerSecond: 40 });
    expect(live.nodes.plan?.state).toBe("working");
    expect(live.nodes.dev).toBeUndefined();
    expect(live.stepsDone).toBe(1);
  });

  it("counts a loop as another run of the node and travels the arrow back", () => {
    const events = [
      started("lead"), completed("lead"), started("plan"), completed("plan"),
      ev("approval.requested", { kind: "gate", node: "approve" }), ev("approval.decided", { kind: "gate", node: "approve", approved: true }),
      started("dev"), completed("dev"), started("test"), completed("test"), started("dev"),
    ];
    const live = deriveGraph(def, events, true, "running");
    expect(live.nodes.dev).toMatchObject({ state: "working", runs: 2 });
    expect(live.taken.has(edgeKey("approve", "dev"))).toBe(true);
    expect(live.taken.has(edgeKey("test", "dev"))).toBe(true);
    // Only the arrow it came back along is flowing now, not the one it first arrived by.
    expect([...live.active]).toEqual([edgeKey("test", "dev")]);
    expect(live.nodes.approve?.state).toBe("done");
  });

  it("lights every arrow of a join, and both branches while they run together", () => {
    const upTo = [started("lead"), completed("lead"), started("plan"), completed("plan"), started("dev"), completed("dev"), started("test"), completed("test")];
    const parallel = deriveGraph(def, [...upTo, started("a"), started("b")], true, "running");
    expect(parallel.working.sort()).toEqual(["a", "b"]);
    expect([...parallel.active].sort()).toEqual([edgeKey("test", "a"), edgeKey("test", "b")]);
    const joined = deriveGraph(def, [...upTo, started("a"), started("b"), completed("a"), completed("b"), started("merge")], true, "running");
    expect([...joined.active].sort()).toEqual([edgeKey("a", "merge"), edgeKey("b", "merge")]);
  });

  it("shows a check that did not pass as stopped, and reaches the end only on a completed run", () => {
    const upTo = [started("merge"), completed("merge"), started("gate")];
    const blocked = deriveGraph(def, [...upTo, completed("gate", { kind: "check", output: { passed: false } })], false, "blocked");
    expect(blocked.nodes.gate?.state).toBe("stopped");
    expect(blocked.taken.has(edgeKey("gate", "END"))).toBe(false);
    const done = deriveGraph(def, [...upTo, completed("gate", { kind: "check", output: { passed: true } })], false, "completed");
    expect(done.nodes.gate?.state).toBe("done");
    expect(done.taken.has(edgeKey("gate", "END"))).toBe(true);
    expect(done.working).toEqual([]);
    expect(done.active.size).toBe(0);
  });

  it("leaves nothing working once the run has ended, and counts what ran short of context", () => {
    const events = [
      ev("model.switch", { phase: "ready", context_length: 4096, requested_context: 16384 }),
      ev("model.switch", { phase: "ready", context_length: 16384, requested_context: 16384 }),
      // Less than the comfortable default, but more than the request needed: not short.
      ev("model.switch", { phase: "ready", context_length: 7168, requested_context: 16384, needed_context: 2400 }),
      started("lead"), ev("node.trimmed", { node: "lead" }),
    ];
    const cancelled = deriveGraph(def, events, false, "cancelled");
    expect(cancelled.nodes.lead?.state).toBe("waiting");
    expect(cancelled.shortOnContext).toBe(2);
    expect(deriveGraph(def, events, false, "failed").nodes.lead?.state).toBe("failed");
    expect(deriveGraph(null, events, true, "running").taken.size).toBe(0);
  });
});

describe("flowSeconds", () => {
  it("makes the arrows run faster the faster the model writes, within limits", () => {
    expect(flowSeconds(null)).toBe(2.4);
    expect(flowSeconds(0)).toBe(2.4);
    expect(flowSeconds(7)).toBe(2);
    expect(flowSeconds(40)).toBeCloseTo(0.35);
    expect(flowSeconds(400)).toBe(0.2);
    expect(flowSeconds(100)).toBeLessThan(flowSeconds(20));
  });
});
