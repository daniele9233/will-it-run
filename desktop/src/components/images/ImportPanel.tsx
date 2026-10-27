import { AlertTriangle, CheckCircle2, FolderSearch, Loader2, Upload } from "lucide-react";
import { useState } from "react";

import { Badge, Button, Field, Note, Panel, ProgressBar, Section, Segmented, TextInput, cx } from "../ui";
import { api } from "../../lib/api";
import { canBrowseFolders, pickAnyFolder } from "../../lib/dialog";
import { errorMessage } from "../../lib/hooks";
import { t } from "../../lib/i18n";
import type { ImageImportScan } from "../../lib/types";
import { sizeLabel } from "./ModelManager";

type Mode = "copy" | "move" | "existing";

/** "Import the models I already have": scan a folder, show what was recognised, then place them. */
export function ImportPanel({ onDone, onClose }: { onDone: () => void; onClose: () => void }) {
  const [directory, setDirectory] = useState("");
  const [scan, setScan] = useState<ImageImportScan | null>(null);
  const [mode, setMode] = useState<Mode>("copy");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<{ imported: number; mode: string } | null>(null);
  const [chosen, setChosen] = useState<Record<string, boolean>>({});

  async function choose() {
    const picked = await pickAnyFolder(t("Where are the models you downloaded?"));
    if (picked) setDirectory(picked);
  }

  async function runScan(path = directory) {
    if (!path.trim()) return;
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      const result = await api.post<ImageImportScan>("/api/images/import/scan", { directory: path });
      setScan(result);
      setChosen(Object.fromEntries(result.components_to_import.map((id) => [id, true])));
    } catch (err) {
      setError(errorMessage(err));
      setScan(null);
    } finally {
      setBusy(false);
    }
  }

  async function apply() {
    if (!scan) return;
    setBusy(true);
    setError(null);
    try {
      const ids = Object.entries(chosen)
        .filter(([, value]) => value)
        .map(([id]) => id);
      const result = await api.post<{ imported: { component_id: string }[]; mode: string }>(
        "/api/images/import/apply",
        { directory: scan.directory, components: ids, mode },
      );
      setDone({ imported: result.imported.length, mode: result.mode });
      setScan(null);
      onDone();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const selected = Object.values(chosen).filter(Boolean).length;

  return (
    <Panel className="grid gap-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">{t("Import local models")}</h2>
          <p className="mt-0.5 max-w-[60ch] text-xs text-ink-3">
            {t(
              "Point at the folder where you downloaded the .safetensors files. Nothing is downloaded: the header of each file is read to work out what it is, then the files are placed where ComfyUI looks.",
            )}
          </p>
        </div>
        <Button size="sm" variant="ghost" onClick={onClose}>
          {t("Close")}
        </Button>
      </div>

      <div className="flex items-end gap-2">
        <Field label={t("Folder")}>
          <TextInput
            value={directory}
            placeholder="D:\\Models"
            onChange={(event) => setDirectory(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && void runScan()}
          />
        </Field>
        {canBrowseFolders && (
          <Button icon={<FolderSearch size={13} />} onClick={() => void choose()}>
            {t("Choose")}
          </Button>
        )}
        <Button variant="primary" icon={<FolderSearch size={13} />} loading={busy && !scan} onClick={() => void runScan()}>
          {t("Scan")}
        </Button>
      </div>

      {error && <Note tone="bad">{error}</Note>}
      {done && (
        <Note tone="ok">
          {t("{n} components imported ({mode}).", { n: done.imported, mode: done.mode })}
        </Note>
      )}

      {scan && (
        <>
          <Section title={t("What was found")} aside={t("{n} files", { n: scan.files.length })}>
            <ul className="grid gap-1">
              {scan.files.map((file) => (
                <li key={file.name} className="grid grid-cols-[1fr_auto] items-center gap-2 border-b border-line/60 py-1.5 last:border-0">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      {file.known ? (
                        <CheckCircle2 size={12} className="shrink-0 text-ok" />
                      ) : (
                        <AlertTriangle size={12} className="shrink-0 text-warn" />
                      )}
                      <span className="truncate text-xs font-medium">{file.component_name ?? file.name}</span>
                      <span className="shrink-0 font-mono text-2xs text-ink-3">{file.role}</span>
                    </div>
                    <div className="truncate pl-5 font-mono text-2xs text-ink-3">{file.name}</div>
                    {file.conflict && <div className="pl-5 text-2xs text-warn">{file.conflict}</div>}
                    {file.lora_check && (
                      <div className={cx("pl-5 text-2xs", file.lora_check.compatible ? "text-ok" : "text-warn")}>
                        {t("{matched} of {total} tensors pair with the base model.", {
                          matched: file.lora_check.matched,
                          total: file.lora_check.lora_tensors,
                        })}
                      </div>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <span className="text-2xs text-ink-3">{sizeLabel(file.size_bytes)}</span>
                    {file.already_visible ? (
                      <Badge tone="ok">{t("Already here")}</Badge>
                    ) : file.known ? (
                      <Badge tone="off">{t("To import")}</Badge>
                    ) : (
                      <Badge tone="warn">{t("Not recognised")}</Badge>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </Section>

          <Section title={t("Models")}>
            <ul className="grid gap-1.5">
              {scan.models.map((model) => (
                <li key={model.key} className="grid gap-0.5 rounded-md border border-line px-2.5 py-2">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-medium">{model.name}</span>
                    {model.blocked ? (
                      <Badge tone="warn">{t("Cannot run here")}</Badge>
                    ) : model.ready ? (
                      <Badge tone="ok">{t("All parts found")}</Badge>
                    ) : (
                      <Badge tone="off">{t("{n} missing", { n: model.missing.length })}</Badge>
                    )}
                  </div>
                  <span className="text-2xs text-ink-3">
                    {t("{n} components found", { n: model.found.length })}
                    {model.found.some((item) => item.already_visible) ? ` · ${t("some already here")}` : ""}
                  </span>
                  {model.blocked && <span className="text-2xs text-warn">{model.blocked.reason}</span>}
                </li>
              ))}
            </ul>
          </Section>

          {scan.unknown.length > 0 && (
            <Section title={t("Not recognised")}>
              <ul className="grid gap-0.5 text-2xs text-ink-3">
                {scan.unknown.map((file) => (
                  <li key={file.name}>
                    <span className="font-mono">{file.name}</span> — {file.reason}
                  </li>
                ))}
              </ul>
            </Section>
          )}

          <div className="grid gap-2 rounded-md border border-line bg-bg/40 p-2.5">
            <Field label={t("How to place them")}>
              <Segmented
                value={mode}
                label={t("How to place them")}
                onChange={(value) => setMode(value as Mode)}
                options={[
                  { value: "copy", label: t("Copy") },
                  { value: "move", label: t("Move") },
                  { value: "existing", label: t("Use the existing location") },
                ]}
              />
            </Field>
            <p className="text-2xs text-ink-3">
              {mode === "copy" && t("The originals stay where they are. {size} of copies.", { size: sizeLabel(scan.bytes_to_copy) })}
              {mode === "move" && t("The files leave the folder you chose and go to ComfyUI's. A copy is made first, so a failure leaves the original in place.")}
              {mode === "existing" &&
                t("Nothing is copied: the files are registered where they are, which only works if ComfyUI already reads that folder for each role. With a flat folder this is refused, because a text encoder would end up among the diffusion models.")}
            </p>
            <div className="flex items-center gap-2">
              <Button
                variant="primary"
                icon={<Upload size={13} />}
                loading={busy}
                disabled={busy || selected === 0 || mode === "existing" || scan.conflicts.length > 0}
                onClick={() => void apply()}
              >
                {t("Import {n} components", { n: selected })}
              </Button>
              {scan.conflicts.length > 0 && <span className="text-2xs text-warn">{t("Resolve the conflicts first.")}</span>}
              {busy && <ProgressBar indeterminate />}
            </div>
          </div>

          {scan.components_to_import.length > 0 && (
            <div className="grid gap-1">
              <span className="text-2xs text-ink-3">{t("Components that will be placed")}</span>
              <div className="flex flex-wrap gap-1.5">
                {scan.files
                  .filter((file) => file.component_id && !file.already_visible && !file.conflict)
                  .map((file) => {
                    const id = String(file.component_id);
                    const on = chosen[id] ?? false;
                    return (
                      <button
                        key={id}
                        type="button"
                        aria-pressed={on}
                        onClick={() => setChosen((current) => ({ ...current, [id]: !on }))}
                        className={cx(
                          "h-6 rounded-sm border px-2 text-2xs transition-colors",
                          on ? "border-accent text-ink" : "border-line text-ink-3 hover:text-ink",
                        )}
                      >
                        {file.component_name ?? id}
                      </button>
                    );
                  })}
              </div>
            </div>
          )}
        </>
      )}

      {busy && !scan && (
        <p className="flex items-center gap-2 text-xs text-ink-3">
          <Loader2 size={12} className="animate-spin" /> {t("Reading the file headers…")}
        </p>
      )}
    </Panel>
  );
}
