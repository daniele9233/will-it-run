// The browser Will It Run drives for the site cloner: one Chromium (the Chrome or Edge on this
// PC) through Playwright's core library, which the app keeps in its own folder.
//
// It reads one JSON command per line on stdin and answers one JSON line on stdout:
//   {"id": 1, "cmd": "navigate", "args": {...}}  ->  {"id": 1, "ok": true, "result": {...}}
// The commands are the ones a coding agent knows from the Playwright MCP server (navigate,
// resize, screenshot, snapshot, evaluate, click, hover, press, wait, network, console), plus
// "fetch" (download a file the page uses, with the page's own cookies) and "crop" (a slice of a
// tall picture, for a model that sees).
import fs from "node:fs";
import path from "node:path";
import readline from "node:readline";
import { createRequire } from "node:module";

const dir = process.env.WIR_BROWSER_DIR;
if (!dir) {
  process.stdout.write(JSON.stringify({ event: "fatal", error: "WIR_BROWSER_DIR is not set" }) + "\n");
  process.exit(2);
}
const require = createRequire(path.join(dir, "node_modules", "playwright-core", "package.json"));
const { chromium } = require("playwright-core");

const MAX_RESULT = 24000;
const state = { browser: null, context: null, page: null, requests: [], console: [] };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const say = (obj) => process.stdout.write(JSON.stringify(obj) + "\n");
const within = (promise, ms, what) =>
  Promise.race([promise, new Promise((_, bad) => setTimeout(() => bad(new Error(`${what} took longer than ${Math.round(ms / 1000)} s`)), ms))]);

function clip(text, max = MAX_RESULT) {
  const s = typeof text === "string" ? text : JSON.stringify(text, null, 1);
  return s.length > max ? s.slice(0, max) + `\n... [cut: ${s.length - max} more characters]` : s;
}

function watch(page) {
  page.on("console", (msg) => {
    if (state.console.length < 300) state.console.push({ type: msg.type(), text: msg.text().slice(0, 400) });
  });
  page.on("pageerror", (err) => {
    if (state.console.length < 300) state.console.push({ type: "pageerror", text: String(err && err.message ? err.message : err).slice(0, 400) });
  });
  page.on("response", (response) => {
    if (state.requests.length >= 3000) return;
    try {
      const req = response.request();
      state.requests.push({
        url: response.url(), status: response.status(), type: req.resourceType(),
        mime: (response.headers()["content-type"] || "").split(";")[0], bytes: Number(response.headers()["content-length"] || 0) || null,
      });
    } catch (_) {
      // the page went on
    }
  });
}

function pngSize(file) {
  try {
    const head = fs.readFileSync(file).subarray(0, 24);
    return { width: head.readUInt32BE(16), height: head.readUInt32BE(20) };
  } catch (_) {
    return { width: null, height: null };
  }
}

// Scroll the page top to bottom once so lazy images and reveal animations have happened, then back.
async function wakeUp(page) {
  await page.evaluate(async () => {
    const step = Math.max(300, Math.floor(window.innerHeight * 0.8));
    const height = () => Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0);
    for (let y = 0; y < Math.min(height(), 40000); y += step) {
      window.scrollTo(0, y);
      await new Promise((r) => setTimeout(r, 120));
    }
    window.scrollTo(0, 0);
  });
  await sleep(500);
}

const target = (page, a) => {
  if (a.selector) return page.locator(a.selector).first();
  if (a.text) return page.getByText(String(a.text), { exact: false }).first();
  throw new Error("Give a selector or a text to point at.");
};

