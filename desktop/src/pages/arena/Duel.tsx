import { useState } from "react";

import { Butterfly, type Dot, Radar, ScoreRing, SpeedScatter, StatusMark } from "../../components/arena/charts";
import { Note, Section, cx } from "../../components/ui";
import { CATEGORY_LABEL, duel, fmt, interval, KIND_LABEL, label, type ModelReport, modelLine, type Report, seriesVar, usedAreas } from "../../lib/arena";
import { t } from "../../lib/i18n";

function Challenger({ model, slot, onPick }: { model: ModelReport; slot: "A" | "B" | null; onPick: () => void }) {
  return (
    <button
      type="button"
      aria-pressed={slot !== null}
      onClick={onPick}
      className={cx("relative flex items-center gap-2.5 rounded-lg border px-3 py-2 text-left transition-colors duration-150", slot ? "bg-raised" : "border-line hover:border-line-strong")}
      style={slot ? { borderColor: seriesVar(model.position) } : undefined}
    >
      {slot && (
        <span className="absolute -top-2 -left-1.5 grid h-4 w-4 place-content-center rounded-sm text-2xs font-bold text-bg" style={{ background: seriesVar(model.position) }}>
          {slot}
        </span>
      )}
      <ScoreRing value={model.score ?? model.score_all} size={34} stroke={4} color={seriesVar(model.position)} caption="" />
      <div className="min-w-0">
        <div className="max-w-[150px] truncate text-sm font-medium">{model.label}</div>
        <div className="tabular text-2xs text-ink-3">
          {fmt(model.score ?? model.score_all)} · {model.summary.speed.tps_mean ? `${fmt(model.summary.speed.tps_mean, model.summary.speed.tps_mean >= 100 ? 0 : 1)} tok/s` : "–"}
        </div>
      </div>
    </button>
  );
}

function Side({ model, wins, align }: { model: ModelReport; wins: number; align: "left" | "right" }) {
  const ring = <ScoreRing value={model.score ?? model.score_all} size={112} stroke={8} color={seriesVar(model.position)} />;
  const facts = (
    <div className={cx("grid min-w-0 gap-2.5", align === "right" && "text-right")}>
      <div>
        <div className="truncate text-base font-semibold" title={model.label}>{model.label}</div>
        <div className="truncate text-xs text-ink-3">{modelLine(model)}</div>
      </div>
      <div className={cx("flex gap-5 text-center", align === "right" && "flex-row-reverse")}>
        <div>
          <div className="tabular text-lg font-semibold">{wins}</div>
          <div className="text-2xs text-ink-3">{t("areas won")}</div>
        </div>
        <div>
          <div className="tabular text-lg font-semibold">{model.summary.speed.tps_mean ? fmt(model.summary.speed.tps_mean, model.summary.speed.tps_mean >= 100 ? 0 : 1) : "–"}</div>
          <div className="text-2xs text-ink-3">tok/s</div>
        </div>
        <div>
          <div className="tabular text-lg font-semibold">{interval(model.ci, model.score)?.replace("±", "") ?? "–"}</div>
          <div className="text-2xs text-ink-3">{t("± confidence")}</div>
        </div>
      </div>
    </div>
  );
  return <div className={cx("flex items-center gap-5", align === "right" && "flex-row-reverse")}>{ring}{facts}</div>;
}

