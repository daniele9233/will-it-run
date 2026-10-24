import { Background, type Edge, Handle, MarkerType, type Node, type NodeProps, Position, ReactFlow, ReactFlowProvider, useNodesState, useReactFlow } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Bot, Maximize2, ShieldCheck, UserCheck } from "lucide-react";
import { useEffect, useMemo } from "react";

import { t } from "../lib/i18n";
import { END, START, edgeKey, flowSeconds, type GraphLive, type NodeState, sourcesOf } from "../lib/liveRun";
import type { InstalledModel, WorkflowDef } from "../lib/types";
import { cx } from "./ui";

type Kind = "agent" | "check" | "approval" | "terminal";

type LiveNodeData = {
  kind: Kind;
  title: string;
  subtitle: string;
  footer: string;
  state: NodeState;
  runs: number;
  speed: number | null;
};

const BORDER: Record<NodeState, string> = {
  waiting: "border-line-strong",
  working: "border-accent",
  done: "border-ok",
  failed: "border-bad",
  stopped: "border-limited",
};

function Handles() {
  const hidden = { opacity: 0, width: 6, height: 6 };
  return (
    <>
      <Handle id="in" type="target" position={Position.Top} style={hidden} isConnectable={false} />
      <Handle id="out" type="source" position={Position.Bottom} style={hidden} isConnectable={false} />
      <Handle id="r-out" type="source" position={Position.Right} style={{ ...hidden, top: "65%" }} isConnectable={false} />
      <Handle id="r-in" type="target" position={Position.Right} style={{ ...hidden, top: "35%" }} isConnectable={false} />
      <Handle id="l-out" type="source" position={Position.Left} style={{ ...hidden, top: "65%" }} isConnectable={false} />
      <Handle id="l-in" type="target" position={Position.Left} style={{ ...hidden, top: "35%" }} isConnectable={false} />
    </>
  );
}

function LiveNode({ data, selected }: NodeProps<Node<LiveNodeData>>) {
  if (data.kind === "terminal") {
    return (
      <div className={cx("flex h-8 w-[210px] items-center justify-center rounded-full border bg-bg text-xs font-semibold tracking-wide", data.state === "done" ? "border-ok text-ink" : "border-line-strong text-ink-3")}>
        <Handles />
        {data.title}
      </div>
    );
  }
  const Icon = data.kind === "check" ? ShieldCheck : data.kind === "approval" ? UserCheck : Bot;
  const working = data.state === "working";
  return (
    <div
      className={cx(
        "w-[210px] rounded-lg border px-3 py-2.5 transition-colors duration-200",
        data.kind === "approval" ? "border-dashed bg-panel" : data.kind === "check" ? "bg-bg" : "bg-surface",
        BORDER[data.state],
        selected && "outline outline-2 outline-offset-2 outline-accent",
        data.state === "waiting" && "opacity-70",
      )}
    >
      <Handles />
      <div className="flex items-center gap-1.5">
        {working ? <span className="pulse-dot h-2 w-2 shrink-0 rounded-full bg-accent" aria-hidden /> : <Icon size={13} className={cx("shrink-0", data.state === "done" ? "text-ok" : data.kind === "approval" ? "text-warn" : "text-ink-3")} aria-hidden />}
        <span className="truncate text-sm font-medium">{data.title}</span>
      </div>
      <div className="mt-1 truncate text-2xs text-ink-2" title={data.subtitle}>
        {data.subtitle}
      </div>
      <div className="mt-1.5 flex items-center justify-between gap-2 text-2xs text-ink-3">
        <span className="truncate">{data.footer}</span>
        {working && data.speed != null && <span className="tabular shrink-0 text-accent">{Math.round(data.speed)} tok/s</span>}
      </div>
      {data.runs > 1 && <div className="mt-1 text-2xs text-ink-2">{t("run {n}", { n: data.runs })}</div>}
    </div>
  );
}

const NODE_TYPES = { live: LiveNode };

