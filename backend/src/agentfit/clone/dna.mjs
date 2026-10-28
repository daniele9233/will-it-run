// The DNA collector: what a live page measured about itself, as numbers. It never reads the page's words.
//
//   node dna.mjs <config.json>        config {"url", "out", "executable", "locale"?}; env WIR_BROWSER_DIR: where playwright-core is installed
//
// It opens the page like a visitor (the Chrome or Edge on this PC), scrolls it once, measures styles and structure, reads
// the page's scripts to spot the libraries it uses, and probes scroll and hover. It writes <out>/dna-raw.json (read by
// ``dna.py``; the schema is in the plan) and <out>/thumb.jpg. Nothing here interprets: every field is a count, a histogram
// or a short list, and a field that could not be measured is null with a "limits" entry that says why.
//
// The launch and banner-hiding code repeats ``capture.mjs`` on purpose: the two scripts share nothing but their habits.
//
// stdout is JSON lines: {"event":"progress","step":"...","detail":"..."}, then {"event":"done","result":{...}}
// or {"event":"fatal","error":"..."}.
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { makeBudget, matchSignatures, settleHover } from "./dna_lib.mjs";

const say = (obj) => process.stdout.write(JSON.stringify(obj) + "\n");
const progress = (step, detail = "") => say({ event: "progress", step, detail });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fail = (error) => {
  say({ event: "fatal", error: String(error && error.message ? error.message : error) });
  process.exit(1);
};

let config;
try {
  config = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
} catch (error) {
  fail(`config unreadable: ${error}`);
}
const engine = process.env.WIR_BROWSER_DIR;
if (!engine) fail("WIR_BROWSER_DIR is not set");
const require = createRequire(path.join(engine, "node_modules", "playwright-core", "package.json"));
const { chromium } = require("playwright-core");

const OUT = path.resolve(config.out);
fs.mkdirSync(OUT, { recursive: true });
const HERE = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"));
const SIGNATURES = JSON.parse(fs.readFileSync(path.join(HERE, "dna_signatures.json"), "utf8").replace(/^﻿/, ""));
const VIEWPORT = [1440, 900];
const PROBE_CAP_MS = 10000;
const SCROLL_BUDGET_MS = 25000; // the scroll pass of a long or slow page stops here
const SCRIPTS_BUDGET_MS = 60000; // reading the page's scripts stops here; the rest count as unread

// -- in the page ---------------------------------------------------------------------------------------------

// Consent banners: hidden with an attribute, never removed (a page that finds a node of its own gone may throw).
const hideBanners = () => {
  if (!window.__wirHide) {
    const sheet = new CSSStyleSheet();
    sheet.replaceSync("[data-wir-hidden]{display:none!important}");
    document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
    window.__wirHide = sheet;
  }
  const CONSENT = [
    "#onetrust-consent-sdk", "#CybotCookiebotDialog", "#CybotCookiebotDialogBodyUnderlay", "#usercentrics-root", "#usercentrics-cmp-ui",
    "#iubenda-cs-banner", "#didomi-host", ".didomi-popup-container", "#cookie-law-info-bar", "#cmpbox", "#cmpwrapper", ".qc-cmp2-container",
    "#truste-consent-track", "[id^=sp_message_container]", ".cc-window", "#cookie-notice", ".cookie-notice", "#hs-eu-cookie-confirmation",
    ".grecaptcha-badge", "#CookieBanner",
  ];
  let hidden = 0;
  for (const selector of CONSENT) {
    try {
      for (const el of document.querySelectorAll(selector)) {
        el.setAttribute("data-wir-hidden", "1");
        hidden++;
      }
    } catch (_) { /* a selector this page does not like */ }
  }
  return hidden;
};

