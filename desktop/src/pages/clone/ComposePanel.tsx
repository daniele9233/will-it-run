import { Copy } from "lucide-react";
import { useEffect, useState } from "react";

import { Button, Note, Select } from "../../components/ui";
import { api } from "../../lib/api";
import type { CloneInfo } from "../../lib/cloneSite";
import { errorMessage } from "../../lib/hooks";
import { t } from "../../lib/i18n";
import { copyText, togglePick, tokenLabel, type Composed, type PromptSummary } from "../../lib/masterPrompts";
import { useApp } from "../../lib/store";
import { SaveBar } from "./SaveBar";

/** The recommendation for a studied site as checkboxes with the reason of each, and the one prompt composed from the ones kept. */
export function ComposePanel({ studyId, host, clones, prompts }: { studyId: string; host: string; clones: CloneInfo[]; prompts: PromptSummary[] }) {
  const toast = useApp((s) => s.toast);
  const [picked, setPicked] = useState<string[] | null>(null);
  const [recommended, setRecommended] = useState<string[]>([]);
  const [composed, setComposed] = useState<Composed | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setPicked(null), [studyId]);
  useEffect(() => {
    let live = true;
    api
      .post<Composed>(`/api/clone/${studyId}/compose`, { picked })
      .then((c) => {
        if (!live) return;
        setError(null);
        setComposed(c);
        if (picked === null) setRecommended(c.reasons.map((r) => r.id));
      })
      .catch((err: unknown) => {
        if (!live) return;
        setComposed(null); // the old prompt must not stay beside new picks that did not compose
        setError(errorMessage(err));
      });
    return () => {
      live = false;
    };
  }, [studyId, picked]);

  const title = (id: string) => prompts.find((p) => p.id === id)?.title ?? id;
  const why = new Map((composed?.reasons ?? []).map((r) => [r.id, r.why]));
  const rows = [...new Set([...recommended, ...(picked ?? [])])];
  const now = picked ?? recommended;
  const addable = prompts.filter((p) => !rows.includes(p.id));
  const copy = async () => {
    if (!composed) return;
    const ok = await copyText(composed.prompt);
    toast(ok ? { tone: "ok", title: t("Prompt copied") } : { tone: "bad", title: t("The prompt could not be copied"), body: t("The clipboard refused it. Select the text and copy it by hand.") });
  };

  return (
    <div className="grid content-start gap-3">
      <div className="clone-label">{t("Prompts that fit this site")}</div>
      {error && <Note tone="bad">{error}</Note>}
      {composed?.recommended_empty && <Note tone="warn">{t("No rule matched this site: only the always-on prompts are included")}</Note>}
      <ul className="grid gap-1.5">
        {rows.map((id) => (
          <li key={id}>
            <label className="flex items-start gap-2 text-xs">
              <input type="checkbox" className="mt-0.5" checked={now.includes(id)} onChange={() => setPicked(togglePick(picked, id, recommended))} />
              <span className="grid gap-0.5">
                <span className="text-ink"><span className="font-mono text-2xs text-ink-3">{id}</span> {title(id)}</span>
                {why.get(id) && <span className="text-ink-3">{why.get(id)}</span>}
              </span>
            </label>
          </li>
        ))}
      </ul>
      {addable.length > 0 && (
        <Select value="" aria-label={t("Add another prompt")} onChange={(e) => e.target.value && setPicked(togglePick(picked, e.target.value, recommended))}>
          <option value="">{t("Add another prompt")}</option>
          {addable.map((p) => (
            <option key={p.id} value={p.id}>{p.id} · {p.title}</option>
          ))}
        </Select>
      )}
      {composed && (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="font-mono text-2xs text-ink-3">{tokenLabel(composed.tokens)}</span>
            <Button size="sm" variant="primary" icon={<Copy size={13} aria-hidden />} onClick={() => void copy()}>{t("Copy")}</Button>
          </div>
          {composed.dropped.length > 0 && <Note tone="warn">{t("Left out to fit the size limit: {ids}", { ids: composed.dropped.map(title).join(", ") })}</Note>}
          {composed.over_budget && <Note tone="bad">{t("Still over the size limit even with the optional prompts left out: split the work in two.")}</Note>}
          <textarea
            readOnly
            value={composed.prompt}
            aria-label={t("Composed prompt")}
            className="h-72 w-full resize-y rounded-md border border-line bg-bg p-3 font-mono text-xs leading-relaxed text-ink-2 outline-none focus:border-accent"
          />
          <SaveBar studyId={studyId} host={host} clones={clones} composed={composed} />
        </>
      )}
    </div>
  );
}
