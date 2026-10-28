import { Download, Save } from "lucide-react";
import { useEffect, useState } from "react";

import { Button, Note, Select } from "../../components/ui";
import { api } from "../../lib/api";
import type { CloneInfo } from "../../lib/cloneSite";
import { errorMessage } from "../../lib/hooks";
import { t } from "../../lib/i18n";
import { packsFor, type Composed, type SkillsCopy } from "../../lib/masterPrompts";
import { useApp } from "../../lib/store";

interface Saved {
  file: string;
  folder: string;
  skills: SkillsCopy;
}

interface SkillsStatus {
  packs: { id: string; skills: { name: string }[] }[];
}

/** The clones of the studied site first, then the others: where a saved prompt can go. */
function targets(clones: CloneInfo[], host: string): CloneInfo[] {
  const rank = (c: CloneInfo) => (c.host === host ? 0 : 1);
  return clones.filter((c) => c.mode !== "study").sort((a, b) => rank(a) - rank(b));
}

/** Save the composed prompt into a project (and the skills it cites), and fetch the skills that are not on this PC. */
export function SaveBar({ studyId, host, clones, composed }: { studyId: string; host: string; clones: CloneInfo[]; composed: Composed }) {
  const toast = useApp((s) => s.toast);
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<Saved | null>(null);
  const [packs, setPacks] = useState<string[]>([]);
  const options = targets(clones, host);
  // A note about a saved prompt describes that prompt: another prompt (new picks, another study) clears it.
  useEffect(() => setSaved(null), [composed.prompt, studyId]);
  // The Install button is there only when a pack holds a skill that is missing.
  const missing = saved?.skills.missing;
  useEffect(() => {
    setPacks([]);
    if (!missing || missing.length === 0) return;
    let live = true;
    api.get<SkillsStatus>("/api/clone/skills").then((s) => live && setPacks(packsFor(missing, s.packs))).catch(() => undefined);
    return () => {
      live = false;
    };
  }, [missing]);

  const save = () => {
    setBusy(true);
    api
      .post<Saved>(`/api/clone/${studyId}/prompts/save`, { text: composed.prompt, used: composed.used, target: target || null })
      .then(setSaved)
      .catch((err: unknown) => toast({ tone: "bad", title: t("The prompt could not be saved"), body: errorMessage(err) }))
      .finally(() => setBusy(false));
  };
  const install = async () => {
    try {
      await api.post("/api/clone/skills/install", { packs });
      toast({ tone: "ok", title: t("The skills are being fetched"), body: t("Save again when they are on this PC.") });
    } catch (err) {
      toast({ tone: "bad", title: t("The skills could not be fetched"), body: errorMessage(err) });
    }
  };

  return (
    <div className="grid gap-2 border-t border-line pt-3">
      <div className="flex flex-wrap items-center gap-2">
        <Select value={target} onChange={(e) => setTarget(e.target.value)} aria-label={t("Save into")} className="min-w-56 flex-1">
          <option value="">{t("The latest clone of this site, else the study folder")}</option>
          {options.map((c) => (
            <option key={c.id} value={c.id}>{c.title} · {c.host ?? c.slug}</option>
          ))}
        </Select>
        <Button size="sm" icon={<Save size={13} aria-hidden />} loading={busy} onClick={save}>{t("Save into the project")}</Button>
      </div>
      {saved && (
        <Note tone="ok">
          <div className="grid gap-1">
            <span>{t("Saved {file} in {folder}", { file: saved.file, folder: saved.folder })}</span>
            {saved.skills.copied.length > 0 && <span>{t("Skills copied: {names}", { names: saved.skills.copied.join(", ") })}</span>}
            {saved.skills.skipped.length > 0 && <span>{t("Skills left as they were because you already have your own: {names}", { names: saved.skills.skipped.join(", ") })}</span>}
            {saved.skills.failed.length > 0 && <span>{t("Skills that could not be copied: {names}", { names: saved.skills.failed.join(", ") })}</span>}
            {saved.skills.missing.length > 0 && (
              <span className="flex flex-wrap items-center gap-2">
                {t("Skills not on this PC: {names}", { names: saved.skills.missing.join(", ") })}
                {packs.length > 0 && <Button size="sm" icon={<Download size={13} aria-hidden />} onClick={() => void install()}>{t("Install the skills")}</Button>}
              </span>
            )}
          </div>
        </Note>
      )}
    </div>
  );
}