interface Props {
  definition: WorkflowDef;
  live: GraphLive;
  /** Tokens per second of the model writing right now: it sets how fast the arrows flow. */
  speed: number | null;
  installed: InstalledModel[];
  selected: string | null;
  onSelect: (node: string | null) => void;
  /** Keep the working node in view. */
  follow: boolean;
  started: boolean;
  finished: boolean;
}

function Graph({ definition: def, live, speed, installed, selected, onSelect, follow, started, finished }: Props) {
  const flow = useReactFlow();

  const drawn = useMemo<Node<LiveNodeData>[]>(() => {
    const xs = Object.values(def.layout).map((p) => p.x);
    const ys = Object.values(def.layout).map((p) => p.y);
    const centerX = xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 400;
    const minY = ys.length ? Math.min(...ys) : 160;
    const maxY = ys.length ? Math.max(...ys) : 160;
    const at = (id: string, index: number) => def.layout[id] ?? { x: centerX + 280, y: minY + index * 120 };
    const modelName = (slot: string) => {
      const key = def.models[slot]?.model_key ?? "";
      const model = installed.find((m) => m.model_key === key);
      return model ? [model.display_name, model.quant].filter(Boolean).join(" · ") : key || t("no model chosen");
    };
    const info = (id: string) => live.nodes[id] ?? { state: "waiting" as NodeState, runs: 0, seconds: null, tokensPerSecond: null };
    const make = (id: string, index: number, data: Omit<LiveNodeData, "state" | "runs" | "speed">): Node<LiveNodeData> => ({
      id,
      type: "live",
      position: at(id, index),
      selected: id === selected,
      data: { ...data, state: info(id).state, runs: info(id).runs, speed: live.working.includes(id) ? speed : null },
    });
    const list: Node<LiveNodeData>[] = [
      { id: START, type: "live", position: def.layout[START] ?? { x: centerX, y: minY - 100 }, selectable: false, data: { kind: "terminal", title: t("USER REQUEST"), subtitle: "", footer: "", state: started ? "done" : "waiting", runs: 0, speed: null } },
    ];
    def.agents.forEach((agent, i) =>
      list.push(make(agent.id, i, { kind: "agent", title: agent.role, subtitle: modelName(agent.model), footer: `${agent.tools.length ? t("{n} tools", { n: agent.tools.length }) : t("no tools")} · ${agent.output_schema}` })),
    );
    (def.checks ?? []).forEach((check, i) => list.push(make(check.id, i, { kind: "check", title: check.label || check.kind, subtitle: t("Check in code · no model"), footer: "" })));
    def.human_approval.forEach((approval, i) => list.push(make(approval.id, i, { kind: "approval", title: t("Human approval"), subtitle: approval.prompt, footer: "" })));
    list.push({ id: END, type: "live", position: def.layout[END] ?? { x: centerX, y: maxY + 130 }, selectable: false, data: { kind: "terminal", title: t("FINAL RESULT"), subtitle: "", footer: "", state: finished ? "done" : "waiting", runs: 0, speed: null } });
    return list;
  }, [def, live, speed, installed, selected, started, finished]);

  // React Flow measures each node once and keeps the result on the node object. The state of
  // a run changes several times a second: update what the nodes show, keep the objects it
  // measured, or every update would throw the measurements away and the arrows with them.
  const [nodes, setNodes, onNodesChange] = useNodesState<Node<LiveNodeData>>([]);
  useEffect(() => {
    setNodes((current) => {
      const known = new Map(current.map((node) => [node.id, node]));
      return drawn.map((node) => {
        const before = known.get(node.id);
        return before ? { ...before, position: node.position, selected: node.selected, data: node.data } : node;
      });
    });
  }, [drawn, setNodes]);

  const edges = useMemo<Edge[]>(() => {
    const order = [START, ...def.agents.map((a) => a.id), ...(def.checks ?? []).map((c) => c.id), ...def.human_approval.map((a) => a.id), END];
    const rank = (id: string) => def.layout[id]?.y ?? order.indexOf(id) * 100;
    const seconds = flowSeconds(speed);
    const list: Edge[] = [];
    let arcs = 0;
    const paint = (key: string, loop: boolean, rejected = false) => {
      const active = live.active.has(key);
      const taken = live.taken.has(key);
      const color = active ? "var(--accent)" : taken ? "var(--ok)" : rejected ? "var(--bad)" : "var(--line-strong)";
      return {
        className: active ? "wir-flowing" : undefined,
        style: {
          stroke: color,
          strokeWidth: active ? 2.25 : taken ? 1.75 : 1.25,
          strokeDasharray: active ? "8 6" : loop && !taken ? "5 4" : undefined,
          opacity: active || taken ? 1 : 0.75,
          ["--wir-flow" as string]: `${seconds}s`,
        },
        markerEnd: { type: MarkerType.ArrowClosed, color, width: 16, height: 16 },
        zIndex: active ? 2 : taken ? 1 : 0,
      };
    };
    def.edges.forEach((edge, index) => {
      sourcesOf(edge).forEach((source, k) => {
        const loop = edge.max_loops !== null && edge.max_loops !== undefined;
        const backward = loop && edge.to !== END && rank(edge.to) <= rank(source);
        const label = edge.label ?? undefined;
        list.push({
          id: `e${index}-${k}`,
          source,
          target: edge.to,
          ...(backward ? { sourceHandle: "r-out", targetHandle: "r-in", pathOptions: { offset: 36 + 26 * arcs++, borderRadius: 10 } } : { sourceHandle: "out", targetHandle: "in" }),
          type: loop ? "smoothstep" : "default",
          label: label ? `${label}${loop ? ` ×${edge.max_loops}` : ""}` : loop ? `×${edge.max_loops}` : undefined,
          selectable: false,
          focusable: false,
          ...paint(edgeKey(source, edge.to), loop),
        } as Edge);
      });
    });
    def.human_approval.forEach((approval) => {
      if (approval.on_reject && approval.on_reject !== END) {
        list.push({
          id: `reject-${approval.id}`,
          source: approval.id,
          target: approval.on_reject,
          sourceHandle: "l-out",
          targetHandle: "l-in",
          pathOptions: { offset: 36, borderRadius: 10 },
          type: "smoothstep",
          label: `REJECTED ×${approval.max_rejections}`,
          selectable: false,
          focusable: false,
          ...paint(edgeKey(approval.id, approval.on_reject), true, true),
        } as Edge);
      }
    });
    return list;
  }, [def, live, speed]);

  // Bring the node that is working into view when it changes, without changing the zoom.
  const current = live.working[0];
  useEffect(() => {
    if (!follow || !current) return;
    const node = flow.getNode(current);
    if (node) void flow.setCenter(node.position.x + 105, node.position.y + 40, { zoom: flow.getZoom(), duration: 350 });
  }, [follow, current, flow]);

  return (
    <div className="relative h-full w-full">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        nodeTypes={NODE_TYPES}
        nodesDraggable={false}
        nodesConnectable={false}
        edgesFocusable={false}
        onNodeClick={(_, node) => onSelect(node.id === START || node.id === END ? null : node.id)}
        onPaneClick={() => onSelect(null)}
        fitView
        fitViewOptions={{ padding: 0.12, maxZoom: 1 }}
        minZoom={0.3}
        maxZoom={1.5}
        proOptions={{ hideAttribution: false }}
      >
        <Background gap={22} size={1} color="var(--line)" />
      </ReactFlow>
      <button
        type="button"
        onClick={() => void flow.fitView({ padding: 0.12, maxZoom: 1, duration: 300 })}
        className="absolute top-3 right-3 z-[5] inline-flex h-7 items-center gap-1.5 rounded-md border border-line-strong bg-surface px-2.5 text-xs text-ink-2 transition-colors duration-150 hover:text-ink"
      >
        <Maximize2 size={12} aria-hidden />
        {t("Fit to view")}
      </button>
    </div>
  );
}

/** The workflow of a run, drawn live: what has run, what is running, and how fast it is writing. */
export function LiveGraph(props: Props) {
  return (
    <ReactFlowProvider>
      <Graph {...props} />
    </ReactFlowProvider>
  );
}
