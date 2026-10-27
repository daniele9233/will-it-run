import { Check } from "lucide-react";
import { useState } from "react";

import { Note, Tabs } from "../../components/ui";
import { fmt, type Report } from "../../lib/arena";
import { getLang, t } from "../../lib/i18n";
import { Duel } from "./Duel";
import { Matrix } from "./Matrix";
import { Podium } from "./Podium";
import { Quants } from "./Quants";

export type Tab = "board" | "questions" | "duel" | "quants";

function Tag({ children, mono = false, tone }: { children: React.ReactNode; mono?: boolean; tone?: "ok" | "warn" }) {
  return (
    <span className={`inline-flex h-6 items-center gap-1 rounded-md border border-line px-2 text-xs ${mono ? "font-mono" : ""} ${tone === "ok" ? "text-ok" : tone === "warn" ? "text-warn" : "text-ink-2"}`}>
      {children}
    </span>
  );
}

/** What a run was, in the small print: the things that make two runs the same or not. */
export function Facts({ report }: { report: Report }) {
  const run = report.run;
  const s = run.settings;
  const counts = report.models.reduce(
    (sum, m) => ({ unscored: sum.unscored + m.summary.counts.unscored, skipped: sum.skipped + m.summary.counts.skipped, error: sum.error + m.summary.counts.error }),
    { unscored: 0, skipped: 0, error: 0 },
  );
  const gpu = run.environment.gpu?.[0];
  return (
    <div className="flex flex-wrap gap-1.5 border-b border-line px-7 py-2.5">
      <Tag mono>suite {run.suite_version}</Tag>
      <Tag mono>hash {run.suite_hash.slice(0, 4)}…{run.suite_hash.slice(-4)}</Tag>
      <Tag mono>seed {run.seed}</Tag>
      <Tag>{t("temperature {n}", { n: fmt(s.temperature, 0) === fmt(s.temperature, 1) ? fmt(s.temperature, 0) : fmt(s.temperature, 1) })}</Tag>
      <Tag>{s.repeats === 1 ? t("1 repeat") : t("{n} repeats", { n: s.repeats })}</Tag>
      <Tag>{t("{n} questions", { n: report.matrix.length })}</Tag>
      <Tag>{t("context {n}", { n: s.context.toLocaleString(getLang()) })}</Tag>
      {gpu && <Tag>{gpu}{run.environment.vram_gb ? ` · ${run.environment.vram_gb.toFixed(0)} GB` : ""}</Tag>}
      <Tag tone="ok">
        <Check size={11} aria-hidden /> {t("reproducible")}
      </Tag>
      {counts.unscored > 0 && <Tag>{t("{n} not evaluated", { n: counts.unscored })}</Tag>}
      {counts.skipped > 0 && <Tag>{t("{n} skipped", { n: counts.skipped })}</Tag>}
      {counts.error > 0 && <Tag tone="warn">{t("{n} run errors", { n: counts.error })}</Tag>}
      {s.cloud_sampling && <Tag>{t("cloud models: sampling decided by the provider")}</Tag>}
    </div>
  );
}

export function Results({ report, tab, onTab, selected, onSelect, onChanged }: { report: Report; tab: Tab; onTab: (tab: Tab) => void; selected: string | null; onSelect: (id: string | null) => void; onChanged: () => void }) {
  const [shown, setShown] = useState(false);
  const failed = report.models.filter((m) => m.status === "failed");
  // The tab is there when the run holds two or more local models: the Arena groups the ones that are one model, and the person can group by hand.
  const quants = report.models.filter((m) => !m.cloud).length >= 2;
  const current: Tab = tab === "quants" && !quants ? "board" : tab;
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Facts report={report} />
      {report.run.settings.merged && (
        <div className="px-7 pt-3">
          <Note>
            <div className="grid gap-1">
              <span>{t("A comparison of {n} separate challenges that asked the same questions. The answers are copies: it has nothing left to ask.", { n: report.run.settings.merged.from.length })}</span>
              {report.run.settings.merged.differences.map((line) => (
                <span key={line} className="text-xs text-ink-3">
                  · {line}
                </span>
              ))}
              {report.run.settings.merged.replaced.map((item) => (
                <span key={`${item.model}-${item.kept}`} className="text-xs text-ink-3">
                  · {t("{model} was in more than one challenge: the newest answers are the ones shown.", { model: item.model })}
                </span>
              ))}
            </div>
          </Note>
        </div>
      )}
      {failed.length > 0 && !shown && (
        <div className="px-7 pt-3">
          <Note tone="warn" action={<button type="button" className="text-xs text-ink-2 hover:text-ink" onClick={() => setShown(true)}>{t("Hide")}</button>}>
            {failed.map((m) => `${m.label}: ${m.error ?? t("it could not run")}`).join(" · ")}
          </Note>
        </div>
      )}
      <div className="px-7 pt-3">
        <Tabs
          value={current}
          onChange={onTab}
          tabs={[
            { value: "board", label: t("Ranking") },
            { value: "questions", label: t("Questions") },
            { value: "duel", label: t("Duel") },
            ...(quants ? [{ value: "quants" as Tab, label: t("Quantizations") }] : []),
          ]}
        />
      </div>
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        {current === "board" && (
          <div className="min-h-0 flex-1 overflow-y-auto">
            <Podium report={report} />
          </div>
        )}
        {current === "questions" && <Matrix report={report} selected={selected} onSelect={onSelect} onChanged={onChanged} />}
        {current === "quants" && (
          <div className="min-h-0 flex-1 overflow-y-auto">
            <Quants report={report} />
          </div>
        )}
        {current === "duel" && (
          <div className="min-h-0 flex-1 overflow-y-auto">
            <Duel
              report={report}
              onOpenQuestion={(id) => {
                onSelect(id);
                onTab("questions");
              }}
            />
          </div>
        )}
      </div>
    </div>
  );
}
