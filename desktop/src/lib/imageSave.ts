// Saving a picture of the gallery: through the window's Save dialog where there is one, through the browser's own download
// where there is not. The link with a `download` attribute that used to do this does nothing inside the desktop window.

import { api, getBackend, inTauri } from "./api";
import { pickSavePath } from "./dialog";
import { t } from "./i18n";
import type { GalleryImage } from "./types";

/** A name a person can recognise in a folder: the model, the seed and the day. */
export function pictureFileName(image: Pick<GalleryImage, "model_key" | "seed" | "created_at">): string {
  const day = (image.created_at || "").slice(0, 10).replace(/-/g, "");
  const model = image.model_key.replace(/[^a-z0-9.]+/gi, "-").replace(/^[-.]+|[-.]+$/g, "") || "picture";
  return `${model}-seed${image.seed}${day ? `-${day}` : ""}.png`;
}

async function downloadThroughBrowser(id: string, name: string): Promise<void> {
  const { url, token } = await getBackend();
  const response = await fetch(`${url}/api/images/gallery/${id}/file`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!response.ok) throw new Error(t("The picture could not be read."));
  const link = document.createElement("a");
  link.href = URL.createObjectURL(await response.blob());
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(link.href), 10_000);
}

/** Save a picture. Returns where it went (null when it went to the browser's downloads, or the person cancelled). */
export async function savePicture(image: Pick<GalleryImage, "id" | "model_key" | "seed" | "created_at">): Promise<string | null> {
  const name = pictureFileName(image);
  if (inTauri) {
    try {
      const path = await pickSavePath(name, "png", t("Save the picture"));
      if (path === null) return null;
      const saved = await api.post<{ path: string }>(`/api/images/gallery/${image.id}/save`, { path });
      return saved.path;
    } catch (err) {
      // A window that was built before it was allowed to save has no Save dialog: the download below is the way out.
      if (!String(err instanceof Error ? err.message : err).toLowerCase().includes("not allowed")) throw err;
    }
  }
  await downloadThroughBrowser(image.id, name);
  return null;
}
