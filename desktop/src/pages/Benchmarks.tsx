import { Play } from "lucide-react";
import { useEffect, useState } from "react";

import { Button, EmptyState, Field, FitBadge, Note, PageHeader, ProgressBar, Section, Select, SkeletonRows } from "../components/ui";
import { api } from "../lib/api";
import { gbFromMb, timeAgo, tps } from "../lib/format";
import { errorMessage, useBus, useLoad } from "../lib/hooks";
import { useApp } from "../lib/store";
import type { Benchmark, InstalledModel, ModelCard } from "../lib/types";
import { t } from "../lib/i18n";

const STAGES: Record<string, string> = {
  unloading: t("Unloading other models"),
  loading: t("Loading the model"),
  warmup: t("Warming up"),
  generating: t("Generating and measuring"),
  done: t("Done"),
};

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div>
      <div className="text-xs text-ink-3">{label}</div>
      <div className="tabular mt-0.5 text-lg font-semibold tracking-tight">{value}</div>
      {note && <div className="text-2xs text-ink-3">{note}</div>}
    </div>
  );
}

export function BenchmarksPage() {
  const routeParams = useApp((s) => s.params);
  const navigate = useApp((s) => s.navigate);
  const toast = useApp((s) => s.toast);
  const connected = useApp((s) => s.live?.lmstudio_connected ?? s.lmstudio?.api_reachable ?? false);
  const installed = useLoad(() => api.get<{ models: InstalledModel[]; cards: ModelCard[] }>("/api/models/installed"));
  const history = useLoad(() => api.get<{ benchmarks: Benchmark[] }>("/api/benchmarks"));
  const [model, setModel] = useState(routeParams.model ?? "");
  const [context, setContext] = useState(8192);
  const [stage, setStage] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  const llms = (installed.data?.models ?? []).filter((m) => m.type === "llm");
  useEffect(() => {
    if (!model && llms[0]) setModel(llms[0].model_key);
  }, [llms, model]);

  useBus("benchmark.progress", (event) => {
    const next = String(event.data.stage);
    if (next === "failed") {
      setStage(null);
      setFailure(String(event.data.error ?? t("Benchmark failed")));
    } else if (next === "done") {
      setStage(null);
      history.reload();
    } else {
      setStage(next);
    }
  });

  const run = () => {
    setFailure(null);
    setStage("loading");
    api.post("/api/benchmarks", { model_key: model, context_length: context }).catch((err: unknown) => {
      setStage(null);
      toast({ tone: "bad", title: t("Benchmark not started"), body: errorMessage(err) });
    });
  };

  const benchmarks = history.data?.benchmarks ?? [];
  const latest = benchmarks.find((b) => b.model_key === model) ?? null;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader title={t("Benchmarks")} subtitle={t("Measure a model on this PC. Measured results replace estimates everywhere in Will It Run.")} />
      <div className="min-h-0 flex-1 overflow-y-auto px-7 py-6">
        <div className="grid max-w-[980px] gap-9">
          <Note tone="accent" action={<Button size="sm" onClick={() => navigate("arena")}>{t("Open the Arena")}</Button>}>
            {t("A benchmark measures how fast a model runs on this PC. The Arena measures how far its answers can be trusted.")}
          </Note>
          {installed.loading && !installed.data && <SkeletonRows rows={2} height={48} />}
          {installed.data && llms.length === 0 && (
            <EmptyState title={t("No installed models to benchmark")} action={<Button variant="primary" onClick={() => navigate("models")}>{t("Explore Models")}</Button>}>
              {t("Benchmarks run on models installed in LM Studio. Install one first.")}
            </EmptyState>
          )}
          {llms.length > 0 && (
            <Section title={t("Run a benchmark")}>
              <div className="grid grid-cols-[minmax(0,1fr)_150px_auto] items-end gap-3">
                <Field label={t("Model")}>
                  <Select value={model} onChange={(e) => setModel(e.target.value)} disabled={stage !== null}>
                    {llms.map((m) => (
                      <option key={m.model_key} value={m.model_key}>
                        {m.display_name} {m.quant ? `· ${m.quant}` : ""}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label={t("Context length")}>
                  <Select value={context} onChange={(e) => setContext(Number(e.target.value))} disabled={stage !== null}>
                    {[4096, 8192, 16384, 32768].map((c) => (
                      <option key={c} value={c}>
                        {t("{n} tokens", { n: c.toLocaleString() })}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Button variant="primary" icon={<Play size={13} />} loading={stage !== null} disabled={!model || !connected} onClick={run}>
                  {t("Benchmark")}
                </Button>
              </div>
              {!connected && <div className="mt-3"><Note tone="warn">{t("LM Studio's local server is not running, so models cannot be loaded.")}</Note></div>}
              {stage && (
                <div className="mt-4 grid gap-2">
                  <ProgressBar indeterminate />
                  <p className="text-sm text-ink-2">{STAGES[stage] ?? stage}. {t("Other loaded models are unloaded first so the memory reading is clean.")}</p>
                </div>
              )}
              {failure && <div className="mt-3"><Note tone="bad">{failure}</Note></div>}
            </Section>
          )}

          {latest && (
            <Section title={t("{model} on your PC", { model: latest.display_name ?? latest.model_key })} aside={`${timeAgo(latest.created_at)} · ${latest.quant ?? ""} · ${t("{n} context", { n: latest.context_length?.toLocaleString() ?? "?" })}`}>
              <div className="grid grid-cols-[repeat(auto-fit,minmax(130px,1fr))] gap-x-8 gap-y-5 border-y border-line py-5">
                <Stat label={t("Generation")} value={tps(latest.generation_tps)} />
                <Stat label={t("Prompt")} value={latest.prompt_tps ? `${Math.round(latest.prompt_tps)} tok/s` : t("Not measured")} note={t("approximate")} />
                <Stat label={t("Time to first token")} value={latest.ttft_ms != null ? `${Math.round(latest.ttft_ms)} ms` : t("Not measured")} />
                <Stat label={t("Load time")} value={latest.load_time_s != null ? `${latest.load_time_s.toFixed(1)} s` : t("Not measured")} />
                <Stat label="VRAM" value={latest.vram_used_mb != null ? gbFromMb(latest.vram_used_mb) : t("Not measured")} />
                <Stat label="RAM" value={latest.ram_used_mb != null ? gbFromMb(latest.ram_used_mb) : t("Not measured")} note={t("system-wide change")} />
                <Stat label={t("GPU utilization")} value={latest.gpu_util_percent != null ? `${latest.gpu_util_percent}%` : t("Not measured")} note={t("peak")} />
              </div>
              <div className="mt-4 flex items-center gap-4">
                {latest.agentfit_score != null && (
                  <div className="flex items-baseline gap-2">
                    <span className="text-sm text-ink-2">{t("Will It Run score")}</span>
                    <span className="tabular text-xl font-semibold">{latest.agentfit_score.toFixed(1)}</span>
                    <span className="text-sm text-ink-3">/ 10</span>
                  </div>
                )}
                {latest.fit && <FitBadge category={latest.fit} solid />}
                <span className="text-xs text-ok">{t("Measured · HIGH confidence")}</span>
              </div>
              {latest.notes.length > 0 && (
                <ul className="mt-3 grid gap-0.5 text-xs text-ink-3">
                  {latest.notes.map((note) => (
                    <li key={note}>{note}</li>
                  ))}
                </ul>
              )}
            </Section>
          )}

          {benchmarks.length > 0 && (
            <Section title={t("History")}>
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-line text-left text-xs text-ink-3">
                    {[t("Model"), t("Quant"), t("Generation"), t("First token"), "VRAM", t("Load"), t("Score"), t("When")].map((h) => (
                      <th key={h} className="py-2 pr-4 font-medium">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {benchmarks.map((b) => (
                    <tr key={b.id} className="tabular border-b border-line">
                      <td className="max-w-[240px] truncate py-2 pr-4">{b.display_name ?? b.model_key}</td>
                      <td className="py-2 pr-4 font-mono text-xs text-ink-2">{b.quant ?? "–"}</td>
                      <td className="py-2 pr-4 font-medium">{tps(b.generation_tps)}</td>
                      <td className="py-2 pr-4">{b.ttft_ms != null ? `${Math.round(b.ttft_ms)} ms` : "–"}</td>
                      <td className="py-2 pr-4">{b.vram_used_mb != null ? gbFromMb(b.vram_used_mb) : "–"}</td>
                      <td className="py-2 pr-4">{b.load_time_s != null ? `${b.load_time_s.toFixed(1)} s` : "–"}</td>
                      <td className="py-2 pr-4">{b.agentfit_score != null ? b.agentfit_score.toFixed(1) : "–"}</td>
                      <td className="py-2 text-ink-3">{timeAgo(b.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Section>
          )}
          {history.data && benchmarks.length === 0 && llms.length > 0 && !stage && (
            <p className="text-sm text-ink-3">{t("No benchmarks yet. Until a model is measured, Will It Run shows estimated speed with its confidence level.")}</p>
          )}
        </div>
      </div>
    </div>
  );
}
