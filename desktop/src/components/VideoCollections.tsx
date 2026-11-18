import { useEffect, useState } from "react";
import { Library, Wand2 } from "lucide-react";
import { Button } from "./ui";
import { PromptText } from "./VideoPrompts";
import { VideoPlayer } from "./VideoPlayer";
import { mediaBlob } from "../lib/api";
import { secondsLabel } from "../lib/format";
import { errorMessage } from "../lib/hooks";
import { t } from "../lib/i18n";
import type { VideoCollection, VideoCollectionEntry, VideoCollectionKeyframe } from "../lib/types";
import { collectionKeyframeOrigin, collectionMissingFiles, keyframeSecondsLabel } from "../lib/video";

function entryBase(collectionId: string, entryId: string): string {
  return `/api/videos/collections/${collectionId}/entries/${entryId}`;
}

function KeyframePicture({ path, alt }: { path: string; alt: string }) {
  const [url, setUrl] = useState("");
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let objectUrl = "";
    let alive = true;
    setUrl(""); setFailed(false);
    mediaBlob(path).then(blob => {
      if (!alive) return;
      objectUrl = URL.createObjectURL(blob);
      setUrl(objectUrl);
    }).catch(() => { if (alive) setFailed(true); });
    return () => { alive = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [path]);
  if (failed) return <div className="grid aspect-video w-full place-content-center rounded-md bg-bg text-xs text-ink-3">{t("This keyframe picture is not on disk.")}</div>;
  return url ? <img src={url} alt={alt} className="aspect-video w-full rounded-md object-cover" /> : <div className="aspect-video w-full animate-pulse rounded-md bg-bg" />;
}

function KeyframeCard({ base, keyframe, index, keyframes }: { base: string; keyframe: VideoCollectionKeyframe; index: number; keyframes: VideoCollectionKeyframe[] }) {
  const settings = [keyframe.image_model, keyframe.width && keyframe.height ? `${keyframe.width} × ${keyframe.height}` : null,
    t("Seed {seed} · {steps} steps · CFG {cfg}", { seed: keyframe.seed ?? t("Unknown"), steps: keyframe.steps ?? t("Unknown"), cfg: keyframe.cfg ?? t("Unknown") })]
    .filter(Boolean).join(" · ");
  return <li className="grid gap-2 rounded-lg border border-line bg-bg p-3 md:grid-cols-[220px_1fr]">
    <div className="grid content-start gap-1">
      {keyframe.ready ? <KeyframePicture path={`${base}/keyframes/${index}/file`} alt={t("Keyframe picture {number}", { number: index + 1 })} />
        : <p className="text-xs text-warn">{t("This keyframe picture is not on disk.")}</p>}
      <p className="text-xs font-medium">{t("Picture {number} · at {seconds} s", { number: index + 1, seconds: keyframe.time ?? t("Unknown") })}</p>
      <p className="text-xs text-ink-2">{collectionKeyframeOrigin(keyframe, keyframes)}</p>
      <p className="break-all text-2xs text-ink-3">{settings}</p>
    </div>
    <PromptText label={t("Image prompt")} value={keyframe.prompt} rows={4} />
  </li>;
}

