import { useMemo, useState } from "react";

import { Button, Note, TextInput, cx } from "../../components/ui";
import { isLive, type RunRow } from "../../lib/arena";
import { timeAgo } from "../../lib/format";
import { t } from "../../lib/i18n";

/** What two challenges must share for their scores to mean the same thing: the very same questions. */
const sameQuestions = (a: RunRow, b: RunRow): boolean => a.suite_hash === b.suite_hash && a.seed === b.seed && a.suite_version === b.suite_version;

/** A challenge that can be put beside another: it has finished (or been stopped), holds answers, and is not itself a comparison. */
export const comparable = (run: RunRow): boolean => !isLive(run.status) && !run.merged && run.done > 0;

export function CompareForm({
  runs,
  working,
  onCancel,
  onCompare,
}: {
  runs: RunRow[];
  working: boolean;
  onCancel: () => void;
  onCompare: (ids: string[], title: string) => void;
}) {
  const candidates = useMemo(() => runs.filter(comparable), [runs]);
  const [chosen, setChosen] = useState<string[]>([]);
  const [title, setTitle] = useState("");
  const anchor = candidates.find((r) => r.id === chosen[0]) ?? null;

  const toggle = (id: string) => setChosen((now) => (now.includes(id) ? now.filter((x) => x !== id) : [...now, id]));

  if (candidates.length < 2) {
    return (
      <div className="grid gap-3">
        <Note tone="warn">{t("No two challenges can be compared yet: you need two finished ones that asked the same questions.")}</Note>
        <div className="flex justify-end">
          <Button onClick={onCancel}>{t("Close")}</Button>
        </div>
      </div>
    );
  }

  return (
    <div className="grid gap-3">
      <p>{t("Models tested in separate challenges can be put side by side when the challenges asked the same questions (same pack and seed). The answers are copied: the originals stay as they are.")}</p>
      <ul className="grid max-h-[40vh] gap-1.5 overflow-y-auto pr-1">
        {candidates.map((run) => {
          const blocked = anchor !== null && run.id !== anchor.id && !sameQuestions(anchor, run);
          const on = chosen.includes(run.id);
          return (
            <li key={run.id}>
              <label
                title={blocked ? t("Other questions: not comparable with the ones chosen") : undefined}
                className={cx(
                  "grid cursor-pointer grid-cols-[auto_1fr] items-start gap-2.5 rounded-md border px-3 py-2",
                  on ? "border-accent bg-raised" : "border-line",
                  blocked && "cursor-not-allowed opacity-45",
                )}
              >
                <input type="checkbox" checked={on} disabled={blocked} onChange={() => toggle(run.id)} className="mt-1" />
                <span className="grid gap-0.5">
                  <span className="text-sm font-medium text-ink">{run.title || run.id}</span>
                  <span className="text-xs text-ink-3">
                    {run.models.map((m) => m.label).join(" · ")} · {run.preset ?? t("custom")} · seed {run.seed} · {timeAgo(run.started_at ?? run.created_at)}
                  </span>
                </span>
              </label>
            </li>
          );
        })}
      </ul>
      <label className="grid gap-1 text-xs text-ink-3">
        {t("Name of the comparison (optional)")}
        <TextInput value={title} onChange={(e) => setTitle(e.target.value)} />
      </label>
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-ink-3">{chosen.length < 2 ? t("Choose two or more challenges.") : t("{n} challenges chosen", { n: chosen.length })}</span>
        <div className="flex gap-2">
          <Button onClick={onCancel}>{t("Cancel")}</Button>
          <Button variant="primary" loading={working} disabled={chosen.length < 2} onClick={() => onCompare(chosen, title)}>
            {t("Compare")}
          </Button>
        </div>
      </div>
    </div>
  );
}
