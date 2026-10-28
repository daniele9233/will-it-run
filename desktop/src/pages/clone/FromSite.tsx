import { Search } from "lucide-react";
import { useEffect, useState } from "react";

import { Button, EmptyState, Note, TextInput, cx } from "../../components/ui";
import { api } from "../../lib/api";
import type { CloneDetail, CloneInfo } from "../../lib/cloneSite";
import type { DnaView } from "../../lib/dna";
import { errorMessage, useInterval, useLoad } from "../../lib/hooks";
import { t } from "../../lib/i18n";
import { studyFailure, type PromptSummary } from "../../lib/masterPrompts";
import { useApp } from "../../lib/store";
import { ComposePanel } from "./ComposePanel";
import { DnaCard } from "./DnaCard";

/** The sites studied so far, the latest first. */
const studied = (clones: CloneInfo[]) => clones.filter((c) => c.mode === "study");

/**
 * "From a site": study an address (the page is opened and measured, no model), see what it measured, and get the master prompts that fit it composed into one.
 * `initialStudy` is the study the person came from.
 */
export function FromSite({ initialStudy, prompts }: { initialStudy: string | null; prompts: PromptSummary[] }) {
  const toast = useApp((s) => s.toast);
  const list = useLoad(() => api.get<{ clones: CloneInfo[] }>("/api/clone"));
  const [address, setAddress] = useState("");
  const [busy, setBusy] = useState(false);
  const [chosen, setChosen] = useState<string | null>(initialStudy);
  const clones = list.data?.clones ?? [];
  const sites = studied(clones);
  const running = sites.some((c) => c.active);
  useInterval(list.reload, running ? 2500 : null);

  const current = sites.find((c) => c.id === chosen) ?? null;
  const dna = useLoad<DnaView | null>(() => (current && !current.active ? api.get<DnaView>(`/api/clone/${current.id}/dna`) : Promise.resolve(null)), [current?.id, current?.active]);
  // A study that ended without its DNA: the reason the site (or the run) gave, not a vague "not finished".
  const stopped = current !== null && !current.active && current.status !== "completed";
  const detail = useLoad<CloneDetail | null>(() => (current && stopped ? api.get<CloneDetail>(`/api/clone/${current.id}`) : Promise.resolve(null)), [current?.id, stopped]);
  const failure = stopped ? studyFailure(detail.data?.runs[detail.data.runs.length - 1] ?? null) : null;
  // The latest study opens by itself when none was asked for.
  const latest = sites[0]?.id ?? null;
  useEffect(() => {
    if (chosen === null && latest !== null) setChosen(latest);
  }, [chosen, latest]);

  const study = () => {
    setBusy(true);
    api
      .post<{ study: string }>("/api/clone/study", { url: address })
      .then((started) => {
        setChosen(started.study);
        list.reload();
      })
      .catch((err: unknown) => toast({ tone: "bad", title: t("The site could not be studied"), body: errorMessage(err) }))
      .finally(() => setBusy(false));
  };

  return (
    <section className="clone-card grid gap-4 p-4" aria-label={t("From a site")}>
      <div className="grid gap-1">
        <div className="clone-label">{t("From a site")}</div>
        <p className="text-xs text-ink-2">{t("Give an address: the page is opened and measured, no model is used, and the prompts that fit it are put together with what was measured.")}</p>
      </div>
      <form
        className="flex flex-wrap items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (address.trim()) study();
        }}
      >
        <TextInput value={address} onChange={(e) => setAddress(e.target.value)} placeholder="https://" aria-label={t("Address of the site")} className="min-w-64 flex-1" />
        <Button type="submit" variant="primary" icon={<Search size={13} aria-hidden />} loading={busy} disabled={!address.trim()}>{t("Study")}</Button>
      </form>
      <p className="text-2xs text-ink-3">{t("Meant for sites you own (a migration, lost source code) and for learning how a site is built. Check the site's terms first; its text, logos and brand belong to its owner. Not legal advice.")}</p>
      {list.error && <Note tone="bad">{list.error}</Note>}
      {sites.length > 0 && (
        <div className="flex flex-wrap gap-2" role="list" aria-label={t("Studied sites")}>
          {sites.map((c) => (
            <button
              key={c.id}
              type="button"
              role="listitem"
              onClick={() => setChosen(c.id)}
              className={cx("rounded-full border px-3 py-1 text-xs transition-colors duration-150", c.id === chosen ? "border-accent text-ink" : "border-line text-ink-3 hover:text-ink")}
            >
              {c.host ?? c.title}
              {c.active ? ` · ${t("Studying...")}` : ""}
            </button>
          ))}
        </div>
      )}
      {list.data && sites.length === 0 && <EmptyState title={t("No site studied yet")}>{t("Study an address to see its colours, fonts and motion, and the prompts that fit.")}</EmptyState>}
      {current?.active && <Note tone="accent">{t("Studying {host}: the page is being opened and measured.", { host: current.host ?? current.title })}</Note>}
      {failure !== null && <Note tone="bad">{t("The study of {host} did not finish. {reason}", { host: current?.host ?? current?.title ?? "", reason: failure })}</Note>}
      {current && !current.active && !stopped && dna.error && <Note tone="warn">{t("There is no Site DNA to show. {reason}", { reason: dna.error })}</Note>}
      {current && dna.data && (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
          <DnaCard dna={dna.data} />
          <ComposePanel key={current.id} studyId={current.id} host={current.host ?? ""} clones={clones} prompts={prompts} />
        </div>
      )}
    </section>
  );
}
