// The mirror recorder: every answer a live page receives during one visit, stored as it came. It never edits a body.
//
//   node mirror.mjs <config.json>     config {"url", "out", "executable", "locale"?, "limits": {total_mb, file_mb, responses, seconds}}
//                                     env WIR_BROWSER_DIR: where playwright-core is installed
//
// It opens the page like a visitor (the Chrome or Edge on this PC), waits for it to settle, drives it (a scroll pass down and back, or
// forty wheel steps when the document does not grow) so that what loads on interaction is recorded too, and writes
// <out>/index.json (a ``MirrorIndex``, see ``mirror.py``), <out>/files/<sha256 of the body> (decoded bodies) and <out>/thumb.jpg.
// Rewriting the stored text so that it points at the replay is a Python step: this script keeps the originals.
// Hover and click are not driven (they would change the page); GET responses only; 3xx answers are not stored (the final answer is).
//
// The launch and banner-hiding code repeats ``capture.mjs`` and ``dna.mjs`` on purpose: the scripts share nothing but their habits.
//
// stdout is JSON lines: {"event":"progress","step":"visit|scroll|wheel|record|write","detail":"..."}, then {"event":"done","result":{...}}
// or {"event":"fatal","error":"..."}.
import crypto from "node:crypto";
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
  if (scheme !== "http:" && scheme !== "https:") fail(`Only http and https addresses can be recorded, not ${scheme.replace(/:$/, "")}`);
} catch (error) {
  fail(`The address is not valid: ${String(error && error.message ? error.message : error).split(/\r?\n/)[0]}`);
}
const engine = process.env.WIR_BROWSER_DIR;
if (!engine) fail("WIR_BROWSER_DIR is not set");
const require = createRequire(path.join(engine, "node_modules", "playwright-core", "package.json"));
const { chromium } = require("playwright-core");

const OUT = path.resolve(config.out);
const FILES = path.join(OUT, "files");
fs.mkdirSync(FILES, { recursive: true });
const VIEWPORT = [1440, 900];
const MB = 1024 * 1024;
const LIMITS = config.limits || { total_mb: 150, file_mb: 40, responses: 3000, seconds: 120 };
const FILE_BYTES = LIMITS.file_mb * MB;
const TOTAL_BYTES = LIMITS.total_mb * MB;
const MAX_SCROLL_STOPS = 200;

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

const documentHeight = () => Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0);

// -- the run -------------------------------------------------------------------------------------------------

const started = Date.now();
const left = () => LIMITS.seconds * 1000 - (Date.now() - started);
const limitCounts = new Map();
const addLimit = (code, count = 1) => limitCounts.set(code, (limitCounts.get(code) || 0) + count);
const entries = [];
const notRecorded = [];
const seen = new Set();
const pending = new Set();
let order = 0;
let storedBytes = 0;
let browser;

const bail = async (error) => {
  if (browser) await browser.close().catch(() => {});
  fail(error);
};

