import { ChevronDown, Play, Trash2, Trophy } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button, EmptyState, IconButton, Note, PageHeader, Select, SkeletonRows, cx } from "../components/ui";
import { api, ApiError } from "../lib/api";
import { isLive, type Report, type RunRow, RUN_STATUS_LABEL } from "../lib/arena";
import { EXPORT_FORMATS, type ExportFormat, saveReport } from "../lib/arenaExport";
import { timeAgo } from "../lib/format";
import { errorMessage, useBus, useInterval, useLoad } from "../lib/hooks";
import { t } from "../lib/i18n";
import { useApp } from "../lib/store";
import { LiveRun } from "./arena/LiveRun";
import { NewRun } from "./arena/NewRun";
import { Results, type Tab } from "./arena/Results";

function Modal({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) {
  return (
    <div className="fade-in fixed inset-0 z-[40] flex items-start justify-center bg-black/55 pt-[18vh]" onMouseDown={onClose}>
      <div role="dialog" aria-modal="true" aria-label={title} onMouseDown={(e) => e.stopPropagation()} className="rise-in z-[50] w-[460px] rounded-lg border border-line-strong bg-panel p-5" style={{ boxShadow: "var(--shadow)" }}>
        <h2 className="text-base font-semibold">{title}</h2>
        <div className="mt-2 text-sm text-ink-2">{children}</div>
      </div>
    </div>
  );
}

function ExportMenu({ report }: { report: Report }) {
  const toast = useApp((s) => s.toast);
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (box.current && !box.current.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [open]);
  const save = async (format: ExportFormat) => {
    setOpen(false);
    try {
      const where = await saveReport(report.run, format);
      if (where) toast({ tone: "ok", title: t("Report saved"), body: where });
    } catch (err) {
      toast({ tone: "bad", title: t("The report was not saved"), body: errorMessage(err) });
    }
  };
  return (
    <div ref={box} className="relative">
      <Button onClick={() => setOpen(!open)} icon={<ChevronDown size={13} />} aria-expanded={open} aria-haspopup="menu">
        {t("Export")}
      </Button>
      {open && (
        <div role="menu" className="rise-in absolute right-0 z-[30] mt-1.5 w-[300px] rounded-lg border border-line-strong bg-panel p-1.5" style={{ boxShadow: "var(--shadow)" }}>
          {EXPORT_FORMATS.map((f) => (
            <button key={f.id} type="button" role="menuitem" onClick={() => void save(f.id)} className="grid w-full gap-0.5 rounded-md px-3 py-2 text-left hover:bg-raised">
              <span className="text-sm font-medium">{f.label}</span>
              <span className="text-xs text-ink-3">{f.hint}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function RunView({ runId, tab, onTab, onGone, onChanged, onReport }: { runId: string; tab: Tab; onTab: (tab: Tab) => void; onGone: () => void; onChanged: () => void; onReport: (report: Report | null) => void }) {
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const pending = useRef<number | null>(null);
  const live = report ? isLive(report.run.status) : false;

  const load = useCallback(async () => {
    try {
      const next = await api.get<Report>(`/api/arena/runs/${runId}`);
      setReport(next);
      onReport(next);
      setError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) onGone();
      else setError(errorMessage(err));
    }
  }, [runId, onGone, onReport]);

  useEffect(() => {
    setReport(null);
    setSelected(null);
    void load();
  }, [load]);

  // A burst of answers is one reload, not one per answer.
  const soon = useCallback(() => {
    if (pending.current !== null) return;
    pending.current = window.setTimeout(() => {
      pending.current = null;
      void load();
    }, 400);
  }, [load]);
  useBus("arena.progress", (event) => {
    if (event.data.run_id === runId && event.data.phase === "answered") soon();
  });
  useBus("arena.run", (event) => {
    if (event.data.run_id === runId) {
      void load();
      onChanged();
    }
  });
  useBus("arena.model", (event) => {
    if (event.data.run_id === runId) soon();
  });
  useInterval(() => void load(), live ? 5000 : null);
  useEffect(() => () => {
    if (pending.current !== null) window.clearTimeout(pending.current);
  }, []);

  if (error && !report) return <div className="px-7 py-6"><Note tone="bad">{error}</Note></div>;
  if (!report) return <div className="px-7 py-6"><SkeletonRows rows={4} height={60} /></div>;
  return live ? <LiveRun report={report} onStopped={() => void load()} /> : <Results report={report} tab={tab} onTab={onTab} selected={selected} onSelect={setSelected} onChanged={() => void load()} />;
}

export function ArenaPage() {
  const params = useApp((s) => s.params);
  const toast = useApp((s) => s.toast);
  const runs = useLoad(() => api.get<{ runs: RunRow[]; busy: string | null }>("/api/arena/runs"));
  const [selected, setSelected] = useState<string | null>(params.run ?? null);
  const [mode, setMode] = useState<"run" | "new">(params.new ? "new" : "run");
  const [tab, setTab] = useState<Tab>("board");
  const [report, setReport] = useState<Report | null>(null);
  const [asking, setAsking] = useState<"delete" | "resume" | null>(null);
  const [working, setWorking] = useState(false);

  const list = runs.data?.runs ?? [];
  const busy = runs.data?.busy ?? null;
  const current = list.find((r) => r.id === selected) ?? null;

  // The first time the list arrives: show the run that is working, or the last one, or the form when there is none.
  useEffect(() => {
    if (!runs.data || selected || mode === "new") return;
    const first = runs.data.busy ?? runs.data.runs[0]?.id ?? null;
    if (first) setSelected(first);
    else setMode("new");
  }, [runs.data, selected, mode]);

  useBus("arena.run", () => runs.reload());

  const started = (id: string) => {
    setSelected(id);
    setMode("run");
    setTab("board");
    runs.reload();
  };
  const gone = useCallback(() => {
    setSelected(null);
    runs.reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const remove = async () => {
    if (!selected) return;
    setWorking(true);
    try {
      await api.del(`/api/arena/runs/${selected}`);
      setAsking(null);
      setSelected(null);
      setReport(null);
      runs.reload();
    } catch (err) {
      toast({ tone: "bad", title: t("The challenge was not deleted"), body: errorMessage(err) });
    } finally {
      setWorking(false);
    }
  };

  const resume = async (confirmUnload: boolean) => {
    if (!selected) return;
    setWorking(true);
    try {
      await api.post(`/api/arena/runs/${selected}/resume`, { confirm_unload: confirmUnload });
      setAsking(null);
      runs.reload();
    } catch (err) {
      if (err instanceof ApiError && err.data.code === "needs_unload") setAsking("resume");
      else {
        setAsking(null);
        toast({ tone: "bad", title: t("The challenge did not resume"), body: errorMessage(err) });
      }
    } finally {
      setWorking(false);
    }
  };

  const run = report?.run;
  const resumable = Boolean(run && !isLive(run.status) && (run.done < run.total || report?.matrix.some((r) => Object.values(r.cells).some((c) => c.status === "error"))));
  const when = run ? timeAgo(run.finished_at ?? run.started_at ?? run.created_at) : "";
  const subtitle =
    mode === "new"
      ? t("Choose the models and how long: the same questions, the same seed, one model at a time in memory.")
      : run && report
        ? t("{models} models put to the test with {questions} questions that code can check. {when}", { models: report.models.length, questions: report.matrix.length, when })
        : t("Models put to the test on questions with a verifiable answer: nothing is judged by another model.");

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        title={t("Arena")}
        subtitle={subtitle}
        actions={
          <>
            {mode === "run" && report && !isLive(report.run.status) && <ExportMenu report={report} />}
            {mode === "run" && resumable && (
              <Button icon={<Play size={12} />} loading={working} onClick={() => void resume(false)}>
                {t("Resume")}
              </Button>
            )}
            {mode === "run" && current && !isLive(current.status) && (
              <IconButton label={t("Delete this challenge")} onClick={() => setAsking("delete")}>
                <Trash2 size={14} />
              </IconButton>
            )}
            {mode === "run" ? (
              <Button variant="primary" icon={<Trophy size={13} />} onClick={() => setMode("new")}>
                {t("New challenge")}
              </Button>
            ) : (
              list.length > 0 && <Button onClick={() => setMode("run")}>{t("Back to the results")}</Button>
            )}
          </>
        }
      />
      {mode === "run" && list.length > 0 && (
        <div className="flex items-center gap-3 border-b border-line px-7 py-2">
          <label htmlFor="arena-run" className="text-xs text-ink-3">
            {t("Challenge")}
          </label>
          <div className="w-[460px] max-w-full">
            <Select
              id="arena-run"
              value={selected ?? ""}
              onChange={(e) => {
                setSelected(e.target.value);
                setTab("board");
              }}
            >
              {list.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.title || r.id} · {RUN_STATUS_LABEL[r.status]} · {timeAgo(r.started_at ?? r.created_at)}
                </option>
              ))}
            </Select>
          </div>
        </div>
      )}
      {mode === "new" && <NewRun busy={busy} onStarted={started} />}
      {mode === "run" && runs.loading && !runs.data && <div className="px-7 py-6"><SkeletonRows rows={4} height={56} /></div>}
      {mode === "run" && runs.error && <div className="px-7 py-6"><Note tone="bad">{runs.error}</Note></div>}
      {mode === "run" && runs.data && list.length === 0 && (
        <EmptyState title={t("No challenge yet")} action={<Button variant="primary" onClick={() => setMode("new")}>{t("New challenge")}</Button>}>
          {t("The Arena puts models through the same questions, each with an answer that code can check, and shows who is reliable and where.")}
        </EmptyState>
      )}
      {mode === "run" && selected && <RunView key={selected} runId={selected} tab={tab} onTab={setTab} onGone={gone} onChanged={() => runs.reload()} onReport={setReport} />}

      {asking === "delete" && (
        <Modal title={t("Delete this challenge?")} onClose={() => setAsking(null)}>
          <p>{t("Its answers and its report go with it. The exported files stay where you saved them.")}</p>
          <div className="mt-4 flex justify-end gap-2">
            <Button onClick={() => setAsking(null)}>{t("Cancel")}</Button>
            <Button variant="danger" loading={working} onClick={() => void remove()}>
              {t("Delete")}
            </Button>
          </div>
        </Modal>
      )}
      {asking === "resume" && (
        <Modal title={t("Unload the models in memory?")} onClose={() => setAsking(null)}>
          <p>{t("LM Studio already holds models, maybe for another program. To measure one model at a time the Arena unloads them.")}</p>
          <div className="mt-4 flex justify-end gap-2">
            <Button onClick={() => setAsking(null)}>{t("Cancel")}</Button>
            <Button variant="primary" loading={working} onClick={() => void resume(true)}>
              {t("Unload and resume")}
            </Button>
          </div>
        </Modal>
      )}
      <span className={cx("sr-only")} aria-live="polite">{run ? RUN_STATUS_LABEL[run.status] : ""}</span>
    </div>
  );
}
