import { t } from "./i18n";
import type { VideoCollectionEntry, VideoCollectionKeyframe, VideoModel } from "./types";

export function videoInputLabel(maxImages = 1): string {
  return t("Keyframes · 1 to {count} pictures", { count: maxImages });
}

export function videoInputDescription(): string {
  return t("One picture is the first frame. Two pictures are the first and last frame. Three or more give one continuous shot that passes through every picture at its time, without cuts.");
}

/** Native frame count on the model's own grid (MiniMax H3 17n+5, LTX-2.5 8n+1), as the core computes it. */
export function videoFrameCount(model: VideoModel | undefined, seconds: number): number {
  const stride = model?.frame_stride ?? 8, offset = model?.frame_offset ?? 1;
  const count = seconds * (model?.defaults.fps ?? 24);
  return count + ((offset - count) % stride + stride) % stride;
}

/** First picture at 0 s, last at the end, the others evenly spaced: the core's default. */
export function defaultKeyframeTimes(count: number, seconds: number): number[] {
  if (count <= 0) return [];
  if (count === 1) return [0];
  return Array.from({ length: count }, (_, index) => Math.round(seconds * index / (count - 1) * 100) / 100);
}

/** The first problem with the chosen times, or "" when the core will accept them. */
export function keyframeTimesProblem(times: number[], seconds: number): string {
  if (times.some(time => !Number.isFinite(time) || time < 0 || time > seconds)) return t("Keyframe times must be between 0 and {seconds} seconds.", { seconds });
  if (times.some((time, index) => index > 0 && time <= (times[index - 1] ?? -1))) return t("Keyframe times must increase and land on different frames.");
  return "";
}

/** The files of a collection video that are not on disk: the video first, then the keyframe pictures in order. */
export function collectionMissingFiles(entry: VideoCollectionEntry): string[] {
  return [...(entry.video_ready ? [] : [entry.video]), ...entry.keyframes.filter(k => !k.ready).map(k => k.file)];
}

/** How a keyframe picture of a collection was made: from text, or as an edit of another keyframe (named by its number in
 * this video when it is one of them). */
export function collectionKeyframeOrigin(keyframe: VideoCollectionKeyframe, keyframes: VideoCollectionKeyframe[] = []): string {
  if (!keyframe.edited_from) return t("Text to image");
  const index = keyframes.findIndex(item => item.label === keyframe.edited_from);
  return index >= 0 ? t("Edit of picture {number}", { number: index + 1 }) : t("Edit of picture \"{label}\"", { label: keyframe.edited_from });
}

/** "0 · 2.5 · 5 · 8 s": the seconds the pictures are pinned to. */
export function keyframeSecondsLabel(times: number[]): string {
  return times.length ? `${times.map(time => Number(time.toFixed(2))).join(" · ")} s` : "";
}
