import { useEffect, useState } from "react";
import { Library, Wand2 } from "lucide-react";
import { Button, Collapsible, KeyValue, Select, cx } from "./ui";
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

function KeyframePicture({ path, alt, className }: { path: string; alt: string; className?: string }) {
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
  const box = cx("aspect-video w-full rounded-md", className);
  if (failed) return <div className={cx(box, "grid place-content-center bg-bg p-1 text-center text-2xs text-ink-3")}>{t("This keyframe picture is not on disk.")}</div>;
  return url ? <img src={url} alt={alt} className={cx(box, "object-cover")} /> : <div className={cx(box, "animate-pulse bg-bg")} />;
}

/** The keyframes as a row of thumbnails; the chosen one is shown large with how it was made and its image prompt. */
function Keyframes({ base, keyframes }: { base: string; keyframes: VideoCollectionKeyframe[] }) {
  const [chosen, setChosen] = useState(0);
  const index = Math.min(chosen, keyframes.length - 1);
  const keyframe = keyframes[index];
  if (!keyframe) return null;
  // A picture made without a model (a crop) has no seed, steps or CFG: the sampling part is left out, not shown as unknown.
  const sampled = keyframe.seed != null || keyframe.steps != null || keyframe.cfg != null;
  const settings = [keyframe.image_model, keyframe.width && keyframe.height ? `${keyframe.width} × ${keyframe.height}` : null,
    sampled ? t("Seed {seed} · {steps} steps · CFG {cfg}", { seed: keyframe.seed ?? t("Unknown"), steps: keyframe.steps ?? t("Unknown"), cfg: keyframe.cfg ?? t("Unknown") }) : null]
    .filter(Boolean).join(" · ");
  return <div className="grid gap-3">
    <h4 className="text-sm font-medium">{t("Keyframe pictures and their prompts")}</h4>
    <ol className="grid grid-cols-[repeat(auto-fill,minmax(104px,1fr))] gap-2">
      {keyframes.map((item, number) => <li key={item.file}>
        <button type="button" aria-pressed={number === index} aria-label={t("Picture {number} · at {seconds} s", { number: number + 1, seconds: item.time ?? t("Unknown") })}
          className={cx("grid w-full gap-1 rounded-md border p-1 text-left transition-colors hover:bg-raised", number === index ? "border-accent bg-raised" : "border-line")}
          onClick={() => setChosen(number)}>
          {item.ready ? <KeyframePicture path={`${base}/keyframes/${number}/file`} alt="" className="rounded" />
            : <span className="grid aspect-video place-content-center rounded bg-bg text-2xs text-warn">{t("Missing")}</span>}
          <span className="truncate text-2xs text-ink-2">{number + 1} · {item.time ?? "?"} s</span>
        </button>
      </li>)}
    </ol>
    <div className="grid gap-3 rounded-lg border border-line bg-bg p-3 md:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
      <div className="grid content-start gap-1">
        {keyframe.ready ? <KeyframePicture path={`${base}/keyframes/${index}/file`} alt={t("Keyframe picture {number}", { number: index + 1 })} />
          : <p className="text-xs text-warn">{t("This keyframe picture is not on disk.")}</p>}
        <p className="text-xs font-medium">{t("Picture {number} · at {seconds} s", { number: index + 1, seconds: keyframe.time ?? t("Unknown") })}</p>
        <p className="text-xs text-ink-2">{collectionKeyframeOrigin(keyframe, keyframes)}</p>
        <p className="break-all text-2xs text-ink-3">{settings}</p>
      </div>
      <PromptText key={keyframe.file} label={t("Image prompt")} value={keyframe.prompt} rows={7} />
    </div>
  </div>;
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
  const facts: [string, string][] = [
    [t("Model"), entry.model_name || entry.model_key],
    [t("Duration"), `${entry.duration_seconds} s${entry.turbo ? ` · ${t("Turbo")}` : ""}`],
    ...(entry.width && entry.height ? [[t("Resolution"), `${entry.width} × ${entry.height}`] as [string, string]] : []),
    [t("Seed"), entry.seed != null ? String(entry.seed) : t("Unknown")],
    ...(entry.elapsed != null ? [[t("Generation time"), secondsLabel(entry.elapsed)] as [string, string]] : []),
    ...(entry.keyframe_times.length ? [[t("Keyframes"), keyframeSecondsLabel(entry.keyframe_times)] as [string, string]] : []),
  ];
  return <div className="grid content-start gap-5">
    <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
      {entry.video_ready ? <VideoPlayer key={`${collection.id}-${entry.id}`} id={entry.id} prefix={`/api/videos/collections/${collection.id}/entries`} title={entry.title} />
        : <p role="alert" className="text-sm text-warn">{t("The video file is not on disk.")}</p>}
      <div className="grid content-start gap-3">
        <h3 className="text-lg font-semibold">{entry.title}</h3>
        <KeyValue rows={facts} />
        {entry.notes && <p className="whitespace-pre-wrap rounded-md border border-line bg-bg p-3 text-sm text-ink-2">{entry.notes}</p>}
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="primary" icon={<Wand2 size={14} />} loading={busy} disabled={disabled || !engineReady || missing.length > 0} onClick={() => void use()}>{t("Use in the generator")}</Button>
          {!engineReady && <span className="text-xs text-ink-3">{t("Start the shared Images and Videos engine here.")}</span>}
        </div>
        {missing.length > 0 && <p role="alert" className="text-sm text-warn">{t("Missing from the collection: {files}", { files: missing.join(", ") })}</p>}
        {error && <p role="alert" className="text-sm text-bad">{error}</p>}
      </div>
    </div>
    <PromptText label={t("Video prompt")} value={entry.prompt} rows={5} />
    <Keyframes base={base} keyframes={entry.keyframes} />
  </div>;
}

