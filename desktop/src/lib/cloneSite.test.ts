import { describe, expect, it } from "vitest";

import { activity, answerOf, buildOf, cleanUrl, modelName, nodeStates, prepSteps, questions, waitingForApproval, type CloneRun } from "./cloneSite";

function run(events: CloneRun["events"], extra: Partial<CloneRun> = {}): CloneRun {
  return { id: "r1", status: "running", error: null, task: "", started_at: "", finished_at: null, events, outputs: {}, pending_approvals: [], ...extra };
}
const ev = (seq: number, type: string, payload: Record<string, unknown>) => ({ seq, type, payload });

describe("cleanUrl", () => {
  it("adds https and refuses what is not a website", () => {
    expect(cleanUrl("example.com")).toBe("https://example.com/");
    expect(cleanUrl("http://localhost:3000/a")).toBe("http://localhost:3000/a");
    expect(cleanUrl("ftp://example.com")).toBeNull();
    expect(cleanUrl("hello")).toBeNull();
    expect(cleanUrl("  ")).toBeNull();
  });
});

describe("nodeStates", () => {
  it("follows the events and counts the rounds of the agent", () => {
    const states = nodeStates(
      run([
        ev(1, "node.started", { node: "prepare", kind: "check" }),
        ev(2, "node.completed", { node: "prepare", output: { passed: true }, seconds: 4.4 }),
        ev(3, "node.started", { node: "cloner" }),
        ev(4, "node.completed", { node: "cloner", seconds: 900 }),
        ev(5, "node.started", { node: "final" }),
        ev(6, "node.completed", { node: "final", output: { passed: false } }),
        ev(7, "node.started", { node: "cloner" }),
      ]),
    );
    expect(states.prepare).toMatchObject({ state: "done", runs: 1, seconds: 4 });
    expect(states.final?.state).toBe("failed");
    expect(states.cloner).toMatchObject({ state: "working", runs: 2 });
  });

  it("marks what was working when the run ended as stopped", () => {
    const states = nodeStates(run([ev(1, "node.started", { node: "cloner" })], { status: "cancelled" }));
    expect(states.cloner?.state).toBe("stopped");
    expect(states.final?.state).toBe("waiting");
  });

  it("ignores nodes it does not know", () => {
    expect(nodeStates(run([ev(1, "node.started", { node: "qa" })])).qa).toBeUndefined();
  });
});

describe("prepSteps", () => {
  it("keeps the latest state of each step, in the order they started", () => {
    const steps = prepSteps(
      run([
        ev(1, "clone.step", { step: "tools", state: "running" }),
        ev(2, "clone.step", { step: "tools", state: "done", detail: "Node v24" }),
        ev(3, "clone.step", { step: "install", state: "running", detail: "added 400 packages" }),
      ]),
    );
    expect(steps).toEqual([
      { step: "tools", state: "done", detail: "Node v24" },
      { step: "install", state: "running", detail: "added 400 packages" },
    ]);
  });
});

describe("questions", () => {
  it("lists what the agent asked, with the answer once the person gave it", () => {
    const asked = questions(
      run([
        ev(1, "clone.ask", { state: "waiting", question: "Which pages?", options: ["Only the home page"] }),
        ev(2, "clone.ask", { state: "answered", question: "Which pages?", answer: "Only the home page" }),
        ev(3, "clone.ask", { state: "waiting", question: "Keep the dark theme?", options: [] }),
      ]),
    );
    expect(asked).toEqual([
      { question: "Which pages?", answer: "Only the home page" },
      { question: "Keep the dark theme?", answer: null },
    ]);
    expect(questions(null)).toEqual([]);
  });
});

describe("activity", () => {
  it("counts reads, writes, commands and browser calls, and remembers the last tool", () => {
    const done = activity(
      run([
        ev(1, "tool.call", { tool: "browser_navigate", status: "success" }),
        ev(2, "tool.call", { tool: "look", status: "success" }),
        ev(3, "tool.call", { tool: "read_file", status: "success", path: "a.md" }),
        ev(4, "tool.call", { tool: "run_command", status: "success" }),
        ev(5, "tool.call", { tool: "write_file", status: "success", path: "src/app/page.tsx" }),
        ev(6, "tool.call", { tool: "edit_file", status: "denied", path: "x" }),
      ]),
    );
    expect(done).toMatchObject({ reads: 1, changes: 1, browser: 2, commands: 1 });
    expect(done.last).toEqual({ tool: "write_file", path: "src/app/page.tsx" });
  });
});

describe("outputs", () => {
  it("reads the agent's report and the final check", () => {
    const r = run([], {
      outputs: {
        cloner: { text: "Cloned the home page." },
        final: { passed: false, summary: "npm run check failed", log: ["error TS2304"] },
      },
    });
    expect(answerOf(r, "cloner")).toBe("Cloned the home page.");
    expect(answerOf(r, "missing")).toBe("");
    expect(buildOf(r)).toEqual({ passed: false, summary: "npm run check failed", log: ["error TS2304"] });
    expect(buildOf(run([]))).toBeNull();
  });
});

describe("helpers", () => {
  it("names a model without its folder or provider prefix", () => {
    expect(modelName("cloud:deepseek/deepseek-v4-pro")).toBe("deepseek-v4-pro");
    expect(modelName("google/gemma-4-12b-qat")).toBe("gemma-4-12b-qat");
  });

  it("waits for approval only while the run works", () => {
    const approval = { id: "a", run_id: "r1", node_id: "cloner", kind: "tool" as const, prompt: "", payload: {}, requested_at: "" };
    expect(waitingForApproval(run([], { pending_approvals: [approval] }))).toBe(true);
    expect(waitingForApproval(run([], { pending_approvals: [approval], status: "cancelled" }))).toBe(false);
    expect(waitingForApproval(null)).toBe(false);
  });
});
