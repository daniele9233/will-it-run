import { describe, expect, it } from "vitest";
import type { VideoModel } from "./types";
import { collectionKeyframeOrigin, collectionMissingFiles, defaultKeyframeTimes, galleryClipLabel, keyframeSecondsLabel, keyframeTimesProblem, oneLine, shortDateTime, videoFrameCount, videoInputDescription, videoInputLabel } from "./video";

const h3 = { defaults: { fps: 24 }, frame_stride: 17, frame_offset: 5, input_mode: "keyframes" } as VideoModel;
const ltx = { defaults: { fps: 24 }, frame_stride: 8, frame_offset: 1, input_mode: "keyframes" } as VideoModel;

describe("video keyframe contracts", () => {
  it("shows the actual H3 frame grid instead of claiming seconds times fps", () => {
    expect(videoFrameCount(h3, 5)).toBe(124);
    expect(videoFrameCount(h3, 10)).toBe(243);
    expect(videoFrameCount(h3, 15)).toBe(362);
  });
  it("keeps LTX-2.5 on 8n+1", () => {
    expect(videoFrameCount(ltx, 5)).toBe(121);
    expect(videoFrameCount(ltx, 10)).toBe(241);
  });
  it("spreads keyframes from the first to the last frame like the core", () => {
    expect(defaultKeyframeTimes(0, 10)).toEqual([]);
    expect(defaultKeyframeTimes(1, 10)).toEqual([0]);
    expect(defaultKeyframeTimes(2, 6)).toEqual([0, 6]);
    expect(defaultKeyframeTimes(6, 10)).toEqual([0, 2, 4, 6, 8, 10]);
  });
  it("rejects times outside the clip or out of order", () => {
    expect(keyframeTimesProblem([0, 1.5, 10], 10)).toBe("");
    expect(keyframeTimesProblem([0, 11], 10)).not.toBe("");
    expect(keyframeTimesProblem([0, 4, 4], 10)).not.toBe("");
    expect(keyframeTimesProblem([3, 1], 10)).not.toBe("");
  });
  it("describes the keyframe input", () => {
    expect(videoInputLabel(9)).toContain("9");
    expect(videoInputDescription()).not.toBe("");
  });
});

describe("prompt collections", () => {
  const keyframe = { file: "01-first.png", label: "first", time: 0, prompt: "p", workflow: "qwen_image_txt2img", edited_from: null,
    image_model: "qwen-image-2.1-uc", width: 1344, height: 768, seed: 42, steps: 25, cfg: 1, ready: true };
  const entry = { id: "01", title: "Headphones", video: "01.mp4", video_ready: true, model_key: "minimax-h3-fl2va", model_name: "H3",
    prompt: "p", duration_seconds: 6, keyframe_times: [0, 6], turbo: true, seed: 42, width: 1344, height: 768, elapsed: 457, notes: "",
    keyframes: [keyframe, { ...keyframe, file: "01-last.png", label: "last", time: 6, workflow: "qwen_image_edit", edited_from: "first", cfg: 2.5 }] };

  it("lists the missing files, the video first", () => {
    expect(collectionMissingFiles(entry)).toEqual([]);
    expect(collectionMissingFiles({ ...entry, video_ready: false, keyframes: [{ ...keyframe, ready: false }] })).toEqual(["01.mp4", "01-first.png"]);
  });

  it("says whether a picture was made from text or edited from another keyframe", () => {
    expect(collectionKeyframeOrigin(keyframe)).toBe("Text to image");
    expect(collectionKeyframeOrigin(entry.keyframes[1]!, entry.keyframes)).toBe("Edit of picture 1");
    expect(collectionKeyframeOrigin(entry.keyframes[1]!)).toBe("Edit of picture \"first\"");
    expect(collectionKeyframeOrigin({ ...keyframe, workflow: "ffmpeg_crop", edited_from: "first" }, entry.keyframes)).toBe("Crop of picture 1");
  });

  it("puts a long prompt on one line for a dropdown, cut at a word", () => {
    expect(oneLine("  short\n prompt ")).toBe("short prompt");
    const long = "The camera glides forward through the open door into a warm living room with a fireplace and a kitchen";
    expect(oneLine(long, 40)).toBe("The camera glides forward through the…");
    expect(oneLine("x".repeat(50), 40)).toBe(`${"x".repeat(40)}…`);
  });

  it("labels a gallery video with its number, seconds and prompt", () => {
    expect(galleryClipLabel(2, "The camera\nrises", 6)).toBe("3. 6 s · The camera rises");
    expect(galleryClipLabel(0, "p", 12.04)).toBe("1. 12 s · p");
    expect(galleryClipLabel(0, "p", null)).toBe("1. p");
    expect(galleryClipLabel(0, "  ")).toBe("1. —");
    const when = 1795010020;
    expect(galleryClipLabel(0, "p", 12, when)).toBe(`1. ${shortDateTime(when)} · 12 s · p`);
    expect(shortDateTime(when)).toMatch(/\d{2}.*\d{2}.*\d{2}.*\d{2}/);
  });

  it("writes the keyframe seconds compactly", () => {
    expect(keyframeSecondsLabel([0, 2.5, 5, 8])).toBe("0 · 2.5 · 5 · 8 s");
    expect(keyframeSecondsLabel([])).toBe("");
  });
});
