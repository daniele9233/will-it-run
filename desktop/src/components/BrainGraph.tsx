import { Background, type Edge, Handle, type Node, type NodeProps, Position, ReactFlow, ReactFlowProvider, useReactFlow } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { FileCode, Maximize2, Tag } from "lucide-react";
import { useMemo, useState } from "react";

import { api } from "../lib/api";
import { KIND, isMemoryKind, layoutGraph, neighbourhood, nodeTone } from "../lib/brainGraph";
import { useLoad } from "../lib/hooks";
import { t } from "../lib/i18n";
import type { BrainGraphData, BrainGraphNode } from "../lib/types";
import { Button, EmptyState, Note, SkeletonRows, cx, toneVar } from "./ui";

type DotData = { node: BrainGraphNode; dim: boolean };

/** Both ends of every edge sit in the middle of a node, so a line runs from centre to centre. */
const CENTRE = { top: "50%", left: "50%", transform: "translate(-50%, -50%)", opacity: 0, width: 1, height: 1, minWidth: 0, minHeight: 0, border: 0 };

function Dot({ data, selected }: NodeProps<Node<DotData>>) {
  const { node, dim } = data;
  const memory = node.type === "memory";
  return (
    <div
      title={node.label}
      className={cx(
        "flex max-w-[210px] items-center gap-1.5 rounded-full border px-2.5 py-1 text-2xs transition-opacity duration-150",
        memory ? "border-line-strong bg-surface text-ink" : "border-line bg-bg text-ink-2",
        selected && "outline outline-2 outline-offset-2 outline-accent",
        dim && "opacity-25",
      )}
    >
      <Handle type="target" position={Position.Top} style={CENTRE} isConnectable={false} />
      <Handle type="source" position={Position.Top} style={CENTRE} isConnectable={false} />
      {memory ? (
        <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: toneVar(nodeTone(node.type, node.kind)) }} aria-hidden />
      ) : node.kind === "file" ? (
        <FileCode size={11} className="shrink-0 text-ink-3" aria-hidden />
      ) : (
        <Tag size={11} className="shrink-0 text-ink-3" aria-hidden />
      )}
      <span className={cx("truncate", !memory && "font-mono")}>{node.label}</span>
    </div>
  );
}

const NODE_TYPES = { dot: Dot };

const RELATION: Record<string, { label: string; color: string; dash?: string }> = {
  fixed_by: { label: t("error fixed by solution"), color: "var(--ok)" },
  touches: { label: t("change to a file"), color: "var(--ink-3)" },
  about: { label: t("memory about a file or topic"), color: "var(--accent)" },
  imports: { label: t("file imports file"), color: "var(--line-strong)" },
  from_session: { label: t("recorded in a session"), color: "var(--line-strong)", dash: "3 4" },
};

