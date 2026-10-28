import { describe, expect, it } from "vitest";

import { dnaFetchProblem, libsLabel, limitLines, motionFacts, paletteSwatches, type DnaView } from "./dna";
import { stagesOf } from "./cloneSite";

function dna(extra: Partial<DnaView> = {}): DnaView {
  return {
    schema_version: 1,
    url: "https://example.com/",
    host: "example.com",
    captured_at: "2026-10-28T10:00:00Z",
    viewport: [1440, 900],
    framework: null,
    libs: [],
    tokens: { palette: null, fonts: null, type_scale: null, spacing: null, radii: null, shadows: null, container_px: null, breakpoints: null },
    sections: null,
    motion: {
      transitions: null, keyframes: null, sticky: null, fixed: null, scroll_snap: null, scroll_timeline: null, view_transitions: null,
      smooth_scroll: null, backdrop: null, blur: null, mask: null, clip_path: null, blend: null, videos: null, canvases: null, lottie: null,
    },
    patterns: { chip_filters: null, dialog: null, carousel: null, masonry: null, sticky_header: null, newsletter_form: null },
    probes: { scroll: null, hover: null },
    limits: [],
    ...extra,
  };
}

describe("study mode", () => {
  it("stagesOf study is the single dna stage", () => {
    expect(stagesOf("study")).toEqual([{ id: "dna", check: true }]);
  });
});

describe("paletteSwatches", () => {
  it("lists roles and skips an unmeasured palette", () => {
    expect(paletteSwatches(dna())).toEqual([]);
    const d = dna({ tokens: { ...dna().tokens, palette: [{ hex: "#0a0a0a", share: 0.7, role: "background", also: [], contrast: null }, { hex: "#ff3b30", share: 0.01, role: null, also: [], contrast: null }] } });
    expect(paletteSwatches(d)).toEqual([{ hex: "#0a0a0a", role: "background" }, { hex: "#ff3b30", role: null }]);
  });
});

describe("motionFacts", () => {
  it("leaves out what was not measured", () => {
    expect(motionFacts(dna())).toEqual([]);
    const d = dna({ motion: { ...dna().motion, sticky: 0, smooth_scroll: "lenis", scroll_snap: false, videos: { count: 2, loop: 2, muted: 2, autoplay: 1, preload_none: 0, playing_without_autoplay: 0 } } });
    const facts = motionFacts(d);
    expect(facts.some((f) => f.includes("Lenis") || f.toLowerCase().includes("smooth"))).toBe(true);
    expect(facts.some((f) => f.includes("2"))).toBe(true);
    // Zero measured is a fact; an unmeasured counter (blur: null) is absent.
    expect(facts.some((f) => f.toLowerCase().includes("blur"))).toBe(false);
    expect(facts.some((f) => f.toLowerCase().includes("sticky"))).toBe(true);
  });
});

describe("limitLines", () => {
  it("translates each code", () => {
    const lines = limitLines(dna({ limits: [{ code: "script_unreadable", count: 3 }, { code: "no_sections", count: null }, { code: "scroll_probe_skipped", count: null }, { code: "weird", count: null }] }));
    expect(lines[0]).toContain("3");
    expect(lines[1]).toContain("sections");
    expect(lines[2]).toContain("scroll");
    expect(lines[3]).toContain("weird");
  });
});

describe("libsLabel", () => {
  it("names the libraries or says none was detected", () => {
    expect(libsLabel(dna())).toBe("None detected");
    expect(libsLabel(dna({ libs: [{ name: "GSAP", evidence: ["a.js"] }, { name: "Lenis", evidence: [] }] }))).toBe("GSAP, Lenis");
  });
});

describe("dnaFetchProblem", () => {
  it("keeps a 404 (not ready yet) apart from a real failure", () => {
    expect(dnaFetchProblem(new Error("not yet"), 404)).toBeNull();
    expect(dnaFetchProblem(new Error("disk unreadable"), 500)).toBe("disk unreadable");
    expect(dnaFetchProblem("boom")).toBe("boom");
  });
});
