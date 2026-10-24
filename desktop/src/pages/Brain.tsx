import { ChevronRight, FolderGit2, FolderOpen, Pencil, Plus, Search, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { BrainGraph } from "../components/BrainGraph";
import { Badge, Button, Chip, EmptyState, Field, Note, Select, SkeletonRows, Tabs, TextArea, TextInput, Toggle, cx } from "../components/ui";
import { api, query } from "../lib/api";
import { KIND, KINDS, viaLabel } from "../lib/brainGraph";
import { timeAgo } from "../lib/format";
import { errorMessage, useDebounced, useInterval, useLoad } from "../lib/hooks";
import { t } from "../lib/i18n";
import { useApp } from "../lib/store";
import type { BrainLink, BrainMemory, BrainOverview, BrainProject, BrainRow, BrainRows, EmbedStatus, MemoryKind } from "../lib/types";

/** What the user may write by hand. File changes and sessions are recorded by the engine. */
const WRITABLE: MemoryKind[] = ["decision", "architecture", "error", "solution", "note"];

function semanticProblem(status: EmbedStatus): string {
  if (status.code === "no_model") {
    return t("No embedding model is installed in LM Studio, so the memory is searched by words and through the graph only. Download one in LM Studio (for example nomic-embed-text) to search by meaning too.");
  }
  if (status.code === "offline") return t("LM Studio is not reachable, so the memory is searched by words and through the graph only.");
  if (status.code === "failed") return t("The embedding model did not answer, so the memory is searched by words and through the graph only for now.");
  return t("Search by meaning is not available here: the memory is searched by words and through the graph only.");
}

function linkLabel(link: BrainLink): string {
  if (link.relation === "fixed_by") return link.outgoing ? t("Fixed by") : t("Fixes");
  if (link.relation === "from_session") return link.outgoing ? t("From the session") : t("Recorded in this session");
  if (link.relation === "supersedes") return link.outgoing ? t("Replaces") : t("Replaced by");
  return link.relation;
}

function MemoryForm({
  initial, busy, onSave, onCancel,
}: {
  initial?: BrainMemory;
  busy: boolean;
  onSave: (fields: { kind: MemoryKind; title: string; body: string; files: string[] }) => void;
  onCancel: () => void;
}) {
  const [kind, setKind] = useState<MemoryKind>(initial?.kind ?? "decision");
  const [title, setTitle] = useState(initial?.title ?? "");
  const [body, setBody] = useState(initial?.body ?? "");
  const [files, setFiles] = useState((initial?.files ?? []).join(", "));
  const kinds = initial && !WRITABLE.includes(initial.kind) ? [initial.kind, ...WRITABLE] : WRITABLE;
  return (
    <div className="grid gap-3 rounded-lg border border-line bg-surface p-4">
      <div className="grid grid-cols-[180px_1fr] gap-3">
        <Field label={t("Kind")}>
          <Select value={kind} onChange={(event) => setKind(event.target.value as MemoryKind)}>
            {kinds.map((value) => (
              <option key={value} value={value}>
                {KIND[value].label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label={t("In one line")}>
          <TextInput value={title} onChange={(event) => setTitle(event.target.value)} placeholder={t("Sessions are stored in SQLite")} autoFocus />
        </Field>
      </div>
      <Field label={t("In full, with the reason")}>
        <TextArea rows={5} value={body} onChange={(event) => setBody(event.target.value)} />
      </Field>
      <Field label={t("Files it is about")} hint={t("Paths inside the project, separated by commas. Optional.")}>
        <TextInput value={files} onChange={(event) => setFiles(event.target.value)} placeholder="src/db.py, src/models.py" className="font-mono text-xs" />
      </Field>
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onCancel}>
          {t("Cancel")}
        </Button>
        <Button
          variant="primary"
          loading={busy}
          disabled={!title.trim()}
          onClick={() => onSave({ kind, title: title.trim(), body: body.trim(), files: files.split(",").map((f) => f.trim()).filter(Boolean) })}
        >
          {t("Save")}
        </Button>
      </div>
    </div>
  );
}

function MemoryRow({
  row, open, searched, onToggle, onOpen, onChanged,
}: {
  row: BrainRow;
  open: boolean;
  searched: boolean;
  onToggle: () => void;
  onOpen: (id: number) => void;
  onChanged: () => void;
}) {
  const navigate = useApp((s) => s.navigate);
  const toast = useApp((s) => s.toast);
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const memory = row.memory;
  const look = KIND[memory.kind] ?? KIND.note;
  const stillOpen = memory.kind === "error" && memory.tags.includes("open");
  const fixedBy = row.links.find((l) => l.relation === "fixed_by" && l.outgoing);
  const reasons = [
    row.semantic !== null && row.semantic !== undefined && searched ? t("meaning {n}", { n: row.semantic.toFixed(2) }) : null,
    row.keyword ? t("words") : null,
    ...row.via.map(viaLabel),
  ].filter(Boolean);
  const author = memory.source === "user" ? t("written by you") : memory.source === "agent" ? t("written by {agent}", { agent: memory.agent || t("an agent") }) : t("recorded by the engine");
  const related = row.links.filter((l) => l.type === "memory");

  const save = (fields: { kind: MemoryKind; title: string; body: string; files: string[] }) => {
    setBusy(true);
    api
      .put(`/api/brain/memories/${memory.id}`, fields)
      .then(() => {
        setEditing(false);
        onChanged();
      })
      .catch((err: unknown) => toast({ tone: "bad", title: t("Could not save the memory"), body: errorMessage(err) }))
      .finally(() => setBusy(false));
  };
  const remove = () => {
    setBusy(true);
    api
      .del(`/api/brain/memories/${memory.id}`)
      .then(onChanged)
      .catch((err: unknown) => toast({ tone: "bad", title: t("Could not delete"), body: errorMessage(err) }))
      .finally(() => setBusy(false));
  };

  return (
    <li id={`memory-${memory.id}`} className={cx("border-b border-line", memory.superseded_by !== null && "opacity-60")}>
      <button type="button" onClick={onToggle} aria-expanded={open} className="grid w-full grid-cols-[14px_112px_1fr_auto] items-baseline gap-x-3 px-1 py-2.5 text-left hover:bg-surface">
        <ChevronRight size={13} className={cx("self-center text-ink-3 transition-transform duration-150", open && "rotate-90")} aria-hidden />
        <span>
          <Badge tone={stillOpen ? "warn" : look.tone}>{look.label.toUpperCase()}</Badge>
        </span>
        <span className="min-w-0">
          <span className="block truncate text-sm text-ink">{memory.title}</span>
          <span className="block truncate text-2xs text-ink-3">
            {[
              stillOpen ? t("still open") : fixedBy ? t("fixed by #{n}", { n: fixedBy.memory_id ?? "" }) : null,
              memory.superseded_by !== null ? t("replaced by #{n}", { n: memory.superseded_by }) : null,
              author,
              memory.files.length === 1 ? memory.files[0] : memory.files.length > 1 ? t("{n} files", { n: memory.files.length }) : null,
              ...reasons,
            ]
              .filter(Boolean)
              .join(" · ")}
          </span>
        </span>
        <span className="tabular shrink-0 text-2xs text-ink-3">
          #{memory.id} · {timeAgo(memory.updated_at)}
        </span>
      </button>
      {open && (
        <div className="fade-in grid gap-3 pr-1 pb-4 pl-[30px]">
          {editing ? (
            <MemoryForm initial={memory} busy={busy} onSave={save} onCancel={() => setEditing(false)} />
          ) : (
            <>
              {memory.body && <div className="selectable max-w-[86ch] text-sm leading-relaxed whitespace-pre-wrap text-ink-2">{memory.body}</div>}
              {memory.files.length > 0 && (
                <ul className="selectable flex flex-wrap gap-1.5 font-mono text-2xs text-ink-2">
                  {memory.files.map((file) => (
                    <li key={file} className="rounded-sm border border-line px-1.5 py-0.5">
                      {file}
                    </li>
                  ))}
                </ul>
              )}
              {related.length > 0 && (
                <ul className="grid gap-1 text-xs">
                  {related.map((link) => (
                    <li key={`${link.relation}-${link.id}-${String(link.outgoing)}`} className="flex items-baseline gap-2">
                      <span className="shrink-0 text-ink-3">{linkLabel(link)}</span>
                      <button type="button" onClick={() => link.memory_id !== undefined && onOpen(link.memory_id)} className="min-w-0 truncate text-left text-accent hover:underline">
                        #{link.memory_id} {link.label}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-2xs text-ink-3">
                <span>{new Date(memory.created_at).toLocaleString()}</span>
                <span>{memory.uses === 1 ? t("given to an agent once") : t("given to agents {n} times", { n: memory.uses })}</span>
                {!memory.embedded && memory.superseded_by === null && <span>{t("not indexed by meaning yet")}</span>}
                {row.run && (
                  <button
                    type="button"
                    onClick={() => (row.run?.chat ? navigate("chat", { chat: row.run.chat }) : navigate("sessions", { run: row.run?.id ?? "" }))}
                    className="text-accent hover:underline"
                  >
                    {row.run.chat ? t("Open the chat it comes from") : t("Open the session it comes from")}
                  </button>
                )}
                <span className="flex-1" />
                {memory.slot ? (
                  <span>{t("Rewritten from the project's files whenever they change.")}</span>
                ) : confirming ? (
                  <span className="flex items-center gap-2">
                    {t("Delete this memory?")}
                    <Button size="sm" variant="danger" loading={busy} onClick={remove}>
                      {t("Delete")}
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>
                      {t("Cancel")}
                    </Button>
                  </span>
                ) : (
                  <span className="flex items-center gap-1.5">
                    <Button size="sm" variant="ghost" icon={<Pencil size={12} />} onClick={() => setEditing(true)}>
                      {t("Edit")}
                    </Button>
                    <Button size="sm" variant="ghost" icon={<Trash2 size={12} />} onClick={() => setConfirming(true)}>
                      {t("Delete")}
                    </Button>
                  </span>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </li>
  );
}

function ProjectView({ projectId, onChanged }: { projectId: string; onChanged: () => void }) {
  const toast = useApp((s) => s.toast);
  const navigate = useApp((s) => s.navigate);
  const [tab, setTab] = useState<"memories" | "graph">("memories");
  const [kind, setKind] = useState<MemoryKind | null>(null);
  const [text, setText] = useState("");
  const [replaced, setReplaced] = useState(false);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);
  const [forgetting, setForgetting] = useState(false);
  const asked = useDebounced(text.trim(), 300);

  const overview = useLoad(() => api.get<BrainOverview>(`/api/brain/projects/${projectId}`), [projectId]);
  const rows = useLoad(
    () => api.get<BrainRows>(`/api/brain/projects/${projectId}/memories${query({ q: asked, kind, replaced: replaced && !asked ? true : null })}`),
    [projectId, asked, kind, replaced],
  );
  const reload = () => {
    overview.reload();
    rows.reload();
    onChanged();
  };
  // A run may be writing to this memory right now.
  useInterval(() => {
    if (!adding && expanded === null) rows.reload();
    overview.reload();
  }, 8000);

  const open = (id: number) => {
    setTab("memories");
    setKind(null);
    setText("");
    setExpanded(id);
  };
  useEffect(() => {
    if (expanded !== null && tab === "memories") document.getElementById(`memory-${expanded}`)?.scrollIntoView({ block: "nearest" });
  }, [expanded, tab, rows.data]);

  const project = overview.data?.project;
  const status = overview.data?.semantic;
  const counts = project?.counts ?? {};
  const list = rows.data?.rows ?? [];

  const add = (fields: { kind: MemoryKind; title: string; body: string; files: string[] }) => {
    setBusy(true);
    api
      .post<{ memory: BrainMemory; state: string }>(`/api/brain/projects/${projectId}/memories`, fields)
      .then((data) => {
        setAdding(false);
        reload();
        setExpanded(data.memory.id);
        if (data.state === "known") toast({ tone: "info", title: t("Already in the memory"), body: `#${data.memory.id} ${data.memory.title}` });
      })
      .catch((err: unknown) => toast({ tone: "bad", title: t("Could not save the memory"), body: errorMessage(err) }))
      .finally(() => setBusy(false));
  };
  const index = () => {
    setBusy(true);
    api
      .post<BrainOverview>(`/api/brain/projects/${projectId}/reindex`)
      .then(reload)
      .catch((err: unknown) => toast({ tone: "bad", title: t("Could not index the memory"), body: errorMessage(err) }))
      .finally(() => setBusy(false));
  };
  const forget = () => {
    setBusy(true);
    api
      .del(`/api/brain/projects/${projectId}`)
      .then(() => {
        onChanged();
        navigate("brain");
      })
      .catch((err: unknown) => toast({ tone: "bad", title: t("Could not delete"), body: errorMessage(err) }))
      .finally(() => setBusy(false));
  };

  if (overview.error && !overview.data) return <div className="p-7"><Note tone="bad">{overview.error}</Note></div>;
  if (!project || !status) return <div className="flex-1 p-7"><SkeletonRows rows={5} height={48} /></div>;

  const codemap = overview.data?.codemap;
  const unindexed = status.available ? project.memories - (overview.data?.embedded ?? 0) : 0;
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <header className="shrink-0 border-b border-line px-7 pt-5 pb-3">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2.5">
              <h1 className="truncate text-xl font-semibold tracking-tight">{project.name}</h1>
              <Badge tone="off">{project.kind === "github" ? "GITHUB" : t("FOLDER")}</Badge>
            </div>
            <p className="selectable mt-1 flex items-center gap-1.5 truncate font-mono text-xs text-ink-3">
              {project.kind === "github" ? <FolderGit2 size={12} aria-hidden /> : <FolderOpen size={12} aria-hidden />}
              {project.kind === "github" ? `github.com/${project.name}` : project.path}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {forgetting ? (
              <span className="flex items-center gap-2 text-xs text-ink-2">
                {t("Delete everything this project remembers?")}
                <Button size="sm" variant="danger" loading={busy} onClick={forget}>
                  {t("Delete it all")}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setForgetting(false)}>
                  {t("Cancel")}
                </Button>
              </span>
            ) : (
              <>
                <Button size="sm" icon={<Plus size={13} />} onClick={() => { setTab("memories"); setAdding(true); }}>
                  {t("Add a memory")}
                </Button>
                <Button size="sm" variant="ghost" icon={<Trash2 size={12} />} onClick={() => setForgetting(true)}>
                  {t("Forget this project")}
                </Button>
              </>
            )}
          </div>
        </div>
        <div className="tabular mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-3">
          <span>{project.memories === 1 ? t("1 memory") : t("{n} memories", { n: project.memories })}</span>
          {codemap && (
            <span>
              {t("Project map: {files} files, {imports} imports between them, read {when}", { files: codemap.files, imports: codemap.imports, when: timeAgo(codemap.scanned_at) })}
            </span>
          )}
          {status.available && (
            <span className="flex items-center gap-2">
              {t("Search by meaning: {model}", { model: status.model ?? "" })}
              {unindexed > 0 && (
                <>
                  <span className="text-warn">{t("{n} not indexed yet", { n: unindexed })}</span>
                  <Button size="sm" variant="ghost" loading={busy} onClick={index}>
                    {t("Index now")}
                  </Button>
                </>
              )}
            </span>
          )}
        </div>
        {!status.available && (
          <div className="mt-3">
            <Note tone="warn">{semanticProblem(status)}</Note>
          </div>
        )}
        <div className="mt-4">
          <Tabs value={tab} tabs={[{ value: "memories", label: t("Memories") }, { value: "graph", label: t("Knowledge graph") }]} onChange={setTab} />
        </div>
      </header>

      {tab === "graph" ? (
        <BrainGraph projectId={projectId} onOpenMemory={open} />
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto px-7 py-4">
          <div className="grid max-w-[1100px] gap-3">
            <div className="relative">
              <Search size={14} className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-ink-3" aria-hidden />
              <TextInput
                value={text}
                onChange={(event) => setText(event.target.value)}
                placeholder={t("Ask the memory: why is the session stored in SQLite?")}
                aria-label={t("Search the memory")}
                className="h-9 pl-9"
              />
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <Chip active={kind === null} onClick={() => setKind(null)}>
                {t("All")} <span className="tabular text-ink-3">{project.memories}</span>
              </Chip>
              {KINDS.filter((value) => (counts[value] ?? 0) > 0 || kind === value).map((value) => (
                <Chip key={value} active={kind === value} onClick={() => setKind(kind === value ? null : value)}>
                  {KIND[value].plural} <span className="tabular text-ink-3">{counts[value] ?? 0}</span>
                </Chip>
              ))}
              <span className="flex-1" />
              {!asked && (
                <label className="flex items-center gap-2 text-xs text-ink-3">
                  {t("Show replaced ones")}
                  <Toggle checked={replaced} onChange={setReplaced} label={t("Show replaced ones")} />
                </label>
              )}
            </div>
            {adding && <MemoryForm busy={busy} onSave={add} onCancel={() => setAdding(false)} />}
            {rows.data?.searched && (
              <p className="text-xs text-ink-3">
                {list.length === 0
                  ? t("Nothing in this memory answers that.")
                  : t("{n} of {total} memories, best first.", { n: list.length, total: rows.data.total })}{" "}
                {rows.data.semantic ? t("Searched by meaning, by words and through the graph.") : t("Searched by words and through the graph.")}
              </p>
            )}
            {rows.error && !rows.data && <Note tone="bad">{rows.error}</Note>}
            {rows.loading && !rows.data ? (
              <SkeletonRows rows={6} height={44} />
            ) : list.length === 0 && !rows.data?.searched ? (
              <p className="py-6 text-sm text-ink-3">
                {kind ? t("No memory of this kind yet.") : t("Nothing is remembered yet. The next session in this project writes down what it does.")}
              </p>
            ) : (
              <ul className="border-t border-line">
                {list.map((row) => (
                  <MemoryRow
                    key={row.memory.id}
                    row={row}
                    open={expanded === row.memory.id}
                    searched={rows.data?.searched ?? false}
                    onToggle={() => setExpanded(expanded === row.memory.id ? null : row.memory.id)}
                    onOpen={open}
                    onChanged={() => {
                      setExpanded(null);
                      reload();
                    }}
                  />
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export function BrainPage() {
  const routeProject = useApp((s) => s.params.project);
  const navigate = useApp((s) => s.navigate);
  const settings = useApp((s) => s.settings);
  const projects = useLoad(() => api.get<{ enabled: boolean; semantic: EmbedStatus; projects: BrainProject[] }>("/api/brain"));
  useInterval(projects.reload, 10000);
  const list = useMemo(() => projects.data?.projects ?? [], [projects.data]);
  const chosen = routeProject && list.some((p) => p.id === routeProject) ? routeProject : (list[0]?.id ?? null);
  const enabled = projects.data?.enabled ?? settings?.memory.enabled ?? true;

  return (
    <div className="flex min-h-0 flex-1">
      <div className="flex w-[260px] shrink-0 flex-col border-r border-line bg-panel">
        <div className="flex h-12 shrink-0 items-center border-b border-line px-4">
          <span className="text-sm font-semibold">{t("Project Brain")}</span>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-2">
          {projects.loading && !projects.data && <SkeletonRows rows={4} height={40} />}
          {projects.data && list.length === 0 && <p className="px-2 py-3 text-xs text-ink-3">{t("No project has a memory yet.")}</p>}
          <ul className="grid gap-px">
            {list.map((project) => {
              const active = project.id === chosen;
              return (
                <li key={project.id}>
                  <button
                    type="button"
                    onClick={() => navigate("brain", { project: project.id })}
                    aria-current={active ? "true" : undefined}
                    className={cx("grid w-full grid-cols-[16px_1fr_auto] items-baseline gap-2 rounded-md px-2 py-1.5 text-left transition-colors duration-150", active ? "bg-raised" : "hover:bg-surface")}
                  >
                    <span className="self-center text-ink-3">{project.kind === "github" ? <FolderGit2 size={13} aria-hidden /> : <FolderOpen size={13} aria-hidden />}</span>
                    <span className="min-w-0">
                      <span className={cx("block truncate text-sm", active ? "text-ink" : "text-ink-2")}>{project.name}</span>
                      <span className="block truncate text-2xs text-ink-3">{timeAgo(project.updated_at)}</span>
                    </span>
                    <span className="tabular text-2xs text-ink-3">{project.memories}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      </div>
      {projects.error && !projects.data ? (
        <div className="flex-1 p-7">
          <Note tone="bad">{projects.error}</Note>
        </div>
      ) : chosen ? (
        <ProjectView key={chosen} projectId={chosen} onChanged={projects.reload} />
      ) : (
        <div className="min-w-0 flex-1 overflow-y-auto px-8">
          <EmptyState
            title={t("Every project gets a permanent memory")}
            action={
              <>
                <Button variant="primary" onClick={() => navigate("sessions")}>
                  {t("Start a session")}
                </Button>
                <Button onClick={() => navigate("chat")}>{t("Open a chat")}</Button>
              </>
            }
          >
            {t("When a team or a chat works in a folder of yours or in a GitHub repository, Will It Run writes down what it learns about that project: how it is built, what was decided, which files were changed and why, what went wrong and what fixed it. The next session is handed only the memories its task needs. Connect a folder or a repository with the + next to the prompt to start one.")}
          </EmptyState>
          {!enabled && (
            <div className="mx-auto max-w-md">
              <Note tone="warn" action={<Button size="sm" onClick={() => navigate("settings")}>{t("Settings")}</Button>}>
                {t("The project memory is turned off in Settings: runs neither read nor write it.")}
              </Note>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