// Styles and structure. Returns histograms, counts and small lists, never the page's text, picture or video addresses.
// (The 5-bit colour bucket repeats quantizeHex of dna_lib.mjs: this function runs in the page and cannot import.)
const probePage = (arg) => {
  const [vw, vh] = arg.viewport;
  const limits = {};
  const limit = (code) => { limits[code] = (limits[code] || 0) + 1; };
  const bump = (map, key, by = 1) => { map[key] = (map[key] || 0) + by; };
  const hex = (r, g, b) => "#" + [r, g, b].map((v) => ((Math.max(0, Math.min(255, Math.round(v))) >> 3) << 3).toString(16).padStart(2, "0")).join("");
  const rgba = (value) => {
    const m = /rgba?\(\s*([\d.]+)[ ,]+([\d.]+)[ ,]+([\d.]+)(?:\s*[,/]\s*([\d.]+%?))?\s*\)/.exec(value || "");
    if (!m) return null;
    let a = m[4] === undefined ? 1 : m[4].endsWith("%") ? parseFloat(m[4]) / 100 : parseFloat(m[4]);
    if (Number.isNaN(a)) a = 1;
    return { r: +m[1], g: +m[2], b: +m[3], a };
  };
  // Modern pages write colours as oklch(), lab() or color(srgb ...): a 1x1 canvas reads any of them back as rgba, once on black and once on
  // white so that the alpha is exact. Null when the browser does not know the syntax.
  const swatch = (() => { const c = document.createElement("canvas"); c.width = c.height = 1; return c.getContext("2d", { willReadFrequently: true }); })();
  const viaCanvas = (value) => {
    if (!swatch) return null;
    swatch.fillStyle = "#123456";
    swatch.fillStyle = value;
    if (swatch.fillStyle === "#123456") return null;
    const read = (backdrop) => {
      swatch.globalCompositeOperation = "source-over";
      swatch.fillStyle = backdrop;
      swatch.fillRect(0, 0, 1, 1);
      swatch.fillStyle = value;
      swatch.fillRect(0, 0, 1, 1);
      return swatch.getImageData(0, 0, 1, 1).data;
    };
    const onBlack = read("#000");
    const onWhite = read("#fff");
    const a = Math.max(0, Math.min(1, 1 - (onWhite[0] - onBlack[0] + onWhite[1] - onBlack[1] + onWhite[2] - onBlack[2]) / (3 * 255)));
    if (a < 0.004) return { r: 0, g: 0, b: 0, a: 0 };
    return { r: Math.min(255, onBlack[0] / a), g: Math.min(255, onBlack[1] / a), b: Math.min(255, onBlack[2] / a), a };
  };
  const colourCache = new Map();
  const parseColor = (value) => {
    if (colourCache.has(value)) return colourCache.get(value);
    const c = rgba(value) || (value ? viaCanvas(value) : null);
    colourCache.set(value, c);
    return c;
  };
  const over = (fg, bg) => ({ r: fg.r * fg.a + bg.r * (1 - fg.a), g: fg.g * fg.a + bg.g * (1 - fg.a), b: fg.b * fg.a + bg.b * (1 - fg.a) });
  // The colour the eye sees behind an element (its ancestors' backgrounds laid over white, the canvas the browser paints) and the opacity it is
  // drawn with. Text and borders are measured laid over that, so a black text at 48% reads as the grey it looks like.
  const behind = new Map();
  const solidBg = (el) => {
    if (!el) return { r: 255, g: 255, b: 255 };
    let v = behind.get(el);
    if (v) return v;
    const own = parseColor(getComputedStyle(el).backgroundColor);
    const below = solidBg(el.parentElement);
    v = own && own.a > 0 ? over(own, below) : below;
    behind.set(el, v);
    return v;
  };
  const faded = new Map();
  const opacityOf = (el) => {
    if (!el) return 1;
    let v = faded.get(el);
    if (v === undefined) {
      const own = parseFloat(getComputedStyle(el).opacity);
      v = (Number.isFinite(own) ? own : 1) * opacityOf(el.parentElement);
      faded.set(el, v);
    }
    return v;
  };
  const ms = (text) => [...String(text || "").matchAll(/(-?\d*\.?\d+)\s*(ms|s)\b/g)].map((m) => Math.round(Number(m[1]) * (m[2] === "s" ? 1000 : 1)));
  const px = (value) => { const n = parseFloat(value); return /px$/.test(value || "") && Number.isFinite(n) ? n : null; };

  const styles = {
    backgrounds: {}, text_colors: {}, border_colors: {}, fonts: {}, sizes: {}, spacings: {}, radii: {}, shadows: {}, keyframes: [], breakpoints: [],
    counts: { sticky: 0, fixed: 0, backdrop: 0, mask: 0, clip_path: 0, blend: 0, blur: 0, scroll_snap: 0, scroll_timeline: 0, view_transition_rules: 0 },
  };
  const transitions = new Map();
  const containers = {};
  const all = [...document.querySelectorAll("body, body *")].slice(0, 8000);
  for (const el of all) {
    if (el.closest("[data-wir-hidden]")) continue;
    const cs = getComputedStyle(el);
    if (cs.display === "none") continue;
    const rect = el.getBoundingClientRect();
    const visible = rect.width > 0 && rect.height > 0 && cs.visibility !== "hidden";
    if (cs.position === "sticky") styles.counts.sticky++;
    if (cs.position === "fixed") styles.counts.fixed++;
    if (cs.backdropFilter && cs.backdropFilter !== "none") styles.counts.backdrop++;
    if ((cs.maskImage && cs.maskImage !== "none") || (cs.webkitMaskImage && cs.webkitMaskImage !== "none")) styles.counts.mask++;
    if (cs.clipPath && cs.clipPath !== "none") styles.counts.clip_path++;
    if (cs.mixBlendMode && cs.mixBlendMode !== "normal") styles.counts.blend++;
    if (/blur\(/.test(cs.filter || "")) styles.counts.blur++;
    if (cs.scrollSnapType && cs.scrollSnapType !== "none") styles.counts.scroll_snap++;
    if ((cs.animationTimeline && !/^(auto)(, auto)*$/.test(cs.animationTimeline)) || (cs.scrollTimelineName && cs.scrollTimelineName !== "none")) styles.counts.scroll_timeline++;
    if (cs.viewTransitionName && cs.viewTransitionName !== "none") styles.counts.view_transition_rules++;
    const maxW = px(cs.maxWidth);
    if (maxW && maxW >= 600 && maxW <= 2000) bump(containers, String(Math.round(maxW)));
    if (!visible) continue;
    const bg = parseColor(cs.backgroundColor);
    if (bg && bg.a >= 0.5) {
      const w = Math.max(0, Math.min(rect.right, vw) - Math.max(rect.left, 0));
      if (w > 0) bump(styles.backgrounds, hex(bg.r, bg.g, bg.b), Math.round(w * rect.height));
    }
    let chars = 0;
    for (const node of el.childNodes) if (node.nodeType === 3) chars += node.textContent.trim().length;
    if (chars > 0) {
      const fg = parseColor(cs.color);
      const alpha = fg ? fg.a * opacityOf(el) : 0;
      if (fg && alpha >= 0.15) {
        const seen = over({ ...fg, a: alpha }, solidBg(el));
        bump(styles.text_colors, hex(seen.r, seen.g, seen.b), chars);
      }
      const family = (cs.fontFamily.split(",")[0] || "").trim().replace(/^["']|["']$/g, "");
      if (family) {
        const font = styles.fonts[family] || (styles.fonts[family] = { chars: 0, weights: {} });
        font.chars += chars;
        bump(font.weights, String(cs.fontWeight), chars);
      }
      const size = px(cs.fontSize);
      if (size) bump(styles.sizes, String(Math.round(size * 10) / 10), chars);
    }
    for (const side of ["Top", "Right", "Bottom", "Left"]) {
      const width = px(cs["border" + side + "Width"]);
      const style = cs["border" + side + "Style"];
      if (!width || width <= 0 || !style || style === "none" || style === "hidden") continue;
      const line = parseColor(cs["border" + side + "Color"]);
      const alpha = line ? line.a * opacityOf(el) : 0;
      if (!line || alpha < 0.05) continue;
      const seen = over({ ...line, a: alpha }, solidBg(el));
      bump(styles.border_colors, hex(seen.r, seen.g, seen.b));
    }
    for (const prop of ["paddingTop", "paddingBottom", "paddingLeft", "paddingRight", "marginTop", "marginBottom", "rowGap", "columnGap"]) {
      const v = px(cs[prop]);
      if (v && v > 0 && v <= 400) bump(styles.spacings, String(Math.round(v)));
    }
    const radius = px(cs.borderTopLeftRadius);
    if (radius && radius > 0) bump(styles.radii, String(Math.min(999, Math.round(radius))));
    if (cs.boxShadow && cs.boxShadow !== "none") bump(styles.shadows, cs.boxShadow.slice(0, 120));
    const duration = ms(cs.transitionDuration)[0];
    if (duration > 0) {
      const easing = (/cubic-bezier\([^)]*\)|steps\([^)]*\)|[a-z-]+/.exec(cs.transitionTimingFunction || "") || ["ease"])[0];
      const key = duration + "|" + easing;
      transitions.set(key, (transitions.get(key) || 0) + 1);
    }
  }
  styles.transitions = [...transitions.entries()].sort((a, b) => b[1] - a[1]).slice(0, 12).map(([key, count]) => {
    const [d, easing] = key.split("|");
    return { duration_ms: Number(d), easing, count };
  });
  styles.container_px = Object.keys(containers).length ? Number(Object.entries(containers).sort((a, b) => b[1] - a[1])[0][0]) : null;

  // Style sheets: keyframes names, breakpoints, view-transition rules. A cross-origin sheet cannot be read from here.
  const names = new Set();
  const widths = new Set();
  const walk = (rules) => {
    for (const rule of rules) {
      // A rule that sets scroll-snap-type counts (a utility class of a carousel not on screen is still the site's choice).
      if (rule.type === 1 && /scroll-snap-type\s*:(?!\s*none)/.test(rule.cssText || "")) styles.counts.scroll_snap++;
      if (rule.type === 7 && rule.name) names.add(rule.name);
      else if (rule.cssRules && rule.type === 4) {
        for (const m of (rule.conditionText || rule.media.mediaText || "").matchAll(/(?:min|max)-width:\s*(\d+(?:\.\d+)?)px/g)) widths.add(Math.round(Number(m[1])));
        walk(rule.cssRules);
      } else if (rule.cssRules) walk(rule.cssRules);
      else if (/^@view-transition/.test(rule.cssText || "")) styles.counts.view_transition_rules++;
    }
  };
  let unreadable = 0;
  for (const sheet of document.styleSheets) {
    try { walk(sheet.cssRules); } catch (_) { unreadable++; }
  }
  for (let i = 0; i < unreadable; i++) limit("stylesheet_unreadable");
  styles.font_faces = [...new Set([...document.fonts].map((f) => String(f.family).replace(/^["']|["']$/g, "")))].slice(0, 20);
  styles.keyframes = [...names].slice(0, 40);
  styles.breakpoints = [...widths].sort((a, b) => a - b).slice(0, 12);

  // Sections: the header, each block of the main content, the footer.
  const SKIP = new Set(["SCRIPT", "STYLE", "LINK", "META", "NOSCRIPT", "TEMPLATE", "BASE", "HEAD"]);
  const kids = (el) => [...el.children].filter((c) => !SKIP.has(c.tagName) && !c.hasAttribute("data-wir-hidden"));
  const inFlow = (el) => { const cs = getComputedStyle(el); return cs.display !== "none" && cs.position !== "fixed" && cs.position !== "absolute"; };
  const isBlock = (el) => {
    const r = el.getBoundingClientRect();
    return r.width >= 0.3 * vw && inFlow(el) && (r.height >= 40 || ["HEADER", "FOOTER"].includes(el.tagName));
  };
  let root = document.body;
  for (let i = 0; i < 4; i++) {
    const flow = kids(root).filter((c) => inFlow(c) && c.getBoundingClientRect().height > 0);
    if (flow.length === 1 && !["MAIN", "HEADER", "FOOTER"].includes(flow[0].tagName)) root = flow[0];
    else break;
  }
  const picked = [];
  for (const el of kids(root)) {
    if (!isBlock(el)) continue;
    if (el.tagName === "MAIN" || el.getAttribute("role") === "main") {
      const inner = kids(el).filter(isBlock);
      if (inner.length) picked.push(...inner);
      else picked.push(el);
    } else picked.push(el);
  }
  const repeat = (section) => {
    let best = 0;
    for (const parent of [section, ...section.querySelectorAll("*")]) {
      const groups = {};
      for (const c of parent.children) {
        const key = c.tagName + "." + (typeof c.className === "string" ? c.className.trim().split(/\s+/)[0] : "");
        groups[key] = (groups[key] || 0) + 1;
      }
      for (const n of Object.values(groups)) if (n >= 3) best = Math.max(best, n);
    }
    return best;
  };
  const CONTROLS = "button, input:not([type=hidden]), select, textarea, [role=tab], [role=button], [role=radio], [role=checkbox]";
  // A row of small buttons, tabs, links or items and nothing else: the chips of a filter bar.
  const isChipGroup = (el) => {
    const items = [...el.children].filter((c) => c.matches("button, [role=tab], a, li"));
    if (items.length < 3 || items.length !== el.children.length) return false;
    return items.every((c) => { const r = c.getBoundingClientRect(); return r.height > 0 && r.height <= 64 && r.width >= 24 && r.width <= 260; });
  };
  // Evidence that a row of chips filters something: a real control (button, tab, radio, checkbox), a pressed/selected state, a filter-like
  // data attribute, or a tablist/radiogroup/toolbar around it. A row of plain links is a menu, not a filter, unless Python sees it above a grid.
  const FILTER_ATTR = /^data-(filter|category|tag|sort|group)/;
  const hasFilterEvidence = (el) => {
    if (el.closest("[role=tablist], [role=radiogroup], [role=toolbar]")) return true;
    return [...el.children].some((c) => c.matches("button, [role=tab], [role=radio], [role=checkbox], [aria-pressed], [aria-selected]")
      || [...c.attributes].some((at) => FILTER_ATTR.test(at.name)));
  };
  // The biggest row of chips of a section and whether anything shows it filters. A row inside a nav or a header counts only with evidence.
  const chipsIn = (section) => {
    let best = { n: 0, evidence: false };
    for (const el of [section, ...section.querySelectorAll("div, ul, nav, section")]) {
      if (!isChipGroup(el)) continue;
      const evidence = hasFilterEvidence(el);
      if (!evidence && el.closest("nav, header")) continue;
      if (el.children.length > best.n) best = { n: el.children.length, evidence };
    }
    return best;
  };
  // Columns: how many cards stand side by side in the first row at this viewport. The repeated cards are found as ``repeat`` finds them
  // (same tag and first class) but only boxes the size of a card count. A list that is one tall column per wrapper (a masonry built from
  // column divs) is read from the wrappers. Null when no repeated card is on screen.
  const cardLike = (c) => { const r = c.getBoundingClientRect(); return r.width >= 80 && r.height >= 60; };
  const firstRow = (items) => {
    const rects = items.map((c) => c.getBoundingClientRect()).filter((r) => r.width > 0 && r.height > 0 && r.left < vw && r.right > 0);
    if (!rects.length) return 0;
    const top = Math.min(...rects.map((r) => r.top));
    const lefts = [];
    for (const r of rects) if (r.top - top <= 24 && !lefts.some((l) => Math.abs(l - r.left) <= 4)) lefts.push(r.left);
    return lefts.length;
  };
  const median = (a) => { if (!a.length) return null; const b = [...a].sort((x, y) => x - y); return b[Math.floor(b.length / 2)]; };
  // The size of a run of cards: how many, the average height, and the gap between them (the spacing of the first row if there is one, else the
  // vertical spacing between a card and the next one below it). Null for what cannot be measured.
  const sizeOf = (items) => {
    const rects = items.map((c) => c.getBoundingClientRect()).filter((r) => r.width > 0 && r.height > 0);
    if (!rects.length) return { cards: null, card_height: null, gap: null };
    const avg = Math.round(rects.reduce((t, r) => t + r.height, 0) / rects.length);
    const top = Math.min(...rects.map((r) => r.top));
    const row = rects.filter((r) => r.top - top <= 24).sort((a, b) => a.left - b.left);
    const across = median(row.slice(1).map((r, i) => r.left - row[i].right).filter((g) => g >= 0));
    const down = [];
    const byLeft = [...rects].sort((a, b) => a.left - b.left || a.top - b.top);
    for (let i = 1; i < byLeft.length; i++) {
      if (Math.abs(byLeft[i].left - byLeft[i - 1].left) <= 4 && byLeft[i].top >= byLeft[i - 1].bottom) down.push(byLeft[i].top - byLeft[i - 1].bottom);
    }
    const gap = across !== null ? across : median(down);
    return { cards: rects.length, card_height: avg, gap: gap === null ? null : Math.round(gap) };
  };
  const keyOf = (c) => c.tagName + "." + (typeof c.className === "string" ? c.className.trim().split(/\s+/)[0] : "");
  // When the repeated boxes are columns (each holds its own run of at least three cards), the cards are the boxes' children, all columns together.
  const cardsIn = (items) => {
    const inner = {};
    for (const c of items[0].children) if (cardLike(c)) inner[keyOf(c)] = (inner[keyOf(c)] || 0) + 1;
    const key = Object.keys(inner).find((k) => inner[k] >= 3);
    if (!key) return items;
    return items.flatMap((w) => [...w.children].filter((c) => cardLike(c) && keyOf(c) === key));
  };
  const columnsOf = (section) => {
    let best = null;
    for (const parent of [section, ...section.querySelectorAll("*")]) {
      const groups = {};
      for (const c of parent.children) {
        if (!cardLike(c)) continue;
        const key = c.tagName + "." + (typeof c.className === "string" ? c.className.trim().split(/\s+/)[0] : "");
        (groups[key] = groups[key] || []).push(c);
      }
      for (const items of Object.values(groups)) if (items.length >= 3 && (!best || items.length > best.items.length)) best = { parent, items };
    }
    if (!best) return null;
    const n = firstRow(best.items);
    if (n === 0) return null;
    if (n > 1) return { columns: n, ...sizeOf(cardsIn(best.items)) };
    const holder = best.parent.parentElement;
    if (holder && holder !== section.parentElement) {
      const wrappers = [...holder.children].filter(cardLike);
      const width = wrappers.length ? wrappers[0].getBoundingClientRect().width : 0;
      const tag = best.items[0].tagName;
      if (wrappers.length >= 2 && wrappers.every((w) => Math.abs(w.getBoundingClientRect().width - width) <= 0.25 * width && w.querySelector(tag))) {
        const across = firstRow(wrappers);
        if (across > 1) {
          // Every card of every column, found as the first column's cards are: same tag and first class.
          const key = best.items[0].tagName + "." + (typeof best.items[0].className === "string" ? best.items[0].className.trim().split(/\s+/)[0] : "");
          const all = wrappers.flatMap((w) => [...w.querySelectorAll(tag)].filter((c) => cardLike(c) && (c.tagName + "." + (typeof c.className === "string" ? c.className.trim().split(/\s+/)[0] : "")) === key));
          return { columns: across, ...sizeOf(all) };
        }
      }
    }
    return { columns: 1, ...sizeOf(best.items) };
  };
  const sections = picked.slice(0, 40).map((el, i) => {
    const heading = el.querySelector("h1, h2, h3");
    const headingPx = heading ? px(getComputedStyle(heading).fontSize) : null;
    const video = el.querySelector("video");
    const rep = repeat(el);
    const grid = rep >= 3 ? columnsOf(el) : null;
    const chipRow = chipsIn(el);
    return {
      index: i + 1, tag: el.tagName.toLowerCase(), height: Math.round(el.getBoundingClientRect().height),
      heading_px: headingPx, heading_vw: headingPx ? Math.round((headingPx / vw) * 10000) / 100 : null,
      img: el.querySelectorAll("img, picture").length, video: el.querySelectorAll("video").length,
      canvas: el.querySelectorAll("canvas").length, svg: el.querySelectorAll("svg").length, repeat: rep,
      columns: grid ? grid.columns : null, cards: grid ? grid.cards : null, card_height: grid ? grid.card_height : null,
      gap: grid ? grid.gap : null, controls: el.querySelectorAll(CONTROLS).length, chips: chipRow.n, chips_evidence: chipRow.evidence,
      first_video_autoplay: !!(video && video.autoplay),
    };
  });
  if (!sections.length) limit("no_sections");

  const videos = [...document.querySelectorAll("video")].slice(0, 300).map((v) => ({
    loop: v.loop, muted: v.muted, autoplay: v.autoplay, preload: v.preload || null,
    playing_without_autoplay: !v.autoplay && !v.paused && v.currentTime > 0,
  }));
  const canvases = [...document.querySelectorAll("canvas")].slice(0, 12).map((c) => {
    const r = c.getBoundingClientRect();
    let context = "unknown";
    try {
      if (c.getContext("webgl2")) context = "webgl2";
      else if (c.getContext("webgl")) context = "webgl";
      else if (c.getContext("2d")) context = "2d";
    } catch (_) { /* control transferred to a worker */ }
    return { context, width: Math.round(r.width), height: Math.round(r.height), covers_viewport: r.width >= 0.9 * vw && r.height >= 0.9 * vh, in_card: !!c.closest("a, figure, li, article") };
  });

  // Patterns.
  const rectOf = (el) => el.getBoundingClientRect();
  const chipGroups = [...document.querySelectorAll("div, ul, nav, section")].filter((el) => {
    const items = [...el.children].filter((c) => c.matches("button, [role=tab], a, li"));
    if (items.length < 3 || items.length !== el.children.length) return false;
    return items.every((c) => { const r = rectOf(c); return r.height > 0 && r.height <= 64 && r.width >= 24 && r.width <= 260; });
  });
  const cardGroups = [...document.querySelectorAll("ul, ol, div, section")].filter((el) => {
    if (el.children.length < 8) return false;
    const tag = el.children[0].tagName;
    return [...el.children].every((c) => c.tagName === tag && rectOf(c).width >= 120 && rectOf(c).height >= 100);
  });
  const chip_filters = chipGroups.some((chips) => cardGroups.some((cards) => !cards.contains(chips) && rectOf(chips).bottom <= rectOf(cards).top + 5));
  const stickyHeader = [...document.querySelectorAll("header, [role=banner], body > nav")].slice(0, 5).some((el) => {
    const cs = getComputedStyle(el);
    return (cs.position === "fixed" || cs.position === "sticky") && (px(cs.top) || 0) <= 5;
  });
  const carousel = !!document.querySelector(".swiper, .splide, .slick-slider, .embla, .glide, [class*=carousel], [aria-roledescription=carousel]");
  const masonry = !!document.querySelector("[class*=masonry]") || [...document.querySelectorAll("div, ul, section")].some((el) => {
    const cs = getComputedStyle(el);
    return ((cs.columnCount && cs.columnCount !== "auto" && Number(cs.columnCount) > 1) || cs.gridTemplateRows === "masonry") && el.querySelectorAll("img").length >= 6;
  });
  const newsletter = [...document.querySelectorAll("form")].some((f) => f.querySelector("input[type=email]") && !f.querySelector("input[type=password]"));
  const dom = {
    sections, videos, canvases,
    lottie: document.querySelectorAll("lottie-player, dotlottie-player, dotlottie-wc, [class*=lottie]").length,
    iframes: document.querySelectorAll("iframe").length,
    patterns: {
      chip_filters, dialog: !!document.querySelector("dialog, [role=dialog], [aria-modal=true]"), carousel, masonry,
      sticky_header: stickyHeader, newsletter_form: newsletter,
    },
    smooth_scroll: { lenis_class: /(^|\s)lenis(\s|$)/.test(document.documentElement.className), css_smooth: getComputedStyle(document.documentElement).scrollBehavior === "smooth" },
  };

  const meta = (name) => (document.querySelector(`meta[name="${name}"]`) || {}).content || "";
  const generator = meta("generator");
  let framework = null;
  if (window.__NEXT_DATA__ || window.__next_f || document.getElementById("__next") || document.querySelector("next-route-announcer")
    || [...document.scripts].some((sc) => /\/_next\/static\//.test(sc.src))) framework = "next";
  else if (window.__NUXT__ || document.getElementById("__nuxt")) framework = "nuxt";
  else if (document.querySelector("astro-island") || /astro/i.test(generator)) framework = "astro";
  else if (document.documentElement.hasAttribute("data-wf-page") || /webflow/i.test(generator)) framework = "webflow";
  else if (/framer/i.test(generator) || document.querySelector("[data-framer-root]")) framework = "framer";
  else if (/wordpress/i.test(generator) || document.querySelector("link[href*='/wp-content/']")) framework = "wordpress";
  else if (window.Shopify) framework = "shopify";
  else if (document.querySelector("script[type=module][src*='/@vite'], script[type=module][src*='/assets/index-']")) framework = "vite";

  return {
    title: document.title || "", lang: document.documentElement.lang || "", framework,
    html_class: String(document.documentElement.className || "").slice(0, 200),
    globals: arg.globals.filter((name) => typeof window[name] !== "undefined"),
    page_height: Math.max(document.documentElement.scrollHeight, document.body.scrollHeight),
    styles, dom, limits,
    inline: document.querySelectorAll("script:not([src])").length,
    inlineText: [...document.querySelectorAll("script:not([src])")].map((s) => s.textContent || "").join("\n").slice(0, 600000),
    resources: performance.getEntriesByType("resource").filter((e) => e.initiatorType === "script" || /\.m?js(\?|$)/.test(e.name)).map((e) => e.name),
  };
};

// Scroll probe: which big visible elements move with the scroll, which are pinned.
const scrollProbe = async (arg) => {
  const [vw, vh] = arg.viewport;
  const wait = (t) => new Promise((r) => setTimeout(r, t));
  if (document.documentElement.scrollHeight - vh < 400) return null;
  window.scrollTo({ top: 0, behavior: "instant" });
  await wait(800);
  const pool = [...document.querySelectorAll("body *")].filter((el) => {
    if (el.closest("[data-wir-hidden]")) return false;
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return r.width * r.height >= 5000 && r.bottom > 0 && r.top < vh && r.right > 0 && r.left < vw && cs.visibility !== "hidden" && cs.display !== "none";
  });
  const step = Math.max(1, Math.ceil(pool.length / 60));
  const sample = pool.filter((_, i) => i % step === 0).slice(0, 60);
  const before = sample.map((el) => el.getBoundingClientRect().top);
  window.scrollTo({ top: 400, behavior: "instant" });
  await wait(800);
  let pinned = 0;
  let parallax = 0;
  sample.forEach((el, i) => {
    const moved = before[i] - el.getBoundingClientRect().top;
    const pos = getComputedStyle(el).position;
    if (pos === "fixed" || pos === "sticky" || Math.abs(moved) < 8) pinned++;
    else if (Math.abs(moved - 400) / 400 > 0.12) parallax++;
  });
  window.scrollTo({ top: 0, behavior: "instant" });
  return { sampled: sample.length, parallax_like: parallax, pinned };
};

// Hover probe, page side: marks at most six card-like elements and says how many.
const markCards = () => {
  const out = [];
  const cands = [...document.querySelectorAll("a, figure, li, article")].filter((el) => {
    if (el.closest("[data-wir-hidden]") || !el.querySelector("img, picture, video")) return false;
    const r = el.getBoundingClientRect();
    return r.width * r.height >= 20000 && getComputedStyle(el).visibility !== "hidden";
  });
  for (const el of cands) {
    if (out.some((o) => o.contains(el) || el.contains(o))) continue;
    out.push(el);
    if (out.length === 6) break;
  }
  out.forEach((el, i) => el.setAttribute("data-wir-card", String(i)));
  return out.length;
};

const PROPS = ["transform", "opacity", "boxShadow", "filter", "backgroundColor", "borderColor"];
const snapshot = ([index, props]) => {
  const el = document.querySelector(`[data-wir-card="${index}"]`);
  if (!el) return null;
  const cs = getComputedStyle(el);
  const video = el.querySelector("video");
  const out = { props: {}, playing: !!(video && !video.paused) };
  for (const p of props) out.props[p] = cs[p];
  return out;
};
const cardCenter = (index) => {
  const el = document.querySelector(`[data-wir-card="${index}"]`);
  el.scrollIntoView({ block: "center", behavior: "instant" });
  const r = el.getBoundingClientRect();
  return [r.left + r.width / 2, r.top + r.height / 2];
};

// -- the run -------------------------------------------------------------------------------------------------

const withCap = (promise, ms) => Promise.race([promise, new Promise((resolve) => setTimeout(() => resolve("__timeout__"), ms))]);

const started = Date.now();
let browser;
try {
  browser = await chromium.launch({
    executablePath: config.executable, headless: config.headless !== false,
    args: ["--no-first-run", "--no-default-browser-check", "--disable-blink-features=AutomationControlled", "--hide-scrollbars"],
  });
  // A headless Chrome introduces itself as "HeadlessChrome", and bot protection answers 403 to that before the site is sent.
  const probe = await browser.newContext();
  const userAgent = (await (await probe.newPage()).evaluate(() => navigator.userAgent)).replace("HeadlessChrome", "Chrome");
  await probe.close();
  const context = await browser.newContext({
    viewport: { width: VIEWPORT[0], height: VIEWPORT[1] }, deviceScaleFactor: 1, locale: config.locale || undefined, userAgent, serviceWorkers: "block",
  });
  const page = await context.newPage();
  const limits = [];
  const addLimit = (code, count = 1) => limits.push({ code, count });

  progress("visit", config.url);
  let response;
  try {
    response = await page.goto(config.url, { waitUntil: "domcontentloaded", timeout: 60000 });
  } catch (error) {
    fail(`The page did not open: ${String(error && error.message ? error.message : error).split("\n")[0]}`);
  }
  const status = response ? response.status() : null;
  if (status && status >= 400) fail(`The site answered HTTP ${status} ${response.statusText() || ""}`.trim() + ". It may refuse automated visitors, or the address is wrong.");
  try { await page.waitForLoadState("load", { timeout: 15000 }); } catch (_) { /* slow: go on */ }
  try { await page.waitForLoadState("networkidle", { timeout: 8000 }); } catch (_) { /* never idle: go on */ }
  await sleep(1500);
  await page.evaluate(hideBanners).catch(() => 0);
  if ((Date.now() - started) / 1000 > 30) addLimit("visit_slow");

  progress("scroll", "one pass down and back");
  const height = await page.evaluate(() => Math.max(document.documentElement.scrollHeight, document.body.scrollHeight));
  const stepPx = Math.round(VIEWPORT[1] * 0.8);
  const stops = [];
  for (let y = stepPx; y < height - VIEWPORT[1] + stepPx && stops.length < 200; y += stepPx) stops.push(y);
  const scrollBudget = makeBudget(SCROLL_BUDGET_MS);
  for (const y of [...stops, ...[...stops].reverse().slice(1), 0]) {
    if (scrollBudget.over() && y !== 0) {
      if (!limits.some((l) => l.code === "visit_slow")) addLimit("visit_slow");
      continue;
    }
    await page.evaluate((top) => window.scrollTo({ top, behavior: "instant" }), y).catch(() => {});
    await sleep(200);
  }
  await page.evaluate(hideBanners).catch(() => 0);
  await sleep(300);
  await page.screenshot({ path: path.join(OUT, "thumb.jpg"), type: "jpeg", quality: 60, fullPage: false, animations: "disabled", timeout: 30000 }).catch(() => {});

  progress("styles", "colours, type, spacing, structure");
  const measured = await page.evaluate(probePage, { viewport: VIEWPORT, globals: Object.keys(SIGNATURES.globals || {}) });
  for (const [code, count] of Object.entries(measured.limits)) addLimit(code, count);

  progress("scripts", `${measured.resources.length} loaded`);
  const urls = [...new Set(measured.resources.filter((u) => /^https?:/.test(u)))].slice(0, 80);
  const hits = {};
  const record = (label, text) => {
    for (const id of matchSignatures(text, SIGNATURES.signatures || [])) (hits[id] = hits[id] || []).includes(label) || hits[id].push(label);
  };
  let scanned = 0;
  let failed = 0;
  const scriptsBudget = makeBudget(SCRIPTS_BUDGET_MS);
  for (const url of urls) {
    if (scriptsBudget.over()) {
      failed++;
      continue;
    }
    try {
      const res = await context.request.get(url, { timeout: Math.max(1000, Math.min(10000, scriptsBudget.left())) });
      if (!res.ok()) throw new Error(String(res.status()));
      const text = (await res.text()).slice(0, 3000000);
      scanned++;
      const label = (new URL(url).pathname.split("/").pop() || new URL(url).host).slice(0, 60);
      record(label, text);
    } catch (_) {
      failed++;
    }
  }
  if (measured.inlineText) record("inline", measured.inlineText);
  if (failed) addLimit("script_unreadable", failed);

  progress("probes", "scroll and hover");
  let scrollResult = await withCap(page.evaluate(scrollProbe, { viewport: VIEWPORT }).catch(() => null), PROBE_CAP_MS);
  if (scrollResult === "__timeout__") scrollResult = null;
  if (scrollResult === null) addLimit("scroll_probe_skipped");

  let marked = null; // how many cards the page-side step marked; null until it has run
  const hoverRun = (async () => {
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
    const count = await page.evaluate(markCards);
    marked = count;
    if (!count) return null;
    const changed = new Set();
    let changedCards = 0;
    let plays = 0;
    for (let i = 0; i < count; i++) {
      await page.mouse.move(2, 2);
      const [x, y] = await page.evaluate(cardCenter, i);
      await sleep(150);
      const a = await page.evaluate(snapshot, [i, PROPS]);
      await page.mouse.move(x, y, { steps: 4 });
      await sleep(350);
      const b = await page.evaluate(snapshot, [i, PROPS]);
      if (!a || !b) continue;
      const diff = PROPS.filter((p) => a.props[p] !== b.props[p]);
      if (diff.length) changedCards++;
      diff.forEach((p) => changed.add(p === "boxShadow" ? "box-shadow" : p === "backgroundColor" ? "background-color" : p === "borderColor" ? "border-color" : p));
      if (!a.playing && b.playing) plays++;
    }
    return { sampled: count, changed: changedCards, plays_video: plays, props: [...changed] };
  })().catch((e) => { process.stderr.write(`hover probe: ${e}
`); return null; });
  let measuredHover = await withCap(hoverRun, PROBE_CAP_MS);
  if (measuredHover === "__timeout__") measuredHover = null;
  const settled = settleHover(marked, measuredHover);
  const hover = settled.value;
  if (settled.skipped) addLimit("hover_probe_skipped");

  progress("write", "dna-raw.json");
  const seconds = Math.round(((Date.now() - started) / 1000) * 10) / 10;
  const raw = {
    raw_version: 1, url: config.url, final_url: page.url(), captured_at: new Date().toISOString(), seconds,
    viewport: VIEWPORT, page_height: measured.page_height, title: measured.title, lang: measured.lang || null,
    framework: measured.framework, html_class: measured.html_class, globals: measured.globals,
    scripts: { urls, scanned, failed, inline: measured.inline },
    signature_hits: hits,
    styles: measured.styles, dom: measured.dom,
    probes: { scroll: scrollResult, hover },
    limits,
  };
  fs.writeFileSync(path.join(OUT, "dna-raw.json"), JSON.stringify(raw, null, 2), "utf8");
  say({ event: "done", result: { out: OUT, final_url: raw.final_url, seconds, limits: limits.map((l) => l.code) } });
  await browser.close().catch(() => {});
  process.exit(0);
} catch (error) {
  if (browser) await browser.close().catch(() => {});
  fail(error);
}
