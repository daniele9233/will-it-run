import { ArrowLeft, Download, Play, Workflow as WorkflowIcon } from "lucide-react";
import { useState } from "react";

import {
  AlignmentTag,
  Badge,
  Button,
  Chip,
  Collapsible,
  Field,
  FitBadge,
  Note,
  PageHeader,
  Section,
  Segmented,
  Select,
  SkeletonRows,
  TextArea,
  TextInput,
  WhyList,
  cx,
  toneBg,
} from "../components/ui";
import { api } from "../lib/api";
import { categoryLabel, fitLabel, sizeLabel, titleCase, type Tone } from "../lib/format";
import { errorMessage, useLoad } from "../lib/hooks";
import { useApp } from "../lib/store";
import type { ModelAssignment, ModelOption, RoleModel, SpecGroup, TeamPlan, TeamSummary } from "../lib/types";
import { t } from "../lib/i18n";

type Mode = "automatic" | "guided" | "expert";
type AlignmentChoice = "any" | "standard" | "uncensored";

interface SavedTeam {
  id: string;
  name: string;
  workflow_id: string;
  created_at: string;
  plan: { summary?: TeamSummary; models?: { name: string; quant: string; needs_download: boolean }[] };
}

const POLICY_LABEL: Record<string, string> = {
  keep_loaded: "KEEP LOADED",
  load_on_demand: "LOAD ON DEMAND",
  unload_after_use: "UNLOAD AFTER USE",
  shared_instance: "SHARED INSTANCE",
  sequential: "SEQUENTIAL",
  parallel: "PARALLEL",
};

const MODEL_TONES: Tone[] = ["accent", "ok", "warn", "limited"];

function MemoryTimeline({ plan }: { plan: TeamPlan }) {
  const rp = plan.resource_plan;
  const scale = Math.max(rp.usable_vram_gb, rp.peak_vram_gb, 1);
  const toneFor = (model: string): Tone => MODEL_TONES[plan.models.findIndex((m) => m.card.entry.name === model) % MODEL_TONES.length] ?? "accent";
  return (
    <div>
      <div className="grid gap-1.5">
        {rp.timeline.map((segment) => (
          <div key={segment.agent} className="grid grid-cols-[170px_1fr_150px] items-center gap-3 text-xs">
            <span className="truncate text-ink-2" title={segment.role}>
              {segment.role}
            </span>
            <div className="relative h-4 rounded-sm bg-raised">
              <div
                className={cx("absolute top-0 left-0 h-full rounded-sm", toneBg[toneFor(segment.model)])}
                style={{ width: `${(segment.vram_gb / scale) * 100}%`, opacity: segment.switch ? 1 : 0.55 }}
                title={`${segment.model}: ${segment.vram_gb} GB`}
              />
              {segment.loaded_vram_gb > segment.vram_gb && (
                <div
                  className="absolute top-0 h-full rounded-r-sm bg-line-strong"
                  style={{ left: `${(segment.vram_gb / scale) * 100}%`, width: `${((segment.loaded_vram_gb - segment.vram_gb) / scale) * 100}%` }}
                  title={t("Other loaded models: {n} GB", { n: (segment.loaded_vram_gb - segment.vram_gb).toFixed(1) })}
                />
              )}
              <div className="absolute top-[-3px] h-[22px] w-px bg-ink-2" style={{ left: `${(rp.usable_vram_gb / scale) * 100}%` }} aria-hidden />
            </div>
            <span className="tabular text-ink-3">
              {segment.vram_gb > 0 ? `${segment.vram_gb} GB` : "CPU"} · {segment.switch ? t("loads model") : t("shared instance")}
            </span>
          </div>
        ))}
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-ink-3">
        {plan.models.map((m, i) => (
          <span key={m.card.entry.repo + m.card.quant} className="inline-flex items-center gap-1.5">
            <span className={cx("h-2 w-2 rounded-sm", toneBg[MODEL_TONES[i % MODEL_TONES.length] ?? "accent"])} />
            {m.card.entry.name}
          </span>
        ))}
        <span className="inline-flex items-center gap-1.5">
          <span className="h-3 w-px bg-ink-2" /> {t("recommended maximum ({n} GB)", { n: rp.usable_vram_gb })}
        </span>
      </div>
    </div>
  );
}

