import { FolderOpen, X } from "lucide-react";
import { useState } from "react";

import { canBrowseFolders, pickFolder } from "../lib/dialog";
import { t } from "../lib/i18n";
import type { RunSource } from "../lib/runSource";
import { GithubPicker } from "./GithubPicker";
import { Button, Segmented, TextInput, cx } from "./ui";

export type Place = "folder" | "github";

interface Props {
  place: Place;
  source: RunSource;
  /** Open GitHub's sign-in now: the user asked for GitHub here, it was not a choice restored from last time. */
  signInNow: boolean;
  label: string;
  /** Offer "ask before overwriting" with a folder. */
  protectable?: boolean;
  folderHint: string;
  githubHint: string;
  className?: string;
  onPlace: (place: Place) => void;
  /** `done`: the choice is complete and the picker can close. */
  onChoose: (source: RunSource, done: boolean) => void;
  onClose: () => void;
}

/**
 * Where something works: a folder on this PC or a GitHub repository. The panel the "+" of the
 * prompt opens, and the "Writes in" of an investigation.
 */
export function PlacePicker({ place, source, signInNow, label, protectable = true, folderHint, githubHint, className, onPlace, onChoose, onClose }: Props) {
  const [folderDraft, setFolderDraft] = useState(() => (source.kind === "folder" ? source.path : ""));
  const [protect, setProtect] = useState(() => (source.kind === "folder" ? source.protect : true));

  const takeFolder = (path: string, ask = protect) => {
    if (!path.trim()) return;
    onChoose({ kind: "folder", path: path.trim(), protect: ask }, true);
  };

  return (
    <div role="dialog" aria-label={label} className={cx("rise-in grid gap-2.5 rounded-xl border border-line-strong bg-surface p-2.5", className)} style={{ boxShadow: "var(--shadow)" }}>
      <div className="flex items-center justify-between gap-2">
        <Segmented
          label={label}
          value={place}
          onChange={onPlace}
          options={[
            { value: "folder", label: t("Folder on this PC") },
            { value: "github", label: "GitHub" },
          ]}
        />
        <button type="button" aria-label={t("Close")} onClick={onClose} className="grid h-6 w-6 place-items-center rounded-md text-ink-3 hover:bg-raised hover:text-ink">
          <X size={13} aria-hidden />
        </button>
      </div>

      {place === "folder" && (
        <div className="grid gap-2">
          <div className="flex gap-2">
            <TextInput
              value={folderDraft}
              autoFocus
              onChange={(event) => setFolderDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  takeFolder(folderDraft);
                }
              }}
              placeholder={t("e.g. D:\\Projects\\my-app")}
              aria-label={t("Folder on this PC")}
              className="h-8 flex-1 font-mono text-xs"
            />
            {canBrowseFolders && (
              <Button
                size="sm"
                icon={<FolderOpen size={13} />}
                onClick={() =>
                  void pickFolder(folderDraft || undefined, t("Choose the folder to work in")).then((picked) => {
                    if (!picked) return;
                    setFolderDraft(picked);
                    takeFolder(picked);
                  })
                }
              >
                {t("Browse")}
              </Button>
            )}
            <Button size="sm" variant="primary" disabled={!folderDraft.trim()} onClick={() => takeFolder(folderDraft)}>
              {t("Use it")}
            </Button>
          </div>
          {protectable && (
            <label className="flex items-center gap-2 text-xs text-ink-2">
              <input
                type="checkbox"
                checked={protect}
                onChange={(event) => {
                  setProtect(event.target.checked);
                  if (source.kind === "folder") onChoose({ ...source, protect: event.target.checked }, false);
                }}
                className="accent-[var(--accent)]"
              />
              {t("Ask me before a file that is already there is overwritten")}
            </label>
          )}
          <p className="text-xs text-ink-3">{folderHint}</p>
        </div>
      )}

      {place === "github" && (
        <div className="grid gap-2">
          <GithubPicker
            url={source.kind === "github" ? source.url : ""}
            branch={source.kind === "github" ? source.branch : ""}
            signInNow={signInNow}
            onChange={(next, picked) => onChoose({ kind: "github", ...next }, picked === true)}
          />
          <p className="text-xs text-ink-3">{githubHint}</p>
        </div>
      )}
    </div>
  );
}
