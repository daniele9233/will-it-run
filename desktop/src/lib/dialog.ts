// Native folder picker. Only the desktop shell has one; in a plain browser the user types the path.

import { inTauri } from "./api";
import { t } from "./i18n";

export const canBrowseFolders = inTauri;

export async function pickFolder(startAt?: string, title?: string): Promise<string | null> {
  if (!inTauri) return null;
  try {
    const { open } = await import("@tauri-apps/plugin-dialog");
    const picked = await open({ directory: true, multiple: false, defaultPath: startAt, title: title ?? t("Choose a folder for models") });
    return typeof picked === "string" ? picked : null;
  } catch {
    return null;
  }
}


/** A folder anywhere on the PC, with a title that says what it is for. */
export async function pickAnyFolder(title: string, startAt?: string): Promise<string | null> {
  return pickFolder(startAt, title);
}

/** Where to save a file, chosen in the window's Save dialog. null when the person cancels, or when there is no such dialog (a plain browser). */
export async function pickSavePath(defaultName: string, extension: string, title: string): Promise<string | null> {
  if (!inTauri) return null;
  const { save } = await import("@tauri-apps/plugin-dialog");
  const picked = await save({ title, defaultPath: defaultName, filters: [{ name: extension.toUpperCase(), extensions: [extension] }] });
  return typeof picked === "string" ? picked : null;
}
