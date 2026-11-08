import { useEffect, useState } from "react";
import { Download } from "lucide-react";
import { Button } from "./ui";
import { getBackend } from "../lib/api";
import { errorMessage } from "../lib/hooks";
import { t } from "../lib/i18n";
import { saveVideo } from "../lib/videoSave";

export function VideoPlayer({ id, title, prefix = "/api/videos/gallery" }: { id: string; title: string; prefix?: string }) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [savedPath, setSavedPath] = useState("");

  async function save() {
    setSaving(true); setError(""); setSavedPath("");
    try { setSavedPath(await saveVideo(id, prefix) ?? ""); }
    catch (err) { setError(errorMessage(err)); }
    finally { setSaving(false); }
  }
  useEffect(() => {
    const controller = new AbortController();
    let objectUrl = "";
    void (async () => {
      try {
        const backend = await getBackend();
        const response = await fetch(`${backend.url}${prefix}/${id}/file`, {
          headers: backend.token ? { Authorization: `Bearer ${backend.token}` } : {}, signal: controller.signal,
        });
        if (!response.ok) throw new Error(t("Could not load the video."));
        const blob = await response.blob();
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      } catch (err) { if (!controller.signal.aborted) setError(errorMessage(err)); }
    })();
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [id, prefix]);
  return <div className="grid gap-2">
    {error && <p role="alert" className="text-bad">{error}</p>}
    {url ? <video className="max-h-[520px] w-full rounded-lg bg-black" src={url} controls controlsList="nodownload" preload="metadata" aria-label={title} /> : <p>{t("Loading…")}</p>}
    <Button variant="primary" className="justify-self-start" icon={<Download size={16} />} loading={saving} onClick={() => void save()}>{t("Save video")}</Button>
    {savedPath && <p role="status" className="break-all text-sm text-ok">{t("Video saved to {path}", { path: savedPath })}</p>}
  </div>;
}
