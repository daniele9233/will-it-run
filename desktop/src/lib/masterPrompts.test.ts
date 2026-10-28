import { afterEach, describe, expect, it, vi } from "vitest";

import { copyText, filterPrompts, packsFor, proofLabel, studyFailure, togglePick, tokenLabel, type PromptProof, type PromptSummary } from "./masterPrompts";

function prompt(over: Partial<PromptSummary>): PromptSummary {
  return { id: "A01", group: "pages", title: "Landing page", summary: "A hero and a footer", tags: [], size: "S", skills: [], libs: {}, proof: null, ...over };
}

const LIST = [
  prompt({ id: "A01", group: "pages", title: "Landing page" }),
  prompt({ id: "B01", group: "gallery", title: "Gallery chip filters", summary: "Filter cards", tags: ["chips"] }),
  prompt({ id: "C01", group: "motion", title: "Smooth scroll", summary: "Lenis and GSAP", tags: ["Lenis"] }),
];

afterEach(() => vi.unstubAllGlobals());

describe("filterPrompts", () => {
  it("filters by group and by words in the title, summary or tags", () => {
    expect(filterPrompts(LIST, "gallery", "").map((p) => p.id)).toEqual(["B01"]);
    expect(filterPrompts(LIST, "all", "LANDING").map((p) => p.id)).toEqual(["A01"]);
    expect(filterPrompts(LIST, "all", "filter cards").map((p) => p.id)).toEqual(["B01"]);
    expect(filterPrompts(LIST, "all", "lenis").map((p) => p.id)).toEqual(["C01"]);
    expect(filterPrompts(LIST, "motion", "landing")).toEqual([]);
  });

  it('a group "all" keeps everything', () => {
    expect(filterPrompts(LIST, "all", "  ")).toHaveLength(3);
  });
});

describe("proofLabel", () => {
  it("says Not tested yet without a proof", () => {
    expect(proofLabel(null)).toBe("Not tested yet");
  });

  it("names the builder and the date", () => {
    const proof: PromptProof = { builder: "Qwen3", date: "2026-10-28", build_ok: true, widths: [1440], dna_match: 0.9, notes: "" };
    expect(proofLabel(proof)).toBe("Tested with Qwen3 on 2026-10-28");
  });
});

describe("copyText", () => {
  it("returns false when the clipboard refuses", async () => {
    vi.stubGlobal("navigator", { clipboard: { writeText: () => Promise.reject(new Error("denied")) } });
    expect(await copyText("x")).toBe(false);
  });

  it("returns true when the clipboard accepts", async () => {
    vi.stubGlobal("navigator", { clipboard: { writeText: () => Promise.resolve() } });
    expect(await copyText("x")).toBe(true);
  });
});

describe("togglePick", () => {
  it("starts from the recommendation", () => {
    expect(togglePick(null, "B01", ["B01", "C01"])).toEqual(["C01"]);
    expect(togglePick(null, "A01", ["B01"])).toEqual(["B01", "A01"]);
  });

  it("removes a picked id and adds an unpicked one", () => {
    expect(togglePick(["B01", "C01"], "B01", ["Z"])).toEqual(["C01"]);
    expect(togglePick(["B01"], "C01", ["Z"])).toEqual(["B01", "C01"]);
  });
});

describe("tokenLabel", () => {
  it("separates thousands", () => {
    expect(tokenLabel(8412)).toBe("~8,412 tokens");
    expect(tokenLabel(950)).toBe("~950 tokens");
  });
});

describe("studyFailure", () => {
  it("gives what the study step said", () => {
    const run = { error: null, outputs: { dna: { passed: false, summary: "The site could not be studied: HTTP 403" } } };
    expect(studyFailure(run)).toBe("The site could not be studied: HTTP 403");
  });

  it("falls back to the run's error, then to a plain sentence", () => {
    expect(studyFailure({ error: "timed out", outputs: {} })).toBe("timed out");
    expect(studyFailure({ error: null, outputs: { dna: { passed: true, summary: "ok" } } })).toBe("The study did not finish.");
    expect(studyFailure(null)).toBe("The study did not finish.");
  });
});

describe("packsFor", () => {
  const packs = [
    { id: "gsap", skills: [{ name: "gsap-core" }] },
    { id: "motionsites", skills: [{ name: "motionsites-web-builder" }] },
  ];
  it("names the packs that hold the missing skills, and none when nothing matches", () => {
    expect(packsFor(["motionsites-web-builder"], packs)).toEqual(["motionsites"]);
    expect(packsFor(["unknown"], packs)).toEqual([]);
  });
});
