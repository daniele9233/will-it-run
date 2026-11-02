import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ pick: vi.fn(), post: vi.fn() }));
vi.mock("./api", () => ({ inTauri: true, api: { post: mocks.post }, getBackend: vi.fn() }));
vi.mock("./dialog", () => ({ pickSavePath: mocks.pick }));
vi.mock("./i18n", () => ({ t: (text: string) => text }));

import { saveVideo } from "./videoSave";

beforeEach(() => vi.resetAllMocks());

describe("saving an MP4 in the desktop app", () => {
  it("uses the native destination and the authenticated copy endpoint", async () => {
    mocks.pick.mockResolvedValue("D:\\Videos\\hero.mp4");
    mocks.post.mockResolvedValue({ path: "D:\\Videos\\hero.mp4" });
    expect(await saveVideo("clip-id")).toBe("D:\\Videos\\hero.mp4");
    expect(mocks.pick).toHaveBeenCalledWith("Will-It-Run-clip-id.mp4", "mp4", "Save video");
    expect(mocks.post).toHaveBeenCalledWith("/api/videos/gallery/clip-id/save", { path: "D:\\Videos\\hero.mp4" });
  });

  it("does not copy anything when the person cancels", async () => {
    mocks.pick.mockResolvedValue(null);
    expect(await saveVideo("clip-id")).toBeNull();
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("reports a failed dialog instead of falling back to the broken blob link", async () => {
    mocks.pick.mockRejectedValue(new Error("dialog unavailable"));
    await expect(saveVideo("clip-id")).rejects.toThrow("dialog unavailable");
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it("reports a disk failure instead of claiming the video was saved", async () => {
    mocks.pick.mockResolvedValue("D:\\Videos\\hero.mp4");
    mocks.post.mockRejectedValue(new Error("disk full"));
    await expect(saveVideo("clip-id")).rejects.toThrow("disk full");
  });
});
