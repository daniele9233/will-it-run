import { describe, expect, it as test } from "vitest";

import { DEFAULT_MODEL_FILTERS, filterModels, nominalParamsB, paramBandBounds, parameterSizes, paramsInBand, quantizationOptions } from "./modelFilters";
import type { ModelCard } from "./types";

function card(name: string, quant: string, paramsB: number | null, tps: number, measured = false, nominalB: number | null = paramsB): ModelCard {
  return {
    entry: { name, params_b: paramsB, nominal_params_b: nominalB },
    quant,
    fit: { speed: { tokens_per_second: tps, measured } },
    accuracy: null,
  } as unknown as ModelCard;
}

const A = card("A", "Q4_K_M", 8, 20);
const B = card("B", "Q8_0", 27, 45, true);
const C = card("C", "Q4_K_M", 3.8, 80, true);
const D = card("D", "BF16", null, 0);

describe("model filters", () => {
  test("the quantization list is unique and puts the higher-quality files first", () => {
    const q4 = card("E", "Q4_K_M", 8, 20);
    q4.accuracy = { bits_per_weight: 4.8 } as unknown as ModelCard["accuracy"];
    const q8 = card("F", "Q8_0", 8, 20);
    q8.accuracy = { bits_per_weight: 8.5 } as unknown as ModelCard["accuracy"];
    expect(quantizationOptions([q4, q8, q4])).toEqual(["Q8_0", "Q4_K_M"]);
  });

  test("parameter bands include their upper boundary and exclude unknown sizes", () => {
    expect(paramsInBand(4, "le4")).toBe(true);
    expect(paramsInBand(4.1, "le4")).toBe(false);
    expect(paramsInBand(4.1, "4to8")).toBe(true);
    expect(paramsInBand(8, "4to8")).toBe(true);
    expect(paramsInBand(8.1, "8to15")).toBe(true);
    expect(paramsInBand(15, "8to15")).toBe(true);
    expect(paramsInBand(15.1, "15to30")).toBe(true);
    expect(paramsInBand(30, "15to30")).toBe(true);
    expect(paramsInBand(30.1, "30to70")).toBe(true);
    expect(paramsInBand(70, "30to70")).toBe(true);
    expect(paramsInBand(70.1, "gt70")).toBe(true);
    expect(paramsInBand(null, "le4")).toBe(false);
    expect(paramsInBand(null, "any")).toBe(true);
  });

  test("filters combine: quantization, parameter band and minimum speed", () => {
    const result = filterModels([A, B, C, D], { ...DEFAULT_MODEL_FILTERS, quant: "Q4_K_M", params: "le4", minTokensPerSecond: 50 });
    expect(result.map((item) => item.entry.name)).toEqual(["C"]);
  });

  test("a minimum speed hides models with no estimate and keeps the ones that reach it", () => {
    expect(filterModels([A, B, C, D], { ...DEFAULT_MODEL_FILTERS, minTokensPerSecond: 45 }).map((item) => item.entry.name)).toEqual(["B", "C"]);
    expect(filterModels([A, B, C], { ...DEFAULT_MODEL_FILTERS, minTokensPerSecond: 20 }).map((item) => item.entry.name)).toEqual(["A", "B", "C"]);
  });

  test("recommended keeps the order from the core; fastest sorts by tokens per second", () => {
    expect(filterModels([A, B, C], DEFAULT_MODEL_FILTERS).map((item) => item.entry.name)).toEqual(["A", "B", "C"]);
    expect(filterModels([A, B, C], { ...DEFAULT_MODEL_FILTERS, sort: "fastest" }).map((item) => item.entry.name)).toEqual(["C", "B", "A"]);
  });

  test("the size is the one the name states, not the exact metadata count", () => {
    const gemma = card("gemma-3-27b-it", "Q4_K_M", 27.4, 30, false, 27);
    const qwen = card("Qwen3.8-27B", "Q4_K_M", 27.8, 38, false, 27);
    const far = card("Qwen3-32B", "Q4_K_M", 32.2, 20, false, 32);
    const fallback = card("Metadata-27B", "Q4_K_M", 27, 12, false, null);
    expect(nominalParamsB(gemma)).toBe(27);
    expect(nominalParamsB(fallback)).toBe(27);
    const result = filterModels([gemma, qwen, far, fallback], { ...DEFAULT_MODEL_FILTERS, paramsExact: 27, sort: "fastest", minTokensPerSecond: 10 });
    expect(result.map((item) => item.entry.name)).toEqual(["Qwen3.8-27B", "gemma-3-27b-it", "Metadata-27B"]);
  });

  test("parameter bands use the named size too", () => {
    const named27 = card("Qwen3.8-27B", "Q4_K_M", 27.8, 30, false, 27);
    expect(filterModels([named27], { ...DEFAULT_MODEL_FILTERS, params: "15to30" })).toHaveLength(1);
    expect(filterModels([named27], { ...DEFAULT_MODEL_FILTERS, paramsExact: 28 })).toHaveLength(0);
  });

  test("the offered sizes are unique and ascend from small to large", () => {
    expect(parameterSizes([B, A, C, card("E", "Q4_K_M", 27.4, 20, false, 27), card("F", "Q4_K_M", null, 0, false, null)])).toEqual([3.8, 8, 27]);
  });

  test("a band becomes the bounds the core filters by", () => {
    expect(paramBandBounds("any")).toEqual({ min: null, max: null });
    expect(paramBandBounds("le4")).toEqual({ min: null, max: 4 });
    expect(paramBandBounds("4to8")).toEqual({ min: 4.000001, max: 8 });
    expect(paramBandBounds("15to30")).toEqual({ min: 15.000001, max: 30 });
    expect(paramBandBounds("gt70")).toEqual({ min: 70.000001, max: null });
  });
});