const store = (body) => {
  const name = crypto.createHash("sha256").update(body).digest("hex");
  const file = path.join(FILES, name);
  if (!fs.existsSync(file)) fs.writeFileSync(file, body);
  storedBytes += body.length;
  return name;
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

  // Fills one entry from a body: stored under its hash unless it is over a limit (then the entry has no file and the limit is counted).
  const keep = (entry, body) => {
    entry.bytes = body.length;
    if (body.length > FILE_BYTES || storedBytes + body.length > TOTAL_BYTES) {
      addLimit("too_large");
      return;
    }
    entry.file = store(body);
  };

  const onResponse = (response) => {
    const request = response.request();
    const url = response.url();
    if (request.method() !== "GET" || !/^https?:/i.test(url)) return;
    const status = response.status();
    if (status >= 300 && status < 400) {
      // A redirect has no body to store; the address that was asked for is not in the recording.
      if (!notRecorded.includes(url)) notRecorded.push(url);
      return;
    }
    if (seen.has(url)) return;
    seen.add(url);
    if (entries.length >= LIMITS.responses) {
      addLimit("too_many");
      notRecorded.push(url);
      return;
    }
    const parsed = new URL(url);
    const headers = response.headers();
    const entry = {
      url, host: parsed.host, path: parsed.pathname + parsed.search, status, content_type: headers["content-type"] || null,
      file: null, bytes: 0, order: order++, rewritten: false,
    };
    entries.push(entry);
    const job = (async () => {
      try {
        if (status === 206) {
          // A byte range of a video or an audio: the file is fetched once, whole, with a plain request.
          const total = /\/(\d+)\s*$/.exec(headers["content-range"] || "");
          if (total && Number(total[1]) > FILE_BYTES) {
            entry.bytes = Number(total[1]);
            addLimit("too_large");
            return;
          }
          const whole = await context.request.get(url, { timeout: Math.max(1000, Math.min(60000, left())), maxRedirects: 0 });
          if (whole.status() !== 200) {
            entry.status = whole.status();
            notRecorded.push(url);
            return;
          }
          entry.status = 200;
          entry.content_type = whole.headers()["content-type"] || entry.content_type;
          keep(entry, await whole.body());
          return;
        }
        keep(entry, await response.body());
      } catch (_) {
        // The body is gone (the page moved on) or the plain request failed: the replay cannot answer this address.
        entry.file = null;
        notRecorded.push(url);
      }
    })();
    pending.add(job);
    job.finally(() => pending.delete(job));
  };
  context.on("response", onResponse);
  const page = await context.newPage();

  progress("visit", config.url);
  let response;
  try {
    response = await page.goto(config.url, { waitUntil: "domcontentloaded", timeout: Math.max(1000, Math.min(60000, left())) });
  } catch (error) {
    await bail(`The page did not open: ${String(error && error.message ? error.message : error).split("\n")[0]}`);
  }
  const status = response ? response.status() : null;
  if (status && status >= 400) {
    await bail(`The site answered HTTP ${status} ${response.statusText() || ""}`.trim() + ". It may refuse automated visitors, or the address is wrong.");
  }
  try { await page.waitForLoadState("load", { timeout: Math.min(15000, Math.max(1000, left())) }); } catch (_) { /* slow: go on */ }
  try { await page.waitForLoadState("networkidle", { timeout: Math.min(8000, Math.max(1000, left())) }); } catch (_) { /* never idle: go on */ }
  await sleep(1500);
  await page.evaluate(hideBanners).catch(() => 0);
  // The address of the navigation's own answer (after redirects): page.url() carries a #fragment or a pushState path that no response has.
  const finalUrl = response ? response.url() : page.url();
  if (config.url !== finalUrl && !notRecorded.includes(config.url)) notRecorded.push(config.url);

  const height = await page.evaluate(documentHeight);
  const driver = height <= VIEWPORT[1] ? "wheel" : "scroll";
  let timedOut = false;
  if (driver === "scroll") {
    progress("scroll", "one pass down and back");
    const step = Math.round(VIEWPORT[1] * 0.8);
    const reached = [];
    for (let y = step; reached.length < MAX_SCROLL_STOPS; y += step) {
      if (left() <= 0) { timedOut = true; break; }
      await page.evaluate((top) => window.scrollTo({ top, behavior: "instant" }), y).catch(() => {});
      reached.push(y);
      await sleep(200);
      const now = await page.evaluate(documentHeight).catch(() => 0);
      if (y >= now - VIEWPORT[1]) break; // the bottom (the document may have grown on the way)
    }
    for (const y of [...reached].reverse().slice(1).concat([0])) {
      await page.evaluate((top) => window.scrollTo({ top, behavior: "instant" }), y).catch(() => {});
      await sleep(200);
    }
  } else {
    progress("wheel", "forty steps down and back");
    await page.mouse.move(VIEWPORT[0] / 2, VIEWPORT[1] / 2);
    for (const delta of [120, -120]) {
      for (let i = 0; i < 40; i++) {
        if (left() <= 0) { timedOut = true; break; }
        await page.mouse.wheel(0, delta).catch(() => {});
        await sleep(100);
      }
    }
  }
  if (timedOut) addLimit("timeout");
  await sleep(500);
  await page.evaluate(hideBanners).catch(() => 0);

  progress("record", `${entries.length} responses`);
  await Promise.allSettled([...pending]);
  await page.screenshot({ path: path.join(OUT, "thumb.jpg"), type: "jpeg", quality: 60, fullPage: false, animations: "disabled", timeout: 30000 }).catch(() => {});

  progress("write", "index.json");
  const main = entries.find((e) => e.url === finalUrl);
  const index = {
    schema_version: 1, url: config.url, final_url: finalUrl, host: new URL(finalUrl).host, captured_at: new Date().toISOString(),
    viewport: VIEWPORT, document: main && main.file ? main.file : null, entries,
    limits_hit: [...limitCounts.entries()].map(([code, count]) => ({ code, count })),
    not_recorded: notRecorded, driver,
  };
  fs.writeFileSync(path.join(OUT, "index.json"), JSON.stringify(index, null, 2), "utf8");
  const seconds = Math.round(((Date.now() - started) / 1000) * 10) / 10;
  say({ event: "done", result: { out: OUT, final_url: finalUrl, seconds, responses: entries.length, driver, limits: index.limits_hit.map((l) => l.code) } });
  await browser.close().catch(() => {});
  process.exit(0);
} catch (error) {
  await bail(error);
}
