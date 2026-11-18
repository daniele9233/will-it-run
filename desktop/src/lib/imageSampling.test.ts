import { describe, expect, it } from "vitest";

import { samplingChanges, samplingFor } from "./imageSampling";

const qwenUc = {
  sampling: { steps: 25, cfg: 1, sampler: "euler", scheduler: "simple" },
  kind_sampling: { edit: { cfg: 2.5 } },
};

describe("the sampling a model wants for a workflow", () => {
  it("raises Qwen-Image UC to CFG 2.5 for an edit and keeps the rest of its recipe", () => {
    expect(samplingFor(qwenUc, "edit")).toEqual({ steps: 25, cfg: 2.5, sampler: "euler", scheduler: "simple" });
  });

  it("keeps the model's own CFG for text to image and for upscaling", () => {
    expect(samplingFor(qwenUc, "txt2img")).toEqual(qwenUc.sampling);
    expect(samplingFor(qwenUc, "upscale")).toEqual(qwenUc.sampling);
  });

  it("uses the model's sampling everywhere when it has no override", () => {
    const sdxl = { sampling: { steps: 30, cfg: 4.5 } };
    expect(samplingFor(sdxl, "edit")).toEqual(sdxl.sampling);
  });

  it("is empty while no model is chosen", () => {
    expect(samplingFor(null, "edit")).toEqual({});
  });
});

describe("switching between generating and editing", () => {
  it("moves only the CFG of Qwen-Image UC, both ways", () => {
    expect(samplingChanges(qwenUc, "txt2img", "edit")).toEqual({ cfg: 2.5 });
    expect(samplingChanges(qwenUc, "edit", "txt2img")).toEqual({ cfg: 1 });
  });

  it("changes nothing for a model that samples every workflow the same way", () => {
    expect(samplingChanges({ sampling: { steps: 30, cfg: 4.5 } }, "txt2img", "edit")).toEqual({});
  });
});
