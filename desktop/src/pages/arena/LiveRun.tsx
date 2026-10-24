import { Square } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { StatusMark } from "../../components/arena/charts";
import { Button, Note } from "../../components/ui";
import { api } from "../../lib/api";
import { CATEGORY_LABEL, fmt, label, laneCells, minutes, modelEventLine, type QuestionDetail, remainingSeconds, type Report, RUN_STATUS_LABEL, scoreSoFar, seriesVar, STATUSES, STATUS_LABEL } from "../../lib/arena";
import { errorMessage, useBus, useInterval } from "../../lib/hooks";
import { getLang, t } from "../../lib/i18n";
import { useApp } from "../../lib/store";

interface Streaming {
  model: string;
  question: string;
  text: string;
  reasoning: string;
}

const LOG_LIMIT = 8;

function Square1({ status, current }: { status: (typeof STATUSES)[number] | null; current: boolean }) {
  const color =
    status === "passed" ? "var(--ok)" : status === "partial" ? "var(--warn)" : status === "failed" ? "var(--bad)" : status === "error" ? "var(--limited)" : status ? "var(--off)" : "var(--raised)";
  return (
    <span
      title={status ? STATUS_LABEL[status] : t("Not asked yet")}
      className={`inline-block h-[9px] w-[9px] rounded-[2px] ${current ? "pulse-dot" : ""}`}
      style={{
        background: status === "unscored" || status === "skipped" ? "transparent" : color,
        border: status === "unscored" || status === "skipped" ? `1.5px ${status === "skipped" ? "dashed" : "solid"} ${color}` : current ? `1.5px solid var(--accent)` : "none",
        boxSizing: "border-box",
      }}
    />
  );
}

