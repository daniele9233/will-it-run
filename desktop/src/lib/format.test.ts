import { describe, expect, it } from "vitest";

import { query } from "./api";
import { compactNumber, contextLabel, duration, fitTone, gbFromBytes, gbFromMb, params, readinessWord, sizeLabel, speedRange, tps } from "./format";
import { relativeLuminance } from "./store";
import type { FitCategory } from "./types";

describe("formatting", () => {
  it("never invents a value for missing data", () => {
    expect(gbFromBytes(null)).toBe("Unknown");
    expect(gbFromMb(undefined)).toBe("Unknown");
    expect(sizeLabel(null)).toBe("Unknown");
    expect(params(null)).toBe("Unknown");
    expect(contextLabel(0)).toBe("Unknown");
    expect(compactNumber(null)).toBe("Unknown");
    expect(tps(null)).toBe("Not measured");
  });

  it("formats sizes and marks estimates", () => {
    expect(gbFromBytes(16 * 1024 ** 3)).toBe("16.0 GB");
    expect(gbFromMb(16376, 0)).toBe("16 GB");
    expect(sizeLabel(9.6 * 1024 ** 3)).toBe("9.6 GB");
    expect(sizeLabel(9.6 * 1024 ** 3, true)).toBe("~9.6 GB");
    expect(sizeLabel(84 * 1024 ** 2)).toBe("84 MB");
  });

  it("formats model facts", () => {
    expect(params(27)).toBe("27B");
    expect(params(3.8)).toBe("3.8B");
    expect(params(0.27)).toBe("270M");
    expect(contextLabel(262144)).toBe("256K");
    expect(contextLabel(1048576)).toBe("1M");
    expect(compactNumber(2115658)).toBe("2.1M");
    expect(compactNumber(18200)).toBe("18k");
  });

  it("formats speed and time", () => {
    expect(tps(47.83)).toBe("47.8 tok/s");
    expect(tps(108.7)).toBe("109 tok/s");
    expect(speedRange(43.1, 49.6)).toBe("43–50 tok/s");
    expect(speedRange(20.2, 20.4)).toBe("~20 tok/s");
    expect(duration(3.06)).toBe("3.1s");
    expect(duration(83)).toBe("1m 23s");
  });
});

describe("semantic color mapping", () => {
  const cases: [FitCategory, string][] = [
    ["PERFECT", "ok"],
    ["EXCELLENT", "ok"],
    ["GOOD", "accent"],
    ["ACCEPTABLE", "warn"],
    ["SLOW", "limited"],
    ["MEMORY TIGHT", "limited"],
    ["NOT RECOMMENDED", "bad"],
    ["IMPOSSIBLE", "bad"],
  ];
  it.each(cases)("%s -> %s", (category, tone) => {
    expect(fitTone(category)).toBe(tone);
  });

  it("uses plain words on the dashboard", () => {
    expect(readinessWord("PERFECT")).toEqual({ word: "Excellent", tone: "ok" });
    expect(readinessWord("SLOW")).toEqual({ word: "Limited", tone: "limited" });
    expect(readinessWord(null)).toEqual({ word: "Not available", tone: "off" });
  });
});

describe("helpers", () => {
  it("builds query strings and skips empty values", () => {
    expect(query({ repo: "a/b", refresh: true, empty: "", none: null })).toBe("?repo=a%2Fb&refresh=true");
    expect(query({})).toBe("");
  });

  it("computes luminance for accent contrast", () => {
    expect(relativeLuminance("#ffffff")).toBeCloseTo(1, 3);
    expect(relativeLuminance("#000000")).toBe(0);
    // The default accent is light enough to need dark text on it.
    expect(relativeLuminance("#4aa8e0")).toBeGreaterThan(0.3);
  });
});
