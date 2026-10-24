import { FileText, X } from "lucide-react";
import { useState } from "react";

import { StatusMark } from "../../components/arena/charts";
import { Badge, Button, Collapsible, IconButton, Note, SkeletonRows, cx } from "../../components/ui";
import { api } from "../../lib/api";
import {
  type Answer,
  CATEGORY_LABEL,
  fmt,
  KIND_LABEL,
  label,
  type QuestionDetail,
  type Report,
  seriesVar,
  STATUS_LABEL,
  statusTone,
} from "../../lib/arena";
import { errorMessage, useLoad } from "../../lib/hooks";
import { t } from "../../lib/i18n";
import { useApp } from "../../lib/store";

function Text({ value, lines = 8, mono = true }: { value: string; lines?: number; mono?: boolean }) {
  const [open, setOpen] = useState(false);
  const rows = value.split("\n");
  const long = rows.length > lines || value.length > 900;
  const shown = open || !long ? value : rows.slice(0, lines).join("\n").slice(0, 900) + "…";
  return (
    <div>
      <pre className={cx("selectable rounded-md border border-line bg-bg p-2.5 text-xs break-words whitespace-pre-wrap text-ink", mono && "font-mono")}>{shown}</pre>
      {long && (
        <button type="button" className="mt-1 text-xs text-accent hover:underline" onClick={() => setOpen(!open)}>
          {open ? t("Show less") : t("Show all")}
        </button>
      )}
    </div>
  );
}

function AnswerCard({ report, model, answer, questionId, onMarked, multiple }: { report: Report; model: Report["models"][number]; answer: Answer; questionId: string; onMarked: () => void; multiple: boolean }) {
  const toast = useApp((s) => s.toast);
  const mark = async (value: "passed" | "failed" | null) => {
    try {
      await api.post(`/api/arena/runs/${report.run.id}/manual`, { model: model.key, question: questionId, repeat: answer.repeat, mark: value, note: "" });
      onMarked();
    } catch (err) {
      toast({ tone: "bad", title: t("The mark was not saved"), body: errorMessage(err) });
    }
  };
  const tone = statusTone(answer.status);
  const speed = [answer.tps ? `${fmt(answer.tps, answer.tps >= 100 ? 0 : 1)} tok/s` : null, answer.output_tokens ? t("{n} tokens", { n: answer.output_tokens }) : null, answer.duration_s ? `${fmt(answer.duration_s, 1)} s` : null]
    .filter(Boolean)
    .join(" · ");
  return (
    <div className="rounded-lg border border-line bg-surface p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="inline-block h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: seriesVar(model.position) }} />
        <span className="text-sm font-semibold">{model.label}</span>
        {multiple && <span className="text-xs text-ink-3">{t("repeat {n}", { n: answer.repeat + 1 })}</span>}
        <StatusMark status={answer.status} size={20} />
        <Badge tone={tone}>{STATUS_LABEL[answer.status]}</Badge>
        {answer.kind && answer.status === "failed" && answer.kind !== "wrong" && <Badge tone="off">{label(KIND_LABEL, answer.kind)}</Badge>}
        {answer.status === "partial" && answer.score !== null && <Badge tone="warn">{fmt(answer.score * 100, 0)}%</Badge>}
        <span className="tabular ml-auto text-xs text-ink-3">{speed}</span>
      </div>
      {answer.reason && <p className="selectable mt-2 text-sm text-ink-2">{answer.reason}</p>}
      {answer.error && answer.status === "error" && <p className="selectable mt-1 text-xs text-ink-3">{answer.error}</p>}
      {answer.checks.length > 1 && (
        <ul className="mt-2 grid gap-0.5 text-xs">
          {answer.checks.map((check, i) => (
            <li key={i} className="grid grid-cols-[14px_1fr] gap-1.5">
              <span className={check.ok ? "text-ok" : "text-bad"} aria-hidden>
                {check.ok ? "✓" : "✗"}
              </span>
              <span className="selectable text-ink-2">{check.text}</span>
            </li>
          ))}
        </ul>
      )}
      {answer.tool_calls.length > 0 && (
        <div className="mt-2 grid gap-1">
          {answer.tool_calls.map((call, i) => (
            <pre key={i} className="selectable rounded-md border border-line bg-bg p-2 font-mono text-xs break-words whitespace-pre-wrap">
              {call.name}({call.arguments === null ? call.raw : JSON.stringify(call.arguments)})
            </pre>
          ))}
        </div>
      )}
      {answer.answer.trim() && (
        <div className="mt-2">
          <Text value={answer.answer.trim()} />
        </div>
      )}
      {!answer.answer.trim() && answer.tool_calls.length === 0 && answer.status !== "skipped" && !answer.reason && <p className="mt-2 text-xs text-ink-3">{t("The reply was empty.")}</p>}
      {answer.reasoning.trim() && (
        <Collapsible title={<span className="text-xs">{t("Its reasoning")}</span>}>
          <Text value={answer.reasoning.trim()} lines={6} />
        </Collapsible>
      )}
      {answer.status === "unscored" && (
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-line pt-2.5">
          <span className="text-xs text-ink-3">{t("Your own reading, kept apart from the score:")}</span>
          <Button size="sm" variant={answer.manual === "passed" ? "primary" : "secondary"} onClick={() => void mark(answer.manual === "passed" ? null : "passed")}>
            {t("Good")}
          </Button>
          <Button size="sm" variant={answer.manual === "failed" ? "primary" : "secondary"} onClick={() => void mark(answer.manual === "failed" ? null : "failed")}>
            {t("Poor")}
          </Button>
        </div>
      )}
    </div>
  );
}

