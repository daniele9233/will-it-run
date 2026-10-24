import { useState } from "react";

import { AreaBars, ErrorBar, KindLegend, Legend, Radar, ScoreBar, ScoreRing, Swatch } from "../../components/arena/charts";
import { Note, Section, cx } from "../../components/ui";
import {
  AREA_LABEL,
  CATEGORY_LABEL,
  fmt,
  interval,
  label,
  type ModelReport,
  modelLine,
  type Report,
  seriesVar,
  speedLine,
  strongestArea,
  usedAreas,
} from "../../lib/arena";
import { t } from "../../lib/i18n";

function PodiumPlace({ model, place }: { model: ModelReport; place: 1 | 2 | 3 }) {
  const first = place === 1;
  const strongest = strongestArea(model);
  return (
    <div className={cx("flex w-[180px] shrink-0 flex-col items-center", first ? "order-2" : place === 2 ? "order-1 pt-8" : "order-3 pt-12")}>
      <ScoreRing value={model.score ?? model.score_all} size={first ? 132 : 104} stroke={first ? 9 : 7} color={seriesVar(model.position)} />
      <div className="mt-3 max-w-[210px] text-center">
        <div className="truncate text-sm font-semibold" title={model.label}>
          {model.label}
        </div>
        <div className="truncate text-xs text-ink-3">{modelLine(model)}</div>
        <div className="tabular mt-0.5 text-xs text-ink-2">{speedLine(model)}</div>
        {strongest && <div className="mt-0.5 text-2xs text-ink-3">{t("strong in {area} ({score})", { area: label(AREA_LABEL, strongest.area).toLowerCase(), score: fmt(strongest.score, 0) })}</div>}
      </div>
      <div
        className={cx("mt-3 grid w-full place-content-center rounded-t-md border border-b-0 border-line text-3xl font-semibold text-ink-3", first ? "h-[84px]" : place === 2 ? "h-[58px]" : "h-[44px]")}
        style={{ borderTopColor: seriesVar(model.position), borderTopWidth: 3, background: "color-mix(in oklch, var(--surface) 70%, transparent)" }}
        aria-hidden
      >
        {place}
      </div>
    </div>
  );
}