const commands = {
  async launch(a) {
    const viewport = { width: a.width || 1440, height: a.height || 900 };
    state.browser = await chromium.launch({
      executablePath: a.executable, headless: a.headless !== false,
      args: ["--no-first-run", "--no-default-browser-check", "--disable-blink-features=AutomationControlled"],
    });
    // A headless Chrome says "HeadlessChrome" in its user agent, and bot protection (Akamai and others) answers
    // "403 Access Denied" to it before the site is even sent (seen on brioni.com). It is the same Chrome: it says
    // "Chrome", as the one the person uses does.
    let userAgent;
    if (a.headless !== false) {
      const probe = await state.browser.newContext();
      try {
        userAgent = (await (await probe.newPage()).evaluate(() => navigator.userAgent)).replace("HeadlessChrome", "Chrome");
      } finally {
        await probe.close();
      }
    }
    state.context = await state.browser.newContext({ viewport, deviceScaleFactor: 1, reducedMotion: "no-preference", serviceWorkers: "block", locale: a.locale || undefined, userAgent });
    state.page = await state.context.newPage();
    watch(state.page);
    return { version: state.browser.version() };
  },

  async navigate(a) {
    const page = state.page;
    state.requests = [];
    state.console = [];
    const started = Date.now();
    const response = await page.goto(a.url, { waitUntil: "domcontentloaded", timeout: a.timeout || 45000 });
    try { await page.waitForLoadState("load", { timeout: 30000 }); } catch (_) { /* slow page: go on */ }
    try { await page.waitForLoadState("networkidle", { timeout: 6000 }); } catch (_) { /* never idle: go on */ }
    await sleep(a.settle || 1200);
    const info = await page.evaluate(() => ({
      height: Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0),
      width: window.innerWidth,
    }));
    return { status: response ? response.status() : null, url: page.url(), title: await page.title(), ms: Date.now() - started, ...info };
  },

  async resize(a) {
    await state.page.setViewportSize({ width: a.width, height: a.height });
    await sleep(500);
    return { width: a.width, height: a.height };
  },

  async screenshot(a) {
    const page = state.page;
    fs.mkdirSync(path.dirname(a.path), { recursive: true });
    if (a.fullPage) await wakeUp(page);
    if (a.selector) {
      await page.locator(a.selector).first().screenshot({ path: a.path, timeout: 30000, animations: "allow" });
    } else {
      await page.screenshot({ path: a.path, fullPage: !!a.fullPage, timeout: 30000, animations: "allow" });
    }
    return { path: a.path, ...pngSize(a.path), bytes: fs.statSync(a.path).size };
  },

  async snapshot(a) {
    const text = await state.page.locator(a.selector || "body").first().ariaSnapshot({ timeout: 15000 });
    return { text: clip(text) };
  },

  async evaluate(a) {
    // "() => ..." and "function () {...}" are called; any other text is an expression. Both go through the
    // protocol as text, so a page's content security policy cannot refuse them.
    const text = String(a.expression).trim();
    const isFunction = /^(async\s+)?(function\b|\([^)]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)/.test(text);
    const call = isFunction ? `(${text})()` : `(${text})`;
    // What the script returns goes through `compact` in the page first: a whole getComputedStyle() is about 400
    // properties of which a model needs ten (seen: a 12B asked for it for every element, filling its context), and
    // an element is its tag, id, class and a bit of its text.
    const wrapped = `(async () => {
      const KEEP = ["display","position","top","left","right","bottom","width","height","min-height","max-width","margin","padding","gap","flex-direction","justify-content","align-items","grid-template-columns","font-family","font-size","font-weight","line-height","letter-spacing","text-transform","text-align","color","background-color","background-image","border","border-radius","box-shadow","opacity","transform","transition","overflow","z-index"];
      const compact = (x, depth) => {
        if (x === null || typeof x !== "object") return x;
        if (x instanceof CSSStyleDeclaration) {
          const o = {};
          for (const p of KEEP) { const v = x.getPropertyValue(p); if (v && v !== "none" && v !== "normal" && v !== "auto" && v !== "0px") o[p] = v; }
          return o;
        }
        if (x instanceof Element) return { tag: x.tagName.toLowerCase(), id: x.id || undefined, class: (x.getAttribute("class") || "").slice(0, 80) || undefined, text: (x.textContent || "").trim().slice(0, 80) || undefined };
        if (depth > 8) return String(x);
        if (Array.isArray(x) || x instanceof NodeList) return Array.from(x).slice(0, 200).map((v) => compact(v, depth + 1));
        const o = {};
        for (const k of Object.keys(x).slice(0, 200)) o[k] = compact(x[k], depth + 1);
        return o;
      };
      return compact(await ${call}, 0);
    })()`;
    let value;
    try {
      value = await within(state.page.evaluate(wrapped), 30000, "The script");
    } catch (err) {
      // Several statements are not an expression: run them as they are.
      if (!/SyntaxError|Unexpected token|Unexpected identifier/.test(String(err && err.message))) throw err;
      value = await within(state.page.evaluate(text), 30000, "The script");
    }
    return { value: clip(value === undefined ? "undefined" : value) };
  },

  async click(a) {
    await target(state.page, a).click({ timeout: 8000 });
    await sleep(600);
    return { ok: true, url: state.page.url() };
  },

  async hover(a) {
    await target(state.page, a).hover({ timeout: 8000 });
    await sleep(500);
    return { ok: true };
  },

  async press(a) {
    await state.page.keyboard.press(a.key);
    await sleep(300);
    return { ok: true };
  },

  // Scroll like a visitor: the wheel, in small steps, so smooth-scroll libraries and scroll-linked
  // animations see it. ``to`` is a pixel position, ``by`` a distance, ``selector`` an element.
  async scroll(a) {
    const page = state.page;
    if (a.selector) {
      await page.locator(a.selector).first().scrollIntoViewIfNeeded({ timeout: 8000 });
    } else {
      const now = await page.evaluate(() => window.scrollY);
      const goal = a.to != null ? a.to : now + (a.by || 600);
      const distance = goal - now;
      const steps = Math.max(1, Math.min(30, Math.round(Math.abs(distance) / 120)));
      await page.mouse.move(400, 300);
      for (let i = 0; i < steps; i++) {
        await page.mouse.wheel(0, distance / steps);
        await sleep(35);
      }
    }
    await sleep(a.settle || 700);
    return page.evaluate(() => ({ y: Math.round(window.scrollY), height: Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0) }));
  },

  async wait(a) {
    const page = state.page;
    if (a.selector) await page.locator(a.selector).first().waitFor({ timeout: Math.min(a.ms || 10000, 30000) });
    else if (a.text) await page.getByText(String(a.text), { exact: false }).first().waitFor({ timeout: Math.min(a.ms || 10000, 30000) });
    else await sleep(Math.min(a.ms || 1000, 15000));
    return { ok: true };
  },

  async network(a) {
    const filter = String(a.filter || "").toLowerCase();
    const rows = state.requests.filter((r) => !filter || r.url.toLowerCase().includes(filter) || r.type === filter || r.mime.toLowerCase().includes(filter));
    const byType = {};
    for (const r of state.requests) byType[r.type] = (byType[r.type] || 0) + 1;
    return { total: state.requests.length, by_type: byType, requests: rows.slice(0, a.limit || 150) };
  },

  async console() {
    return { messages: state.console.slice(-100) };
  },

  // A file the page uses, downloaded with the page's own cookies and referer.
  async fetch(a) {
    const response = await state.context.request.get(a.url, { timeout: 60000, headers: { referer: state.page.url() } });
    if (!response.ok()) throw new Error(`${a.url} answered ${response.status()}`);
    const body = await response.body();
    if (body.length > (a.max_bytes || 40_000_000)) throw new Error(`${a.url} is ${body.length} bytes: too large`);
    fs.mkdirSync(path.dirname(a.path), { recursive: true });
    fs.writeFileSync(a.path, body);
    return { path: a.path, bytes: body.length, mime: (response.headers()["content-type"] || "").split(";")[0] };
  },

  // A horizontal slice of a picture, for a model that sees a screenshot taller than it can read.
  async crop(a) {
    const page = await state.context.newPage();
    try {
      const data = "data:image/png;base64," + fs.readFileSync(a.path).toString("base64");
      const b64 = await page.evaluate(async ([url, top, height]) => {
        const img = await new Promise((ok, bad) => { const i = new Image(); i.onload = () => ok(i); i.onerror = bad; i.src = url; });
        const h = Math.max(1, Math.min(height, img.height - top));
        const canvas = new OffscreenCanvas(img.width, h);
        canvas.getContext("2d").drawImage(img, 0, top, img.width, h, 0, 0, img.width, h);
        const blob = await canvas.convertToBlob({ type: "image/png" });
        const bytes = new Uint8Array(await blob.arrayBuffer());
        let bin = "";
        for (let i = 0; i < bytes.length; i += 8192) bin += String.fromCharCode(...bytes.subarray(i, i + 8192));
        return btoa(bin);
      }, [data, a.top || 0, a.height || 1800]);
      fs.mkdirSync(path.dirname(a.out), { recursive: true });
      fs.writeFileSync(a.out, Buffer.from(b64, "base64"));
      return { out: a.out, ...pngSize(a.out) };
    } finally {
      await page.close();
    }
  },

  async close() {
    try { if (state.context) await state.context.close(); } catch (_) { /* already gone */ }
    try { if (state.browser) await state.browser.close(); } catch (_) { /* already gone */ }
    state.browser = state.context = state.page = null;
    return { ok: true };
  },
};

const rl = readline.createInterface({ input: process.stdin });
let queue = Promise.resolve();
rl.on("line", (line) => {
  let msg;
  try {
    msg = JSON.parse(line);
  } catch (_) {
    return;
  }
  queue = queue.then(async () => {
    const fn = commands[msg.cmd];
    if (!fn) return say({ id: msg.id, ok: false, error: `unknown command ${msg.cmd}` });
    try {
      say({ id: msg.id, ok: true, result: await fn(msg.args || {}) });
    } catch (err) {
      say({ id: msg.id, ok: false, error: String(err && err.message ? err.message : err).split("\n")[0].slice(0, 600) });
    }
    if (msg.cmd === "close") setTimeout(() => process.exit(0), 50);
  });
});
rl.on("close", async () => {
  try { if (state.context) await state.context.close(); } catch (_) { /* */ }
  try { if (state.browser) await state.browser.close(); } catch (_) { /* */ }
  process.exit(0);
});
say({ event: "ready" });
