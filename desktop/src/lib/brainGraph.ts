// The knowledge graph of a project, laid out for the screen, and the words for what is in it.

import type { Tone } from "./format";
import { t } from "./i18n";
import type { BrainGraphData, MemoryKind } from "./types";

export interface Point {
  x: number;
  y: number;
}

export const KINDS: MemoryKind[] = ["architecture", "decision", "change", "error", "solution", "note", "session"];

/** How a kind of memory is named, alone and in a count, and the tone it is drawn in. */
export const KIND: Record<MemoryKind, { label: string; plural: string; tone: Tone }> = {
  architecture: { label: t("Architecture"), plural: t("Architecture"), tone: "accent" },
  decision: { label: t("Decision"), plural: t("Decisions"), tone: "accent" },
  change: { label: t("File change"), plural: t("File changes"), tone: "off" },
  error: { label: t("Error"), plural: t("Errors"), tone: "bad" },
  solution: { label: t("Solution"), plural: t("Solutions"), tone: "ok" },
  note: { label: t("Note"), plural: t("Notes"), tone: "off" },
  session: { label: t("Session"), plural: t("Sessions"), tone: "off" },
};

export function isMemoryKind(value: string): value is MemoryKind {
  return (KINDS as string[]).includes(value);
}

/** The tone of a node of the graph: a memory by its kind, a file or a topic in a neutral one. */
export function nodeTone(type: string, kind: string): Tone {
  if (type === "memory" && isMemoryKind(kind)) return kind === "change" || kind === "note" || kind === "session" ? "off" : KIND[kind].tone;
  return kind === "file" ? "warn" : "limited";
}

/** A reason the search gives for a result ("file src/a.py", "fixes #12"), in the language of the interface. */
export function viaLabel(via: string): string {
  const [head, ...rest] = via.split(" ");
  const tail = rest.join(" ");
  if (head === "file") return t("file {name}", { name: tail });
  if (head === "topic") return t("topic {name}", { name: tail });
  if (via.startsWith("fixes #")) return t("fixes #{n}", { n: via.slice(7) });
  if (via.startsWith("fixed by #")) return t("fixed by #{n}", { n: via.slice(10) });
  return via;
}

/** The room a node's label takes on screen, at most. */
export const LABEL_WIDTH = 214;
export const LABEL_HEIGHT = 36;

/**
 * Positions for the graph: connected nodes pull together, every node pushes the others away
 * (Fruchterman and Reingold's method). It starts from a circle in the order given and uses no
 * chance, so the same graph is always drawn the same way.
 */
export function layoutGraph(graph: BrainGraphData, iterations = 260): Record<string, Point> {
  const count = graph.nodes.length;
  const out: Record<string, Point> = {};
  if (count === 0) return out;
  const index = new Map(graph.nodes.map((node, i) => [node.id, i]));
  const radius = 80 + count * 16;
  const x = new Float64Array(count);
  const y = new Float64Array(count);
  for (let i = 0; i < count; i++) {
    const angle = (2 * Math.PI * i) / count;
    x[i] = Math.cos(angle) * radius;
    y[i] = Math.sin(angle) * radius;
  }
  const links: [number, number][] = [];
  for (const edge of graph.edges) {
    const a = index.get(edge.source);
    const b = index.get(edge.target);
    if (a !== undefined && b !== undefined && a !== b) links.push([a, b]);
  }
  // Labels are wide and short: nodes need more room sideways than up and down.
  const ideal = 150;
  const reach = ideal * 3;
  const dx = new Float64Array(count);
  const dy = new Float64Array(count);
  let heat = radius / 3;
  for (let step = 0; step < iterations; step++) {
    dx.fill(0);
    dy.fill(0);
    for (let i = 0; i < count; i++) {
      for (let j = i + 1; j < count; j++) {
        const vx = (x[i] as number) - (x[j] as number);
        const vy = (y[i] as number) - (y[j] as number);
        const distance = Math.sqrt(vx * vx + vy * vy) || 0.01;
        // Nodes far apart no longer push each other, or groups that share no link would fly off the screen.
        if (distance > reach) continue;
        const push = (ideal * ideal) / distance;
        const fx = (vx / distance) * push;
        const fy = (vy / distance) * push;
        dx[i] = (dx[i] as number) + fx;
        dy[i] = (dy[i] as number) + fy;
        dx[j] = (dx[j] as number) - fx;
        dy[j] = (dy[j] as number) - fy;
      }
    }
    for (const [a, b] of links) {
      const vx = (x[a] as number) - (x[b] as number);
      const vy = (y[a] as number) - (y[b] as number);
      const distance = Math.sqrt(vx * vx + vy * vy) || 0.01;
      const pull = (distance * distance) / ideal;
      const fx = (vx / distance) * pull;
      const fy = (vy / distance) * pull;
      dx[a] = (dx[a] as number) - fx;
      dy[a] = (dy[a] as number) - fy;
      dx[b] = (dx[b] as number) + fx;
      dy[b] = (dy[b] as number) + fy;
    }
    for (let i = 0; i < count; i++) {
      // A gentle pull to the middle keeps what is not connected from drifting off.
      const fx = (dx[i] as number) - (x[i] as number) * 0.9;
      const fy = (dy[i] as number) - (y[i] as number) * 0.9;
      const size = Math.sqrt(fx * fx + fy * fy) || 0.01;
      const move = Math.min(size, heat);
      x[i] = (x[i] as number) + (fx / size) * move;
      y[i] = (y[i] as number) + (fy / size) * move;
    }
    heat = Math.max(1, heat * 0.975);
  }
  for (let i = 0; i < count; i++) {
    x[i] = (x[i] as number) * 1.35;
    y[i] = (y[i] as number) * 0.75;
  }
  // A label is a wide, low box: two that still overlap are moved apart up and down.
  for (let pass = 0; pass < 40; pass++) {
    let moved = false;
    for (let i = 0; i < count; i++) {
      for (let j = i + 1; j < count; j++) {
        const overlap = LABEL_HEIGHT - Math.abs((y[i] as number) - (y[j] as number));
        if (Math.abs((x[i] as number) - (x[j] as number)) >= LABEL_WIDTH || overlap <= 0) continue;
        const up = (y[i] as number) <= (y[j] as number) ? i : j;
        const down = up === i ? j : i;
        y[up] = (y[up] as number) - overlap / 2 - 1;
        y[down] = (y[down] as number) + overlap / 2 + 1;
        moved = true;
      }
    }
    if (!moved) break;
  }
  graph.nodes.forEach((node, i) => {
    out[node.id] = { x: Math.round(x[i] as number), y: Math.round(y[i] as number) };
  });
  return out;
}

/** The ids one step away from a node, the node included. */
export function neighbourhood(graph: BrainGraphData, id: string): Set<string> {
  const near = new Set([id]);
  for (const edge of graph.edges) {
    if (edge.source === id) near.add(edge.target);
    if (edge.target === id) near.add(edge.source);
  }
  return near;
}