export function Podium({ report }: { report: Report }) {
  const ranked = report.models.filter((m) => m.rank !== null);
  const top = ranked.slice(0, 3);
  // Which models the radar and the bars draw: the first three until the person picks others.
  const axes = usedAreas(report.models, report.areas);
  const [picked, setPicked] = useState<string[] | null>(null);
  const shownKeys = picked ?? top.map((m) => m.key);
  const series = report.models.filter((m) => shownKeys.includes(m.key)).map((m) => ({ key: m.key, label: m.label, color: seriesVar(m.position), values: m.areas }));
  const toggle = (key: string) => {
    if (shownKeys.includes(key)) {
      if (shownKeys.length > 1) setPicked(shownKeys.filter((k) => k !== key));
    } else {
      setPicked([...shownKeys, key]);
    }
  };

  const scoredCategories = report.categories.filter((c) => c.scored);
  const unscored = report.models.reduce((n, m) => n + m.summary.counts.unscored, 0);
  const common = report.common.map((c) => label(CATEGORY_LABEL, c)).join(", ");

  if (ranked.length === 0) {
    return (
      <div className="px-7 py-6">
        <Note tone="warn">{t("No model has a score yet: none of the questions asked so far could be scored.")}</Note>
      </div>
    );
  }

  return (
    <div className="grid gap-9 px-7 py-6">
      <div className="grid grid-cols-1 items-start gap-10 min-[1400px]:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)]">
        <div className="flex items-end justify-center gap-4 pt-2">
          {top.map((m, i) => (
            <PodiumPlace key={m.key} model={m} place={(i + 1) as 1 | 2 | 3} />
          ))}
        </div>
        <div>
          <div className="mb-1 flex items-baseline justify-between">
            <h2 className="text-sm font-semibold">{t("Profile by area")}</h2>
            <span className="text-xs text-ink-3">{t("tap a model to add or remove it")}</span>
          </div>
          <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3">
            <Radar axes={axes} series={series} size={320} />
            <div className="grid max-w-[180px] gap-2">
              {report.models.map((m) => {
                const on = series.some((s) => s.key === m.key);
                const strongest = strongestArea(m);
                return (
                  <button key={m.key} type="button" aria-pressed={on} onClick={() => toggle(m.key)} className={cx("grid text-left transition-opacity duration-150", !on && "opacity-45")}>
                    <span className="flex items-center gap-1.5 text-xs font-medium">
                      <span className="inline-block h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: seriesVar(m.position) }} />
                      <span className="truncate">{m.label}</span>
                    </span>
                    {strongest && <span className="pl-4 text-2xs text-ink-3">{t("strong in {area} ({score})", { area: label(AREA_LABEL, strongest.area).toLowerCase(), score: fmt(strongest.score, 0) })}</span>}
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      </div>

      <Section title={t("Ranking")} aside={t("score ± the 95% confidence interval")}>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs text-ink-3">
                <th className="w-8 py-2 font-medium">#</th>
                <th className="py-2 pr-4 font-medium">{t("Model")}</th>
                <th className="w-[26%] py-2 pr-4 font-medium">{t("Score")}</th>
                <th className="py-2 pr-4 font-medium">{t("Scored")}</th>
                <th className="py-2 pr-4 font-medium">tok/s</th>
                <th className="w-[22%] py-2 font-medium">{t("Where it goes wrong")}</th>
              </tr>
            </thead>
            <tbody>
              {report.models.map((m) => (
                <tr key={m.key} className="border-b border-line align-middle">
                  <td className="tabular py-3 text-ink-3">{m.rank ?? "–"}</td>
                  <td className="py-3 pr-4">
                    <div className="flex items-center gap-2">
                      <span className="inline-block h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: seriesVar(m.position) }} />
                      <div className="min-w-0">
                        <div className="truncate font-medium" title={m.label}>
                          {m.label}
                        </div>
                        <div className="truncate text-xs text-ink-3">{modelLine(m)}</div>
                      </div>
                    </div>
                  </td>
                  <td className="py-3 pr-4">
                    {m.status === "failed" && m.summary.answers === 0 ? (
                      <span className="text-xs text-bad">{m.error ?? t("it could not run")}</span>
                    ) : (
                      <div className="grid grid-cols-[1fr_auto] items-center gap-3">
                        <ScoreBar value={m.score ?? m.score_all} ci={m.score !== null ? m.ci : m.ci_all} color={seriesVar(m.position)} />
                        <div className="tabular text-right">
                          <div className="font-semibold">{fmt(m.score ?? m.score_all)}</div>
                          <div className="text-2xs text-ink-3">{interval(m.score !== null ? m.ci : m.ci_all, m.score ?? m.score_all)}</div>
                        </div>
                      </div>
                    )}
                    {m.score !== null && m.score_all !== null && Math.abs(m.score - m.score_all) >= 0.05 && (
                      <div className="mt-0.5 text-2xs text-ink-3">{t("{score} on everything it did", { score: fmt(m.score_all) })}</div>
                    )}
                  </td>
                  <td className="tabular py-3 pr-4 text-ink-2">
                    {m.summary.questions.scored}/{m.summary.questions.total}
                  </td>
                  <td className="tabular py-3 pr-4 text-ink-2">{m.summary.speed.tps_mean ? fmt(m.summary.speed.tps_mean, m.summary.speed.tps_mean >= 100 ? 0 : 1) : "–"}</td>
                  <td className="py-3">
                    <ErrorBar kinds={m.summary.fail_kinds} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="mt-3 grid gap-1.5">
          <KindLegend />
          {common && <p className="text-xs text-ink-3">{t("The score is the mean of the categories every model could be scored on: {names}.", { names: common })}</p>}
          {unscored > 0 && <p className="text-xs text-ink-3">{t("{n} answers could not be judged by code: they are not evaluated, and they are not counted as wrong.", { n: unscored })}</p>}
        </div>
      </Section>

      <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-10">
        <Section title={t("By area")} aside={<Legend>{series.slice(0, 4).map((s) => <Swatch key={s.key} color={s.color}>{s.label}</Swatch>)}</Legend>}>
          <AreaBars areas={axes} series={series} />
        </Section>
        <Section title={t("By category")} aside={t("share of right answers · scored / asked")}>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-ink-3">
                  <th className="py-1.5 pr-3 font-medium">{t("Category")}</th>
                  {report.models.map((m) => (
                    <th key={m.key} className="py-1.5 pr-2 text-right font-medium">
                      <span className="inline-flex items-center justify-end gap-1">
                        <span className="inline-block h-2 w-2 rounded-full" style={{ background: seriesVar(m.position) }} />
                        <span className="max-w-[72px] truncate" title={m.label}>
                          {m.label}
                        </span>
                      </span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {scoredCategories.map((c) => (
                  <tr key={c.id} className="border-t border-line">
                    <td className="py-1.5 pr-3 text-ink-2">{label(CATEGORY_LABEL, c.id)}</td>
                    {report.models.map((m) => {
                      const cell = c.models[m.key];
                      const score = cell?.score ?? null;
                      return (
                        <td key={m.key} className="tabular py-1 pr-2 text-right">
                          <span
                            className="inline-block min-w-[58px] rounded-[4px] px-1.5 py-0.5"
                            style={{ background: score === null ? "transparent" : `color-mix(in oklch, var(--accent) ${Math.round(8 + 42 * (score / 100))}%, transparent)` }}
                            title={cell ? t("{scored} scored of {total} asked", { scored: cell.scored, total: cell.total }) : undefined}
                          >
                            {score === null ? "–" : `${fmt(score, 0)}%`}
                            <span className="ml-1 text-2xs text-ink-3">{cell ? `${cell.scored}/${cell.total}` : ""}</span>
                          </span>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      </div>
    </div>
  );
}
