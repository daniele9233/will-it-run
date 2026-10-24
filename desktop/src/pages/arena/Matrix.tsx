import { ChevronRight } from "lucide-react";
import { useMemo, useState } from "react";

import { Legend, StatusMark } from "../../components/arena/charts";
import { Chip, Select, cx } from "../../components/ui";
import {
  CATEGORY_LABEL,
  fmt,
  groupByCategory,
  inFilter,
  label,
  type MatrixFilter,
  type Report,
  seriesVar,
  STATUS_GLYPH,
  STATUS_LABEL,
  STATUSES,
} from "../../lib/arena";
import { t } from "../../lib/i18n";
import { QuestionPanel } from "./QuestionPanel";

const FILTERS: { id: MatrixFilter; label: string }[] = [
  { id: "all", label: t("All") },
  { id: "wrong", label: t("Only mistakes") },
  { id: "unscored", label: t("Not evaluated") },
  { id: "split", label: t("Where they differ") },
];

export function Matrix({ report, selected, onSelect, onChanged }: { report: Report; selected: string | null; onSelect: (id: string | null) => void; onChanged: () => void }) {
  const models = report.models;
  const keys = useMemo(() => models.map((m) => m.key), [models]);
  const [filter, setFilter] = useState<MatrixFilter>("all");
  const [category, setCategory] = useState("");
  const [open, setOpen] = useState<string[]>([]);

  const rows = useMemo(
    () => report.matrix.filter((row) => (!category || row.category === category) && inFilter(row, keys, filter)),
    [report.matrix, category, keys, filter],
  );
  const groups = useMemo(() => groupByCategory(rows), [rows]);
  // Choosing a filter is asking to see what it finds: the groups open for it.
  const expanded = (id: string) => filter !== "all" || category === id || open.includes(id);
  const columns = `minmax(220px,1fr) repeat(${models.length}, minmax(64px, 84px))`;
  const categoryOf = Object.fromEntries(report.categories.map((c) => [c.id, c]));

  const move = (delta: number) => {
    const visible = groups.filter((g) => expanded(g.category)).flatMap((g) => g.rows);
    const at = visible.findIndex((r) => r.id === selected);
    const next = visible[Math.max(0, Math.min(visible.length - 1, at + delta))];
    if (next) onSelect(next.id);
  };

  return (
    <div className={cx("grid min-h-0 flex-1", selected ? "grid-cols-[minmax(0,1fr)_minmax(340px,430px)]" : "grid-cols-1")}>
      <div
        className="flex min-h-0 min-w-0 flex-col outline-none"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            move(1);
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            move(-1);
          } else if (e.key === "Escape") onSelect(null);
        }}
      >
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-7 py-2.5">
          <div role="radiogroup" aria-label={t("Which questions")} className="inline-flex rounded-md border border-line bg-bg p-0.5">
            {FILTERS.map((f) => (
              <button
                key={f.id}
                type="button"
                role="radio"
                aria-checked={filter === f.id}
                onClick={() => setFilter(f.id)}
                className={cx("h-6 rounded-sm px-2.5 text-xs transition-colors duration-150", filter === f.id ? "bg-raised font-medium text-ink" : "text-ink-3 hover:text-ink")}
              >
                {f.label}
              </button>
            ))}
          </div>
          <div className="w-[190px]">
            <Select aria-label={t("Category")} value={category} onChange={(e) => setCategory(e.target.value)} className="h-7 text-xs">
              <option value="">{t("All the categories")}</option>
              {report.categories.map((c) => (
                <option key={c.id} value={c.id}>
                  {label(CATEGORY_LABEL, c.id)}
                </option>
              ))}
            </Select>
          </div>
          <Chip active={false} onClick={() => setOpen(open.length ? [] : report.categories.map((c) => c.id))}>
            {open.length ? t("Close the groups") : t("Open the groups")}
          </Chip>
          <span className="ml-auto text-xs text-ink-3">{t("{n} questions", { n: rows.length })}</span>
        </div>

        <div className="min-h-0 flex-1 overflow-auto">
          <div className="min-w-fit">
            <div className="sticky top-0 z-10 grid items-end gap-x-1 border-b border-line bg-bg px-7 py-2" style={{ gridTemplateColumns: columns }}>
              <span className="text-xs text-ink-3">{t("Question")}</span>
              {models.map((m) => (
                <div key={m.key} className="min-w-0 text-center" title={m.label}>
                  <span className="mx-auto mb-1 block h-2 w-2 rounded-full" style={{ background: seriesVar(m.position) }} />
                  <div className="truncate text-2xs font-medium">{m.label}</div>
                  <div className="tabular text-2xs text-ink-3">{m.score !== null ? `${fmt(m.score, 0)}%` : m.score_all !== null ? `${fmt(m.score_all, 0)}%` : "–"}</div>
                </div>
              ))}
            </div>
            {groups.length === 0 && <p className="px-7 py-10 text-sm text-ink-3">{t("No question matches.")}</p>}
            {groups.map((group) => {
              const info = categoryOf[group.category];
              const isOpen = expanded(group.category);
              return (
                <div key={group.category}>
                  <button
                    type="button"
                    aria-expanded={isOpen}
                    onClick={() => setOpen((now) => (now.includes(group.category) ? now.filter((c) => c !== group.category) : [...now, group.category]))}
                    className="grid w-full items-center gap-x-1 border-b border-line bg-panel px-7 py-1.5 text-left hover:bg-surface"
                    style={{ gridTemplateColumns: columns }}
                  >
                    <span className="flex items-center gap-1.5 text-sm font-semibold">
                      <ChevronRight size={13} className={cx("transition-transform duration-150", isOpen && "rotate-90")} aria-hidden />
                      {label(CATEGORY_LABEL, group.category)}
                      <span className="text-xs font-normal text-ink-3">{t("{n} questions", { n: group.rows.length })}</span>
                    </span>
                    {models.map((m) => {
                      const cell = info?.models[m.key];
                      const score = cell?.score ?? null;
                      return (
                        <span key={m.key} className="tabular text-center text-xs font-medium" style={{ color: score === null ? "var(--ink-3)" : score >= 70 ? "var(--ok)" : score >= 40 ? "var(--warn)" : "var(--bad)" }}>
                          {score === null ? (cell && cell.skipped === cell.total ? STATUS_GLYPH.skipped : cell && cell.unscored ? STATUS_GLYPH.unscored : "–") : `${fmt(score, 0)}%`}
                        </span>
                      );
                    })}
                  </button>
                  {isOpen &&
                    group.rows.map((row) => (
                      <button
                        key={row.id}
                        type="button"
                        onClick={() => onSelect(row.id === selected ? null : row.id)}
                        aria-pressed={row.id === selected}
                        className={cx("grid w-full items-center gap-x-1 border-b border-line px-7 py-1 text-left transition-colors duration-100", row.id === selected ? "bg-raised" : "hover:bg-surface")}
                        style={{ gridTemplateColumns: columns }}
                      >
                        <span className="flex min-w-0 items-baseline gap-2 pl-5">
                          <span className="shrink-0 font-mono text-2xs text-ink-3">{row.id}</span>
                          <span className="truncate text-sm text-ink-2">{row.title}</span>
                        </span>
                        {models.map((m) => (
                          <span key={m.key} className="text-center">
                            <StatusMark status={row.cells[m.key]?.status ?? null} />
                          </span>
                        ))}
                      </button>
                    ))}
                </div>
              );
            })}
          </div>
        </div>
        <div className="border-t border-line px-7 py-2">
          <Legend>
            {STATUSES.map((s) => (
              <span key={s} className="inline-flex items-center gap-1.5 text-2xs text-ink-3">
                <StatusMark status={s} size={16} /> {STATUS_LABEL[s]}
              </span>
            ))}
          </Legend>
        </div>
      </div>
      {selected && <QuestionPanel key={selected} report={report} questionId={selected} onClose={() => onSelect(null)} onChanged={onChanged} />}
    </div>
  );
}
