import { describe, expect, it } from "vitest";

import { pictureFileName } from "./imageSave";

describe("the name a saved picture gets", () => {
  it("says the model, the seed and the day", () => {
    expect(pictureFileName({ model_key: "qwen-image-2.1-uc-bf16", seed: 41091, created_at: "2026-10-27T12:30:00Z" })).toBe(
      "qwen-image-2.1-uc-bf16-seed41091-20261027.png",
    );
  });

  it("leaves out what it does not know and never produces a path", () => {
    expect(pictureFileName({ model_key: "", seed: 5, created_at: "" })).toBe("picture-seed5.png");
    expect(pictureFileName({ model_key: "../a b\\c", seed: 1, created_at: "2026-01-02" })).toBe("a-b-c-seed1-20260102.png");
  });
});
