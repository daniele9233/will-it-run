// Tiny gettext-style i18n: the English text is the key.
//
//   t("Explore Models")                     -> "Esplora modelli"
//   t("{n} GB free", { n: 116 })            -> "116 GB liberi"
//
// The language is fixed for the lifetime of the page and read synchronously at start,
// so `t()` is safe anywhere, including module-level constants. Switching language
// stores the choice and reloads the window (see `storeLang`).

import { it } from "../locales/it";

export type Lang = "en" | "it";
export type LangSetting = "system" | Lang;

const STORAGE_KEY = "agentfit.lang";
const CATALOGS: Record<Lang, Record<string, string>> = { en: {}, it };

export const LANGUAGES: { value: Lang; label: string }[] = [
  { value: "it", label: "Italiano" },
  { value: "en", label: "English" },
];

export function systemLang(): Lang {
  const tag = typeof navigator !== "undefined" ? (navigator.language ?? "") : "";
  return tag.toLowerCase().startsWith("it") ? "it" : "en";
}

export function resolveLang(setting: LangSetting | string | null | undefined): Lang {
  return setting === "en" || setting === "it" ? setting : systemLang();
}

function readStored(): Lang | null {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    return saved === "en" || saved === "it" ? saved : null;
  } catch {
    return null;
  }
}

const current: Lang = readStored() ?? systemLang();
if (typeof document !== "undefined") document.documentElement.lang = current;

export function getLang(): Lang {
  return current;
}

/**
 * Remember `lang` for the next start. Returns true when the window must reload to
 * show it: the language differs from the running one and the choice was saved.
 */
export function storeLang(lang: Lang): boolean {
  try {
    localStorage.setItem(STORAGE_KEY, lang);
    return lang !== current && localStorage.getItem(STORAGE_KEY) === lang;
  } catch {
    return false;
  }
}

export function translate(lang: Lang, text: string, vars?: Record<string, string | number>): string {
  const template = CATALOGS[lang][text] ?? text;
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) => (name in vars ? String(vars[name]) : match));
}

export function t(text: string, vars?: Record<string, string | number>): string {
  return translate(current, text, vars);
}

/** True when the Italian catalog has this key (used by tests and for dynamic text). */
export function hasTranslation(lang: Lang, text: string): boolean {
  return lang === "en" || text in CATALOGS[lang];
}
