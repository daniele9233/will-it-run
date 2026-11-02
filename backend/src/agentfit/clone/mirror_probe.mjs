// The mirror probe: one page, N scroll or wheel steps, and what moved. Numbers only: no page text and no address leaves this script.
//
//   node mirror_probe.mjs <config.json>   config {"url", "out", "executable", "viewport": [w, h], "steps": 20, "driver": "scroll" | "wheel",
//                                         "shots": "<dir>", "watch_hosts"?: bool}; env WIR_BROWSER_DIR: where playwright-core is installed
//
// It opens the page like a visitor (the Chrome or Edge on this PC), follows the 60 largest visible elements through the steps (their 2D
// transform, opacity and box in document coordinates) and takes screenshots at five positions: 0, 25, 50, 75 and 100 % of the scroll for a
// page that scrolls, after 0, 5, 10, 15 and 20 wheel steps for a page that does not. ``watch_hosts`` also lists the hosts (no path) the page
// asked for other than its own: the replay uses it to show a leak. Hover and click are not driven.
//
// The launch and banner-hiding code repeats ``mirror.mjs``, ``capture.mjs`` and ``dna.mjs`` on purpose.
//
// stdout is JSON lines: {"event":"progress","step":"visit|probe|shots","detail":"..."}, then {"event":"done","result":{...}} or {"event":"fatal","error":"..."}.
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

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
try {
  const scheme = new URL(config.url).protocol;
  if (scheme !== "http:" && scheme !== "https:") fail(`Only http and https addresses can be probed, not ${scheme.replace(/:$/, "")}`);
} catch (error) {
  fail(`The address is not valid: ${String(error && error.message ? error.message : error).split(/\r?\n/)[0]}`);
}
const engine = process.env.WIR_BROWSER_DIR;
if (!engine) fail("WIR_BROWSER_DIR is not set");
const require = createRequire(path.join(engine, "node_modules", "playwright-core", "package.json"));
const { chromium } = require("playwright-core");

const VIEWPORT = Array.isArray(config.viewport) && config.viewport.length === 2 ? config.viewport : [1440, 900];
const STEPS = Math.max(1, Math.min(60, Number(config.steps) || 20));
const DRIVER = config.driver === "wheel" ? "wheel" : "scroll";
const TRACKED = 60;
const SHOTS = config.shots ? path.resolve(config.shots) : null;
if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });

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

// What the document is made of, as numbers.
const facts = () => {
  const height = Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0);
  const text = document.body ? document.body.innerText || "" : "";
  const scrolls = height > window.innerHeight + 1;
  const lenis = !!window.Lenis || !!window.lenis || document.documentElement.classList.contains("lenis");
  return {
    height, sections: document.querySelectorAll("section").length, elements: document.querySelectorAll("*").length,
    img: document.querySelectorAll("img").length, video: document.querySelectorAll("video").length,
    canvas: document.querySelectorAll("canvas").length, words: text.split(/\s+/).filter(Boolean).length,
    smooth_scroll: lenis ? "lenis" : scrolls ? "native" : "none", scroll_trigger: !!window.ScrollTrigger,
  };
};

// The largest visible elements (by the part inside the first screen), kept for the whole run. A key is a path of tag, index and class.
const track = (limit) => {
  const keyOf = (el) => {
    const parts = [];
    for (let node = el; node && node.nodeType === 1 && node !== document.documentElement; node = node.parentElement) {
      const siblings = node.parentElement ? Array.from(node.parentElement.children) : [node];
      const cls = typeof node.className === "string" ? node.className.trim().split(/\s+/).filter(Boolean).slice(0, 2).join(".") : "";
      parts.push(`${node.tagName.toLowerCase()}[${siblings.indexOf(node)}]${cls ? "." + cls.slice(0, 40) : ""}`);
    }
    return parts.reverse().join(">");
  };
  const found = [];
  for (const el of document.querySelectorAll("body *")) {
    const r = el.getBoundingClientRect();
    const w = Math.min(r.right, window.innerWidth) - Math.max(r.left, 0);
    const h = Math.min(r.bottom, window.innerHeight) - Math.max(r.top, 0);
    if (w <= 0 || h <= 0) continue;
    const s = getComputedStyle(el);
    if (s.display === "none" || s.visibility === "hidden") continue;
    found.push({ el, area: w * h });
  }
  found.sort((a, b) => b.area - a.area);
  window.__wirTracked = found.slice(0, limit).map((f) => ({ el: f.el, key: keyOf(f.el) }));
  return window.__wirTracked.length;
};

