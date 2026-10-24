// Saving an Arena report: through the window's Save dialog where there is one, through the browser's own download where there is not.

import { getBackend, inTauri, api } from "./api";
import { pickSavePath } from "./dialog";
import { t } from "./i18n";

export type ExportFormat = "html" | "md" | "csv" | "json";

export const EXPORT_FORMATS: { id: ExportFormat; label: string; hint: string }[] = [
  { id: "html", label: "HTML", hint: t("A page that opens anywhere, with the charts") },
  { id: "md", label: "Markdown", hint: t("A text report for notes and repositories") },
  { id: "csv", label: "CSV", hint: t("One row per answer, for a spreadsheet") },
  { id: "json", label: "JSON", hint: t("Everything, for another program") },
];

export function slug(text: string): string {
  return (
    text
      .toLowerCase()
      .normalize("NFKD")
      .replace(/\p{M}/gu, "")
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 40) || "run"
  );
}

export function fileName(run: { title: string; started_at: string | null; created_at: string }, format: ExportFormat): string {
  return `arena-${(run.started_at ?? run.created_at).slice(0, 10)}-${slug(run.title)}.${format}`;
}

async function download(runId: string, format: ExportFormat, name: string): Promise<void> {
  const { url, token } = await getBackend();
  const response = await fetch(`${url}/api/arena/runs/${runId}/export?format=${format}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!response.ok) throw new Error(t("The report could not be made."));
  const link = document.createElement("a");
  link.href = URL.createObjectURL(await response.blob());
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(link.href), 10_000);
}

/** Save a run's report. Returns where it went (null when it went to the browser's downloads, or the person cancelled). */
export async function saveReport(run: { id: string; title: string; started_at: string | null; created_at: string }, format: ExportFormat): Promise<string | null> {
  const name = fileName(run, format);
  if (inTauri) {
    try {
      const path = await pickSavePath(name, format, t("Save the report"));
      if (path === null) return null;
      const saved = await api.post<{ path: string }>(`/api/arena/runs/${run.id}/export`, { format, path });
      return saved.path;
    } catch (err) {
      // A window that was built before it was allowed to save has no Save dialog: the download below is the way out.
      if (!String(err instanceof Error ? err.message : err).toLowerCase().includes("not allowed")) throw err;
    }
  }
  await download(run.id, format, name);
  return null;
}
