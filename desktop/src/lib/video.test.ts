import { describe, expect, it } from "vitest";
import type { VideoModel } from "./types";
import { videoFrameCount, videoInputDescription, videoInputLabel } from "./video";

const wan = { defaults: { fps: 16 }, frame_stride: 4, frame_offset: 1, input_mode: "single_or_first_last" } as VideoModel;
const ref = { defaults: { fps: 24 }, frame_stride: 17, frame_offset: 5, input_mode: "reference" } as VideoModel;

describe("video input contracts", () => {
  it("keeps Wan endpoints on 4n+1, including explicit slow motion", () => {
    expect(videoFrameCount(wan, 6)).toBe(97);
    expect(videoFrameCount(wan, 15)).toBe(241);
    expect(videoFrameCount(wan, 8, 33)).toBe(33);
  });
  it("shows the actual H3 frame grid instead of claiming seconds times fps", () => {
    expect(videoFrameCount(ref, 5)).toBe(124);
    expect(videoFrameCount(ref, 6)).toBe(158);
    expect(videoFrameCount(ref, 15)).toBe(362);
  });
  it("distinguishes an endpoint frame from an identity reference", () => {
    expect(videoInputLabel("first_last")).not.toBe(videoInputLabel("reference"));
    expect(videoInputDescription("reference")).not.toBe(videoInputDescription("single_or_first_last"));
  });
});
