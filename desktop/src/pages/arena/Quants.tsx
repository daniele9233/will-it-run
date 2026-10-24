import { Check, TriangleAlert } from "lucide-react";
import { useMemo, useState } from "react";

import { QuantScatter, type QuantDot } from "../../components/arena/charts";
import { Badge, Button, Collapsible, Note, Section, Select, SkeletonRows, cx } from "../../components/ui";
import { api, query } from "../../lib/api";
import {
  bottomLine,
  CATEGORY_LABEL,
  deltaTint,
  fmt,
  label,
  type QuantAxis,
  type QuantCandidate,
  quantAxisValue,
  type QuantComparison,
  type QuantMember,
  type QuantsResponse,
  quantVerdictTone,
  QUANT_VERDICT_LABEL,
  type Report,
  repeatAdvice,
  seriesVar,
  signed,
} from "../../lib/arena";
import { gbFromMb, sizeLabel, tps } from "../../lib/format";
import { useLoad } from "../../lib/hooks";
import { t } from "../../lib/i18n";

const AXES: Record<QuantAxis, { label: () => string; format: (value: number) => string }> = {
  size: { label: () => t("File size"), format: (v) => `${fmt(v, 1)} GB` },
  memory: { label: () => t("Graphics memory"), format: (v) => `${fmt(v, 1)} GB` },
  speed: { label: () => t("Speed"), format: (v) => `${fmt(v, 0)} tok/s` },
};
const AXIS_ORDER: QuantAxis[] = ["size", "memory", "speed"];

function name(member: Pick<QuantMember, "quant" | "label">): string {
  return member.quant ?? member.label;
}

function Interval({ range }: { range: [number, number] | null | undefined }) {
  if (!range) return null;
  return <span className="tabular text-2xs text-ink-3">{`${signed(range[0])} … ${signed(range[1])}`}</span>;
}

function VerdictCell({ member }: { member: QuantMember }) {
  if (member.is_reference || !member.verdict) return <Badge tone="accent">{t("reference")}</Badge>;
  const parted = (member.better ?? 0) + (member.worse ?? 0);
  return (
    <div className="grid gap-0.5">
      <span>
        <Badge tone={quantVerdictTone(member.verdict)}>{member.identical ? t("identical answers") : QUANT_VERDICT_LABEL[member.verdict]}</Badge>
      </span>
      {!member.identical && (
        <span className="text-2xs text-ink-3">
          {parted === 0 ? "" : t("{better} better · {worse} worse than the reference", { better: member.better ?? 0, worse: member.worse ?? 0 })}
        </span>
      )}
      {member.verdict === "noise" && !member.identical && member.detectable !== undefined && (
        <span className="text-2xs text-ink-3">{t("under {n} points it cannot be seen", { n: fmt(member.detectable, 1) })}</span>
      )}
    </div>
  );
}

