import { ExternalLink } from "lucide-react";
import { useEffect, useRef } from "react";

import { AccuracyMeter, GLOSSARY, pplLabel } from "../components/Guide";
import { Button, FitBadge, Kbd, Note, PageHeader, SkeletonRows, cx } from "../components/ui";
import { api, openExternal } from "../lib/api";
import { useLoad } from "../lib/hooks";
import { t } from "../lib/i18n";
import { type PageId, useApp } from "../lib/store";
import type { FitCategory, QuantizationReference } from "../lib/types";

const STEPS: { title: string; text: string; action: string; page: PageId }[] = [
  {
    title: t("Will It Run reads your computer"),
    text: t("It measures the graphics card, the memory and the disks, then sets aside a margin so Windows keeps working while a model runs."),
    action: t("See your hardware"),
    page: "hardware",
  },
  {
    title: t("It works out what fits"),
    text: t("Every model file is checked against that budget: does it fit in graphics memory, how fast will it write, how much quality does its quantization keep."),
    action: t("Explore Models"),
    page: "models",
  },
  {
    title: t("You choose, it downloads safely"),
    text: t("Will It Run suggests the best file for this PC, and you can take a different one. You can pause or stop a download and choose where it is saved. Nothing starts if the disk would end up too full."),
    action: t("Open Downloads"),
    page: "downloads",
  },
  {
    title: t("You use the model"),
    text: t("Load it in LM Studio and chat there, or let Will It Run build a team of agents that work on a task together and show you every step."),
    action: t("Build AI Team"),
    page: "teams",
  },
];

const VERDICTS: [FitCategory, string][] = [
  ["PERFECT", t("Fits in graphics memory with room to spare and runs well above the target speed.")],
  ["EXCELLENT", t("Fits and runs at or above the target speed.")],
  ["GOOD", t("Works well. A little slower, or part of it runs from normal memory.")],
  ["ACCEPTABLE", t("Usable, but noticeably slow.")],
  ["SLOW", t("Runs, but you wait for every answer.")],
  ["MEMORY TIGHT", t("Fits with almost nothing left over. Close other apps first.")],
  ["NOT RECOMMENDED", t("Technically runs, too slowly to be useful.")],
  ["IMPOSSIBLE", t("Needs more memory than this PC can safely give.")],
];

const QUESTIONS: [string, string][] = [
  [t("Is anything sent to the Internet?"), t("Local models run entirely on this PC. Will It Run goes online to search Hugging Face, to download the files you ask for and, only if you add an API key, to call that cloud provider with what you send its model.")],
  [t("What does Clone a site do?"), t("You give the address of a website and choose a model, on this PC or in the cloud. It runs the ai-site-cloner kit the way it runs in a coding agent: the agent opens the site in your Chrome or Edge, asks you which pages to clone (only the home page, two pages, three…), measures them, downloads what it needs and rebuilds them as a Next.js project in a folder or a GitHub repository you choose. It is meant for sites you own, for migrations and for learning.")],
  [t("Can I use Claude, ChatGPT, DeepSeek or Gemini?"), t("Yes, with your own API key, in Settings > Cloud models. The key is encrypted for your Windows account. Those models run on the provider's servers and are paid on your account; they can be chosen in Clone a site, in Chat and in any workflow.")],
  [t("Why not simply the biggest model?"), t("A model that does not fit in graphics memory runs many times slower. A smaller model that fits is usually the better tool.")],
  [t("Which quantization should I take?"), t("The one Will It Run marks as recommended: the highest quality that still fits your graphics memory comfortably. The Files tab of every model shows the score and the accuracy of each alternative.")],
  [t("Can I trust the speed figures?"), t("Estimates are calculated from your hardware and can be off. Run a benchmark once and Will It Run replaces the estimate with the speed it measured on this PC.")],
];

function Heading({ id, children }: { id: string; children: string }) {
  return (
    <h2 id={id} className="mb-3 scroll-mt-6 text-lg font-semibold tracking-tight">
      {children}
    </h2>
  );
}