// One sample of the tracked elements: 2D transform, opacity, box in document coordinates (so that a plain scroll is not a movement).
const sample = () => {
  const round = (v) => Math.round(v * 10) / 10;
  return {
    scroll_y: Math.round(window.scrollY),
    items: (window.__wirTracked || []).map(({ el, key }) => {
      const r = el.getBoundingClientRect();
      const s = getComputedStyle(el);
      let tx = 0, ty = 0;
      try {
        if (s.transform && s.transform !== "none") {
          const m = new DOMMatrix(s.transform);
          tx = m.e;
          ty = m.f;
        }
      } catch (_) { /* a transform this engine cannot read */ }
      return { key, tx: round(tx), ty: round(ty), opacity: Math.round(Number(s.opacity) * 100) / 100, left: round(r.left + window.scrollX), top: round(r.top + window.scrollY) };
    }),
  };
};

// -- the run -------------------------------------------------------------------------------------------------

let browser;
const bail = async (error) => {
  if (browser) await browser.close().catch(() => {});
  fail(error);
};

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
  const ownHost = new URL(config.url).host;
  const foreign = new Set();
  if (config.watch_hosts) {
    page.on("request", (request) => {
      try {
        const u = new URL(request.url());
        if ((u.protocol === "http:" || u.protocol === "https:") && u.host !== ownHost) foreign.add(u.host);
      } catch (_) { /* not an address */ }
    });
  }

  progress("visit", `${VIEWPORT[0]}`);
  let response;
  try {
    response = await page.goto(config.url, { waitUntil: "domcontentloaded", timeout: 60000 });
  } catch (error) {
    await bail(`The page did not open: ${String(error && error.message ? error.message : error).split("\n")[0]}`);
  }
  const status = response ? response.status() : null;
  if (status && status >= 400) await bail(`The page answered HTTP ${status} ${response.statusText() || ""}`.trim());
  try { await page.waitForLoadState("load", { timeout: 15000 }); } catch (_) { /* slow: go on */ }
  try { await page.waitForLoadState("networkidle", { timeout: 8000 }); } catch (_) { /* never idle: go on */ }
  await sleep(1500);
  await page.evaluate(hideBanners).catch(() => 0);

  const measured = await page.evaluate(facts);
  const tracked = await page.evaluate(track, TRACKED);
  progress("probe", `${tracked} elements, ${STEPS} steps, ${DRIVER}`);

  const steps = [];
  const shots = [];
  const shoot = async (name) => {
    if (!SHOTS) return;
    await page.evaluate(hideBanners).catch(() => 0);
    try {
      await page.screenshot({ path: path.join(SHOTS, `shot-${name}.png`), type: "png", fullPage: false, timeout: 30000 });
      shots.push(name);
    } catch (_) { /* no picture for this position */ }
  };
  const stepEvery = Math.max(1, Math.round(STEPS / 4));
  if (DRIVER === "wheel") {
    await page.mouse.move(VIEWPORT[0] / 2, VIEWPORT[1] / 2);
    await shoot(0);
  }
  for (let i = 1; i <= STEPS; i++) {
    if (DRIVER === "wheel") await page.mouse.wheel(0, 120).catch(() => {});
    else await page.evaluate((top) => window.scrollTo({ top, behavior: "instant" }), Math.round(i * 0.5 * VIEWPORT[1])).catch(() => {});
    await sleep(150);
    steps.push(await page.evaluate(sample).catch(() => ({ scroll_y: 0, items: [] })));
    if (DRIVER === "wheel" && i % stepEvery === 0) {
      progress("shots", `after ${i}`);
      await shoot(i);
    }
  }
  if (DRIVER === "scroll") {
    const room = await page.evaluate(() => Math.max(0, Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0) - window.innerHeight));
    for (const share of [0, 25, 50, 75, 100]) {
      await page.evaluate((top) => window.scrollTo({ top, behavior: "instant" }), Math.round((room * share) / 100)).catch(() => {});
      await sleep(300);
      progress("shots", `${share}%`);
      await shoot(share);
    }
  }

  const result = { ...measured, driver: DRIVER, shots: shots.length, steps };
  if (config.watch_hosts) result.foreign_hosts = [...foreign].sort();
  fs.mkdirSync(path.dirname(path.resolve(config.out)), { recursive: true });
  fs.writeFileSync(path.resolve(config.out), JSON.stringify(result), "utf8");
  say({ event: "done", result: { out: path.resolve(config.out), steps: steps.length, tracked } });
  await browser.close().catch(() => {});
  process.exit(0);
} catch (error) {
  await bail(error);
}