function Table({ comparison, colorOf }: { comparison: QuantComparison; colorOf: (key: string) => string }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[820px] border-collapse text-sm">
        <thead>
          <tr className="text-left text-xs text-ink-3">
            <th className="py-2 pr-3 font-medium">{t("Quantization")}</th>
            <th className="px-3 py-2 text-right font-medium">{t("File")}</th>
            <th className="px-3 py-2 text-right font-medium" title={t("Graphics memory the model took when it was loaded, with the same context for all")}>{t("Graphics memory")}</th>
            <th className="px-3 py-2 text-right font-medium">{t("Speed")}</th>
            <th className="px-3 py-2 text-right font-medium" title={t("Median time before the first word")}>{t("First token")}</th>
            <th className="px-3 py-2 text-right font-medium">{t("Score")}</th>
            <th className="px-3 py-2 text-right font-medium">{t("Difference")}</th>
            <th className="py-2 pl-3 font-medium">{t("Verdict")}</th>
          </tr>
        </thead>
        <tbody>
          {comparison.order.map((key) => {
            const m = comparison.models[key];
            if (!m) return null;
            return (
              <tr key={key} className="border-t border-line align-top">
                <td className="py-2.5 pr-3">
                  <div className="flex items-center gap-2">
                    <span className="inline-block h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: colorOf(key) }} aria-hidden />
                    <div className="min-w-0">
                      <div className="font-mono text-sm font-semibold">{m.quant ?? "?"}</div>
                      <div className="max-w-[220px] truncate text-2xs text-ink-3" title={m.label}>{m.label}</div>
                    </div>
                  </div>
                </td>
                <td className="tabular px-3 py-2.5 text-right">{m.size_bytes ? sizeLabel(m.size_bytes) : "–"}</td>
                <td className="tabular px-3 py-2.5 text-right">
                  {m.vram_mib ? gbFromMb(m.vram_mib) : "–"}
                  {m.spilled && (
                    <span className="ml-1 inline-block align-middle text-warn" title={t("Part of this model did not fit in graphics memory and was in system RAM: its speed is not a fair measure.")}>
                      <TriangleAlert size={12} aria-label={t("Part of it was in system RAM")} />
                    </span>
                  )}
                </td>
                <td className="tabular px-3 py-2.5 text-right">{m.tps_mean ? tps(m.tps_mean) : "–"}</td>
                <td className="tabular px-3 py-2.5 text-right">{m.ttft_median_s !== null ? `${fmt(m.ttft_median_s, m.ttft_median_s < 10 ? 2 : 1)} s` : "–"}</td>
                <td className="tabular px-3 py-2.5 text-right">
                  <div className="font-semibold">{fmt(m.score)}</div>
                  {m.ci && <div className="text-2xs text-ink-3">{`${fmt(m.ci[0], 0)}–${fmt(m.ci[1], 0)}`}</div>}
                </td>
                <td className="px-3 py-2.5 text-right">
                  {m.is_reference || m.delta === undefined ? (
                    <span className="text-ink-3">–</span>
                  ) : (
                    <div className="grid justify-items-end">
                      <span className={cx("tabular font-semibold", m.verdict === "worse" ? "text-bad" : m.verdict === "better" ? "text-ok" : "text-ink-2")}>{signed(m.delta)}</span>
                      <Interval range={m.delta_ci} />
                    </div>
                  )}
                </td>
                <td className="py-2.5 pl-3"><VerdictCell member={m} /></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ByCategory({ comparison, colorOf }: { comparison: QuantComparison; colorOf: (key: string) => string }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="text-left text-xs text-ink-3">
            <th className="py-2 pr-3 font-medium">{t("Category")}</th>
            <th className="px-3 py-2 text-right font-medium">{t("Questions")}</th>
            {comparison.order.map((key) => (
              <th key={key} className="px-3 py-2 text-right font-medium">
                <span className="inline-flex items-center gap-1.5">
                  <span className="inline-block h-2 w-2 rounded-full" style={{ background: colorOf(key) }} aria-hidden />
                  <span className="font-mono">{name(comparison.models[key] as QuantMember)}</span>
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {comparison.categories.map((c) => (
            <tr key={c.id} className="border-t border-line">
              <td className="py-2 pr-3">{label(CATEGORY_LABEL, c.id)}</td>
              <td className="tabular px-3 py-2 text-right text-ink-3">{c.n}</td>
              {comparison.order.map((key) => {
                const cell = c.models[key];
                if (!cell) return <td key={key} className="px-3 py-2 text-right text-ink-3">–</td>;
                return (
                  <td key={key} className="tabular px-3 py-2 text-right" style={{ background: deltaTint(cell.delta) }}>
                    <span className="font-medium">{fmt(cell.score, 0)}</span>
                    {cell.delta !== null && <span className="ml-1.5 text-2xs text-ink-3">{signed(cell.delta, 0)}</span>}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Picker({ candidates, initial, onApply }: { candidates: QuantCandidate[]; initial: string[]; onApply: (keys: string[]) => void }) {
  const [keys, setKeys] = useState<string[]>(initial);
  const toggle = (key: string) => setKeys((now) => (now.includes(key) ? now.filter((k) => k !== key) : [...now, key]));
  return (
    <div className="grid gap-2 rounded-lg border border-line p-3">
      <p className="text-xs text-ink-3">{t("The Arena groups models by name, size and architecture. If yours are not grouped, or you want to compare other models of this challenge, choose them here.")}</p>
      <div className="grid gap-1.5 sm:grid-cols-2">
        {candidates.map((c) => {
          const on = keys.includes(c.key);
          return (
            <button
              key={c.key}
              type="button"
              role="checkbox"
              aria-checked={on}
              onClick={() => toggle(c.key)}
              className={cx("flex min-w-0 items-center gap-2.5 rounded-md border px-2.5 py-2 text-left transition-colors duration-150", on ? "border-line-strong bg-raised" : "border-line hover:border-line-strong")}
            >
              <span className={cx("grid h-4 w-4 shrink-0 place-content-center rounded-[4px] border", on ? "border-accent bg-accent text-accent-ink" : "border-line-strong bg-bg")} aria-hidden>
                {on && <Check size={11} strokeWidth={3} />}
              </span>
              <span className="min-w-0">
                <span className="block truncate text-sm font-medium">{c.label}</span>
                <span className="block truncate text-2xs text-ink-3">{[c.quant, c.params, c.size_bytes ? sizeLabel(c.size_bytes) : null].filter(Boolean).join(" · ")}</span>
              </span>
            </button>
          );
        })}
      </div>
      <div>
        <Button size="sm" variant="primary" disabled={keys.length < 2} onClick={() => onApply(keys)}>
          {t("Compare these")}
        </Button>
      </div>
    </div>
  );
}

function BottomLine({ comparison }: { comparison: QuantComparison }) {
  const reference = comparison.models[comparison.reference] as QuantMember;
  const line = bottomLine(comparison);
  const others = comparison.order.length - 1;
  const losses = line.losses.map((m) => `${name(m)} ${signed(m.delta)}`).join(" · ");
  if (others > 0 && line.losses.length === others) {
    return <Note tone="bad">{t("Every other quantization loses accuracy against {reference}, and the loss is real: {losses}.", { reference: name(reference), losses })}</Note>;
  }
  if (line.lightest && line.saved !== null) {
    const m = line.lightest;
    return (
      <Note tone={line.losses.length ? "warn" : "ok"}>
        <div className="grid gap-1">
          <span>
            {m.identical
              ? t("{quant} weighs {saved} less than {reference} and answered every question the same way.", { quant: name(m), saved: sizeLabel(line.saved), reference: name(reference) })
              : t("{quant} weighs {saved} less than {reference}, and its difference from it is not a real loss. This run could not have seen a loss smaller than {points} points.", {
                  quant: name(m),
                  saved: sizeLabel(line.saved),
                  reference: name(reference),
                  points: fmt(m.detectable ?? 0, 1),
                })}
          </span>
          {line.losses.length > 0 && <span>{t("Real loss: {losses}.", { losses })}</span>}
        </div>
      </Note>
    );
  }
  return <Note tone="accent">{t("None of the other quantizations is lighter than {reference} without a real loss.", { reference: name(reference) })}</Note>;
}

function Advice({ comparison }: { comparison: QuantComparison }) {
  const advice = repeatAdvice(comparison.settings);
  if (!advice) return null;
  return (
    <Note tone="accent">
      {advice === "greedy"
        ? t("The temperature is 0, so a repeat gives the same answer: the extra repeats cost time and add nothing. To average out chance, raise the temperature.")
        : t("Sampling at temperature {temperature} with only {n} repeat(s): the result depends on luck. Use 3 or more repeats to compare quantizations.", {
            temperature: fmt(comparison.settings.temperature ?? 0, 1),
            n: comparison.settings.repeats ?? 1,
          })}
    </Note>
  );
}

export function Quants({ report }: { report: Report }) {
  const runId = report.run.id;
  const [family, setFamily] = useState<string | null>(null);
  const [manual, setManual] = useState<string[] | null>(null);
  const [reference, setReference] = useState<string | null>(null);
  const [picking, setPicking] = useState(false);
  const [axis, setAxis] = useState<QuantAxis>("size");
  const manualKey = manual?.join(",") ?? "";
  const loaded = useLoad(
    () => api.get<QuantsResponse>(`/api/arena/runs/${runId}/quants${query({ family, models: manualKey, reference })}`),
    [runId, family, manualKey, reference, report.run.done],
  );

  const data = loaded.data;
  const comparison = data?.comparison ?? null;
  const colorOf = useMemo(() => {
    const positions = new Map(report.models.map((m) => [m.key, m.position]));
    return (key: string) => seriesVar(positions.get(key) ?? 0);
  }, [report.models]);

  if (loaded.loading && !data) return <div className="px-7 py-6"><SkeletonRows rows={4} height={52} /></div>;
  if (!data) return <div className="px-7 py-6"><Note tone="bad">{loaded.error ?? t("Something went wrong")}</Note></div>;

  const axisInfo = AXES[axis];
  const dots: QuantDot[] = comparison
    ? comparison.order.map((key) => {
        const m = comparison.models[key] as QuantMember;
        return { key, label: name(m), x: quantAxisValue(m, axis), y: m.score, low: m.ci?.[0] ?? null, high: m.ci?.[1] ?? null, color: colorOf(key), reference: m.is_reference };
      })
    : [];
  const choose = (next: string | null) => {
    setFamily(next);
    setManual(null);
    setReference(null);
    setPicking(false);
  };
  const local = data.candidates;
  const cannotGroup = data.families.length === 0 && !manual;

  return (
    <div className="grid gap-7 px-7 py-6">
      <div className="flex flex-wrap items-end gap-4">
        {data.families.length > 1 && !manual && (
          <label className="grid gap-1 text-xs text-ink-3">
            {t("Model")}
            <Select value={family ?? data.families[0]?.id ?? ""} onChange={(e) => choose(e.target.value)} className="min-w-[240px]">
              {data.families.map((f) => (
                <option key={f.id} value={f.id}>{f.label}</option>
              ))}
            </Select>
          </label>
        )}
        {comparison && (
          <label className="grid gap-1 text-xs text-ink-3">
            {t("Compare with")}
            <Select value={comparison.reference} onChange={(e) => setReference(e.target.value)} className="min-w-[200px]">
              {data.members.map((key) => {
                const m = comparison.models[key];
                return <option key={key} value={key}>{m ? `${name(m)} · ${m.label}` : key}</option>;
              })}
            </Select>
          </label>
        )}
        {local.length >= 2 && (
          <Button size="sm" onClick={() => setPicking(!picking)} aria-expanded={picking}>
            {t("Choose the models by hand")}
          </Button>
        )}
        {manual && (
          <Button size="sm" onClick={() => choose(null)}>
            {t("Back to the grouping")}
          </Button>
        )}
      </div>

      {(picking || (cannotGroup && local.length >= 2)) && (
        <Picker
          candidates={local}
          initial={data.members}
          onApply={(keys) => {
            setManual(keys);
            setFamily(null);
            setReference(null);
            setPicking(false);
          }}
        />
      )}

      {cannotGroup && local.length < 2 && (
        <Note tone="accent">
          {t("This tab compares quantizations of one model. Start a challenge with two or more of them (for example the IQ3, Q3_K and Q4 files of the same model) and they appear here, grouped.")}
        </Note>
      )}
      {data.problem === "needs_two" && !cannotGroup && <Note tone="warn">{t("Choose at least two models to compare.")}</Note>}
      {data.problem === "no_common" && <Note tone="warn">{t("These models were not scored on any question in common, so there is nothing to compare yet.")}</Note>}

      {comparison && (
        <>
          <BottomLine comparison={comparison} />

          <Section
            title={t("One by one against {reference}", { reference: name(comparison.models[comparison.reference] as QuantMember) })}
            aside={t("{n} questions in common{dropped}", { n: comparison.questions, dropped: comparison.dropped ? t(", {n} left out because one of them was not scored", { n: comparison.dropped }) : "" })}
          >
            <Table comparison={comparison} colorOf={colorOf} />
            {!comparison.speed_comparable && (
              <div className="mt-3">
                <Note tone="warn">{t("At least one of these models did not fit in graphics memory: the speeds are not a fair comparison between quantizations.")}</Note>
              </div>
            )}
          </Section>

          <Section
            title={t("Accuracy against weight")}
            aside={
              <span className="inline-flex gap-1" role="group" aria-label={t("What the horizontal axis shows")}>
                {AXIS_ORDER.map((id) => (
                  <button
                    key={id}
                    type="button"
                    aria-pressed={axis === id}
                    onClick={() => setAxis(id)}
                    className={cx("h-6 rounded-md border px-2 text-2xs transition-colors duration-150", axis === id ? "soft-accent border-accent font-medium text-ink" : "border-line text-ink-2 hover:border-line-strong")}
                  >
                    {AXES[id].label()}
                  </button>
                ))}
              </span>
            }
          >
            <div className="max-w-[640px]">
              <QuantScatter dots={dots} xLabel={axisInfo.label()} xFormat={axisInfo.format} />
            </div>
            <p className="mt-1 text-2xs text-ink-3">{t("Each point is the score with its 95% interval. The dashed line joins the quantizations from the lightest to the heaviest.")}</p>
          </Section>

          <Section title={t("Category by category")} aside={t("difference from the reference in points")}>
            <ByCategory comparison={comparison} colorOf={colorOf} />
            <p className="mt-2 text-2xs text-ink-3">{t("A category holds few questions: read these figures as a hint about where a quantization loses, not as a verdict.")}</p>
          </Section>

          <Advice comparison={comparison} />

          <Collapsible title={t("How to read this: real or noise")}>
            <div className="grid max-w-[760px] gap-2 pt-2 text-sm text-ink-2">
              <p>{t("Every quantization answered the same questions with the same seed and the same sampling, so they are compared question by question: how many each one got right that the other missed.")}</p>
              <p>{t("The difference is the change in score against the reference, each category weighing the same. Its interval comes from {draws} resamplings of the questions, with a fixed seed: the same results always give the same interval.", { draws: comparison.draws })}</p>
              <p>{t("A difference is called real only if its interval leaves out zero and the sign test, corrected for the number of comparisons, gives less than {alpha}. Everything else is noise: not a proof that the two are equal, only that this run cannot tell them apart.", { alpha: fmt(comparison.alpha, 2) })}</p>
              <p>{t("«Under N points it cannot be seen» is the smallest gap this run could reliably detect. To see smaller ones, ask more questions (the Complete pack) or average more repeats at a temperature above 0.")}</p>
            </div>
          </Collapsible>
        </>
      )}
    </div>
  );
}