function Canvas({ graph, onOpenMemory }: { graph: BrainGraphData; onOpenMemory: (id: number) => void }) {
  const flow = useReactFlow();
  const [selected, setSelected] = useState<string | null>(null);
  const positions = useMemo(() => layoutGraph(graph), [graph]);
  const near = useMemo(() => (selected ? neighbourhood(graph, selected) : null), [graph, selected]);

  const nodes = useMemo<Node<DotData>[]>(
    () =>
      graph.nodes.map((node) => ({
        id: node.id,
        type: "dot",
        position: positions[node.id] ?? { x: 0, y: 0 },
        selected: node.id === selected,
        data: { node, dim: near !== null && !near.has(node.id) },
      })),
    [graph, positions, selected, near],
  );
  const edges = useMemo<Edge[]>(
    () =>
      graph.edges.map((edge, index) => {
        const look = RELATION[edge.relation] ?? { label: edge.relation, color: "var(--line-strong)" };
        const lit = selected !== null && (edge.source === selected || edge.target === selected);
        return {
          id: `g${index}`,
          source: edge.source,
          target: edge.target,
          type: "straight",
          selectable: false,
          focusable: false,
          style: { stroke: look.color, strokeWidth: lit ? 2 : 1.1, strokeDasharray: look.dash, opacity: selected === null ? 0.7 : lit ? 1 : 0.12 },
          zIndex: lit ? 1 : 0,
        } as Edge;
      }),
    [graph, selected],
  );

  const chosen = graph.nodes.find((n) => n.id === selected) ?? null;
  const linked = useMemo(() => {
    if (!chosen || !near) return [];
    return graph.nodes.filter((n) => n.id !== chosen.id && near.has(n.id)).sort((a, b) => Number(b.type === "memory") - Number(a.type === "memory") || a.label.localeCompare(b.label));
  }, [graph, chosen, near]);
  const present = [...new Set(graph.edges.map((e) => e.relation))].filter((r) => r in RELATION);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="relative min-h-[320px] flex-1">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          nodesDraggable={false}
          nodesConnectable={false}
          edgesFocusable={false}
          onNodeClick={(_, node) => setSelected(node.id === selected ? null : node.id)}
          onPaneClick={() => setSelected(null)}
          fitView
          fitViewOptions={{ padding: 0.1, maxZoom: 1.1 }}
          minZoom={0.15}
          maxZoom={1.6}
          proOptions={{ hideAttribution: false }}
        >
          <Background gap={22} size={1} color="var(--line)" />
        </ReactFlow>
        <button
          type="button"
          onClick={() => void flow.fitView({ padding: 0.1, maxZoom: 1.1, duration: 300 })}
          className="absolute top-3 right-3 z-[5] inline-flex h-7 items-center gap-1.5 rounded-md border border-line-strong bg-surface px-2.5 text-xs text-ink-2 transition-colors duration-150 hover:text-ink"
        >
          <Maximize2 size={12} aria-hidden />
          {t("Fit to view")}
        </button>
        <ul className="absolute bottom-3 left-3 z-[5] grid gap-1 rounded-md border border-line bg-panel px-2.5 py-2 text-2xs text-ink-2">
          {present.map((relation) => (
            <li key={relation} className="flex items-center gap-2">
              <svg width="22" height="6" aria-hidden>
                <line x1="0" y1="3" x2="22" y2="3" stroke={RELATION[relation]?.color} strokeWidth="2" strokeDasharray={RELATION[relation]?.dash} />
              </svg>
              {RELATION[relation]?.label}
            </li>
          ))}
        </ul>
      </div>
      <div className="shrink-0 border-t border-line bg-panel px-5 py-3 text-sm">
        {!chosen ? (
          <p className="text-xs text-ink-3">
            {graph.total_nodes > graph.nodes.length
              ? t("Showing the {shown} most connected of {total} nodes. Select a node to see what it is linked to.", { shown: graph.nodes.length, total: graph.total_nodes })
              : t("{nodes} nodes and {edges} links. Select a node to see what it is linked to.", { nodes: graph.nodes.length, edges: graph.edges.length })}
          </p>
        ) : (
          <div className="grid gap-2">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="min-w-0">
                <span className="text-xs text-ink-3">
                  {chosen.type === "memory" && isMemoryKind(chosen.kind) ? KIND[chosen.kind].label : chosen.kind === "file" ? t("File") : t("Topic")}
                </span>
                <div className={cx("selectable truncate", chosen.type === "entity" && "font-mono text-xs")}>{chosen.label}</div>
              </div>
              {chosen.type === "memory" && (
                <Button size="sm" onClick={() => onOpenMemory(Number(chosen.id.slice(2)))}>
                  {t("Open the memory")}
                </Button>
              )}
            </div>
            {linked.length > 0 && (
              <ul className="flex max-h-24 flex-wrap gap-1.5 overflow-y-auto">
                {linked.map((node) => (
                  <li key={node.id}>
                    <button
                      type="button"
                      onClick={() => setSelected(node.id)}
                      className={cx("inline-flex max-w-[260px] items-center gap-1.5 rounded-md border border-line px-2 py-0.5 text-2xs text-ink-2 hover:border-line-strong hover:text-ink", node.type === "entity" && "font-mono")}
                    >
                      {node.type === "memory" && <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: toneVar(nodeTone(node.type, node.kind)) }} aria-hidden />}
                      <span className="truncate">{node.label}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/** A project's knowledge graph: its memories, the files and topics they are about, and how the files import each other. */
export function BrainGraph({ projectId, onOpenMemory }: { projectId: string; onOpenMemory: (id: number) => void }) {
  const graph = useLoad(() => api.get<BrainGraphData>(`/api/brain/projects/${projectId}/graph`), [projectId]);
  if (graph.loading && !graph.data) return <div className="p-6"><SkeletonRows rows={4} height={48} /></div>;
  if (graph.error && !graph.data) return <div className="p-6"><Note tone="bad">{graph.error}</Note></div>;
  if (!graph.data || graph.data.nodes.length === 0) {
    return (
      <div className="px-6">
        <EmptyState title={t("Nothing to draw yet")}>{t("The graph links memories to the files and topics they are about. It fills in as the project is worked on.")}</EmptyState>
      </div>
    );
  }
  return (
    <ReactFlowProvider>
      <Canvas graph={graph.data} onOpenMemory={onOpenMemory} />
    </ReactFlowProvider>
  );
}