function EntryDetail({ collection, entry, disabled, engineReady, onUse }: {
  collection: VideoCollection; entry: VideoCollectionEntry; disabled: boolean; engineReady: boolean;
  onUse: (collectionId: string, entry: VideoCollectionEntry) => Promise<void>;
}) {
  const base = entryBase(collection.id, entry.id);
  const missing = collectionMissingFiles(entry);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function use() {
    setBusy(true); setError("");
    try { await onUse(collection.id, entry); }
    catch (err) { setError(errorMessage(err)); }
    finally { setBusy(false); }
  }
  const facts = [entry.model_name || entry.model_key, `${entry.duration_seconds} s`,
    entry.width && entry.height ? `${entry.width} × ${entry.height}` : null, entry.turbo ? t("Turbo") : null,
    entry.seed != null ? `Seed ${entry.seed}` : null,
    entry.elapsed != null ? t("Generated in {time}", { time: secondsLabel(entry.elapsed) }) : null].filter(Boolean).join(" · ");
  return <div className="grid content-start gap-4">
    <h3 className="text-lg font-semibold">{entry.title}</h3>
    {entry.video_ready ? <VideoPlayer key={`${collection.id}-${entry.id}`} id={entry.id} prefix={`/api/videos/collections/${collection.id}/entries`} title={entry.title} />
      : <p role="alert" className="text-sm text-warn">{t("The video file is not on disk.")}</p>}
    <p className="text-xs text-ink-3">{facts}</p>
    {entry.keyframe_times.length > 0 && <p className="text-xs text-ink-2">{t("Keyframes at {times}", { times: keyframeSecondsLabel(entry.keyframe_times) })}</p>}
    {entry.notes && <p className="whitespace-pre-wrap rounded-md border border-line bg-bg p-3 text-sm text-ink-2">{entry.notes}</p>}
    <div className="flex flex-wrap items-center gap-2">
      <Button variant="primary" icon={<Wand2 size={14} />} loading={busy} disabled={disabled || !engineReady || missing.length > 0} onClick={() => void use()}>{t("Use in the generator")}</Button>
      {!engineReady && <span className="text-xs text-ink-3">{t("Start the shared Images and Videos engine here.")}</span>}
    </div>
    {missing.length > 0 && <p role="alert" className="text-sm text-warn">{t("Missing from the collection: {files}", { files: missing.join(", ") })}</p>}
    {error && <p role="alert" className="text-sm text-bad">{error}</p>}
    <PromptText label={t("Video prompt")} value={entry.prompt} rows={6} />
    <div className="grid gap-2">
      <h4 className="font-medium">{t("Keyframe pictures and their prompts")}</h4>
      <ol className="grid gap-3">{entry.keyframes.map((keyframe, index) => <KeyframeCard key={keyframe.file} base={base} keyframe={keyframe} index={index} keyframes={entry.keyframes} />)}</ol>
    </div>
  </div>;
}

export function VideoCollections({ collections, loading, error, disabled, engineReady, onUse }: {
  collections: VideoCollection[]; loading: boolean; error: string | null; disabled: boolean; engineReady: boolean;
  onUse: (collectionId: string, entry: VideoCollectionEntry) => Promise<void>;
}) {
  const [selected, setSelected] = useState<Record<string, string>>({});
  return <div className="grid gap-4">
    <h2 className="flex items-center gap-2 font-semibold"><Library size={18} />{t("Prompt collections")}</h2>
    <p className="text-sm text-ink-2">{t("Finished videos kept with everything that made them: each keyframe picture with its image prompt, the video prompt and the settings. Use one in the generator to make it again or to change it.")}</p>
    {error && <p role="alert" className="text-sm text-bad">{error}</p>}
    {!collections.length && <p className="text-sm text-ink-3">{loading ? t("Loading…") : t("Your prompt collections will appear here.")}</p>}
    {collections.map(collection => {
      const entry = collection.entries.find(e => e.id === selected[collection.id]) ?? collection.entries[0];
      return <article key={collection.id} className="grid gap-3 rounded-lg border border-line p-3">
        <div><h3 className="font-semibold">{collection.title} <span className="font-normal text-ink-3">({t("{count} videos", { count: collection.entries.length })})</span></h3>
          {collection.description && <p className="mt-1 whitespace-pre-wrap text-sm text-ink-2">{collection.description}</p>}</div>
        <div className="grid items-start gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
          <nav aria-label={t("Videos of {collection}", { collection: collection.title })} className="grid min-w-0 max-h-[720px] gap-1 overflow-y-auto overflow-x-hidden pr-1">
            {collection.entries.map((item, index) => <button key={item.id} type="button" aria-current={item.id === entry?.id ? "true" : undefined}
              className={`min-w-0 rounded-md border p-2 text-left text-sm hover:bg-raised ${item.id === entry?.id ? "border-accent bg-raised" : "border-line"}`}
              onClick={() => setSelected({ ...selected, [collection.id]: item.id })}>
              <span className="block truncate font-medium">{index + 1}. {item.title}</span>
              <span className="block truncate text-xs text-ink-3">{(item.model_name || item.model_key).split(" · ")[0]} · {item.duration_seconds} s · {t("{count} pictures", { count: item.keyframes.length })}</span>
            </button>)}
          </nav>
          {entry ? <EntryDetail collection={collection} entry={entry} disabled={disabled} engineReady={engineReady} onUse={onUse} /> : <p className="text-sm text-ink-3">{t("This collection has no videos.")}</p>}
        </div>
      </article>;
    })}
  </div>;
}
