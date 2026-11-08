import { t } from "./i18n";
import type { VideoInputMode, VideoModel } from "./types";

export function videoInputLabel(mode: VideoInputMode): string {
  if (mode === "first_last") return t("First / last frame · both required");
  if (mode === "reference") return t("Single reference image");
  return t("Single frame · optional last frame");
}

export function videoInputDescription(mode: VideoInputMode): string {
  if (mode === "first_last") return t("Designed for a transition between a first and a last frame. Upload both images; native 720p generation is required.");
  if (mode === "reference") return t("Uses one image as a visual reference for the subject. It does not lock the first or last frame. Generates video and audio with the 4-step Turbo recipe.");
  return t("Animate one starting frame. An ending image is optional guidance; choose Wan 2.1 FLF2V for a dedicated first/last-frame workflow.");
}

export function videoFrameCount(model: VideoModel | undefined, seconds: number, motionFrames?: number | null): number {
  if (motionFrames) return motionFrames;
  const stride = model?.frame_stride ?? 4, offset = model?.frame_offset ?? 1;
  const count = seconds * (model?.defaults.fps ?? 16) + (model?.input_mode === "reference" ? 0 : 1);
  return count + ((offset - count) % stride + stride) % stride;
}
