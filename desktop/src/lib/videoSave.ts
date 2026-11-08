// WebView2 needs the native Save dialog: a blob download link alone does nothing.
import { api, getBackend, inTauri } from "./api";
import { pickSavePath } from "./dialog";
import { t } from "./i18n";

/** null means the person cancelled, or the browser handled the download. */
export async function saveVideo(id: string, prefix = "/api/videos/gallery"): Promise<string | null> {
  const name = `Will-It-Run-${id}.mp4`;
  if (inTauri) {
    const path = await pickSavePath(name, "mp4", t("Save video"));
    if (path === null) return null;
    const saved = await api.post<{ path: string }>(`${prefix}/${id}/save`, { path });
    return saved.path;
  }
  const { url, token } = await getBackend();
  const response = await fetch(`${url}${prefix}/${id}/file`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) throw new Error(t("Could not load the video."));
  const objectUrl = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 10_000);
  return null;
}