const optionKey = (o: { repo: string; quant: string }) => `${o.repo}|${o.quant}`;

function optionText(option: ModelOption): string {
  return [`${option.name} ${option.quant}`, `${option.score.toFixed(1)}/10`, option.runnable ? fitLabel(option.fit) : t("does not fit this PC")].join(" · ");
}

/** The plan read role by role: who does what, and on which model. */
function RoleTable({ plan, mode, onPick, onReset }: { plan: TeamPlan; mode: Mode; onPick: (agent: string, option: ModelOption) => void; onReset: (agent: string) => void }) {
  const toneOf = (row: RoleModel): Tone => MODEL_TONES[plan.models.findIndex((m) => m.card.entry.repo === row.repo && m.card.quant === row.quant) % MODEL_TONES.length] ?? "accent";
  const editable = mode !== "automatic";
  return (
    <ol className="border-t border-line">
      {plan.roles.map((row, index) => {
        const options = plan.choices[row.kind] ?? [];
        const downloaded = options.filter((o) => o.installed);
        const toDownload = options.filter((o) => !o.installed);
        return (
          <li key={row.agent} className="grid grid-cols-[22px_minmax(0,1fr)_minmax(0,1.15fr)] items-start gap-x-4 border-b border-line py-3">
            <span className="tabular pt-0.5 text-xs text-ink-3">{index + 1}</span>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{row.role}</span>
                <Badge tone="off">{categoryLabel(row.kind_label).toUpperCase()}</Badge>
              </div>
              <div className="mt-0.5 line-clamp-2 text-xs text-ink-3" title={row.goal}>
                {row.goal}
              </div>
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className={cx("h-2 w-2 shrink-0 rounded-sm", toneBg[toneOf(row)])} aria-hidden />
                <span className="truncate font-medium">{row.model}</span>
                <span className="font-mono text-xs text-ink-2">{row.quant}</span>
                {row.manual && <Badge tone="accent">{t("YOUR CHOICE")}</Badge>}
              </div>
              <div className="tabular mt-0.5 text-xs text-ink-3">
                {t("{score}/10 for this kind of work", { score: row.score.toFixed(1) })} · ~{Math.round(row.tokens_per_second)} tok/s · {row.vram_gb > 0 ? `${row.vram_gb} GB VRAM` : "CPU"} ·{" "}
                {row.installed ? t("already installed") : t("to download")}
              </div>
              {row.loads_model && index > 0 && <div className="mt-0.5 text-xs text-ink-2">{t("Changes the model in memory: this step waits for a reload.")}</div>}
              {editable && options.length > 0 && (
                <div className="mt-2 flex items-center gap-2">
                  <Select
                    value={optionKey(row)}
                    onChange={(e) => {
                      const option = options.find((o) => optionKey(o) === e.target.value);
                      if (option && optionKey(option) !== optionKey(row)) onPick(row.agent, option);
                    }}
                    className="h-7 min-w-0 flex-1 text-xs"
                    aria-label={t("Model for {role}", { role: row.role })}
                  >
                    {!options.some((o) => optionKey(o) === optionKey(row)) && <option value={optionKey(row)}>{`${row.model} ${row.quant}`}</option>}
                    {downloaded.length > 0 && (
                      <optgroup label={t("Downloaded")}>
                        {downloaded.map((o) => (
                          <option key={optionKey(o)} value={optionKey(o)}>
                            {optionText(o)}
                          </option>
                        ))}
                      </optgroup>
                    )}
                    {toDownload.length > 0 && (
                      <optgroup label={t("To download")}>
                        {toDownload.map((o) => (
                          <option key={optionKey(o)} value={optionKey(o)}>
                            {optionText(o)} · {o.download_gb} GB
                          </option>
                        ))}
                      </optgroup>
                    )}
                  </Select>
                  {row.manual && (
                    <Button size="sm" variant="ghost" onClick={() => onReset(row.agent)}>
                      {t("Automatic")}
                    </Button>
                  )}
                </div>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function ModelRow({ assignment, plan, mode, onOverride }: { assignment: ModelAssignment; plan: TeamPlan; mode: Mode; onOverride: (slot: string, repo: string, quant: string, installedKey: string | null) => void }) {
  const { card } = assignment;
  const roles = plan.agents.filter((a) => assignment.agents.includes(a.id)).map((a) => a.role);
  return (
    <li className="border-b border-line py-3.5">
      <div className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-4">
        <div className="min-w-0">
          <div className="text-xs text-ink-3">{assignment.label}</div>
          <div className="mt-0.5 flex flex-wrap items-center gap-2">
            <span className="font-medium">{card.entry.name}</span>
            <span className="font-mono text-xs text-ink-2">{card.quant}</span>
            <AlignmentTag alignment={card.entry.alignment} />
            <FitBadge category={card.fit.category} />
          </div>
          <div className="mt-1 text-xs text-ink-3">
            {assignment.vram_gb > 0 ? `${assignment.vram_gb} GB VRAM` : `${assignment.ram_gb} GB RAM (CPU)`} · ~{Math.round(card.fit.speed.tokens_per_second)} tok/s ·{" "}
            {assignment.needs_download ? t("download {size}", { size: sizeLabel(card.download_size_bytes, card.size_is_estimate) }) : t("already installed")}
          </div>
          <div className="mt-1.5 text-xs text-ink-2">
            {roles.length === 1 ? t("1 agent") : t("{n} agents", { n: roles.length })}: {roles.join(", ")}
          </div>
        </div>
        <Badge tone={assignment.loading_policy === "keep_loaded" ? "ok" : "accent"}>{POLICY_LABEL[assignment.loading_policy]}</Badge>
      </div>
      {mode !== "automatic" && assignment.alternatives.length > 0 && (
        <div className="mt-2.5 flex items-center gap-2 text-xs text-ink-3">
          {t("Use a different model:")}
          <Select
            value=""
            onChange={(e) => {
              const alt = assignment.alternatives.find((a) => `${a.entry.repo}|${a.quant}` === e.target.value);
              if (alt) for (const slot of assignment.slots) onOverride(slot, alt.entry.repo, alt.quant, alt.installed_key);
            }}
            className="h-7 w-[340px] text-xs"
            aria-label={t("Change model for {name}", { name: assignment.label })}
          >
            <option value="">{t("Keep {name}", { name: card.entry.name })}</option>
            {assignment.alternatives.map((alt) => (
              <option key={`${alt.entry.repo}|${alt.quant}`} value={`${alt.entry.repo}|${alt.quant}`}>
                {`${alt.entry.name} ${alt.quant} · ${fitLabel(alt.fit.category)} · ${t("score {n}", { n: alt.score.agentfit_score.toFixed(1) })}`}
                {alt.installed ? ` · ${t("installed")}` : ""}
              </option>
            ))}
          </Select>
        </div>
      )}
      <Collapsible title={t("Why this model?")}>
        <WhyList lines={assignment.why} />
      </Collapsible>
    </li>
  );
}

function PlanView({
  plan,
  summary,
  mode,
  busy,
  onBack,
  onCreate,
  onOverride,
  onRolePick,
  onRoleReset,
}: {
  plan: TeamPlan;
  summary: TeamSummary;
  mode: Mode;
  busy: boolean;
  onBack: () => void;
  onCreate: () => void;
  onOverride: (slot: string, repo: string, quant: string, installedKey: string | null) => void;
  onRolePick: (agent: string, option: ModelOption) => void;
  onRoleReset: (agent: string) => void;
}) {
  const rp = plan.resource_plan;
  const statusTone: Tone = rp.status === "SAFE" ? "ok" : rp.status === "TIGHT" ? "limited" : "bad";
  return (
    <div className="grid max-w-[1040px] gap-8">
      <button type="button" onClick={onBack} className="inline-flex items-center gap-1.5 justify-self-start text-sm text-ink-2 hover:text-ink">
        <ArrowLeft size={14} /> {t("Change the team")}
      </button>

      <div className="grid grid-cols-[minmax(0,1fr)_auto] items-end gap-6">
        <div>
          <div className="text-sm text-ink-2">{t("Will It Run recommendation")}</div>
          <h2 className="mt-0.5 text-2xl font-semibold tracking-tight">{plan.name}</h2>
        </div>
        <Button variant="primary" disabled={!plan.can_build} loading={busy} onClick={onCreate}>
          {t("Build Team")}
        </Button>
      </div>

      <dl className="grid grid-cols-[repeat(auto-fit,minmax(120px,1fr))] gap-x-6 gap-y-4 border-y border-line py-5">
        {(
          [
            [t("Agents"), String(summary.agents)],
            [t("Models"), String(summary.models)],
            [t("Download size"), summary.download_gb > 0 ? `${summary.download_gb} GB` : t("Nothing")],
            [t("Peak VRAM"), `${summary.peak_vram_gb} GB`],
            ["RAM", summary.ram_gb > 0 ? `${summary.ram_gb} GB` : t("Minimal")],
            [t("Model switching"), `${summary.model_switches_per_run} / run`],
            [t("Expected speed"), summary.expected_speed],
            [t("Model preference"), summary.model_preference],
          ] as [string, string][]
        ).map(([label, value]) => (
          <div key={label}>
            <dt className="text-xs text-ink-3">{label}</dt>
            <dd className="tabular mt-0.5 text-lg font-semibold tracking-tight">{value}</dd>
          </div>
        ))}
        <div>
          <dt className="text-xs text-ink-3">{t("Fit")}</dt>
          <dd className="mt-1.5">
            <FitBadge category={plan.fit} solid />
          </dd>
        </div>
      </dl>

      {plan.blockers.map((blocker) => (
        <Note key={blocker} tone="bad">
          <div className="font-medium text-ink">{t("This team cannot be built yet.")}</div>
          <div className="mt-0.5">{blocker.replace(/^NOT SAFE: /, "")}</div>
        </Note>
      ))}
      {plan.notes.map((note) => (
        <Note key={note} tone={note.startsWith("Option:") ? "accent" : "warn"}>
          {note}
        </Note>
      ))}

      {rp.optimization.needed && (
        <Note tone="ok">
          <div className="font-medium text-ink">{t("Memory limit handled automatically")}</div>
          <div className="mt-1 grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5">
            <span className="text-ink-3">{t("Before")}</span>
            <span>
              {t("{n} models loaded together, {gb} GB peak", { n: rp.optimization.before_models_loaded, gb: rp.optimization.before_peak_gb })}
            </span>
            <span className="text-ink-3">{t("After")}</span>
            <span>
              {t("{persistent} persistent model, {on_demand} loaded on demand, {gb} GB peak", { persistent: rp.optimization.after_persistent, on_demand: rp.optimization.after_on_demand, gb: rp.optimization.after_peak_gb })}
            </span>
          </div>
        </Note>
      )}

      <Section
        title={t("Which model does which job")}
        aside={mode === "automatic" ? t("In running order") : t("In running order. Change any role's model below.")}
      >
        <RoleTable plan={plan} mode={mode} onPick={onRolePick} onReset={onRoleReset} />
        <p className="mt-2.5 max-w-[80ch] text-xs text-ink-3">
          {t("The score says how well a model does that kind of work on this PC, speed and fit included. Roles with the same model share it in memory; each different model you add is one more reload per run.")}
        </p>
      </Section>

      <Section title={t("Models")} aside={summary.models === 1 ? t("{agents} agents share 1 model instance", { agents: summary.agents }) : t("{agents} agents share {models} model instances", { agents: summary.agents, models: summary.models })}>
        <ul className="border-t border-line">
          {plan.models.map((assignment) => (
            <ModelRow key={assignment.card.entry.repo + assignment.card.quant} assignment={assignment} plan={plan} mode={mode} onOverride={onOverride} />
          ))}
        </ul>
      </Section>

      <Section
        title={t("Workflow memory plan")}
        aside={
          <span className="inline-flex items-center gap-2">
            {t("Peak VRAM {peak} / {usable} GB", { peak: rp.peak_vram_gb, usable: rp.usable_vram_gb })} <Badge tone={statusTone}>{rp.status}</Badge>
          </span>
        }
      >
        <MemoryTimeline plan={plan} />
      </Section>

      {rp.disk && (
        <Section title={t("Disk")}>
          <Note tone={rp.disk.safe ? "ok" : "bad"}>{rp.disk.verdict}</Note>
        </Section>
      )}

      <Section title={t("Why this plan?")}>
        <ul className="selectable grid max-w-[75ch] gap-1.5 text-sm text-ink-2">
          {plan.explanations.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      </Section>

      {mode === "expert" && (
        <div>
          <Collapsible title={t("Workflow definition (YAML)")}>
            <pre className="max-h-[420px] overflow-auto rounded-md border border-line bg-bg p-3 font-mono text-xs leading-relaxed text-ink-2">{plan.workflow_yaml}</pre>
          </Collapsible>
        </div>
      )}
    </div>
  );
}

function Created({ plan, onRun }: { plan: TeamPlan; onRun: (task: string) => void }) {
  const navigate = useApp((s) => s.navigate);
  const toast = useApp((s) => s.toast);
  const [task, setTask] = useState("");
  const [queued, setQueued] = useState(false);
  const missing = plan.models.filter((m) => m.needs_download);

  const download = () => {
    Promise.all(
      missing.map((m) => api.post("/api/downloads", { repo: m.card.entry.repo, filename: m.card.file?.filename ?? "", quant: m.card.quant })),
    )
      .then(() => {
        setQueued(true);
        toast({ tone: "ok", title: missing.length === 1 ? t("1 download started") : t("{n} downloads started", { n: missing.length }) });
      })
      .catch((err: unknown) => toast({ tone: "bad", title: t("Download not started"), body: errorMessage(err) }));
  };

  return (
    <div className="grid max-w-[720px] gap-7">
      <div>
        <Badge tone="ok">{t("TEAM CREATED")}</Badge>
        <h2 className="mt-2 text-2xl font-semibold tracking-tight">{plan.name}</h2>
        <p className="mt-1 text-ink-2">
          {plan.models.length === 1 ? t("{agents} agents on 1 model instance.", { agents: plan.agents.length }) : t("{agents} agents on {models} model instances.", { agents: plan.agents.length, models: plan.models.length })}{" "}
              {t("The workflow is saved and can be edited visually.")}
        </p>
      </div>
      {missing.length > 0 ? (
        <Section title={t("Install the team's models")}>
          <ul className="mb-3 border-t border-line">
            {missing.map((m) => (
              <li key={m.card.entry.repo} className="flex items-center justify-between border-b border-line py-2.5 text-sm">
                <span>
                  {m.card.entry.name} <span className="font-mono text-xs text-ink-2">{m.card.quant}</span>
                </span>
                <span className="tabular text-ink-3">{m.download_gb} GB</span>
              </li>
            ))}
          </ul>
          <div className="flex gap-2">
            <Button variant="primary" icon={<Download size={13} />} disabled={queued} onClick={download}>
              {queued ? t("Downloading") : missing.length === 1 ? t("Download model") : t("Download models")}
            </Button>
            {queued && <Button onClick={() => navigate("downloads")}>{t("View downloads")}</Button>}
          </div>
          <p className="mt-2 text-xs text-ink-3">{t("The team can run as soon as LM Studio lists the models. Benchmark them afterwards to replace speed estimates with measurements.")}</p>
        </Section>
      ) : (
        <Section title={t("Run the team")}>
          <Field label={t("What should the team do?")}>
            <TextArea rows={4} value={task} onChange={(e) => setTask(e.target.value)} placeholder={t("Describe the task in a few sentences. The first agent turns it into a brief with acceptance criteria.")} />
          </Field>
          <div className="mt-3 flex gap-2">
            <Button variant="primary" icon={<Play size={13} />} disabled={!task.trim()} onClick={() => onRun(task)}>
              {t("Run")}
            </Button>
          </div>
        </Section>
      )}
      <div className="flex gap-2 border-t border-line pt-5">
        <Button icon={<WorkflowIcon size={13} />} onClick={() => navigate("workflows", { id: plan.workflow.workflow.id })}>
          {t("Open in workflow editor")}
        </Button>
      </div>
    </div>
  );
}

export function TeamsPage() {
  const settings = useApp((s) => s.settings);
  const navigate = useApp((s) => s.navigate);
  const toast = useApp((s) => s.toast);
  const specs = useLoad(() => api.get<{ groups: SpecGroup[] }>("/api/specializations"));
  const teams = useLoad(() => api.get<{ teams: SavedTeam[] }>("/api/teams"));
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [description, setDescription] = useState("");
  const [name, setName] = useState("");
  const [mode, setMode] = useState<Mode>(settings?.ui.mode ?? "guided");
  const [alignment, setAlignment] = useState<AlignmentChoice>(settings?.models.alignment_preference ?? "any");
  const [strategy, setStrategy] = useState<"auto" | "single" | "specialists">("auto");
  const [overrides, setOverrides] = useState<Record<string, { repo: string; quant: string; installed_key: string | null }>>({});
  // A model picked for one agent. That agent then stops sharing a model with the others.
  const [agentOverrides, setAgentOverrides] = useState<Record<string, { repo: string; quant: string; installed_key: string | null }>>({});
  const [planned, setPlanned] = useState<{ plan: TeamPlan; summary: TeamSummary } | null>(null);
  const [created, setCreated] = useState<TeamPlan | null>(null);
  const [busy, setBusy] = useState(false);

  const request = (extra: Record<string, unknown> = {}) => ({
    specializations: [...selected],
    description,
    name: name.trim() || null,
    mode,
    alignment: alignment === "uncensored" ? "uncensored" : alignment === "standard" ? "standard" : null,
    model_strategy: strategy,
    overrides,
    agent_overrides: agentOverrides,
    ...extra,
  });

  const plan = (nextOverrides = overrides, nextAgentOverrides = agentOverrides) => {
    setBusy(true);
    api
      .post<{ plan: TeamPlan; summary: TeamSummary }>("/api/teams/plan", request({ overrides: nextOverrides, agent_overrides: nextAgentOverrides }))
      .then(setPlanned)
      .catch((err: unknown) => toast({ tone: "bad", title: t("Could not plan this team"), body: errorMessage(err) }))
      .finally(() => setBusy(false));
  };

  const create = () => {
    setBusy(true);
    api
      .post<{ plan: TeamPlan }>("/api/teams", request())
      .then((data) => {
        setCreated(data.plan);
        teams.reload();
      })
      .catch((err: unknown) => toast({ tone: "bad", title: t("Could not build this team"), body: errorMessage(err) }))
      .finally(() => setBusy(false));
  };

  const run = (workflowId: string, task: string) => {
    api
      .post<{ id: string }>("/api/runs", { workflow_id: workflowId, task })
      .then((data) => navigate("sessions", { run: data.id }))
      .catch((err: unknown) => toast({ tone: "bad", title: t("Could not start the run"), body: errorMessage(err) }));
  };

  const toggle = (id: string) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  };

  const reset = () => {
    setPlanned(null);
    setCreated(null);
    setOverrides({});
    setAgentOverrides({});
  };

  const unsupported = (specs.data?.groups ?? []).flatMap((g) => g.items).filter((i) => selected.has(i.id) && i.unsupported_modality);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader title={t("Build AI Team")} subtitle={t("Say what the team should be good at. Will It Run picks the agents, the fewest models that cover them, and a loading plan that fits this PC.")} />
      <div className="min-h-0 flex-1 overflow-y-auto px-7 py-6">
        {created ? (
          <div className="grid gap-6">
            <Created plan={created} onRun={(task) => run(created.workflow.workflow.id, task)} />
            <button type="button" onClick={reset} className="justify-self-start text-sm text-accent hover:underline">
              {t("Build another team")}
            </button>
          </div>
        ) : planned ? (
          <PlanView
            plan={planned.plan}
            summary={planned.summary}
            mode={mode}
            busy={busy}
            onBack={() => {
              setPlanned(null);
              setOverrides({});
              setAgentOverrides({});
            }}
            onCreate={create}
            onOverride={(slot, repo, quant, installedKey) => {
              const next = { ...overrides, [slot]: { repo, quant, installed_key: installedKey } };
              setOverrides(next);
              plan(next);
            }}
            onRolePick={(agent, option) => {
              const next = { ...agentOverrides, [agent]: { repo: option.repo, quant: option.quant, installed_key: option.installed_key } };
              setAgentOverrides(next);
              plan(overrides, next);
            }}
            onRoleReset={(agent) => {
              const next = Object.fromEntries(Object.entries(agentOverrides).filter(([id]) => id !== agent));
              setAgentOverrides(next);
              plan(overrides, next);
            }}
          />
        ) : (
          <div className="grid max-w-[1040px] gap-8">
            <Section title={t("What would you like your AI team to specialize in?")} aside={t("Choose one or several")}>
              {specs.loading && !specs.data && <SkeletonRows rows={6} height={34} />}
              {specs.error && <Note tone="bad">{specs.error}</Note>}
              <div className="grid gap-3">
                {specs.data?.groups.map((group) => (
                  <div key={group.group} className="grid grid-cols-[130px_1fr] items-baseline gap-3">
                    <div className="text-sm text-ink-3">{group.group}</div>
                    <div className="flex flex-wrap gap-1.5">
                      {group.items.map((item) => (
                        <Chip key={item.id} active={selected.has(item.id)} onClick={() => toggle(item.id)}>
                          {item.label}
                        </Chip>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
              {unsupported.length > 0 && (
                <div className="mt-3">
                  <Note tone="warn">
                    {titleCase(unsupported[0]?.unsupported_modality ?? "image")} generation models do not run in LM Studio. Will It Run will build the prompt-writing and review agents; the generator itself needs a separate runtime such as ComfyUI, which you connect on the Images page.
                  </Note>
                </div>
              )}
            </Section>

            <Section title={t("Custom specialization")} aside={t("Optional")}>
              <TextArea
                rows={3}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder={t("Describe the AI team you want, e.g. a team specialized in enterprise PostgreSQL Patroni clusters, WAL management, pgBackRest, performance and documentation.")}
                aria-label={t("Describe the AI team you want")}
              />
            </Section>

            <Field
              label={t("How many models")}
              hint={
                strategy === "single"
                  ? t("One model serves every agent. No waiting between steps.")
                  : strategy === "specialists"
                    ? t("The best model for each kind of role. Only one is in memory at a time, so a step that changes model waits for a reload. You can change each one in the plan.")
                    : t("Will It Run uses more than one model only when it clearly beats a shared one.")
              }
            >
              <Segmented
                label={t("How many models")}
                value={strategy}
                onChange={setStrategy}
                options={[
                  { value: "auto", label: t("Automatic") },
                  { value: "single", label: t("One for all") },
                  { value: "specialists", label: t("One per role") },
                ]}
              />
            </Field>

            <div className="grid grid-cols-[1fr_1fr_1fr] items-end gap-5">
              <Field label={t("Mode")} hint={mode === "automatic" ? t("Will It Run decides everything.") : mode === "guided" ? t("Will It Run recommends, you can swap models.") : t("Also shows the workflow definition.")}>
                <Segmented
                  label={t("Mode")}
                  value={mode}
                  onChange={setMode}
                  options={[
                    { value: "automatic", label: t("Automatic") },
                    { value: "guided", label: t("Guided") },
                    { value: "expert", label: t("Expert") },
                  ]}
                />
              </Field>
              <Field label={t("Models")} hint={alignment === "any" ? t("Standard and uncensored models compete on equal terms.") : alignment === "uncensored" ? t("Only uncensored models are used.") : t("Only standard models are used.")}>
                <Segmented
                  label={t("Model alignment")}
                  value={alignment}
                  onChange={setAlignment}
                  options={[
                    { value: "any", label: t("Any") },
                    { value: "standard", label: t("Standard") },
                    { value: "uncensored", label: t("Uncensored") },
                  ]}
                />
              </Field>
              <Field label={t("Team name")} hint={t("Optional")}>
                <TextInput value={name} onChange={(e) => setName(e.target.value)} placeholder={t("Named after its specializations")} />
              </Field>
            </div>

            <div>
              <Button variant="primary" loading={busy} disabled={selected.size === 0 && !description.trim()} onClick={() => plan()}>
                {t("Plan Team")}
              </Button>
            </div>

            {(teams.data?.teams.length ?? 0) > 0 && (
              <Section title={t("Your teams")}>
                <ul className="border-t border-line">
                  {teams.data?.teams.map((team) => (
                    <li key={team.id} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-4 border-b border-line py-3">
                      <div className="min-w-0">
                        <div className="truncate font-medium">{team.name}</div>
                        <div className="text-xs text-ink-3">
                          {team.plan.summary ? `${team.plan.summary.agents} agents · ${team.plan.summary.models} models · peak ${team.plan.summary.peak_vram_gb} GB VRAM` : ""}
                          {team.plan.models?.some((m) => m.needs_download) ? ` · ${t("models to download")}` : ""}
                        </div>
                      </div>
                      <div className="flex gap-2">
                        <Button size="sm" onClick={() => navigate("workflows", { id: team.workflow_id })}>
                          {t("Open workflow")}
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => void api.del(`/api/teams/${team.id}`).then(teams.reload)}>
                          {t("Remove")}
                        </Button>
                      </div>
                    </li>
                  ))}
                </ul>
              </Section>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