export function LiveRun({ report, onStopped }: { report: Report; onStopped: () => void }) {
  const toast = useApp((s) => s.toast);
  const run = report.run;
  const [stream, setStream] = useState<Streaming | null>(null);
  const [asked, setAsked] = useState<string>("");
  const [log, setLog] = useState<{ at: string; text: string }[]>([]);
  const [now, setNow] = useState(() => Date.now());
  const [stopping, setStopping] = useState(false);
  const labels = useMemo(() => Object.fromEntries(report.models.map((m) => [m.key, m.label])), [report.models]);
  const labelOf = (key: string) => labels[key] ?? key;
  const labelRef = useRef(labelOf);
  labelRef.current = labelOf;

  useInterval(() => setNow(Date.now()), 1000);

  useBus("arena.progress", (event) => {
    if (event.data.run_id !== run.id) return;
    if (event.data.phase === "asking") {
      setStream({ model: String(event.data.model), question: String(event.data.question), text: "", reasoning: "" });
    }
  });
  useBus("arena.delta", (event) => {
    if (event.data.run_id !== run.id) return;
    const kind = event.data.kind === "reasoning" ? "reasoning" : "text";
    setStream((now) => {
      const base = now && now.model === event.data.model && now.question === event.data.question ? now : { model: String(event.data.model), question: String(event.data.question), text: "", reasoning: "" };
      return { ...base, [kind]: (base[kind] + String(event.data.text ?? "")).slice(-4000) };
    });
  });
  useBus("arena.model", (event) => {
    if (event.data.run_id !== run.id) return;
    const text = modelEventLine(event.data, (key) => labelRef.current(key));
    if (text) setLog((lines) => [...lines, { at: new Date().toLocaleTimeString(), text }].slice(-LOG_LIMIT));
  });

  // The question the model in front is answering, for the left side of its box.
  const questionId = stream?.question ?? "";
  useEffect(() => {
    if (!questionId || questionId === asked) return;
    let cancelled = false;
    api
      .get<QuestionDetail>(`/api/arena/runs/${run.id}/questions/${questionId}`)
      .then((detail) => {
        if (!cancelled) setAsked(`${detail.question.id}\n${detail.question.prompt}`);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [questionId, asked, run.id]);

  const stop = async () => {
    setStopping(true);
    try {
      await api.post(`/api/arena/runs/${run.id}/stop`);
      onStopped();
    } catch (err) {
      setStopping(false);
      toast({ tone: "bad", title: t("Could not stop the challenge"), body: errorMessage(err) });
    }
  };

  const doneModels = report.models.filter((m) => m.status === "done").length;
  const working = report.models.find((m) => m.status === "running");
  const eta = remainingSeconds(run, now);
  const total = report.matrix.length;

  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-7 py-6">
      <div className="mb-5 flex flex-wrap items-center gap-2 text-xs">
        <span className="rounded-md border border-line px-2.5 py-1 text-ink-2">{t("{done} of {total} models", { done: doneModels, total: report.models.length })}</span>
        {working && (
          <span className="rounded-md border border-line px-2.5 py-1 text-ink-2">
            {t("{done} of {total} questions of the model at work", { done: report.matrix.filter((r) => r.cells[working.key]).length, total })}
          </span>
        )}
        {eta !== null && <span className="rounded-md border border-line px-2.5 py-1 text-ink-2">{t("about {time} left", { time: minutes(eta) })}</span>}
        <span className="rounded-md border border-line px-2.5 py-1 font-mono text-ink-2">seed {run.seed}</span>
        <span className="rounded-md border border-line px-2.5 py-1 font-mono text-ink-2">suite {run.suite_version}</span>
        <span className="ml-auto">
          <Button variant="danger" size="sm" icon={<Square size={11} />} loading={stopping || run.status === "stopping"} onClick={() => void stop()}>
            {run.status === "stopping" ? RUN_STATUS_LABEL.stopping : t("Stop")}
          </Button>
        </span>
      </div>

      <div className="grid gap-3">
        {report.models.map((model) => {
          const cells = laneCells(report.matrix, model.key);
          const answered = cells.filter((c) => c !== null).length;
          const active = model.status === "running";
          const score = scoreSoFar(report.matrix, model.key);
          const mine = stream && stream.model === model.key ? stream : null;
          return (
            <div key={model.key} className={`rounded-lg border px-4 py-3.5 transition-colors duration-150 ${active ? "border-accent bg-surface" : "border-line"} ${model.status === "pending" ? "opacity-60" : ""}`}>
              <div className="grid grid-cols-[210px_minmax(0,1fr)_120px] items-start gap-4">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="inline-block h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: seriesVar(model.position) }} />
                    <span className="truncate text-sm font-semibold">{model.label}</span>
                  </div>
                  <div className="mt-0.5 text-xs text-ink-3">
                    {model.status === "pending" && t("waiting: it is loaded when its turn comes")}
                    {model.status === "running" && (mine ? t("working") : t("loading or thinking"))}
                    {model.status === "done" && t("done · {n} answers", { n: answered })}
                    {model.status === "failed" && <span className="text-bad">{model.error ?? t("it could not run")}</span>}
                    {model.status === "stopped" && t("stopped")}
                    {model.context_granted ? ` · ${t("context {n}", { n: model.context_granted.toLocaleString(getLang()) })}` : ""}
                  </div>
                </div>
                <div className="flex flex-wrap content-start gap-[3px]" role="img" aria-label={t("{done} of {total} questions answered", { done: answered, total })}>
                  {cells.map((status, i) => (
                    <Square1 key={report.matrix[i]?.id ?? i} status={status} current={active && mine?.question === report.matrix[i]?.id} />
                  ))}
                </div>
                <div className="text-right">
                  <div className="tabular text-2xl font-semibold tracking-tight">{fmt(score)}</div>
                  <div className="text-2xs text-ink-3">{t("{done} of {total} questions", { done: answered, total })}</div>
                </div>
              </div>
              {active && (
                <div className="mt-3 grid grid-cols-2 gap-3 border-t border-line pt-3">
                  <div className="min-w-0">
                    <div className="mb-1 text-2xs text-ink-3">
                      {mine ? `${t("Question")} ${mine.question} · ${label(CATEGORY_LABEL, report.matrix.find((r) => r.id === mine.question)?.category ?? "")}` : t("Question")}
                    </div>
                    <pre className="selectable max-h-[150px] overflow-y-auto rounded-md border border-line bg-bg p-2.5 font-mono text-xs break-words whitespace-pre-wrap text-ink-2">
                      {mine && asked.startsWith(mine.question) ? asked.split("\n").slice(1).join("\n") : "…"}
                    </pre>
                  </div>
                  <div className="min-w-0">
                    <div className="mb-1 text-2xs text-ink-3">{t("The answer of {model}, as it writes", { model: model.label })}</div>
                    <pre className="selectable max-h-[150px] overflow-y-auto rounded-md border border-line bg-bg p-2.5 font-mono text-xs break-words whitespace-pre-wrap text-ink">
                      {mine ? mine.text || (mine.reasoning ? t("(thinking…)") : "…") : "…"}
                      <span className="pulse-dot inline-block h-3 w-1.5 translate-y-0.5 bg-accent align-baseline" aria-hidden />
                    </pre>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {log.length > 0 && (
        <div className="selectable mt-4 rounded-lg border border-line bg-surface px-4 py-3 font-mono text-xs leading-relaxed text-ink-2">
          {log.map((line, i) => (
            <div key={i}>
              <span className="text-ink-3">{line.at}</span> {line.text}
            </div>
          ))}
        </div>
      )}
      <p className="mt-4 text-xs text-ink-3">{t("The same questions in the same words, the same seed and the same context for every model. A server error is not held against a model.")}</p>
      {run.status === "stopping" && <div className="mt-3"><Note tone="warn">{t("Stopping: the model finishes the word it is writing, then it is unloaded.")}</Note></div>}
      <div className="mt-3 flex flex-wrap gap-3 text-xs text-ink-3">
        {STATUSES.map((s) => (
          <span key={s} className="inline-flex items-center gap-1.5">
            <StatusMark status={s} size={16} /> {STATUS_LABEL[s]}
          </span>
        ))}
      </div>
    </div>
  );
}
