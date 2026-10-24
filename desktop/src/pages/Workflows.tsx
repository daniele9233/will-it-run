import {
  Background,
  type Connection,
  Controls,
  type Edge,
  Handle,
  MarkerType,
  type Node,
  type NodeProps,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { ArrowLeft, Bot, CircleCheck, Copy, FileCode, PanelLeft, PanelRight, Play, Plus, Save, ShieldCheck, Trash2, UserCheck } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { CloudModelsLink, ModelOptions } from "../components/ModelOptions";
import { RunComposer } from "../components/RunComposer";
import { Badge, Button, Collapsible, EmptyState, Field, IconButton, Note, PageHeader, Section, Segmented, Select, SkeletonRows, StatusDot, TextArea, TextInput, cx } from "../components/ui";
import { api } from "../lib/api";
import { policyLabel, timeAgo, titleCase } from "../lib/format";
import { errorMessage, useInterval, useLoad } from "../lib/hooks";
import { useApp } from "../lib/store";
import type { AgentDef, ApprovalDef, CheckDef, CheckInfo, CloudProvider, ContextAdvice, EdgeDef, InstalledModel, LoadingPolicy, ModelSlot, ToolInfo, ValidationResult, WorkflowDef, WorkflowInfo } from "../lib/types";
import { t } from "../lib/i18n";
import { isCloudKey, useModelChoices } from "../lib/models";
import { PANEL_MAX, PANEL_MIN, clampPanelWidth, loadPanelPrefs, savePanelPrefs, sourcePayload, type ApprovalMode, type PanelPrefs, type RunSource } from "../lib/runSource";
import { agentsWithoutModel, assignModel, distinctModels, modelLabel } from "../lib/workflow";

const START = "START";
const END = "END";
const POLICIES: LoadingPolicy[] = ["keep_loaded", "load_on_demand", "unload_after_use", "shared_instance", "sequential", "parallel"];

// -- nodes --------------------------------------------------------------------

type AgentNodeData = { agent: AgentDef; modelLabel: string; hasModel: boolean; issue: boolean };
type CheckNodeData = { check: CheckDef; kindLabel: string };
type ApprovalNodeData = { approval: ApprovalDef };
type TerminalNodeData = { kind: "START" | "END" };

/** Side handles carry edges that go back up the graph, so loops read as arcs beside the main flow. */
function LoopHandles() {
  const hidden = { opacity: 0, width: 6, height: 6 };
  return (
    <>
      <Handle id="r-out" type="source" position={Position.Right} style={{ ...hidden, top: "65%" }} isConnectable={false} />
      <Handle id="r-in" type="target" position={Position.Right} style={{ ...hidden, top: "35%" }} isConnectable={false} />
      <Handle id="l-out" type="source" position={Position.Left} style={{ ...hidden, top: "65%" }} isConnectable={false} />
      <Handle id="l-in" type="target" position={Position.Left} style={{ ...hidden, top: "35%" }} isConnectable={false} />
    </>
  );
}

function AgentNode({ data, selected }: NodeProps<Node<AgentNodeData>>) {
  const { agent, modelLabel, hasModel, issue } = data;
  return (
    <div className={cx("w-[210px] rounded-lg border bg-surface px-3 py-2.5 transition-colors duration-150", selected ? "border-accent" : issue ? "border-bad" : "border-line-strong")}>
      <Handle id="in" type="target" position={Position.Top} />
      <div className="flex items-center gap-1.5">
        <Bot size={13} className="shrink-0 text-accent" aria-hidden />
        <span className="truncate text-sm font-medium">{agent.role}</span>
      </div>
      <div className={cx("mt-1 truncate text-2xs", hasModel ? "text-ink-2" : "text-warn")} title={modelLabel}>
        {modelLabel}
      </div>
      <div className="mt-1.5 flex items-center gap-2 text-2xs text-ink-3">
        <span>{agent.tools.length ? t("{n} tools", { n: agent.tools.length }) : t("no tools")}</span>
        <span>·</span>
        <span>{agent.output_schema}</span>
      </div>
      <Handle id="out" type="source" position={Position.Bottom} />
      <LoopHandles />
    </div>
  );
}

/** A deterministic step. Drawn apart from agents: no model sits behind it. */
function CheckNode({ data, selected }: NodeProps<Node<CheckNodeData>>) {
  return (
    <div className={cx("w-[210px] rounded-lg border bg-bg px-3 py-2.5 transition-colors duration-150", selected ? "border-accent" : "border-ok")}>
      <Handle id="in" type="target" position={Position.Top} />
      <div className="flex items-center gap-1.5">
        <ShieldCheck size={13} className="shrink-0 text-ok" aria-hidden />
        <span className="truncate text-sm font-medium">{data.check.label || data.kindLabel}</span>
      </div>
      <div className="mt-1 truncate text-2xs text-ink-3">{t("Check in code · no model")}</div>
      <Handle id="out" type="source" position={Position.Bottom} />
      <LoopHandles />
    </div>
  );
}

function ApprovalNode({ data, selected }: NodeProps<Node<ApprovalNodeData>>) {
  return (
    <div className={cx("w-[210px] rounded-lg border border-dashed bg-panel px-3 py-2.5", selected ? "border-accent" : "border-warn")}>
      <Handle id="in" type="target" position={Position.Top} />
      <div className="flex items-center gap-1.5">
        <UserCheck size={13} className="shrink-0 text-warn" aria-hidden />
        <span className="text-sm font-medium">{t("Human approval")}</span>
      </div>
      <div className="mt-1 line-clamp-2 text-2xs text-ink-3">{data.approval.prompt}</div>
      <Handle id="out" type="source" position={Position.Bottom} />
      <LoopHandles />
    </div>
  );
}

function TerminalNode({ data }: NodeProps<Node<TerminalNodeData>>) {
  return (
    <div className="flex h-8 w-[210px] items-center justify-center rounded-full border border-line-strong bg-bg text-xs font-semibold tracking-wide text-ink-2">
      {data.kind === "END" && <Handle id="in" type="target" position={Position.Top} />}
      {data.kind === "START" ? t("USER REQUEST") : t("FINAL RESULT")}
      {data.kind === "START" && <Handle id="out" type="source" position={Position.Bottom} />}
    </div>
  );
}

const NODE_TYPES = { agent: AgentNode, check: CheckNode, approval: ApprovalNode, terminal: TerminalNode };

function sourcesOf(edge: EdgeDef): string[] {
  return Array.isArray(edge.from) ? edge.from : [edge.from];
}

/** What a node shows under the role: the model that will run it. */
function slotModelName(slot: ModelSlot | undefined, installed: InstalledModel[]): { label: string; hasModel: boolean } {
  if (!slot) return { label: t("no model chosen"), hasModel: false };
  if (slot.model_key) {
    const model = installed.find((m) => m.model_key === slot.model_key);
    return { label: model ? [model.display_name, model.quant].filter(Boolean).join(" · ") : slot.model_key, hasModel: true };
  }
  if (slot.repo) return { label: t("to download: {model}", { model: `${slot.repo.split("/")[1] ?? slot.repo} ${slot.quant ?? ""}`.trim() }), hasModel: false };
  return { label: t("no model chosen"), hasModel: false };
}

function buildNodes(def: WorkflowDef, validation: ValidationResult | null, previous: Node[], installed: InstalledModel[], checkLabels: Record<string, string>): Node[] {
  const kept = new Map(previous.map((n) => [n.id, n.position]));
  const ys = Object.values(def.layout).map((p) => p.y);
  const xs = Object.values(def.layout).map((p) => p.x);
  const centerX = xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 400;
  const minY = ys.length ? Math.min(...ys) : 160;
  const maxY = ys.length ? Math.max(...ys) : 160;
  const position = (id: string, fallbackIndex: number) => kept.get(id) ?? def.layout[id] ?? { x: centerX + 280, y: minY + fallbackIndex * 120 };
  const failing = new Set((validation?.issues ?? []).filter((i) => i.level === "error" && i.node).map((i) => i.node));
  const nodes: Node[] = [
    { id: START, type: "terminal", position: kept.get(START) ?? def.layout[START] ?? { x: centerX, y: minY - 100 }, data: { kind: "START" }, deletable: false },
  ];
  def.agents.forEach((agent, index) => {
    const { label, hasModel } = slotModelName(def.models[agent.model], installed);
    nodes.push({ id: agent.id, type: "agent", position: position(agent.id, index), data: { agent, modelLabel: label, hasModel, issue: failing.has(agent.id) } });
  });
  (def.checks ?? []).forEach((check, index) => {
    nodes.push({ id: check.id, type: "check", position: position(check.id, index), data: { check, kindLabel: checkLabels[check.kind] ?? check.kind } });
  });
  def.human_approval.forEach((approval, index) => {
    nodes.push({ id: approval.id, type: "approval", position: position(approval.id, index), data: { approval } });
  });
  nodes.push({ id: END, type: "terminal", position: kept.get(END) ?? def.layout[END] ?? { x: centerX, y: maxY + 130 }, data: { kind: "END" }, deletable: false });
  return nodes;
}

/** Side-handle routing for a smooth-step edge; `pathOptions` is specific to that edge type. */
function arc(side: "l" | "r", offset: number): Partial<Edge> {
  return { sourceHandle: `${side}-out`, targetHandle: `${side}-in`, pathOptions: { offset, borderRadius: 10 } } as Partial<Edge>;
}

function buildEdges(def: WorkflowDef): Edge[] {
  const edges: Edge[] = [];
  const order = [START, ...def.agents.map((a) => a.id), ...(def.checks ?? []).map((c) => c.id), ...def.human_approval.map((a) => a.id), END];
  const rank = (id: string) => def.layout[id]?.y ?? order.indexOf(id) * 100;
  let arcs = 0;
  def.edges.forEach((edge, index) => {
    sourcesOf(edge).forEach((source, k) => {
      const loop = edge.max_loops !== null && edge.max_loops !== undefined;
      // An edge that returns to an earlier node is drawn as an arc on the right side.
      const backward = loop && edge.to !== END && rank(edge.to) <= rank(source);
      const label = edge.label ?? (edge.when ? edge.when : undefined);
      edges.push({
        id: `e${index}-${k}`,
        source,
        target: edge.to,
        ...(backward ? arc("r", 36 + 26 * arcs++) : { sourceHandle: "out", targetHandle: "in" }),
        label: label ? `${label}${loop ? ` ×${edge.max_loops}` : ""}` : loop ? `×${edge.max_loops}` : undefined,
        type: loop ? "smoothstep" : "default",
        animated: false,
        style: { stroke: loop ? "var(--limited)" : edge.when ? "var(--accent)" : "var(--line-strong)", strokeWidth: 1.5, strokeDasharray: loop ? "5 4" : undefined },
        markerEnd: { type: MarkerType.ArrowClosed, color: loop ? "var(--limited)" : edge.when ? "var(--accent)" : "var(--line-strong)", width: 16, height: 16 },
        data: { index },
      });
    });
  });
  def.human_approval.forEach((approval) => {
    if (approval.on_reject && approval.on_reject !== END) {
      edges.push({
        id: `reject-${approval.id}`,
        source: approval.id,
        target: approval.on_reject,
        ...arc("l", 36),
        label: `REJECTED ×${approval.max_rejections}`,
        type: "smoothstep",
        deletable: false,
        selectable: false,
        style: { stroke: "var(--bad)", strokeWidth: 1.5, strokeDasharray: "5 4" },
        markerEnd: { type: MarkerType.ArrowClosed, color: "var(--bad)", width: 16, height: 16 },
      });
    }
  });
  return edges;
}

// -- side panels --------------------------------------------------------------

/** Pick one of the downloaded models. `slot` is what is planned when nothing is installed for it yet. */
function ModelSelect({ value, slot, installed, providers, onPick, label, placeholder }: { value: string; slot?: ModelSlot; installed: InstalledModel[]; providers: CloudProvider[]; onPick: (model: InstalledModel) => void; label: string; placeholder?: string }) {
  const missing = value && !installed.some((m) => m.model_key === value);
  return (
    <Select
      value={value}
      aria-label={label}
      onChange={(e) => {
        const model = installed.find((m) => m.model_key === e.target.value);
        if (model) onPick(model);
      }}
    >
      <option value="">
        {placeholder ?? (slot?.repo && !value ? t("Not installed yet: {model}", { model: `${slot.repo.split("/")[1] ?? slot.repo} ${slot.quant ?? ""}` }) : t("Choose a model"))}
      </option>
      {missing && <option value={value}>{t("{model} (no longer installed)", { model: value })}</option>}
      <ModelOptions models={installed} providers={providers} label={modelLabel} />
    </Select>
  );
}

function NoModelsNote() {
  const navigate = useApp((s) => s.navigate);
  return (
    <Note tone="warn" action={<Button size="sm" onClick={() => navigate("models")}>{t("Explore Models")}</Button>}>
      {t("No model is downloaded yet. Download one to give it to an agent.")}
    </Note>
  );
}

function AgentPanel({ def, agent, tools, schemas, installed, providers, onChange, onModel, onDelete }: { def: WorkflowDef; agent: AgentDef; tools: ToolInfo[]; schemas: string[]; installed: InstalledModel[]; providers: CloudProvider[]; onChange: (patch: Partial<AgentDef>) => void; onModel: (model: InstalledModel) => void; onDelete: () => void }) {
  const slot = def.models[agent.model];
  const sharedWith = def.agents.filter((a) => a.id !== agent.id && a.model === agent.model).map((a) => a.role);
  const groups = useMemo(() => {
    const map = new Map<string, ToolInfo[]>();
    for (const tool of tools) map.set(tool.group, [...(map.get(tool.group) ?? []), tool]);
    return [...map.entries()];
  }, [tools]);
  const toggleTool = (name: string) => onChange({ tools: agent.tools.includes(name) ? agent.tools.filter((t) => t !== name) : [...agent.tools, name] });
  return (
    <div className="grid gap-4">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-xs text-ink-3">{t("Agent")}</div>
          <div className="truncate text-lg font-semibold tracking-tight">{agent.role}</div>
        </div>
        <IconButton label={t("Delete agent")} onClick={onDelete}>
          <Trash2 size={14} />
        </IconButton>
      </div>
      <Field label={t("Role")}>
        <TextInput value={agent.role} onChange={(e) => onChange({ role: e.target.value })} />
      </Field>
      <Field label={t("Goal")}>
        <TextArea rows={2} value={agent.goal} onChange={(e) => onChange({ goal: e.target.value })} />
      </Field>
      {installed.length === 0 ? (
        <NoModelsNote />
      ) : (
        <Field
          label={t("Model")}
          hint={
            sharedWith.length > 0 && slot?.model_key
              ? t("Also used by {roles}. Choosing another model here changes only this agent.", { roles: sharedWith.join(", ") })
              : t("Any model you have downloaded, or a cloud model with a key. Agents with the same model share it in memory.")
          }
        >
          <div className="grid gap-1.5">
            <ModelSelect value={slot?.model_key ?? ""} slot={slot} installed={installed} providers={providers} onPick={onModel} label={t("Model for {role}", { role: agent.role })} />
            <div>
              <CloudModelsLink models={installed} />
            </div>
          </div>
        </Field>
      )}
      <Field label={t("Role prompt")} hint={t("Role, goal, responsibilities and the output contract are added automatically around this text.")}>
        <TextArea rows={9} value={agent.system_prompt} onChange={(e) => onChange({ system_prompt: e.target.value })} className="font-mono text-xs" />
      </Field>
      <div className="grid gap-2">
        <span className="text-xs font-medium text-ink-2">{t("Tools")}</span>
        {groups.map(([group, items]) => (
          <div key={group}>
            <div className="mb-1 text-2xs text-ink-3">{group}</div>
            <div className="grid gap-1">
              {items.map((tool) => (
                <label key={tool.name} className="flex items-center gap-2 text-sm" title={tool.description}>
                  <input type="checkbox" checked={agent.tools.includes(tool.name)} onChange={() => toggleTool(tool.name)} className="accent-[var(--accent)]" />
                  <span className="flex-1">{tool.label}</span>
                  {tool.risk === "high" && <Badge tone="bad">{t("asks first")}</Badge>}
                  {tool.name === "powershell" && <Badge tone="warn">{t("asks if it changes state")}</Badge>}
                </label>
              ))}
            </div>
          </div>
        ))}
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Field label={t("Output schema")}>
          <Select value={agent.output_schema} onChange={(e) => onChange({ output_schema: e.target.value })}>
            {schemas.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </Select>
        </Field>
        <Field label={t("Max retries")}>
          <TextInput type="number" min={0} max={10} value={agent.max_retries} onChange={(e) => onChange({ max_retries: Number(e.target.value) })} />
        </Field>
        <Field label={t("Model policy")}>
          <Select value={agent.loading_policy} onChange={(e) => onChange({ loading_policy: e.target.value as LoadingPolicy })}>
            {POLICIES.map((p) => (
              <option key={p} value={p}>
                {titleCase(p)}
              </option>
            ))}
          </Select>
        </Field>
        <Field label={t("When it fails")}>
          <Select value={agent.escalation} onChange={(e) => onChange({ escalation: e.target.value as AgentDef["escalation"] })}>
            <option value="fail">{t("Stop the run")}</option>
            <option value="human">{t("Ask a human")}</option>
            <option value="continue">{t("Continue without it")}</option>
          </Select>
        </Field>
      </div>
      <Collapsible title={t("What this agent is shown")} defaultOpen={Boolean(agent.reads)}>
        <div className="grid gap-3">
          <Field
            label={t("Earlier work it can read")}
            hint={t("Empty: everything so far. Otherwise node ids separated by commas, or one field of a node (researcher.claims). What is not listed never reaches this agent.")}
          >
            <TextInput
              value={(agent.reads ?? []).join(", ")}
              onChange={(e) => {
                const list = e.target.value.split(",").map((part) => part.trim()).filter(Boolean);
                onChange({ reads: e.target.value.trim() ? list : null });
              }}
              placeholder={t("everything so far")}
              className="font-mono text-xs"
            />
          </Field>
          <Field label={t("Input budget (tokens)")} hint={t("The most earlier work it is handed, whatever the model could hold. Empty: no fixed limit.")}>
            <TextInput type="number" min={256} step={256} value={agent.max_input_tokens ?? ""} onChange={(e) => onChange({ max_input_tokens: e.target.value ? Number(e.target.value) : null })} placeholder={t("no limit")} />
          </Field>
          {Object.entries(agent.choices ?? {}).map(([field, values]) => (
            <div key={field} className="text-xs text-ink-2">
              {t("{field} can only be:", { field })} <span className="font-mono text-ink">{values.join(", ")}</span>
            </div>
          ))}
        </div>
      </Collapsible>
    </div>
  );
}

const CHECK_PARAMS: Record<string, string> = {
  include_task: t("The request counts as evidence"),
  max_chars: t("Most evidence read (characters)"),
  min_chars: t("Least evidence needed (characters)"),
  max_task_tokens: t("Longest request accepted (tokens)"),
  claims: t("Claims come from"),
  audit: t("Audited by"),
  evidence: t("Checked against"),
  min_accepted: t("Claims needed to continue"),
  min_quote_chars: t("Shortest quote that counts (characters)"),
  panel: t("Panel"),
  propositions: t("Propositions come from"),
  threshold: t("Share of the panel a side needs (0 to 1)"),
  quorum: t("Share of propositions that must be decided (0 to 1)"),
  previous: t("Earlier round"),
  commits: t("Commits"),
  review: t("Reviewed by"),
  min_approved: t("Commits needed to continue"),
  answer: t("Answer comes from"),
  accepted: t("Accepted items come from"),
  verdict: t("Verdict"),
  message: t("Message"),
};

function CheckPanel({ def, check, info, onChange, onDelete }: { def: WorkflowDef; check: CheckDef; info: CheckInfo | undefined; onChange: (patch: Partial<CheckDef>) => void; onDelete: () => void }) {
  const params: Record<string, unknown> = { ...(info?.defaults ?? {}), ...check.params };
  const nodeName = (id: string) => def.agents.find((a) => a.id === id)?.role ?? def.checks?.find((c) => c.id === id)?.label ?? id;
  const setParam = (key: string, value: unknown) => onChange({ params: { ...check.params, [key]: value } });
  return (
    <div className="grid gap-4">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-xs text-ink-3">{t("Check in code")}</div>
          <div className="truncate text-lg font-semibold tracking-tight">{check.label || info?.label || check.kind}</div>
        </div>
        <IconButton label={t("Delete check")} onClick={onDelete}>
          <Trash2 size={14} />
        </IconButton>
      </div>
      <p className="text-sm text-ink-2">{info?.description ?? check.kind}</p>
      <p className="text-xs text-ink-3">{t("No model runs here, so nothing an agent writes can talk its way past it.")}</p>
      <Field label={t("Name")}>
        <TextInput value={check.label} onChange={(e) => onChange({ label: e.target.value })} placeholder={info?.label ?? check.kind} />
      </Field>
      {Object.entries(params).map(([key, value]) => {
        const fallback = info?.defaults[key];
        const numeric = typeof value === "number" || typeof fallback === "number" || /^(max|min)_/.test(key);
        if (typeof value === "boolean") {
          return (
            <label key={key} className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={value} onChange={(e) => setParam(key, e.target.checked)} className="accent-[var(--accent)]" />
              <span>{CHECK_PARAMS[key] ?? key}</span>
            </label>
          );
        }
        if (numeric && !Array.isArray(value) && typeof value !== "string") {
          return (
            <Field key={key} label={CHECK_PARAMS[key] ?? key}>
              <TextInput type="number" step="any" value={value === null || value === undefined ? "" : Number(value)} onChange={(e) => setParam(key, e.target.value === "" ? null : Number(e.target.value))} placeholder={t("not set")} />
            </Field>
          );
        }
        const ids = Array.isArray(value) ? value.map(String) : value ? [String(value)] : [];
        const known = ids.length > 0 && ids.every((id) => def.agents.some((a) => a.id === id) || def.checks?.some((c) => c.id === id));
        return (
          <div key={key} className="grid gap-1">
            <span className="text-xs font-medium text-ink-2">{CHECK_PARAMS[key] ?? key}</span>
            <span className="selectable text-sm">{ids.length === 0 ? t("not set") : known ? ids.map(nodeName).join(", ") : ids.join(", ")}</span>
          </div>
        );
      })}
    </div>
  );
}

function EdgePanel({ def, edge, onChange, onDelete }: { def: WorkflowDef; edge: EdgeDef; onChange: (patch: Partial<EdgeDef>) => void; onDelete: () => void }) {
  const source = sourcesOf(edge).join(" + ");
  return (
    <div className="grid gap-4">
      <div className="flex items-start justify-between">
        <div>
          <div className="text-xs text-ink-3">{t("Edge")}</div>
          <div className="text-sm font-semibold">
            {source} → {edge.to}
          </div>
        </div>
        <IconButton label={t("Delete edge")} onClick={onDelete}>
          <Trash2 size={14} />
        </IconButton>
      </div>
      <Field label={t("Condition")} hint={t('Taken only when this is true. Fields come from the output of {source}, e.g. passed == false, approved == true, len(failures) > 0. Leave empty for "always".', { source })}>
        <TextInput value={edge.when ?? ""} onChange={(e) => onChange({ when: e.target.value || null })} placeholder={t("always")} className="font-mono text-xs" disabled={sourcesOf(edge).length > 1} />
      </Field>
      <Field label={t("Label")}>
        <TextInput value={edge.label ?? ""} onChange={(e) => onChange({ label: e.target.value || null })} placeholder={t("e.g. FAIL")} />
      </Field>
      <Field label={t("Loop limit")} hint={t("Required for an edge that goes back to an earlier node. Loops are always bounded.")}>
        <TextInput type="number" min={1} max={20} value={edge.max_loops ?? ""} onChange={(e) => onChange({ max_loops: e.target.value ? Number(e.target.value) : null })} placeholder={t("not a loop")} />
      </Field>
      {edge.max_loops != null && (
        <Field label={t("When the limit is reached")}>
          <Select value={edge.on_exhausted ?? ""} onChange={(e) => onChange({ on_exhausted: e.target.value || null })}>
            <option value="">{t("End the run (retry limit)")}</option>
            {[...def.agents.map((a) => a.id), ...(def.checks ?? []).map((c) => c.id), ...def.human_approval.map((a) => a.id), END].map((id) => (
              <option key={id} value={id}>
                {t("Continue to {id}", { id })}
              </option>
            ))}
          </Select>
        </Field>
      )}
    </div>
  );
}

function ApprovalPanel({ def, approval, onChange, onDelete }: { def: WorkflowDef; approval: ApprovalDef; onChange: (patch: Partial<ApprovalDef>) => void; onDelete: () => void }) {
  return (
    <div className="grid gap-4">
      <div className="flex items-start justify-between">
        <div>
          <div className="text-xs text-ink-3">{t("Human approval")}</div>
          <div className="text-lg font-semibold tracking-tight">{approval.id}</div>
        </div>
        <IconButton label={t("Delete approval")} onClick={onDelete}>
          <Trash2 size={14} />
        </IconButton>
      </div>
      <Field label={t("Question shown to you")}>
        <TextArea rows={3} value={approval.prompt} onChange={(e) => onChange({ prompt: e.target.value })} />
      </Field>
      <Field label={t("Show the output of")}>
        <Select value={approval.show ?? ""} onChange={(e) => onChange({ show: e.target.value || null })}>
          <option value="">{t("The previous step")}</option>
          {def.agents.map((a) => (
            <option key={a.id} value={a.id}>
              {a.role}
            </option>
          ))}
        </Select>
      </Field>
      <Field label={t("If rejected")}>
        <Select value={approval.on_reject} onChange={(e) => onChange({ on_reject: e.target.value })}>
          <option value={END}>{t("End the run")}</option>
          {def.agents.map((a) => (
            <option key={a.id} value={a.id}>
              {t("Send back to {role}", { role: a.role })}
            </option>
          ))}
        </Select>
      </Field>
      <Field label={t("Rejections allowed before the run ends")}>
        <TextInput type="number" min={0} max={10} value={approval.max_rejections} onChange={(e) => onChange({ max_rejections: Number(e.target.value) })} />
      </Field>
    </div>
  );
}

const CONTEXTS = [4096, 8192, 16384, 32768, 49152, 65536, 131072];

function ContextSelect({ value, onChange, label }: { value: number; onChange: (value: number) => void; label: string }) {
  const options = CONTEXTS.includes(value) ? CONTEXTS : [...CONTEXTS, value].sort((a, b) => a - b);
  return (
    <Select value={value} onChange={(e) => onChange(Number(e.target.value))} aria-label={label}>
      {options.map((c) => (
        <option key={c} value={c}>
          {c.toLocaleString()}
        </option>
      ))}
    </Select>
  );
}

type SlotPatch = Partial<ModelSlot>;

/** The panel shown when nothing is selected: which model each agent uses. */
function ModelsPanel({ def, installed, providers, onAssign, onChange, onAllAutomatic }: { def: WorkflowDef; installed: InstalledModel[]; providers: CloudProvider[]; onAssign: (agentIds: string[], model: InstalledModel) => void; onChange: (slot: string, patch: SlotPatch) => void; onAllAutomatic: () => void }) {
  const used = distinctModels(def);
  const missing = agentsWithoutModel(def);
  const everyone = used.length === 1 && missing.length === 0 ? (used[0] ?? "") : "";
  const slots = Object.entries(def.models).filter(([id]) => def.agents.some((a) => a.model === id));
  // Context and memory are about this PC's graphics card: a cloud model has neither to set.
  const localSlots = slots.filter(([, slot]) => slot.model_key && !isCloudKey(slot.model_key));
  return (
    <div className="grid gap-5">
      <div>
        <div className="text-lg font-semibold tracking-tight">{t("Models")}</div>
        <p className="mt-1 text-sm text-ink-2">
          {t("Choose which of your downloaded models each agent uses. One model for everyone has no waiting between steps; give an agent a different model where it does the job better. There is no limit to how many you use.")}
        </p>
        <div className="mt-2">
          <CloudModelsLink models={installed} />
        </div>
      </div>

      {installed.length === 0 ? (
        <NoModelsNote />
      ) : (
        <>
          <Field label={t("Same model for every agent")}>
            <ModelSelect value={everyone} installed={installed} providers={providers} onPick={(model) => onAssign(def.agents.map((a) => a.id), model)} label={t("Same model for every agent")} placeholder={used.length > 1 ? t("{n} different models in use", { n: used.length }) : t("Choose a model")} />
          </Field>

          <div className="grid gap-2 border-t border-line pt-4">
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-sm font-semibold">{t("Model for each agent")}</span>
              {missing.length > 0 && <span className="text-xs text-warn">{missing.length === 1 ? t("1 without a model") : t("{n} without a model", { n: missing.length })}</span>}
            </div>
            {def.agents.map((agent) => {
              const slot = def.models[agent.model];
              return (
                <label key={agent.id} className="grid gap-1">
                  <span className="truncate text-xs text-ink-2">{agent.role}</span>
                  <ModelSelect value={slot?.model_key ?? ""} slot={slot} installed={installed} providers={providers} onPick={(model) => onAssign([agent.id], model)} label={t("Model for {role}", { role: agent.role })} />
                </label>
              );
            })}
          </div>
        </>
      )}

      {used.length > 1 && (
        <p className="border-t border-line pt-4 text-sm text-ink-2">
          {t("This workflow uses {n} models. Will It Run keeps one in memory at a time unless two measurably fit, and sets each context from the VRAM that is free at that moment. Every change of model is a reload.", { n: used.length })}
        </p>
      )}
      {def.workflow.category === "verification" && used.length === 1 && def.agents.length > 1 && (
        <Note tone="accent">
          {t("Every role runs on the same model. The checks in code still apply, but two copies of one model tend to make the same mistake: give the roles that check each other different models when you have them.")}
        </Note>
      )}

      {localSlots.length > 0 && (
        <Collapsible
          title={
            <span>
              {t("Context and memory settings")}
              {localSlots.every(([, slot]) => slot.auto_tune) && <span className="text-ink-3"> · {t("automatic")}</span>}
            </span>
          }
        >
          <ModelSettings def={def} installed={installed.filter((m) => !isCloudKey(m.model_key))} slots={localSlots} onChange={onChange} onAllAutomatic={onAllAutomatic} />
        </Collapsible>
      )}
    </div>
  );
}

/** What automatic will do for one model on this PC, in a sentence. */
function AutoAdvice({ advice, loading, usableGb }: { advice: ContextAdvice | undefined; loading: boolean; usableGb: number | null }) {
  if (!advice && loading) return <p className="text-xs text-ink-3">{t("Working out what fits this PC...")}</p>;
  const supports = advice?.max_context ? ` ${t("The model itself supports up to {n}.", { n: advice.max_context.toLocaleString() })}` : "";
  if (advice && advice.safe_context !== null && advice.fits_gpu === false) {
    return (
      <p className="text-xs text-limited">
        {t("This model is larger than the usable graphics memory ({gb} GB): part of it runs from system RAM and it answers slowly whatever the context. A smaller model or a smaller quantization avoids that.", { gb: usableGb ?? "?" })}
      </p>
    );
  }
  if (advice && advice.safe_context && advice.safe_context < 8192) {
    return (
      <p className="text-xs text-limited">
        {t("Next to this model only about {n} tokens of context fit on this PC. That is little for a team: each agent will be shown a small part of the earlier work, and long answers may be cut. A smaller quantization of the same model, or a smaller model, leaves more room.", { n: advice.safe_context.toLocaleString() })}
      </p>
    );
  }
  if (advice && advice.safe_context) {
    return (
      <p className="text-xs text-ink-2">
        {t("On this PC it holds up to about {n} tokens of context entirely on the graphics card.", { n: advice.safe_context.toLocaleString() })}{" "}
        <span className="text-ink-3">{advice.basis === "measured" ? t("Measured on this PC.") : t("LM Studio's estimate; a real measurement replaces it after the first run.")}</span>{" "}
        {t("Each request gets the context it needs, never more than fits, and the model is reloaded with more only if a later request outgrows it.")}
        {supports}
      </p>
    );
  }
  return (
    <p className="text-xs text-ink-2">
      {t("Each time the model is loaded, the free graphics memory is measured and the request gets the context it needs, never more than fits.")}
      {supports}
    </p>
  );
}

/** Context and memory of each model in use: worked out automatically, or set by hand. */
function ModelSettings({ def, installed, slots, onChange, onAllAutomatic }: { def: WorkflowDef; installed: InstalledModel[]; slots: [string, ModelSlot][]; onChange: (slot: string, patch: SlotPatch) => void; onAllAutomatic: () => void }) {
  const keys = [...new Set(slots.map(([, slot]) => slot.model_key))];
  const advice = useLoad(() => api.post<{ advice: ContextAdvice[]; usable_vram_gb: number }>("/api/models/context-advice", { model_keys: keys }), [keys.join("|")]);
  const name = (key: string) => installed.find((m) => m.model_key === key)?.display_name ?? key;
  const manual = slots.filter(([, slot]) => !slot.auto_tune);
  return (
    <div className="grid gap-5">
      <p className="text-xs text-ink-3">
        {t("Automatic works out the context and what stays in memory from this PC's hardware at the moment of each load, so a model is not given more than it can hold. Manual uses the numbers you set.")}
      </p>
      {manual.length > 0 && slots.length > 1 && (
        <div>
          <Button size="sm" onClick={onAllAutomatic}>
            {t("Set every model to automatic")}
          </Button>
        </div>
      )}
      {slots.map(([id, slot]) => (
        <div key={id} className="grid gap-2.5">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="truncate text-sm font-medium">{name(slot.model_key)}</div>
              <div className="text-xs text-ink-3">{def.agents.filter((a) => a.model === id).map((a) => a.role).join(", ")}</div>
            </div>
            <Segmented
              label={t("Context and memory for {model}", { model: name(slot.model_key) })}
              value={slot.auto_tune ? "auto" : "manual"}
              onChange={(mode) => onChange(id, { auto_tune: mode === "auto" })}
              options={[
                { value: "auto", label: t("Automatic") },
                { value: "manual", label: t("Manual") },
              ]}
            />
          </div>
          {slot.auto_tune ? (
            <AutoAdvice advice={advice.data?.advice.find((a) => a.model_key === slot.model_key)} loading={advice.loading} usableGb={advice.data?.usable_vram_gb ?? null} />
          ) : (
            <>
              <div className="grid grid-cols-2 gap-3">
                <Field label={t("Context length")} hint={t("The most it asks for. Less is loaded when VRAM is short.")}>
                  <ContextSelect label={t("Context length")} value={slot.context_length} onChange={(context_length) => onChange(id, { context_length })} />
                </Field>
                <Field label={t("Loading policy")}>
                  <Select value={slot.loading_policy} onChange={(e) => onChange(id, { loading_policy: e.target.value as LoadingPolicy })}>
                    {POLICIES.filter((p) => p !== "shared_instance").map((p) => (
                      <option key={p} value={p}>
                        {policyLabel(p)}
                      </option>
                    ))}
                  </Select>
                </Field>
              </div>
              <Field label={t("Wide-context variant")} hint={t("Optional. Serves the requests too large for the model above: usually the same model at a smaller quantization, whose saved memory becomes context.")}>
                <Select
                  value={slot.wide_model_key ?? ""}
                  onChange={(e) => onChange(id, { wide_model_key: e.target.value, wide_context_length: e.target.value ? (slot.wide_context_length ?? Math.max(slot.context_length * 2, 32768)) : null })}
                >
                  <option value="">{t("None")}</option>
                  {installed
                    .filter((m) => m.model_key !== slot.model_key)
                    .map((m) => (
                      <option key={m.model_key} value={m.model_key}>
                        {modelLabel(m)}
                      </option>
                    ))}
                </Select>
              </Field>
              {slot.wide_model_key && (
                <Field label={t("Context of the variant")}>
                  <ContextSelect label={t("Context of the variant")} value={slot.wide_context_length ?? 32768} onChange={(wide_context_length) => onChange(id, { wide_context_length })} />
                </Field>
              )}
            </>
          )}
        </div>
      ))}
    </div>
  );
}

// -- editor -------------------------------------------------------------------

function Editor({ id, onBack }: { id: string; onBack: () => void }) {
  const navigate = useApp((s) => s.navigate);
  const toast = useApp((s) => s.toast);
  const isTemplate = id.startsWith("template:");
  const loaded = useLoad(() => api.get<{ workflow: WorkflowDef; validation: ValidationResult }>(`/api/workflows/${encodeURIComponent(id).replace(/%3A/g, ":")}`), [id]);
  const toolInfo = useLoad(() => api.get<{ tools: ToolInfo[]; schemas: string[]; checks: CheckInfo[] }>("/api/tools"));
  // The models on this PC, then the cloud ones that have a key.
  const { all: llms, providers } = useModelChoices();
  const checkLabels = useMemo(() => Object.fromEntries((toolInfo.data?.checks ?? []).map((c) => [c.kind, c.label])), [toolInfo.data]);
  // The newest names, for the effect that draws a workflow when it finishes loading.
  const names = useRef({ llms, checkLabels });
  names.current = { llms, checkLabels };

  const [def, setDef] = useState<WorkflowDef | null>(null);
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [dirty, setDirty] = useState(false);
  const [selection, setSelection] = useState<{ kind: "node" | "edge"; id: string } | null>(null);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [showYaml, setShowYaml] = useState<string | null>(null);
  const [runOpen, setRunOpen] = useState(false);
  const [starting, setStarting] = useState(false);
  // Which side the panel sits on and how wide it is: the user's choice, remembered.
  const [panel, setPanel] = useState<PanelPrefs>(loadPanelPrefs);
  const drag = useRef<{ x: number; width: number } | null>(null);
  useEffect(() => savePanelPrefs(panel), [panel]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (loaded.data) {
      setDef(loaded.data.workflow);
      setValidation(loaded.data.validation);
      setDirty(false);
      setSelection(null);
      setNodes(buildNodes(loaded.data.workflow, loaded.data.validation, [], names.current.llms, names.current.checkLabels));
      setEdges(buildEdges(loaded.data.workflow));
    }
  }, [loaded.data, setNodes, setEdges]);

  const apply = useCallback(
    (next: WorkflowDef) => {
      setDef(next);
      setDirty(true);
      setNodes((current) => buildNodes(next, validation, current, llms, checkLabels));
      setEdges(buildEdges(next));
    },
    [setNodes, setEdges, validation, llms, checkLabels],
  );

  // Model names and check names arrive after the workflow: redraw the nodes when they do.
  useEffect(() => {
    if (def) setNodes((current) => buildNodes(def, validation, current, llms, checkLabels));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [llms, checkLabels]);

  // Validate as the definition changes, without blocking typing.
  useEffect(() => {
    if (!def || !dirty) return;
    const timer = window.setTimeout(() => {
      api
        .post<ValidationResult>("/api/workflows/validate", def)
        .then(setValidation)
        .catch(() => undefined);
    }, 500);
    return () => window.clearTimeout(timer);
  }, [def, dirty]);

  const withLayout = (base: WorkflowDef): WorkflowDef => ({ ...base, layout: Object.fromEntries(nodes.map((n) => [n.id, { x: Math.round(n.position.x), y: Math.round(n.position.y) }])) });

  const save = async (): Promise<string | null> => {
    if (!def) return null;
    setSaving(true);
    try {
      let next = withLayout(def);
      if (isTemplate) {
        const suffix = Math.random().toString(36).slice(2, 8);
        next = { ...next, workflow: { ...next.workflow, id: `${next.workflow.id}-${suffix}`, name: `${next.workflow.name} (copy)` } };
      }
      const result = await api.put<{ saved: boolean; validation: ValidationResult; id?: string }>("/api/workflows", next);
      setValidation(result.validation);
      if (!result.saved) {
        toast({ tone: "bad", title: t("Not saved"), body: t("Fix the errors listed above the canvas first.") });
        return null;
      }
      setDirty(false);
      toast({ tone: "ok", title: isTemplate ? t("Saved as your own copy") : t("Workflow saved") });
      if (isTemplate && result.id) navigate("workflows", { id: result.id });
      return result.id ?? next.workflow.id;
    } catch (err) {
      toast({ tone: "bad", title: t("Save failed"), body: errorMessage(err) });
      return null;
    } finally {
      setSaving(false);
    }
  };

  const run = async (task: string, source: RunSource, approval: ApprovalMode) => {
    setStarting(true);
    try {
      const workflowId = dirty || isTemplate ? await save() : id;
      if (!workflowId) return;
      // A GitHub source is cloned before the run starts, so this can take a while.
      const data = await api.post<{ id: string }>("/api/runs", { workflow_id: workflowId, task, approval, ...sourcePayload(source) });
      navigate("sessions", { run: data.id });
    } catch (err) {
      toast({ tone: "bad", title: t("Could not start the run"), body: errorMessage(err) });
    } finally {
      setStarting(false);
    }
  };

  const onConnect = (connection: Connection) => {
    if (!def || !connection.source || !connection.target || connection.source === END || connection.target === START) return;
    if (def.edges.some((e) => sourcesOf(e).length === 1 && sourcesOf(e)[0] === connection.source && e.to === connection.target)) return;
    apply({ ...def, edges: [...def.edges, { from: connection.source, to: connection.target, when: null, label: null, max_loops: null, on_exhausted: null }] });
  };

  const removeNode = (nodeId: string) => {
    if (!def) return;
    apply({
      ...def,
      agents: def.agents.filter((a) => a.id !== nodeId),
      checks: (def.checks ?? []).filter((c) => c.id !== nodeId),
      human_approval: def.human_approval.filter((a) => a.id !== nodeId).map((a) => (a.on_reject === nodeId ? { ...a, on_reject: END } : a)),
      edges: def.edges
        .map((e) => ({ ...e, from: Array.isArray(e.from) ? e.from.filter((s) => s !== nodeId) : e.from }))
        .filter((e) => e.to !== nodeId && sourcesOf(e).length > 0 && sourcesOf(e)[0] !== nodeId)
        .map((e) => (Array.isArray(e.from) && e.from.length === 1 ? { ...e, from: e.from[0] ?? "" } : e)),
    });
    setSelection(null);
  };

  const removeEdge = (index: number) => {
    if (!def) return;
    apply({ ...def, edges: def.edges.filter((_, i) => i !== index) });
    setSelection(null);
  };

  const addAgent = () => {
    if (!def) return;
    let n = def.agents.length + 1;
    while (def.agents.some((a) => a.id === `agent_${n}`)) n++;
    const slot = Object.keys(def.models)[0] ?? "reasoning";
    const agent: AgentDef = {
      id: `agent_${n}`,
      role: `New Agent ${n}`,
      goal: "",
      responsibilities: [],
      model: slot,
      system_prompt: "## How you work\n- Describe how this agent should approach its job.\n- State what it must never do.\n- Say what a good result looks like.",
      tools: [],
      input: "The task and the work so far.",
      output_schema: "GenericOutput",
      loading_policy: "shared_instance",
      max_retries: 2,
      escalation: "fail",
      temperature: 0.2,
      max_tool_rounds: 30,
    };
    apply({ ...def, agents: [...def.agents, agent], models: def.models[slot] ? def.models : { ...def.models, [slot]: { label: t("Reasoning"), model_key: "", repo: null, quant: null, context_length: 16384, loading_policy: "load_on_demand", vram_gb: null, purpose: "", wide_model_key: "", wide_context_length: null } } });
    setSelection({ kind: "node", id: agent.id });
  };

  const addApproval = () => {
    if (!def) return;
    let n = def.human_approval.length + 1;
    while (def.human_approval.some((a) => a.id === `approval_${n}`)) n++;
    apply({ ...def, human_approval: [...def.human_approval, { id: `approval_${n}`, prompt: "Approve to continue?", show: null, on_reject: END, max_rejections: 2 }] });
    setSelection({ kind: "node", id: `approval_${n}` });
  };

  if (loaded.error) {
    return (
      <div className="p-7">
        <Note tone="bad" action={<Button size="sm" onClick={onBack}>{t("Back")}</Button>}>{loaded.error}</Note>
      </div>
    );
  }
  if (!def) return <div className="p-7"><SkeletonRows rows={6} height={60} /></div>;

  const selectedAgent = selection?.kind === "node" ? def.agents.find((a) => a.id === selection.id) : undefined;
  const selectedCheck = selection?.kind === "node" ? (def.checks ?? []).find((c) => c.id === selection.id) : undefined;
  const selectedApproval = selection?.kind === "node" ? def.human_approval.find((a) => a.id === selection.id) : undefined;
  const selectedEdgeIndex = selection?.kind === "edge" ? Number(edges.find((e) => e.id === selection.id)?.data?.index ?? -1) : -1;
  const selectedEdge = selectedEdgeIndex >= 0 ? def.edges[selectedEdgeIndex] : undefined;
  const errors = validation?.issues.filter((i) => i.level === "error") ?? [];
  const warnings = validation?.issues.filter((i) => i.level === "warning") ?? [];
  const unassigned = agentsWithoutModel(def).length;
  const takesEvidence = (def.checks ?? []).some((c) => c.kind === "evidence_dossier");

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex h-12 shrink-0 items-center gap-3 border-b border-line px-4">
        <IconButton label={t("Back to workflows")} onClick={onBack}>
          <ArrowLeft size={15} />
        </IconButton>
        <input
          value={def.workflow.name}
          onChange={(e) => apply({ ...def, workflow: { ...def.workflow, name: e.target.value } })}
          aria-label={t("Workflow name")}
          className="min-w-0 flex-1 rounded-sm bg-transparent text-sm font-semibold hover:bg-surface focus:bg-surface focus:outline-none"
        />
        {isTemplate && <Badge tone="off">{t("TEMPLATE")}</Badge>}
        {errors.length > 0 ? <Badge tone="bad">{errors.length} {errors.length === 1 ? t("ERROR") : t("ERRORS")}</Badge> : <Badge tone="ok"><CircleCheck size={10} /> {t("VALID")}</Badge>}
        {dirty && <span className="text-xs text-ink-3">{t("Unsaved changes")}</span>}
        <Button size="sm" icon={<Plus size={13} />} onClick={addAgent}>
          {t("Agent")}
        </Button>
        <Button size="sm" icon={<Plus size={13} />} onClick={addApproval}>
          {t("Approval")}
        </Button>
        <Button size="sm" icon={<FileCode size={13} />} onClick={() => void api.text(`/api/workflows/${id}/yaml`).then(setShowYaml).catch((err: unknown) => toast({ tone: "bad", title: t("Could not export"), body: errorMessage(err) }))}>
          {t("YAML")}
        </Button>
        <Button size="sm" icon={isTemplate ? <Copy size={13} /> : <Save size={13} />} loading={saving} disabled={!dirty && !isTemplate} onClick={() => void save()}>
          {isTemplate ? t("Save as copy") : t("Save")}
        </Button>
        <Button size="sm" variant="primary" icon={<Play size={13} />} disabled={errors.length > 0} onClick={() => setRunOpen(!runOpen)}>
          {t("Run")}
        </Button>
      </div>

      {(errors.length > 0 || warnings.length > 0) && (
        <div className="max-h-28 shrink-0 overflow-y-auto border-b border-line px-4 py-2">
          <ul className="grid gap-0.5 text-xs">
            {errors.map((issue) => (
              <li key={issue.message} className="text-bad">
                {issue.message}
              </li>
            ))}
            {warnings.map((issue) => (
              <li key={issue.message} className="text-warn">
                {issue.message}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="min-h-0 flex-1">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={NODE_TYPES}
            onNodesChange={(changes) => {
              onNodesChange(changes);
              if (changes.some((c) => c.type === "position" && c.dragging === false)) setDirty(true);
            }}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onNodeClick={(_, node) => setSelection(node.type === "terminal" ? null : { kind: "node", id: node.id })}
            onEdgeClick={(_, edge) => setSelection(edge.data ? { kind: "edge", id: edge.id } : null)}
            onPaneClick={() => setSelection(null)}
            onNodesDelete={(deleted) => deleted.forEach((n) => removeNode(n.id))}
            onEdgesDelete={(deleted) => {
              const index = Number(deleted[0]?.data?.index ?? -1);
              if (index >= 0) removeEdge(index);
            }}
            fitView
            fitViewOptions={{ padding: 0.15, maxZoom: 1 }}
            minZoom={0.3}
            maxZoom={1.5}
            proOptions={{ hideAttribution: false }}
          >
            <Background gap={22} size={1} color="var(--line)" />
            <Controls showInteractive={false} />
          </ReactFlow>
          </div>
          {runOpen && (
            <div className="rise-in shrink-0 border-t border-line bg-panel px-4 pt-3 pb-2.5">
              <RunComposer
                takesEvidence={takesEvidence}
                busy={starting}
                onStart={(task, source, approval) => void run(task, source, approval)}
                onClose={() => setRunOpen(false)}
                blocked={
                  unassigned > 0 ? (
                    <Note tone="warn" action={<Button size="sm" onClick={() => setSelection(null)}>{t("Choose models")}</Button>}>
                      {unassigned === 1 ? t("1 agent has no model yet.") : t("{n} agents have no model yet.", { n: unassigned })}{" "}
                      {t("Choose one for each in the Models panel, or one for everyone.")}
                    </Note>
                  ) : undefined
                }
              />
            </div>
          )}
        </div>
        <aside style={{ width: panel.width }} className={cx("relative flex shrink-0 flex-col border-line bg-panel", panel.side === "left" ? "order-first border-r" : "border-l")}>
          <div
            role="separator"
            aria-orientation="vertical"
            aria-label={t("Panel width")}
            aria-valuemin={PANEL_MIN}
            aria-valuemax={PANEL_MAX}
            aria-valuenow={panel.width}
            tabIndex={0}
            title={t("Drag to resize, double-click to reset")}
            onPointerDown={(e) => {
              drag.current = { x: e.clientX, width: panel.width };
              e.currentTarget.setPointerCapture(e.pointerId);
            }}
            onPointerMove={(e) => {
              const start = drag.current;
              if (!start) return;
              const delta = e.clientX - start.x;
              setPanel((p) => ({ ...p, width: clampPanelWidth(start.width + (p.side === "right" ? -delta : delta)) }));
            }}
            onPointerUp={(e) => {
              drag.current = null;
              e.currentTarget.releasePointerCapture(e.pointerId);
            }}
            onDoubleClick={() => setPanel((p) => ({ ...p, width: 360 }))}
            onKeyDown={(e) => {
              if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
              e.preventDefault();
              const wider = (e.key === "ArrowLeft") === (panel.side === "right");
              setPanel((p) => ({ ...p, width: clampPanelWidth(p.width + (wider ? 24 : -24)) }));
            }}
            className={cx("absolute top-0 bottom-0 z-[5] w-1.5 cursor-col-resize transition-colors duration-150 hover:bg-accent focus-visible:bg-accent", panel.side === "right" ? "-left-[3px]" : "-right-[3px]")}
          />
          <div className="flex h-9 shrink-0 items-center justify-between border-b border-line pr-2 pl-4">
            <span className="text-xs text-ink-3">{t("Side panel")}</span>
            <div role="radiogroup" aria-label={t("Panel position")} className="flex gap-0.5">
              {(["left", "right"] as const).map((side) => (
                <button
                  key={side}
                  type="button"
                  role="radio"
                  aria-checked={panel.side === side}
                  aria-label={side === "left" ? t("Panel on the left") : t("Panel on the right")}
                  title={side === "left" ? t("Panel on the left") : t("Panel on the right")}
                  onClick={() => setPanel((p) => ({ ...p, side }))}
                  className={cx("grid h-6 w-7 place-items-center rounded-sm transition-colors duration-150", panel.side === side ? "bg-raised text-ink" : "text-ink-3 hover:text-ink")}
                >
                  {side === "left" ? <PanelLeft size={14} aria-hidden /> : <PanelRight size={14} aria-hidden />}
                </button>
              ))}
            </div>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {selectedAgent && toolInfo.data && (
            <AgentPanel
              def={def}
              agent={selectedAgent}
              tools={toolInfo.data.tools}
              schemas={toolInfo.data.schemas}
              installed={llms}
              providers={providers}
              onChange={(patch) => apply({ ...def, agents: def.agents.map((a) => (a.id === selectedAgent.id ? { ...a, ...patch } : a)) })}
              onModel={(model) => apply(assignModel(def, [selectedAgent.id], model))}
              onDelete={() => removeNode(selectedAgent.id)}
            />
          )}
          {selectedCheck && (
            <CheckPanel
              def={def}
              check={selectedCheck}
              info={toolInfo.data?.checks.find((c) => c.kind === selectedCheck.kind)}
              onChange={(patch) => apply({ ...def, checks: (def.checks ?? []).map((c) => (c.id === selectedCheck.id ? { ...c, ...patch } : c)) })}
              onDelete={() => removeNode(selectedCheck.id)}
            />
          )}
          {selectedApproval && (
            <ApprovalPanel
              def={def}
              approval={selectedApproval}
              onChange={(patch) => apply({ ...def, human_approval: def.human_approval.map((a) => (a.id === selectedApproval.id ? { ...a, ...patch } : a)) })}
              onDelete={() => removeNode(selectedApproval.id)}
            />
          )}
          {selectedEdge && (
            <EdgePanel
              def={def}
              edge={selectedEdge}
              onChange={(patch) => apply({ ...def, edges: def.edges.map((e, i) => (i === selectedEdgeIndex ? { ...e, ...patch } : e)) })}
              onDelete={() => removeEdge(selectedEdgeIndex)}
            />
          )}
          {!selectedAgent && !selectedCheck && !selectedApproval && !selectedEdge && (
            <ModelsPanel
              def={def}
              installed={llms}
              providers={providers}
              onAssign={(agentIds, model) => apply(assignModel(def, agentIds, model))}
              onChange={(slot, patch) => apply({ ...def, models: { ...def.models, [slot]: { ...def.models[slot]!, ...patch } } })}
              onAllAutomatic={() => apply({ ...def, models: Object.fromEntries(Object.entries(def.models).map(([slot, value]) => [slot, { ...value, auto_tune: true }])) })}
            />
          )}
          </div>
        </aside>
      </div>

      {showYaml !== null && (
        <div className="fade-in fixed inset-0 z-[40] flex items-center justify-center bg-black/55" onMouseDown={() => setShowYaml(null)}>
          <div role="dialog" aria-modal="true" aria-label={t("Workflow YAML")} onMouseDown={(e) => e.stopPropagation()} className="z-[50] flex max-h-[80vh] w-[760px] flex-col rounded-lg border border-line-strong bg-panel" style={{ boxShadow: "var(--shadow)" }}>
            <div className="flex items-center justify-between border-b border-line px-4 py-3">
              <span className="text-sm font-semibold">workflow.yaml {dirty && <span className="font-normal text-ink-3">{t("(last saved version)")}</span>}</span>
              <div className="flex gap-2">
                <Button size="sm" onClick={() => void navigator.clipboard.writeText(showYaml).then(() => toast({ tone: "ok", title: t("Copied") }))}>
                  {t("Copy")}
                </Button>
                <Button size="sm" onClick={() => setShowYaml(null)}>
                  {t("Close")}
                </Button>
              </div>
            </div>
            <pre className="selectable overflow-auto p-4 font-mono text-xs leading-relaxed text-ink-2">{showYaml}</pre>
          </div>
        </div>
      )}
    </div>
  );
}

// -- list ---------------------------------------------------------------------

export function WorkflowsPage() {
  const routeId = useApp((s) => s.params.id);
  const navigate = useApp((s) => s.navigate);
  const toast = useApp((s) => s.toast);
  const list = useLoad(() => api.get<{ workflows: WorkflowInfo[] }>("/api/workflows"), [routeId]);
  // The list shows which workflow is working: it has to notice when one starts or ends.
  useInterval(list.reload, routeId ? null : 5000);

  if (routeId) {
    return (
      <ReactFlowProvider key={routeId}>
        <Editor id={routeId} onBack={() => navigate("workflows")} />
      </ReactFlowProvider>
    );
  }

  const workflows = list.data?.workflows ?? [];
  const mine = workflows.filter((w) => w.source === "user");
  const templates = workflows.filter((w) => w.source === "template");
  const groups = [
    {
      title: t("Verification workflows"),
      aside: t("No agent is trusted: code decides what passes"),
      items: templates.filter((w) => w.category === "verification"),
    },
    { title: t("Team templates"), aside: t("Each role builds on the one before"), items: templates.filter((w) => w.category !== "verification") },
  ];

  const remove = (id: string) => {
    api
      .del(`/api/workflows/${id}`)
      .then(list.reload)
      .catch((err: unknown) => toast({ tone: "bad", title: t("Could not delete"), body: errorMessage(err) }));
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title={t("Workflows")}
        subtitle={t("A workflow is a graph of agents, approvals and conditions. It is stored as YAML, validated, then compiled to LangGraph when it runs.")}
        actions={<Button variant="primary" onClick={() => navigate("teams")}>{t("Build AI Team")}</Button>}
      />
      <div className="min-h-0 flex-1 overflow-y-auto px-7 py-6">
        <div className="grid max-w-[980px] gap-9">
          {list.loading && !list.data && <SkeletonRows rows={5} height={52} />}
          {list.error && <Note tone="bad">{list.error}</Note>}
          {list.data && (
            <>
              <Section title={t("Your workflows")}>
                {mine.length === 0 ? (
                  <EmptyState title={t("No workflows of your own yet")}>
                    {t("Build an AI team to generate one with models assigned, or open a template below and save it as your own copy.")}
                  </EmptyState>
                ) : (
                  <ul className="border-t border-line">
                    {mine.map((w) => {
                      // A workflow that is working opens on its run, the same view as in Sessions. Its editor is one click further.
                      const open = () => (w.running_run ? navigate("sessions", { run: w.running_run }) : navigate("workflows", { id: w.id }));
                      return (
                      <li key={w.id} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-4 border-b border-line py-3">
                        <button type="button" onClick={open} className="min-w-0 text-left">
                          <div className="flex items-center gap-2">
                            {w.running_run && <StatusDot tone="ok" pulse />}
                            <span className="truncate font-medium hover:text-accent">{w.name}</span>
                            {w.running_run && <Badge tone="ok">{t("Running").toUpperCase()}</Badge>}
                            {!w.runnable && <Badge tone="warn">{t("NEEDS MODELS")}</Badge>}
                          </div>
                          <div className="truncate text-xs text-ink-3">
                            {t("{n} agents", { n: w.agents })}
                            {w.checks > 0 ? ` · ${t("{n} checks in code", { n: w.checks })}` : ""} · {t("edited {when}", { when: timeAgo(w.updated_at) })}
                          </div>
                        </button>
                        <div className="flex gap-1">
                          {w.running_run && (
                            <Button size="sm" variant="ghost" onClick={() => navigate("workflows", { id: w.id })}>
                              {t("Edit")}
                            </Button>
                          )}
                          <Button size="sm" onClick={open}>
                            {t("Open")}
                          </Button>
                          <IconButton label={t("Delete {name}", { name: w.name })} onClick={() => remove(w.id)}>
                            <Trash2 size={14} />
                          </IconButton>
                        </div>
                      </li>
                      );
                    })}
                  </ul>
                )}
              </Section>
              {groups.map(
                (group) =>
                  group.items.length > 0 && (
                    <Section key={group.title} title={group.title} aside={group.aside}>
                      <ul className="border-t border-line">
                        {group.items.map((w) => (
                          <li key={w.id} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-4 border-b border-line py-3">
                            <div className="min-w-0">
                              <div className="flex items-baseline gap-2.5">
                                <span className="font-medium">{w.name}</span>
                                <span className="text-xs text-ink-3">
                                  {t("{n} agents", { n: w.agents })}
                                  {w.checks > 0 ? ` · ${t("{n} checks in code", { n: w.checks })}` : ""}
                                </span>
                              </div>
                              <div className="mt-0.5 max-w-[80ch] text-xs text-ink-2">{w.description}</div>
                            </div>
                            <Button size="sm" onClick={() => navigate("workflows", { id: w.id })}>
                              {t("Open")}
                            </Button>
                          </li>
                        ))}
                      </ul>
                    </Section>
                  ),
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
