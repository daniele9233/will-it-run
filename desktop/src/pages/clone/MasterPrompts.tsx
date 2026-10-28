import { Copy, X } from "lucide-react";
import { useEffect, useState } from "react";

import { Button, EmptyState, IconButton, Note, cx } from "../../components/ui";
import { api } from "../../lib/api";
import { useLoad } from "../../lib/hooks";
import { t } from "../../lib/i18n";
import { GROUPS, copyText, filterPrompts, proofLabel, type PromptDetail, type PromptGroup, type PromptSummary } from "../../lib/masterPrompts";
import { useApp } from "../../lib/store";
import { FromSite } from "./FromSite";

/** The text of one prompt, in a panel beside the list, with a button that copies it. */
function PromptDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const toast = useApp((s) => s.toast);
  const detail = useLoad(() => api.get<PromptDetail>(`/api/clone/prompts/${id}`), [id]);
  const data = detail.data;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const copy = async () => {
    if (!data) return;
    const ok = await copyText(data.text);
    toast(ok ? { tone: "ok", title: t("Prompt copied") } : { tone: "bad", title: t("The prompt could not be copied"), body: t("The clipboard refused it. Select the text and copy it by hand.") });
  };
  return (
    <aside className="fade-in fixed inset-y-0 right-0 z-[30] flex w-[min(560px,100vw)] flex-col border-l border-line-strong bg-panel" role="dialog" aria-label={t("Prompt")} style={{ boxShadow: "var(--shadow)" }}>
      <div className="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
        <div className="min-w-0">
          <div className="font-mono text-2xs text-ink-3">{id}</div>
          <h2 className="truncate text-sm font-semibold">{data?.title ?? ""}</h2>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="primary" icon={<Copy size={13} aria-hidden />} disabled={!data} onClick={() => void copy()}>{t("Copy")}</Button>
          <IconButton label={t("Close")} onClick={onClose}><X size={15} aria-hidden /></IconButton>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {detail.error && <Note tone="bad">{detail.error}</Note>}
        {data && <pre className="font-mono text-xs leading-relaxed whitespace-pre-wrap text-ink-2">{data.text}</pre>}
      </div>
    </aside>
  );
}

/**
 * The library of master prompts: group chips, a search box, one card for each prompt. `initialStudy` is the study the person came from
 * ("Master prompts from this site"); the "From a site" panel opens on it.
 */
export function MasterPrompts({ initialStudy = null }: { initialStudy?: string | null }) {
  const library = useLoad(() => api.get<{ version: string; prompts: PromptSummary[] }>("/api/clone/prompts"));
  const [group, setGroup] = useState<PromptGroup | "all">("all");
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const prompts = library.data?.prompts ?? [];
  const shown = filterPrompts(prompts, group, query);
  const chip = (active: boolean) => cx("rounded-full border px-3 py-1 text-xs transition-colors duration-150", active ? "border-accent text-ink" : "border-line text-ink-3 hover:text-ink");

  return (
    <div className="mx-auto grid w-full max-w-[1040px] gap-4" data-study={initialStudy ?? undefined}>
      <FromSite initialStudy={initialStudy} prompts={prompts} />
      <p className="text-xs text-ink-2">{t("Original prompts you copy into a coding agent to build one part of a site. Each says which skills it needs and whether it was tested.")}</p>
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className={chip(group === "all")} onClick={() => setGroup("all")}>{t("All")}</button>
        {GROUPS.map((g) => (
          <button key={g.id} type="button" className={chip(group === g.id)} onClick={() => setGroup(g.id)}>{g.label()}</button>
        ))}
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("Search the prompts")}
          aria-label={t("Search the prompts")}
          className="ml-auto h-8 w-56 rounded-md border border-line bg-bg px-3 text-sm outline-none focus:border-accent"
        />
      </div>
      {library.error && <Note tone="bad">{t("The prompts could not be loaded. {reason}", { reason: library.error })}</Note>}
      {library.data && shown.length === 0 && <EmptyState title={t("No prompt matches")}>{t("Try another word or another group.")}</EmptyState>}
      <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
        {shown.map((p) => (
          <button key={p.id} type="button" onClick={() => setOpen(p.id)} className="clone-card grid content-start gap-1.5 p-3 text-left transition-colors duration-150 hover:border-accent">
            <span className="flex items-center justify-between gap-2 font-mono text-2xs text-ink-3">
              <span>{p.id}</span>
              <span>{p.size}</span>
            </span>
            <span className="text-[13px] font-semibold text-ink">{p.title}</span>
            <span className="text-xs leading-relaxed text-ink-2">{p.summary}</span>
            <span className={cx("text-2xs", p.proof ? "text-ok" : "text-ink-3")}>{proofLabel(p.proof)}</span>
          </button>
        ))}
      </div>
      {open && <PromptDrawer key={open} id={open} onClose={() => setOpen(null)} />}
    </div>
  );
}
