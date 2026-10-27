// Filters and ordering of the model cards the Models page receives from the core. They work on
// what is already shown, so changing one never waits for another search.

import type { ModelCard } from "./types";

export type ModelSort = "recommended" | "fastest";

/** The parameter bands offered as a filter, in billions of parameters. */
export type ParamBand = "any" | "le4" | "4to8" | "8to15" | "15to30" | "30to70" | "gt70";

export interface ModelFilters {
  sort: ModelSort;
  /** 0 = any speed. Otherwise the card's estimated or measured generation speed must reach it. */
  minTokensPerSecond: number;
  /** null = any quantization. */
  quant: string | null;
  params: ParamBand;
  /** A size the model's name states, such as 27 for "…-27B": null = any size. */
  paramsExact: number | null;
}

export const DEFAULT_MODEL_FILTERS: ModelFilters = {
  sort: "recommended",
  minTokensPerSecond: 0,
  quant: null,
  params: "any",
  paramsExact: null,
};

/**
 * The size to filter and show: what the model's name declares ("27B"), falling back to the
 * metadata count. A 27B model can report 26.9 or 27.8 in its metadata, so the exact count
 * is the wrong thing to group by.
 */
export function nominalParamsB(card: ModelCard): number | null {
  return card.entry.nominal_params_b ?? card.entry.params_b;
}

/** The quantization labels present in the cards, best quality first. */
export function quantizationOptions(cards: ModelCard[]): string[] {
  const bits = new Map<string, number>();
  for (const card of cards) {
    if (!bits.has(card.quant) && typeof card.accuracy?.bits_per_weight === "number") {
      bits.set(card.quant, card.accuracy.bits_per_weight);
    }
  }
  return [...new Set(cards.map((card) => card.quant))].sort(
    (a, b) => (bits.get(b) ?? 0) - (bits.get(a) ?? 0) || a.localeCompare(b, "en", { numeric: true }),
  );
}

/** The named model sizes present in the cards, smallest first. */
export function parameterSizes(cards: ModelCard[]): number[] {
  const sizes = new Set<number>();
  for (const card of cards) {
    const value = nominalParamsB(card);
    if (value && value > 0) sizes.add(value);
  }
  return [...sizes].sort((a, b) => a - b);
}

/** The numeric bounds of a band, for the core's server-side size filter. Lower bounds are exclusive. */
export function paramBandBounds(band: ParamBand): { min: number | null; max: number | null } {
  const justAbove = 0.000001;
  switch (band) {
    case "any":
      return { min: null, max: null };
    case "le4":
      return { min: null, max: 4 };
    case "4to8":
      return { min: 4 + justAbove, max: 8 };
    case "8to15":
      return { min: 8 + justAbove, max: 15 };
    case "15to30":
      return { min: 15 + justAbove, max: 30 };
    case "30to70":
      return { min: 30 + justAbove, max: 70 };
    case "gt70":
      return { min: 70 + justAbove, max: null };
  }
}

export function paramsInBand(paramsB: number | null | undefined, band: ParamBand): boolean {
  if (band === "any") return true;
  if (paramsB == null || paramsB <= 0) return false;
  switch (band) {
    case "le4":
      return paramsB <= 4;
    case "4to8":
      return paramsB > 4 && paramsB <= 8;
    case "8to15":
      return paramsB > 8 && paramsB <= 15;
    case "15to30":
      return paramsB > 15 && paramsB <= 30;
    case "30to70":
      return paramsB > 30 && paramsB <= 70;
    case "gt70":
      return paramsB > 70;
    default:
      return false;
  }
}

function matchesSize(card: ModelCard, filters: ModelFilters): boolean {
  const size = nominalParamsB(card);
  if (filters.paramsExact !== null) {
    // A name can state 27.5B; compare a little loosely so 27 and 27.0 are the same choice.
    return size !== null && Math.abs(size - filters.paramsExact) < 0.01;
  }
  return paramsInBand(size, filters.params);
}

export function filterModels(cards: ModelCard[], filters: ModelFilters): ModelCard[] {
  const speed = Math.max(0, filters.minTokensPerSecond);
  const filtered = cards.filter((card) => {
    if (filters.quant && card.quant !== filters.quant) return false;
    if (!matchesSize(card, filters)) return false;
    if (speed > 0 && (card.fit.speed.tokens_per_second || 0) < speed) return false;
    return true;
  });
  if (filters.sort === "recommended") return filtered;
  return [...filtered].sort(
    (a, b) =>
      (b.fit.speed.tokens_per_second || 0) - (a.fit.speed.tokens_per_second || 0) ||
      Number(b.fit.speed.measured) - Number(a.fit.speed.measured) ||
      a.entry.name.localeCompare(b.entry.name, "en", { numeric: true }),
  );
}
