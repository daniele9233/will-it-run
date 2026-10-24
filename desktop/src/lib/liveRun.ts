// What a run's events say about its graph: which nodes ran, which is working now, and
// which arrows the work has travelled along.

import type { EdgeDef, RunEvent, WorkflowDef } from "./types";

export const START = "START";
export const END = "END";

export type NodeState = "waiting" | "working" | "done" | "failed" | "stopped";

export interface NodeLive {
  state: NodeState;
  /** Times the node has started: more than one means a loop sent work back to it. */
  runs: number;
  seconds: number | null;
  tokensPerSecond: number | null;
}

export interface GraphLive {
  nodes: Record<string, NodeLive>;
  /** Arrows the run has followed, as "from->to". */
  taken: Set<string>;
  /** Arrows into the nodes working right now. */
  active: Set<string>;
  working: string[];
  stepsDone: number;
  /** Model loads that got less context than asked, plus conversations shortened to fit. */
  shortOnContext: number;
}

export const edgeKey = (from: string, to: string): string => `${from}->${to}`;

export function sourcesOf(edge: EdgeDef): string[] {
  return Array.isArray(edge.from) ? edge.from : [edge.from];
}

interface Incoming {
  sources: string[];
  join: boolean;
}

function incomingOf(def: WorkflowDef): Map<string, Incoming[]> {
  const map = new Map<string, Incoming[]>();
  const add = (target: string, entry: Incoming) => map.set(target, [...(map.get(target) ?? []), entry]);
  for (const edge of def.edges) {
    const sources = sourcesOf(edge);
    add(edge.to, { sources, join: sources.length > 1 });
    if (edge.on_exhausted) add(edge.on_exhausted, { sources, join: false });
  }
  for (const approval of def.human_approval) {
    if (approval.on_reject && approval.on_reject !== END) add(approval.on_reject, { sources: [approval.id], join: false });
  }
  return map;
}

/**
 * Read the events of a run, in order, into the state of its graph.
 * `running` is false once the run has ended: nothing is "working" any more.
 */
export function deriveGraph(def: WorkflowDef | null, events: RunEvent[], running: boolean, status: string): GraphLive {
  const out: GraphLive = { nodes: {}, taken: new Set(), active: new Set(), working: [], stepsDone: 0, shortOnContext: 0 };
  if (!def) return out;
  const incoming = incomingOf(def);
  const completed: string[] = [];
  const open = new Set<string>();
  const lastInto = new Map<string, string[]>();
  const node = (id: string): NodeLive => (out.nodes[id] ??= { state: "waiting", runs: 0, seconds: null, tokensPerSecond: null });

  const enter = (id: string) => {
    const entry = node(id);
    entry.runs += 1;
    entry.state = "working";
    open.add(id);
    const options = incoming.get(id) ?? [];
    let keys: string[] = [];
    const join = options.find((o) => o.join && o.sources.every((s) => completed.includes(s)));
    if (join) {
      keys = join.sources.map((s) => edgeKey(s, id));
    } else {
      const candidates = new Set(options.filter((o) => !o.join).flatMap((o) => o.sources));
      // The node that finished most recently among those with an arrow to this one.
      const from = [...completed].reverse().find((s) => candidates.has(s)) ?? (candidates.has(START) ? START : undefined);
      if (from) keys = [edgeKey(from, id)];
    }
    for (const key of keys) out.taken.add(key);
    lastInto.set(id, keys);
  };
  const leave = (id: string, state: NodeState) => {
    node(id).state = state;
    open.delete(id);
    completed.push(id);
  };

  for (const event of events) {
    const p = event.payload;
    const id = typeof p.node === "string" ? p.node : "";
    switch (event.type) {
      case "node.started":
        enter(id);
        break;
      case "node.completed": {
        const output = (p.output ?? {}) as Record<string, unknown>;
        leave(id, p.kind === "check" && output.passed === false ? "stopped" : "done");
        const entry = node(id);
        entry.seconds = typeof p.seconds === "number" ? p.seconds : null;
        entry.tokensPerSecond = typeof p.tokens_per_second === "number" ? p.tokens_per_second : null;
        out.stepsDone += 1;
        break;
      }
      case "node.failed":
        leave(id, "failed");
        break;
      case "approval.requested":
        if (p.kind === "gate" && id) enter(id);
        break;
      case "approval.decided":
        if (p.kind === "gate" && id) leave(id, p.approved ? "done" : "stopped");
        break;
      case "model.switch":
        if (p.phase === "spilling") out.shortOnContext += 1;
        if (p.phase === "ready") {
          // Short means less than the request needed. Less than the comfortable default is not: it was enough.
          const wanted = typeof p.needed_context === "number" ? p.needed_context : p.requested_context;
          if (p.saturated === true || (typeof p.context_length === "number" && typeof wanted === "number" && p.context_length < wanted)) out.shortOnContext += 1;
        }
        break;
      case "node.trimmed":
        out.shortOnContext += 1;
        break;
      default:
        break;
    }
  }

  if (running) {
    out.working = [...open];
    for (const id of open) for (const key of lastInto.get(id) ?? []) out.active.add(key);
  } else {
    // Whatever was still working when the run ended did not finish.
    for (const id of open) node(id).state = status === "failed" ? "failed" : "waiting";
    const last = completed[completed.length - 1];
    if (status === "completed" && last && def.edges.some((e) => e.to === END && sourcesOf(e).includes(last))) out.taken.add(edgeKey(last, END));
  }
  return out;
}

/** Seconds one dash cycle of a flowing arrow takes: faster the more tokens per second. */
export function flowSeconds(tokensPerSecond: number | null): number {
  if (!tokensPerSecond || tokensPerSecond <= 0) return 2.4; // loading, or reading the prompt
  return Math.min(2.4, Math.max(0.2, 14 / tokensPerSecond));
}
