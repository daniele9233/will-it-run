import { describe, expect, it } from "vitest";

import { stagesOf } from "./cloneSite";
import { gapLines, matchLines, mirrorFailure, motionLines, pixelLines, type MirrorReport } from "./mirror";

const EMPTY: MirrorReport = {
  schema_version: 1,
  live_at: null,
  replay_at: null,
  dna_match: null,
  facts: null,
  motion: null,
  pixels: null,
  requests: null,
  loads: null,
  driver: null,
  not_covered: null,
  seconds: null,
};

const report = (over: Partial<MirrorReport>): MirrorReport => ({ ...EMPTY, ...over });

describe("stagesOf mirror", () => {
  it("is record then verify", () => {
    expect(stagesOf("mirror").map((s) => s.id)).toEqual(["record", "verify"]);
  });
});

describe("matchLines", () => {
  it("says Not measured for a null part", () => {
    const lines = matchLines(report({ dna_match: { overall: 0.5, parts: { palette: 0.9, fonts: null, libs: null, motion: 1, sections: 0.75 }, not_covered: [] } }));
    expect(lines).toContain("Fonts: Not measured");
    expect(lines).toContain("Palette: 90%");
    expect(lines).toContain("Overall: 50%");
  });

  it("says Not measured for a whole missing part and never shows a zero", () => {
    expect(matchLines(EMPTY).join("|")).toContain("Not measured");
    expect(matchLines(EMPTY).join("|")).not.toContain("0%");
  });
});

describe("motionLines", () => {
  it("gives counts and the matching share as a percentage", () => {
    const lines = motionLines(report({ motion: { moving_live: 4, moving_replay: 3, matching: 0.75 }, driver: "wheel" }));
    expect(lines).toContain("Moving elements on the live page: 4");
    expect(lines).toContain("Moving elements on the mirror: 3");
    expect(lines).toContain("Moving the same way: 75%");
    expect(lines.join("|")).toContain("wheel");
  });

  it("reads Not measured for a null share and for no motion test", () => {
    expect(motionLines(report({ motion: { moving_live: 0, moving_replay: 0, matching: null } })).join("|")).toContain("Moving the same way: Not measured");
    expect(motionLines(EMPTY)).toEqual(["Motion: Not measured"]);
  });
});

describe("pixelLines", () => {
  it("lists each shot and says Not measured for a null share", () => {
    const lines = pixelLines(report({ pixels: [{ at: 0, within_16: 0.987 }, { at: 50, within_16: null }] }));
    expect(lines).toEqual(["At 0%: 98.7% of pixels close", "At 50%: Not measured"]);
  });
});

describe("gapLines", () => {
  it("lists leaked hosts and the 404 count", () => {
    const lines = gapLines(report({ requests: { not_recorded: 3, leaked_hosts: ["cdn.example.com"] } }));
    expect(lines).toContain("Requests the recording did not hold (404): 3");
    expect(lines.join("|")).toContain("cdn.example.com");
  });

  it("lists what is not covered", () => {
    const lines = gapLines(report({ not_covered: ["hover", "login", "odd"] }));
    expect(lines.join("|")).toContain("Hover");
    expect(lines.join("|")).toContain("Login");
    expect(lines.join("|")).toContain("odd");
  });

  it("says a blank first load was loaded again", () => {
    const lines = gapLines(report({ loads: [{ width: 1440, visible_words: 0 }, { width: 1440, visible_words: 120 }] }));
    expect(lines.join("|")).toContain("1440");
  });
});

describe("mirrorFailure", () => {
  it("gives the reason of the step that failed", () => {
    const run = { error: null, outputs: { record: { passed: false, summary: "The site could not be recorded: The page answered 403." } } };
    expect(mirrorFailure(run)).toBe("The site could not be recorded: The page answered 403.");
  });
  it("falls back to the run's error", () => {
    expect(mirrorFailure({ error: "timed out", outputs: {} })).toBe("timed out");
  });
});
