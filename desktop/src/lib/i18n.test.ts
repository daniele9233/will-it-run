import { describe, expect, it as test } from "vitest";

import { it as italian } from "../locales/it";
import { hasTranslation, resolveLang, translate } from "./i18n";
import { clampZoom, ZOOM_MAX, ZOOM_MIN } from "./zoom";

// Every source file as text, so the test sees the same t("...") calls the build ships.
const SOURCES = import.meta.glob<string>(["../**/*.ts", "../**/*.tsx", "!../**/*.test.ts", "!../locales/**"], { query: "?raw", import: "default", eager: true });
const CALL = /\bt\(\s*(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')/g;

function keysInSource(): Set<string> {
  const keys = new Set<string>();
  for (const text of Object.values(SOURCES)) {
    for (const match of text.matchAll(CALL)) {
      const raw = match[1] ?? (match[2] ?? "").replace(/\\'/g, "'").replace(/"/g, '\\"');
      keys.add(JSON.parse(`"${raw}"`) as string);
    }
  }
  return keys;
}

const placeholders = (text: string) => [...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();

describe("Italian catalog", () => {
  const keys = keysInSource();

  test("covers every string the interface shows", () => {
    expect(keys.size).toBeGreaterThan(500);
    const missing = [...keys].filter((key) => !(key in italian));
    expect(missing).toEqual([]);
  });

  test("keeps every placeholder", () => {
    const broken = Object.entries(italian).filter(([key, value]) => placeholders(key).join() !== placeholders(value).join());
    expect(broken).toEqual([]);
  });

  test("has no empty translation", () => {
    expect(Object.entries(italian).filter(([, value]) => !value.trim())).toEqual([]);
  });
});

describe("translate", () => {
  test("returns English as is and Italian from the catalog", () => {
    expect(translate("en", "Explore Models")).toBe("Explore Models");
    expect(translate("it", "Explore Models")).toBe("Esplora modelli");
  });

  test("fills placeholders in both languages", () => {
    expect(translate("en", "{n} GB free", { n: 116 })).toBe("116 GB free");
    expect(translate("it", "{n} GB free", { n: 116 })).toBe("116 GB liberi");
    expect(translate("it", "{free} GB free of {total} GB", { free: 12, total: 465 })).toBe("12 GB liberi su 465 GB");
  });

  test("falls back to the English text for an unknown string, and leaves unknown placeholders alone", () => {
    expect(translate("it", "Never translated {x}", { x: 1 })).toBe("Never translated 1");
    expect(translate("it", "{n} GB free", {})).toBe("{n} GB liberi");
    expect(hasTranslation("it", "Never translated")).toBe(false);
    expect(hasTranslation("en", "Never translated")).toBe(true);
  });

  test("translates the fit verdicts the core sends as codes", () => {
    expect(translate("it", "PERFECT")).toBe("PERFETTO");
    expect(translate("it", "MEMORY TIGHT")).toBe("MEMORIA AL LIMITE");
  });
});

describe("language and zoom settings", () => {
  test("an explicit language wins; anything else follows the system", () => {
    expect(resolveLang("it")).toBe("it");
    expect(resolveLang("en")).toBe("en");
    expect(["en", "it"]).toContain(resolveLang("system"));
    expect(["en", "it"]).toContain(resolveLang(undefined));
  });

  test("zoom stays inside its range and survives bad input", () => {
    expect(clampZoom(1)).toBe(1);
    expect(clampZoom(5)).toBe(ZOOM_MAX);
    expect(clampZoom(0.1)).toBe(ZOOM_MIN);
    expect(clampZoom(1.2000000001)).toBe(1.2);
    expect(clampZoom(Number.NaN)).toBe(1);
  });
});
