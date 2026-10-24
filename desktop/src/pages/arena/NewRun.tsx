import { Check, Eye, Play, Wrench } from "lucide-react";
import { useMemo, useState } from "react";

import { Badge, Button, Collapsible, Field, Note, Section, SkeletonRows, TextInput } from "../../components/ui";
import { api, ApiError } from "../../lib/api";
import {
  CATEGORY_LABEL,
  chosenQuantizations,
  DEFAULT_REQUEST,
  type ModelsResponse,
  type Overview,
  type Preset,
  PRESET_LABEL,
  perCategory,
  questionCount,
  type RunRequest,
  THINKING_MAX_TOKENS,
  label,
} from "../../lib/arena";
import { sizeLabel } from "../../lib/format";
import { errorMessage, useLoad } from "../../lib/hooks";
import { t } from "../../lib/i18n";
import { useApp } from "../../lib/store";

const PRESETS: Preset[] = ["quick", "standard", "full"];

function positive(text: string, fallback: number): number {
  const value = Number(text.replace(",", "."));
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

/** The box of a checkbox, drawn. It is a button only where it stands alone (see ``Checkbox``). */
function Box({ checked }: { checked: boolean }) {
  return (
    <span className={`grid h-4 w-4 shrink-0 place-content-center rounded-[4px] border transition-colors duration-150 ${checked ? "border-accent bg-accent text-accent-ink" : "border-line-strong bg-bg"}`} aria-hidden>
      {checked && <Check size={11} strokeWidth={3} />}
    </span>
  );
}

function Checkbox({ checked, onChange, label: text, disabled = false }: { checked: boolean; onChange: (value: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <button type="button" role="checkbox" aria-checked={checked} aria-label={text} disabled={disabled} onClick={() => onChange(!checked)} className="disabled:opacity-40">
      <Box checked={checked} />
    </button>
  );
}

/** A model to choose: the whole row is the checkbox, so there is nothing to click inside it. */
function ModelRow({ on, onToggle, label: text, children }: { on: boolean; onToggle: () => void; label: string; children: React.ReactNode }) {
  return (
    <div
      role="checkbox"
      aria-checked={on}
      aria-label={text}
      tabIndex={0}
      onClick={onToggle}
      onKeyDown={(e) => {
        if (e.key === " " || e.key === "Enter") {
          e.preventDefault();
          onToggle();
        }
      }}
      className={`flex min-w-0 cursor-pointer items-center gap-3 rounded-lg border px-3 py-2.5 text-left transition-colors duration-150 ${on ? "border-line-strong bg-raised" : "border-line hover:border-line-strong"}`}
    >
      <Box checked={on} />
      {children}
    </div>
  );
}

export function NewRun({ busy, onStarted }: { busy: string | null; onStarted: (id: string) => void }) {
  const toast = useApp((s) => s.toast);
  const navigate = useApp((s) => s.navigate);
  const overview = useLoad(() => api.get<Overview>("/api/arena/overview"));
  const models = useLoad(() => api.get<ModelsResponse>("/api/arena/models"));
  const [chosen, setChosen] = useState<string[]>([]);
  const [preset, setPreset] = useState<Preset>("standard");
  const [off, setOff] = useState<string[]>([]);
  const [seed, setSeed] = useState(String(DEFAULT_REQUEST.seed));
  const [temperature, setTemperature] = useState("0");
  const [repeats, setRepeats] = useState("1");
  const [maxTokens, setMaxTokens] = useState(String(DEFAULT_REQUEST.max_tokens));
  const [context, setContext] = useState("");
  const [penalty, setPenalty] = useState("1");
  const [topP, setTopP] = useState("");
  const [topK, setTopK] = useState("");
  const [timeout, setTimeoutS] = useState(String(DEFAULT_REQUEST.timeout_s));
  const [title, setTitle] = useState("");
  const [unloadOk, setUnloadOk] = useState(false);
  const [cloudOk, setCloudOk] = useState(false);
  const [starting, setStarting] = useState(false);

  const categories = useMemo(() => overview.data?.categories ?? [], [overview.data]);
  const allowed = useMemo(() => categories.filter((c) => !off.includes(c.id)), [categories, off]);
  const count = questionCount(categories, preset, off.length ? allowed.map((c) => c.id) : null);

  const local = models.data?.local ?? [];
  const cloud = models.data?.cloud ?? [];
  const picked = chosen.map((key) => local.find((m) => m.key === key) ?? cloud.find((m) => m.key === key)).filter((m) => m !== undefined);
  const pickedLocal = chosen.filter((key) => local.some((m) => m.key === key));
  const pickedCloud = chosen.filter((key) => cloud.some((m) => m.key === key));
  const loaded = models.data?.loaded ?? [];
  const blind = picked.filter((m) => !m.vision);
  const thinking = pickedLocal.some((key) => local.find((m) => m.key === key)?.reasoning);
  const quantized = chosenQuantizations(chosen, local);
  const prepared = Number(repeats) >= 3 && Number(temperature.replace(",", ".")) > 0;
  const visionIncluded = allowed.some((c) => c.needs.includes("vision") && perCategory(c, preset) > 0);

  const toggleModel = (key: string) => setChosen((now) => (now.includes(key) ? now.filter((k) => k !== key) : [...now, key]));
  const toggleCategory = (id: string) => setOff((now) => (now.includes(id) ? now.filter((c) => c !== id) : [...now, id]));

  const needUnload = pickedLocal.length > 0 && loaded.length > 0 && !unloadOk;
  const needCloud = pickedCloud.length > 0 && !cloudOk;
  const can = chosen.length > 0 && count > 0 && !busy && !needUnload && !needCloud;

  const start = async () => {
    const body: RunRequest = {
      ...DEFAULT_REQUEST,
      models: chosen,
      seed: Math.round(positive(seed, DEFAULT_REQUEST.seed)),
      preset,
      categories: off.length ? allowed.map((c) => c.id) : null,
      repeats: Math.round(positive(repeats, 1)),
      temperature: Number(temperature.replace(",", ".")) || 0,
      repeat_penalty: positive(penalty, 1),
      top_p: topP.trim() ? positive(topP, 1) : null,
      top_k: topK.trim() ? Math.round(positive(topK, 40)) : null,
      max_tokens: Math.round(positive(maxTokens, DEFAULT_REQUEST.max_tokens)),
      context: context.trim() ? Math.round(positive(context, 8192)) : null,
      timeout_s: Math.round(positive(timeout, DEFAULT_REQUEST.timeout_s)),
      title: title.trim(),
      confirm_unload: unloadOk,
      confirm_cloud: cloudOk,
    };
    setStarting(true);
    try {
      const reply = await api.post<{ id: string }>("/api/arena/runs", body);
      onStarted(reply.id);
    } catch (err) {
      // The core asks for what it needs before it will start: show it where the person can agree.
      if (err instanceof ApiError && err.data.code === "needs_unload") setUnloadOk(false);
      toast({ tone: "bad", title: t("The challenge did not start"), body: errorMessage(err) });
      void models.reload();
    } finally {
      setStarting(false);
    }
  };

  if (overview.loading && !overview.data) return <div className="p-7"><SkeletonRows rows={4} height={56} /></div>;
  if (!overview.data) return <div className="p-7"><Note tone="bad">{overview.error ?? t("Something went wrong")}</Note></div>;

  const tools = overview.data.tools;
  const toolRows: { id: string; name: string; fallback: string }[] = [
    { id: "terraform", name: "Terraform", fallback: t("without it the configurations are checked on their structure") },
    { id: "ansible_lint", name: "ansible-lint", fallback: t("without it the playbooks are checked on their YAML and their rules") },
    { id: "node", name: "Node", fallback: t("without it JavaScript code is not run") },
    { id: "python", name: "Python", fallback: t("without it Python code is not run") },
    { id: "psql", name: "PostgreSQL", fallback: t("without it SQL is run on SQLite after a translation") },
  ];

  return (
    <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)] overflow-hidden">
      <div className="min-h-0 overflow-y-auto border-r border-line px-7 py-6">
        <Section title={t("Who takes part")} aside={t("one at a time in memory")}>
          {models.loading && !models.data && <SkeletonRows rows={4} height={44} />}
          {models.error && <Note tone="bad">{models.error}</Note>}
          {models.data && (
            <div className="grid min-w-0 gap-5">
              <div className="grid min-w-0 gap-1.5">
                <div className="text-2xs font-semibold tracking-wider text-ink-3 uppercase">{t("On this PC")}</div>
                {local.length === 0 && <p className="text-sm text-ink-3">{t("No model is installed in LM Studio yet.")}</p>}
                {local.map((m) => (
                  <ModelRow key={m.key} on={chosen.includes(m.key)} onToggle={() => toggleModel(m.key)} label={m.label}>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium">{m.label}</div>
                      <div className="truncate text-xs text-ink-3">{[m.params, m.quant, m.size_bytes ? sizeLabel(m.size_bytes) : null].filter(Boolean).join(" · ")}</div>
                    </div>
                    <div className="flex shrink-0 items-center gap-1.5">
                      {m.loaded && <Badge tone="warn" title={t("LM Studio holds it in memory right now")}>{t("loaded")}</Badge>}
                      {m.reasoning && <Badge tone="accent" title={t("It thinks before it answers: give it more tokens")}>{t("thinks")}</Badge>}
                      {m.vision && <Badge tone="accent"><Eye size={11} aria-hidden /> {t("vision")}</Badge>}
                      {m.tool_use && <Badge tone="off"><Wrench size={11} aria-hidden /> {t("tools")}</Badge>}
                    </div>
                  </ModelRow>
                ))}
              </div>
              <div className="grid gap-1.5">
                <div className="text-2xs font-semibold tracking-wider text-ink-3 uppercase">{t("Cloud · paid with your key")}</div>
                {cloud.length === 0 ? (
                  <div className="rounded-lg border border-dashed border-line px-3 py-2.5 text-sm text-ink-3">
                    {t("Claude, ChatGPT, DeepSeek and Gemini turn on in")}{" "}
                    <button type="button" className="text-accent underline-offset-2 hover:underline" onClick={() => navigate("settings", { tab: "cloud" })}>
                      {t("Settings › Cloud models")}
                    </button>
                    .
                  </div>
                ) : (
                  cloud.map((m) => (
                    <ModelRow key={m.key} on={chosen.includes(m.key)} onToggle={() => toggleModel(m.key)} label={m.label}>
                      <div className="min-w-0 flex-1 truncate text-sm font-medium">{m.label}</div>
                      {m.vision && <Badge tone="accent"><Eye size={11} aria-hidden /> {t("vision")}</Badge>}
                      <Badge tone="limited">{t("costs money")}</Badge>
                    </ModelRow>
                  ))
                )}
              </div>

              {pickedLocal.length > 0 && loaded.length > 0 && (
                <Note tone="warn">
                  <div className="grid gap-2">
                    <span>
                      {t("LM Studio already holds {names}. It may be in use by another program: to measure one model at a time the Arena unloads what is loaded.", {
                        names: loaded.map((l) => l.model_key).join(", "),
                      })}
                    </span>
                    <span className="flex items-center gap-2">
                      <Checkbox checked={unloadOk} onChange={setUnloadOk} label={t("Unload the models that are loaded")} />
                      <button type="button" className="text-left text-ink" onClick={() => setUnloadOk(!unloadOk)}>
                        {t("Unload the models that are loaded")}
                      </button>
                    </span>
                  </div>
                </Note>
              )}
              {pickedCloud.length > 0 && (
                <Note tone="warn">
                  <div className="grid gap-2">
                    <span>{t("The questions are sent to the provider of each cloud model and every answer is paid for with your key.")}</span>
                    <span className="flex items-center gap-2">
                      <Checkbox checked={cloudOk} onChange={setCloudOk} label={t("I agree to use cloud models")} />
                      <button type="button" className="text-left text-ink" onClick={() => setCloudOk(!cloudOk)}>
                        {t("I agree to use cloud models")}
                      </button>
                    </span>
                  </div>
                </Note>
              )}
              {quantized && !prepared && (
                <Note
                  tone="accent"
                  action={
                    <Button
                      size="sm"
                      onClick={() => {
                        setRepeats("3");
                        setTemperature("0.5");
                      }}
                    >
                      {t("Use 3 repeats, temperature 0.5")}
                    </Button>
                  }
                >
                  {t("{n} of the chosen models are quantizations of {model}. To tell a real difference from noise, each is asked the same questions several times: 3 repeats at a temperature above 0 give the Quantizations tab something to measure.", { n: quantized.keys.length, model: quantized.label })}
                </Note>
              )}
              {blind.length > 0 && visionIncluded && (
                <Note tone="accent">
                  {t("{names} cannot see pictures: the image questions are skipped for it, and a skipped question is not a wrong answer.", { names: blind.map((m) => m.label).join(", ") })}
                </Note>
              )}
            </div>
          )}
        </Section>
      </div>

      <div className="min-h-0 overflow-y-auto px-7 py-6">
        <div className="grid gap-7">
          <Section title={t("How long")} aside={t("the same questions for everyone")}>
            <div className="grid grid-cols-3 gap-2.5">
              {PRESETS.map((p) => (
                <button
                  key={p}
                  type="button"
                  aria-pressed={preset === p}
                  onClick={() => setPreset(p)}
                  className={`rounded-lg border px-3.5 py-3 text-left transition-colors duration-150 ${preset === p ? "soft-accent border-accent" : "border-line hover:border-line-strong"}`}
                >
                  <div className="text-sm font-semibold">{PRESET_LABEL[p]}</div>
                  <div className="tabular mt-1 text-2xl font-semibold tracking-tight">{overview.data?.presets[p]}</div>
                  <div className="text-xs text-ink-3">{t("questions for each model")}</div>
                </button>
              ))}
            </div>
          </Section>

          <Section title={t("Categories")} aside={t("{n} of {total}", { n: allowed.length, total: categories.length })}>
            <div className="flex flex-wrap gap-1.5">
              {categories.map((c) => {
                const empty = perCategory(c, preset) === 0;
                const on = !off.includes(c.id) && !empty;
                return (
                  <button
                    key={c.id}
                    type="button"
                    disabled={empty}
                    aria-pressed={on}
                    title={empty ? t("The quick pack does not ask this category.") : c.scored ? undefined : t("Never scored: the answers are kept to be read side by side.")}
                    onClick={() => toggleCategory(c.id)}
                    className={`inline-flex h-7 items-center gap-1.5 rounded-md border px-2.5 text-xs transition-colors duration-150 disabled:opacity-40 ${on ? "soft-accent border-accent font-medium text-ink" : "border-line text-ink-2 hover:border-line-strong"}`}
                  >
                    {on && <Check size={11} aria-hidden />}
                    {label(CATEGORY_LABEL, c.id)}
                    <span className="tabular text-ink-3">{perCategory(c, preset)}</span>
                  </button>
                );
              })}
            </div>
          </Section>

          <Section title={t("Settings")} aside={t("all of it is kept in the report")}>
            <div className="grid grid-cols-3 items-start gap-3">
              <Field label="Seed" hint={t("42 is the reference challenge")}>
                <TextInput inputMode="numeric" value={seed} onChange={(e) => setSeed(e.target.value)} />
              </Field>
              <Field label={t("Temperature")} hint={t("0 gives the same answer every time")}>
                <TextInput inputMode="decimal" value={temperature} onChange={(e) => setTemperature(e.target.value)} />
              </Field>
              <Field label={t("Repeats")} hint={t("each repeat adds to the seed")}>
                <TextInput inputMode="numeric" value={repeats} onChange={(e) => setRepeats(e.target.value)} />
              </Field>
              <Field label={t("Max tokens per answer")}>
                <TextInput inputMode="numeric" value={maxTokens} onChange={(e) => setMaxTokens(e.target.value)} />
              </Field>
              <Field label={t("Context")} hint={t("empty: chosen by the pack")}>
                <TextInput inputMode="numeric" value={context} placeholder="8192" onChange={(e) => setContext(e.target.value)} />
              </Field>
              <Field label={t("Repeat penalty")}>
                <TextInput inputMode="decimal" value={penalty} onChange={(e) => setPenalty(e.target.value)} />
              </Field>
            </div>
            {thinking && Number(maxTokens) < THINKING_MAX_TOKENS && (
              <div className="mt-3">
                <Note tone="warn" action={<Button size="sm" onClick={() => setMaxTokens(String(THINKING_MAX_TOKENS))}>{t("Use {n}", { n: THINKING_MAX_TOKENS })}</Button>}>
                  {t("A model that thinks before it answers can run out of tokens before it writes the answer, and then the answer counts as cut off.")}
                </Note>
              </div>
            )}
            <div className="mt-2">
              <Collapsible title={t("More settings")}>
                <div className="grid grid-cols-3 items-start gap-3 pt-1">
                  <Field label="Top-p">
                    <TextInput inputMode="decimal" value={topP} placeholder="–" onChange={(e) => setTopP(e.target.value)} />
                  </Field>
                  <Field label="Top-k">
                    <TextInput inputMode="numeric" value={topK} placeholder="–" onChange={(e) => setTopK(e.target.value)} />
                  </Field>
                  <Field label={t("Time per answer (s)")}>
                    <TextInput inputMode="numeric" value={timeout} onChange={(e) => setTimeoutS(e.target.value)} />
                  </Field>
                  <div className="col-span-3">
                    <Field label={t("Name of the challenge")} hint={t("empty: the names of the models")}>
                      <TextInput value={title} onChange={(e) => setTitle(e.target.value)} />
                    </Field>
                  </div>
                </div>
              </Collapsible>
            </div>
          </Section>

          <Section title={t("What checks the answers")} aside={t("found on this PC")}>
            <div className="flex flex-wrap gap-1.5">
              {toolRows.map((row) => {
                const info = tools[row.id];
                return info?.available ? (
                  <span key={row.id} className="inline-flex h-7 items-center gap-1.5 rounded-md border border-line px-2.5 text-xs text-ink-2">
                    <Check size={11} className="text-ok" aria-hidden /> {row.name} {info.version ?? ""}
                  </span>
                ) : (
                  <span key={row.id} className="inline-flex h-7 items-center rounded-md border border-dashed border-line px-2.5 text-xs text-ink-3" title={row.fallback}>
                    {row.name} · {row.fallback}
                  </span>
                );
              })}
            </div>
          </Section>

          <div className="flex items-center gap-4 border-t border-line pt-5">
            <Button variant="primary" icon={<Play size={13} />} loading={starting} disabled={!can} onClick={() => void start()}>
              {t("Start the challenge")}
            </Button>
            <p className="text-xs text-ink-3">
              {chosen.length === 0
                ? t("Choose at least one model.")
                : busy
                  ? t("Another challenge is working: stop it or wait for it.")
                  : needUnload
                    ? t("Agree to unload the models that are loaded.")
                    : needCloud
                      ? t("Agree to use the cloud models.")
                      : t("{models} models · {questions} questions each · {answers} answers", { models: chosen.length, questions: count, answers: chosen.length * count * Math.round(positive(repeats, 1)) })}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