function QuantizationTable() {
  const data = useLoad(() => api.get<QuantizationReference>("/api/quantization"));
  if (data.error) return <Note tone="bad">{data.error}</Note>;
  if (!data.data) return <SkeletonRows rows={6} height={30} />;
  const { table, reference } = data.data;
  return (
    <>
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-line-strong text-left text-xs text-ink-3">
            <th className="py-2 pr-4 font-medium">{t("Quantization")}</th>
            <th className="py-2 pr-4 text-right font-medium">{t("Bits per weight")}</th>
            <th className="py-2 pr-4 text-right font-medium">{t("File size vs original")}</th>
            <th className="py-2 pr-4 font-medium">{t("Quality kept")}</th>
            <th className="py-2 text-right font-medium">{t("Perplexity increase")}</th>
          </tr>
        </thead>
        <tbody className="tabular">
          {table.map((row) => (
            <tr key={row.quant} className="border-b border-line">
              <td className="py-2 pr-4 font-mono font-medium">{row.quant}</td>
              <td className="py-2 pr-4 text-right text-ink-2">{row.bits_per_weight?.toFixed(1) ?? "–"}</td>
              <td className="py-2 pr-4 text-right text-ink-2">{row.bits_per_weight ? `${Math.round((row.bits_per_weight / 16) * 100)}%` : "–"}</td>
              <td className="py-2 pr-4">
                <AccuracyMeter accuracy={row} />
              </td>
              <td className="py-2 text-right text-ink-2">{pplLabel(row)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-3 max-w-[72ch] text-xs text-ink-3">
        {t("Reference: {source}, measured on {model}.", { source: reference.source, model: reference.model })} {reference.caveat}{" "}
        {t("Will It Run did not measure these on your models.")}
      </p>
      <Button size="sm" className="mt-2.5" icon={<ExternalLink size={12} />} onClick={() => void openExternal(reference.url)}>
        {t("Open the source table")}
      </Button>
    </>
  );
}

export function GuidePage() {
  const navigate = useApp((s) => s.navigate);
  const term = useApp((s) => s.params.term);
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!term) return;
    const target = document.getElementById(term === "quant" || term === "accuracy" ? "guide-quantization" : `term-${term}`);
    target?.scrollIntoView({ block: "start" });
  }, [term]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader title={t("Guide")} subtitle={t("What Will It Run does and how to read it, in plain words.")} />
      <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto px-7 py-6">
        <div className="grid max-w-[880px] gap-10">
          <section>
            <Heading id="guide-how">{t("How it works")}</Heading>
            <ol className="grid gap-0 border-t border-line">
              {STEPS.map((step, index) => (
                <li key={step.title} className="grid grid-cols-[28px_minmax(0,1fr)_auto] items-start gap-x-4 border-b border-line py-3.5">
                  <span className="tabular mt-px text-lg leading-6 font-semibold text-ink-3">{index + 1}</span>
                  <div>
                    <div className="font-medium">{step.title}</div>
                    <p className="mt-0.5 max-w-[64ch] text-sm text-ink-2">{step.text}</p>
                  </div>
                  <Button size="sm" onClick={() => navigate(step.page)}>
                    {step.action}
                  </Button>
                </li>
              ))}
            </ol>
          </section>

          <section>
            <Heading id="guide-colors">{t("Reading the verdicts")}</Heading>
            <p className="mb-3 max-w-[70ch] text-sm text-ink-2">{t("Every model file gets one of eight verdicts for this PC. Green is the comfortable zone, blue works well, yellow and orange come with a cost, red does not run.")}</p>
            <ul className="border-t border-line">
              {VERDICTS.map(([category, text]) => (
                <li key={category} className="grid grid-cols-[168px_minmax(0,1fr)] items-baseline gap-4 border-b border-line py-2 text-sm">
                  <span>
                    <FitBadge category={category} />
                  </span>
                  <span className="text-ink-2">{text}</span>
                </li>
              ))}
            </ul>
          </section>

          <section>
            <Heading id="guide-quantization">{t("Quantization and accuracy")}</Heading>
            <div className="mb-4 grid max-w-[72ch] gap-2 text-sm text-ink-2">
              <p>{t("A model is published at full precision, then shrunk so it fits on ordinary hardware. Each step down makes the file smaller and the answers a little worse.")}</p>
              <p>{t("The table shows what each step costs. Perplexity is how surprised the model is by real text, so lower is better: an increase of +0.02 cannot be noticed in use, +0.2 is a small loss, above +1 the model is visibly worse.")}</p>
              <p>{t("Will It Run picks the highest quality that still fits your graphics memory comfortably, and tells you when it had to go lower.")}</p>
            </div>
            <QuantizationTable />
          </section>

          <section>
            <Heading id="guide-words">{t("Words you will see")}</Heading>
            <dl className="border-t border-line">
              {GLOSSARY.map((entry) => (
                <div key={entry.id} id={`term-${entry.id}`} className={cx("grid scroll-mt-6 grid-cols-[220px_minmax(0,1fr)] gap-4 border-b border-line py-2.5 text-sm", term === entry.id && "bg-surface")}>
                  <dt className="font-medium">{entry.term}</dt>
                  <dd className="max-w-[62ch] text-ink-2">{entry.text}</dd>
                </div>
              ))}
            </dl>
          </section>

          <section>
            <Heading id="guide-questions">{t("Good to know")}</Heading>
            <dl className="grid gap-4">
              {QUESTIONS.map(([question, answer]) => (
                <div key={question}>
                  <dt className="text-sm font-medium">{question}</dt>
                  <dd className="mt-0.5 max-w-[70ch] text-sm text-ink-2">{answer}</dd>
                </div>
              ))}
            </dl>
            <div className="mt-6 flex flex-wrap items-center gap-x-6 gap-y-2 text-sm text-ink-2">
              <span>
                <Kbd>Ctrl K</Kbd> {t("every command")}
              </span>
              <span>
                <Kbd>Ctrl +</Kbd> <Kbd>Ctrl −</Kbd> {t("zoom in and out")}
              </span>
              <span>
                <Kbd>Ctrl 0</Kbd> {t("normal size")}
              </span>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