export function VideoCollections({ collections, loading, error, disabled, engineReady, onUse }: {
  collections: VideoCollection[]; loading: boolean; error: string | null; disabled: boolean; engineReady: boolean;
  onUse: (collectionId: string, entry: VideoCollectionEntry) => Promise<void>;
}) {
  const [chosenCollection, setChosenCollection] = useState("");
  // The video last shown in each collection, so switching collection and back keeps it.
  const [chosenEntry, setChosenEntry] = useState<Record<string, string>>({});
  const collection = collections.find(c => c.id === chosenCollection) ?? collections[0];
  const entry = collection && (collection.entries.find(e => e.id === chosenEntry[collection.id]) ?? collection.entries[0]);
  return <div className="grid gap-4">
    <h2 className="flex items-center gap-2 font-semibold"><Library size={18} />{t("Prompt collections")} {collections.length > 0 && <span className="font-normal text-ink-3">({collections.length})</span>}</h2>
    <p className="text-sm text-ink-2">{t("Finished videos kept with everything that made them: each keyframe picture with its image prompt, the video prompt and the settings. Use one in the generator to make it again or to change it.")}</p>
    {error && <p role="alert" className="text-sm text-bad">{error}</p>}
    {!collections.length && <p className="text-sm text-ink-3">{loading ? t("Loading…") : t("Your prompt collections will appear here.")}</p>}
    {collection && <>
      <div className="grid gap-3 md:grid-cols-2">
        <label className="grid min-w-0 gap-1 text-xs font-medium text-ink-2">{t("Collection")}
          <Select value={collection.id} onChange={e => setChosenCollection(e.target.value)}>
            {collections.map(c => <option key={c.id} value={c.id}>{c.title} · {t("{count} videos", { count: c.entries.length })}</option>)}
          </Select></label>
        <label className="grid min-w-0 gap-1 text-xs font-medium text-ink-2">{t("Video of the collection")}
          <Select value={entry?.id ?? ""} disabled={!collection.entries.length} onChange={e => setChosenEntry({ ...chosenEntry, [collection.id]: e.target.value })}>
            {collection.entries.map((item, index) => <option key={item.id} value={item.id}>{index + 1}. {item.title} · {item.duration_seconds} s</option>)}
          </Select></label>
      </div>
      {collection.description && <Collapsible key={collection.id} title={t("About this collection")}><p className="selectable whitespace-pre-wrap text-sm text-ink-2">{collection.description}</p></Collapsible>}
      {entry ? <EntryDetail key={`${collection.id}-${entry.id}`} collection={collection} entry={entry} disabled={disabled} engineReady={engineReady} onUse={onUse} />
        : <p className="text-sm text-ink-3">{t("This collection has no videos.")}</p>}
    </>}
  </div>;
}