export function QuestionPanel({ report, questionId, models, onClose, onChanged }: { report: Report; questionId: string; models?: string[]; onClose?: () => void; onChanged: () => void }) {
  const detail = useLoad(() => api.get<QuestionDetail>(`/api/arena/runs/${report.run.id}/questions/${questionId}`), [report.run.id, questionId]);
  const row = report.matrix.find((r) => r.id === questionId);
  const data = detail.data;
  const method = data ? Object.values(data.answers).flat().find((a) => a.method)?.method : null;
  const shown = report.models.filter((m) => !models || models.includes(m.key));
  return (
    <aside className="flex min-h-0 flex-col border-l border-line bg-panel" aria-label={t("Detail of the question")}>
      <div className="flex items-start gap-2 border-b border-line px-4 py-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2 text-xs text-ink-3">
            <Badge tone="off">{label(CATEGORY_LABEL, row?.category ?? data?.question.category ?? "")}</Badge>
            <span className="font-mono">{questionId}</span>
            {row && <span>{t("difficulty {n}", { n: row.difficulty })}</span>}
          </div>
          <h3 className="mt-1.5 text-base font-semibold tracking-tight">{row?.title ?? data?.question.title ?? questionId}</h3>
        </div>
        {onClose && (
          <IconButton label={t("Close")} onClick={onClose}>
            <X size={14} />
          </IconButton>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3.5">
        {detail.loading && !data && <SkeletonRows rows={3} height={64} />}
        {detail.error && <Note tone="bad">{detail.error}</Note>}
        {data && (
          <div className="grid gap-4">
            <div>
              <div className="mb-1 text-2xs font-semibold tracking-wider text-ink-3 uppercase">{t("The question")}</div>
              {data.question.system && <p className="selectable mb-1.5 text-xs text-ink-3">{t("System prompt")}: {data.question.system}</p>}
              <Text value={data.question.prompt} lines={6} mono={false} />
              {data.question.images.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-2">
                  {data.question.images.map((image) => (
                    <img key={image.sha256} src={image.data_url} alt={image.name} width={image.width} height={image.height} className="max-h-[220px] w-auto max-w-full rounded-md border border-line bg-white" />
                  ))}
                </div>
              )}
              {data.question.attachments.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-2">
                  {data.question.attachments.map((file) => (
                    <a key={file.sha256} download={file.name} href={`data:${file.mime};base64,${file.data_b64}`} className="inline-flex h-7 items-center gap-1.5 rounded-md border border-line px-2.5 text-xs text-ink-2 hover:border-line-strong hover:text-ink">
                      <FileText size={12} aria-hidden /> {file.name} · {t("{n} pages", { n: file.pages })}
                    </a>
                  ))}
                </div>
              )}
              {data.question.tools && data.question.tools.length > 0 && (
                <p className="mt-2 text-xs text-ink-3">{t("Tools offered")}: {data.question.tools.map((tool) => tool.function.name).join(", ")}</p>
              )}
            </div>
            <div>
              <div className="mb-1 flex items-baseline justify-between">
                <div className="text-2xs font-semibold tracking-wider text-ink-3 uppercase">{t("Expected")}</div>
                {method && <span className="text-2xs text-ink-3">{t("checked by {method}", { method })}</span>}
              </div>
              <Text value={data.question.expected} lines={4} />
              {data.question.reference && data.question.reference !== data.question.expected && (
                <Collapsible title={<span className="text-xs">{t("An answer that passes")}</span>}>
                  <Text value={data.question.reference} lines={8} />
                </Collapsible>
              )}
            </div>
            <div className="grid gap-2.5">
              <div className="text-2xs font-semibold tracking-wider text-ink-3 uppercase">{t("What each model answered")}</div>
              {shown.map((model) => {
                const answers = data.answers[model.key];
                if (!answers?.length) {
                  return (
                    <div key={model.key} className="rounded-lg border border-dashed border-line px-3 py-2.5 text-xs text-ink-3">
                      <span className="mr-2 inline-block h-2 w-2 rounded-full align-middle" style={{ background: seriesVar(model.position) }} />
                      {model.label}: {t("not asked yet")}
                    </div>
                  );
                }
                return answers.map((answer) => (
                  <AnswerCard key={`${model.key}-${answer.repeat}`} report={report} model={model} answer={answer} questionId={questionId} multiple={answers.length > 1} onMarked={() => { detail.reload(); onChanged(); }} />
                ));
              })}
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}