export function Duel({ report, onOpenQuestion }: { report: Report; onOpenQuestion: (id: string) => void }) {
  const ranked = report.models.filter((m) => m.score !== null || m.score_all !== null);
  const [pair, setPair] = useState<[string, string]>([ranked[0]?.key ?? "", ranked[1]?.key ?? ranked[0]?.key ?? ""]);
  const [next, setNext] = useState<0 | 1>(1);
  const [all, setAll] = useState(false);

  if (ranked.length < 2) {
    return (
      <div className="px-7 py-6">
        <Note tone="warn">{t("A duel needs two models with a score. Run the challenge with at least two models.")}</Note>
      </div>
    );
  }
  const data = duel(report, pair[0], pair[1]);
  const axes = data ? usedAreas([data.a, data.b], report.areas) : report.areas;
  const pick = (key: string) => {
    if (pair.includes(key)) return;
    const copy: [string, string] = [pair[0], pair[1]];
    copy[next] = key;
    setPair(copy);
    setNext(next === 0 ? 1 : 0);
  };
  const dots: Dot[] = report.models.map((m) => ({ key: m.key, label: m.label, x: m.summary.speed.tps_mean, y: m.score ?? m.score_all, color: seriesVar(m.position), highlight: pair.includes(m.key) }));

  return (
    <div className="grid gap-8 px-7 py-6">
      <div className="flex flex-wrap items-center gap-2.5">
        <span className="text-xs text-ink-3">{t("Challengers")}</span>
        {ranked.map((m) => (
          <Challenger key={m.key} model={m} slot={m.key === pair[0] ? "A" : m.key === pair[1] ? "B" : null} onPick={() => pick(m.key)} />
        ))}
        <span className="text-xs text-ink-3">{t("tap to change A or B")}</span>
      </div>

      {data && (
        <>
          <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-6 rounded-xl border border-line bg-surface px-7 py-5">
            <Side model={data.a} wins={data.aWins} align="left" />
            <div className="grid justify-items-center gap-1.5 text-center">
              <div className="text-2xl font-semibold tracking-widest text-ink-3">VS</div>
              {data.delta !== null && (
                <div className="tabular rounded-md border border-line bg-bg px-2.5 py-1 text-xs font-semibold">
                  {data.delta === 0 ? t("tied") : t("{n} points", { n: `${data.delta > 0 ? "+" : "−"}${fmt(Math.abs(data.delta))}` })}
                </div>
              )}
              {data.compared > 0 && (
                <div className="max-w-[160px] text-2xs text-ink-3">
                  {data.aWins === data.bWins
                    ? t("{n} areas each out of {total}", { n: data.aWins, total: data.compared })
                    : t("{model} wins {n} areas out of {total}", { model: (data.aWins > data.bWins ? data.a : data.b).label, n: Math.max(data.aWins, data.bWins), total: data.compared })}
                </div>
              )}
            </div>
            <Side model={data.b} wins={data.bWins} align="right" />
          </div>

          <div className="grid grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)] gap-10">
            <Section title={t("Area by area")} aside={t("who wins where")}>
              <Butterfly rows={data.areas.filter((r) => axes.includes(r.area))} aColor={seriesVar(data.a.position)} bColor={seriesVar(data.b.position)} aLabel={data.a.label} bLabel={data.b.label} />
            </Section>
            <Section title={t("Profile")} aside={t("overlaid")}>
              <Radar
                axes={axes}
                size={300}
                series={[data.a, data.b].map((m) => ({ key: m.key, label: m.label, color: seriesVar(m.position), values: m.areas }))}
              />
            </Section>
          </div>

          <Section title={t("Where they part")} aside={t("{n} questions with a different outcome", { n: data.splits.length })}>
            {data.splits.length === 0 ? (
              <p className="text-sm text-ink-3">{t("They did the same on every question both were scored on.")}</p>
            ) : (
              <div className="grid gap-2">
                {(all ? data.splits : data.splits.slice(0, 6)).map((split) => {
                  const better = split.better === "a" ? data.a : data.b;
                  const worse = split.better === "a" ? data.b : data.a;
                  const loser = split.better === "a" ? split.b : split.a;
                  return (
                    <button
                      key={split.row.id}
                      type="button"
                      onClick={() => onOpenQuestion(split.row.id)}
                      className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 rounded-lg border border-line px-3.5 py-2.5 text-left transition-colors duration-150 hover:border-line-strong hover:bg-surface"
                    >
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium">{split.row.title}</div>
                        <div className="truncate text-xs text-ink-3">
                          {label(CATEGORY_LABEL, split.row.category)} · <span className="font-mono">{split.row.id}</span>
                          {loser.kind && ` · ${worse.label}: ${label(KIND_LABEL, loser.kind).toLowerCase()}`}
                        </div>
                      </div>
                      <div className="flex shrink-0 items-center gap-2 text-xs">
                        <span className="inline-flex items-center gap-1.5">
                          <StatusMark status={split.a.status} size={18} /> <span className="max-w-[90px] truncate text-ink-2">{data.a.label}</span>
                        </span>
                        <span className="inline-flex items-center gap-1.5">
                          <StatusMark status={split.b.status} size={18} /> <span className="max-w-[90px] truncate text-ink-2">{data.b.label}</span>
                        </span>
                        <span className="sr-only">{t("{model} did better", { model: better.label })}</span>
                      </div>
                    </button>
                  );
                })}
                {data.splits.length > 6 && (
                  <button type="button" className="justify-self-start text-xs text-accent hover:underline" onClick={() => setAll(!all)}>
                    {all ? t("Show fewer") : t("Show all {n}", { n: data.splits.length })}
                  </button>
                )}
              </div>
            )}
          </Section>
        </>
      )}

      <Section title={t("Speed against reliability")} aside={t("up and to the right is better")}>
        <div className="max-w-[560px]">
          <SpeedScatter dots={dots} />
        </div>
      </Section>
    </div>
  );
}
