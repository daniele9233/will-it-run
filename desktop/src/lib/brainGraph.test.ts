import { describe, expect, it } from "vitest";

import { LABEL_HEIGHT, LABEL_WIDTH, layoutGraph, neighbourhood, nodeTone, viaLabel } from "./brainGraph";
import type { BrainGraphData } from "./types";

const graph: BrainGraphData = {
  nodes: [
    { id: "m:1", type: "memory", kind: "error", label: "Tests failed", degree: 2 },
    { id: "m:2", type: "memory", kind: "solution", label: "Fix", degree: 1 },
    { id: "e:1", type: "entity", kind: "file", label: "src/auth.py", degree: 2 },
    { id: "e:2", type: "entity", kind: "file", label: "src/db.py", degree: 1 },
    { id: "m:3", type: "memory", kind: "note", label: "Unrelated", degree: 0 },
  ],
  edges: [
    { source: "m:1", target: "m:2", relation: "fixed_by", origin: "memory" },
    { source: "m:1", target: "e:1", relation: "about", origin: "memory" },
    { source: "e:1", target: "e:2", relation: "imports", origin: "codemap" },
    { source: "m:9", target: "e:1", relation: "about", origin: "memory" },
  ],
  total_nodes: 5,
  total_edges: 4,
};

function distance(a: { x: number; y: number }, b: { x: number; y: number }): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

describe("the knowledge graph", () => {
  it("is drawn the same way every time", () => {
    expect(layoutGraph(graph)).toEqual(layoutGraph(graph));
  });

  it("gives every node a place of its own and keeps linked nodes closer than unlinked ones", () => {
    const at = layoutGraph(graph);
    const ids = graph.nodes.map((n) => n.id);
    expect(Object.keys(at).sort()).toEqual([...ids].sort());
    for (const a of ids) {
      for (const b of ids) {
        // No two labels on top of each other.
        if (a < b) expect(Math.abs(at[a]!.x - at[b]!.x) >= LABEL_WIDTH || Math.abs(at[a]!.y - at[b]!.y) >= LABEL_HEIGHT).toBe(true);
      }
    }
    expect(distance(at["m:1"]!, at["m:2"]!)).toBeLessThan(distance(at["m:2"]!, at["e:2"]!));
    for (const point of Object.values(at)) expect(Number.isFinite(point.x) && Number.isFinite(point.y)).toBe(true);
  });

  it("handles an empty graph and a single node", () => {
    expect(layoutGraph({ nodes: [], edges: [], total_nodes: 0, total_edges: 0 })).toEqual({});
    const one = layoutGraph({ nodes: [graph.nodes[0]!], edges: [], total_nodes: 1, total_edges: 0 });
    expect(Number.isFinite(one["m:1"]!.x)).toBe(true);
  });

  it("finds what is one step away", () => {
    expect([...neighbourhood(graph, "e:1")].sort()).toEqual(["e:1", "e:2", "m:1", "m:9"]);
    expect([...neighbourhood(graph, "m:3")]).toEqual(["m:3"]);
  });

  it("names tones and search reasons", () => {
    expect(nodeTone("memory", "error")).toBe("bad");
    expect(nodeTone("memory", "solution")).toBe("ok");
    expect(nodeTone("entity", "file")).toBe("warn");
    expect(viaLabel("file src/auth.py")).toBe("file src/auth.py");
    expect(viaLabel("fixes #12")).toBe("fixes #12");
    expect(viaLabel("something else")).toBe("something else");
  });
});
