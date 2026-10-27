// The measure step of a site clone: how much does the clone look like the original?
//
//   node measure.mjs <config.json>        (env WIR_BROWSER_DIR: where playwright-core is installed)
//
// It opens the clone (a URL the app serves) at each viewport the original was captured at, takes the same full-page
// screenshot, and compares it with the reference picture, pixel by pixel and structure by structure, over the whole page
// and over each section. No model judges: the numbers come from the two pictures. What they mean, in short:
//
//   pixels   the share of pixels whose colour is within a small tolerance of the original's
//   ssim     structural similarity (8x8 blocks of the grey picture): is the same thing drawn in the same place?
//   visual   the mean of the two: the headline of a viewport
//   layout   how close the page's and each section's height is to the original's
//   text     the words of the original that the clone shows too (F1 over the multiset of words)
//
// stdout is JSON lines: {"event":"progress",...}, then {"event":"done","result":{...}} or {"event":"fatal","error":"..."}.
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
const engine = process.env.WIR_BROWSER_DIR;
if (!engine) fail("WIR_BROWSER_DIR is not set");
const require = createRequire(path.join(engine, "node_modules", "playwright-core", "package.json"));
const { chromium } = require("playwright-core");
const OUT = path.resolve(config.out);
fs.mkdirSync(OUT, { recursive: true });

// -- in the page: the comparison ---------------------------------------------------------------------------------

// Runs in a blank page. Both pictures arrive as base64 PNG. ``regions`` are [{id, ref: {x,y,w,h}, clone: {x,y,w,h}}].
const compare = async ({ refB64, cloneB64, regions, tolerance }) => {
  const decode = async (b64) => {
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    return createImageBitmap(new Blob([bytes], { type: "image/png" }));
  };
  const a = await decode(refB64);
  const b = await decode(cloneB64);
  const W = Math.max(a.width, b.width);
  const H = Math.max(a.height, b.height);
  // Where one picture is shorter, the other is compared with a colour no page has: a missing piece counts as wrong.
  const read = (bitmap) => {
    const canvas = new OffscreenCanvas(W, H);
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    ctx.fillStyle = "#ff00ff";
    ctx.fillRect(0, 0, W, H);
    ctx.drawImage(bitmap, 0, 0);
    return ctx.getImageData(0, 0, W, H).data;
  };
  const A = read(a);
  const B = read(b);
  const C1 = (0.01 * 255) ** 2;
  const C2 = (0.03 * 255) ** 2;
  const luma = (d, i) => 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
  // ``ra`` and ``rb`` are the same-sized rectangles of the two pictures to compare.
  const score = (ra, rb) => {
    const w = Math.max(0, Math.min(ra.w, rb.w, W - ra.x, W - rb.x));
    const h = Math.max(0, Math.min(ra.h, rb.h));
    if (w < 8 || h < 8) return { pixels: 0, ssim: 0, visual: 0, area: 0 };
    let same = 0;
    for (let y = 0; y < h; y++) {
      const ya = ra.y + y;
      const yb = rb.y + y;
      if (ya >= H || yb >= H) continue;
      let ia = (ya * W + ra.x) * 4;
      let ib = (yb * W + rb.x) * 4;
      for (let x = 0; x < w; x++, ia += 4, ib += 4) {
        const d = Math.max(Math.abs(A[ia] - B[ib]), Math.abs(A[ia + 1] - B[ib + 1]), Math.abs(A[ia + 2] - B[ib + 2]));
        if (d <= tolerance) same++;
      }
    }
    let total = 0;
    let blocks = 0;
    for (let by = 0; by + 8 <= h; by += 8) {
      for (let bx = 0; bx + 8 <= w; bx += 8) {
        let sa = 0, sb = 0, saa = 0, sbb = 0, sab = 0;
        for (let y = 0; y < 8; y++) {
          const ya = ra.y + by + y;
          const yb = rb.y + by + y;
          if (ya >= H || yb >= H) continue;
          let ia = (ya * W + ra.x + bx) * 4;
          let ib = (yb * W + rb.x + bx) * 4;
          for (let x = 0; x < 8; x++, ia += 4, ib += 4) {
            const la = luma(A, ia);
            const lb = luma(B, ib);
            sa += la; sb += lb; saa += la * la; sbb += lb * lb; sab += la * lb;
          }
        }
        const n = 64;
        const ma = sa / n, mb = sb / n;
        const va = saa / n - ma * ma, vb = sbb / n - mb * mb, cov = sab / n - ma * mb;
        total += ((2 * ma * mb + C1) * (2 * cov + C2)) / ((ma * ma + mb * mb + C1) * (va + vb + C2));
        blocks++;
      }
    }
    const pixels = same / (w * h);
    const ssim = blocks ? Math.max(0, total / blocks) : 0;
    return { pixels, ssim, visual: (pixels + ssim) / 2, area: w * h };
  };
  const whole = score({ x: 0, y: 0, w: W, h: H }, { x: 0, y: 0, w: W, h: H });
  const parts = regions.map((r) => ({ id: r.id, ...score(r.ref, r.clone) }));
  // The picture of where they differ: the original dimmed, wrong pixels in red, at half size.
  const scale = 2;
  const dw = Math.ceil(W / scale);
  const dh = Math.ceil(H / scale);
  const canvas = new OffscreenCanvas(dw, dh);
  const ctx = canvas.getContext("2d");
  const out = ctx.createImageData(dw, dh);
  for (let y = 0; y < dh; y++) {
    for (let x = 0; x < dw; x++) {
      const i = ((y * scale) * W + x * scale) * 4;
      const d = Math.max(Math.abs(A[i] - B[i]), Math.abs(A[i + 1] - B[i + 1]), Math.abs(A[i + 2] - B[i + 2]));
      const o = (y * dw + x) * 4;
      if (d <= tolerance) {
        const g = 255 - (255 - luma(A, i)) * 0.25;
        out.data[o] = out.data[o + 1] = out.data[o + 2] = g;
      } else {
        out.data[o] = 230; out.data[o + 1] = 30; out.data[o + 2] = 30;
      }
      out.data[o + 3] = 255;
    }
  }
  ctx.putImageData(out, 0, 0);
  const encode = async (blob) => {
    const buffer = new Uint8Array(await blob.arrayBuffer());
    let binary = "";
    for (let i = 0; i < buffer.length; i += 32768) binary += String.fromCharCode(...buffer.subarray(i, i + 32768));
    return btoa(binary);
  };
  // The two pictures at half size, as JPEG, for the page that shows them (the full ones are several MB each).
  const half = async (bitmap) => {
    const small = new OffscreenCanvas(dw, Math.ceil(bitmap.height / scale));
    small.getContext("2d").drawImage(bitmap, 0, 0, small.width, small.height);
    return encode(await small.convertToBlob({ type: "image/jpeg", quality: 0.72 }));
  };
  return {
    whole, parts, size: { ref: [a.width, a.height], clone: [b.width, b.height] }, diff: await encode(await canvas.convertToBlob({ type: "image/png" })),
    refPreview: await half(a), clonePreview: await half(b),
  };
};

const cloneBoxes = () => {
  const out = {};
  for (const el of document.querySelectorAll("[data-clone-section]")) {
    const r = el.getBoundingClientRect();
    let bottom = r.bottom;
    for (const c of el.children) {
      const cs = getComputedStyle(c);
      if (cs.display !== "none" && cs.position !== "fixed" && cs.position !== "absolute") bottom = Math.max(bottom, c.getBoundingClientRect().bottom);
    }
    const key = el.getAttribute("data-clone-section");
    // A section that has a variant for phones is in the page twice, and one of them is not displayed: the one shown counts.
    if (out[key] && (r.width === 0 || r.height === 0)) continue;
    out[key] = { x: Math.round(r.left + scrollX), y: Math.round(r.top + scrollY), w: Math.round(r.width), h: Math.round(Math.max(r.height, bottom - r.top)) };
  }
  return out;
};

const words = (text) => (String(text || "").toLowerCase().match(/[\p{L}\p{N}]+/gu) || []);
const textScore = (reference, shown) => {
  const count = (list) => list.reduce((m, w) => m.set(w, (m.get(w) || 0) + 1), new Map());
  const a = count(words(reference));
  const b = count(words(shown));
  let common = 0;
  for (const [w, n] of a) common += Math.min(n, b.get(w) || 0);
  const wa = [...a.values()].reduce((x, y) => x + y, 0);
  const wb = [...b.values()].reduce((x, y) => x + y, 0);
  if (!wa) return null;
  const precision = wb ? common / wb : 0;
  const recall = common / wa;
  return precision + recall ? (2 * precision * recall) / (precision + recall) : 0;
};

// -- the run -------------------------------------------------------------------------------------------------------

let browser;
try {
  progress("browser", "starting Chrome");
  browser = await chromium.launch({
    executablePath: config.executable, headless: config.headless !== false,
    args: ["--no-first-run", "--no-default-browser-check", "--disable-blink-features=AutomationControlled", "--hide-scrollbars"],
  });
  const probe = await browser.newContext();
  const userAgent = (await (await probe.newPage()).evaluate(() => navigator.userAgent)).replace("HeadlessChrome", "Chrome");
  await probe.close();
  const tolerance = config.tolerance || 16;
  const report = { url: config.url, tolerance, viewports: {}, formula: "visual = (pixels + ssim) / 2; accuracy = mean of visual over the viewports" };
  const judge = await browser.newPage();
  await judge.setContent("<html><body></body></html>");

  for (const vp of config.viewports) {
    progress("viewport", `${vp.name} ${vp.width}x${vp.height}`);
    const context = await browser.newContext({ viewport: { width: vp.width, height: vp.height }, deviceScaleFactor: 1, locale: config.locale || undefined, userAgent, serviceWorkers: "block" });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (e) => errors.length < 20 && errors.push(String(e.message || e).slice(0, 200)));
    page.on("console", (m) => m.type() === "error" && errors.length < 20 && errors.push(m.text().slice(0, 200)));
    const failed = [];
    page.on("requestfailed", (r) => failed.length < 30 && failed.push(r.url().slice(0, 160)));
    page.on("response", (r) => r.status() >= 400 && failed.length < 30 && failed.push(`${r.status()} ${r.url().slice(0, 160)}`));
    await page.goto(config.url, { waitUntil: "load", timeout: 90000 });
    try { await page.waitForLoadState("networkidle", { timeout: 8000 }); } catch (_) { /* go on */ }
    await page.evaluate(async () => {
      const step = Math.max(300, Math.floor(window.innerHeight * 0.8));
      const height = () => Math.max(document.documentElement.scrollHeight, document.body.scrollHeight);
      for (let y = 0, n = 0; y < Math.min(height(), 60000) && n < 80; y += step, n++) {
        window.scrollTo(0, y);
        await new Promise((r) => setTimeout(r, 100));
      }
      window.scrollTo(0, 0);
    });
    try { await page.evaluate(() => document.fonts && document.fonts.ready); } catch (_) { /* nothing to wait for */ }
    // A page with an intro animation is measured when the intro is over: the caller says how long that takes.
    await sleep(config.settleMs ?? 800);
    const shot = path.join(OUT, `clone-${vp.name}.png`);
    await page.screenshot({ path: shot, fullPage: true, animations: "disabled", timeout: 90000 });
    const boxes = await page.evaluate(cloneBoxes);
    const shown = await page.evaluate(() => (document.body.innerText || "").replace(/\s+/g, " ").trim());
    const pageHeight = await page.evaluate(() => Math.max(document.documentElement.scrollHeight, document.body.scrollHeight));
    await context.close();

    const referenceFile = path.resolve(config.reference[vp.name]);
    const refBoxes = (config.boxes && config.boxes[vp.name]) || {};
    const refHeight = (config.pageHeights && config.pageHeights[vp.name]) || 0;
    const regions = [];
    for (const section of config.sections || []) {
      const r = refBoxes[String(section.id)];
      const c = boxes[String(section.id)];
      // A section below the end of the page cannot be seen (a panel that slides in): it is not scored.
      if (!r || !c || r.h < 8 || (refHeight && r.y >= refHeight - 8)) continue;
      regions.push({ id: section.id, ref: r, clone: { x: c.x, y: c.y, w: c.w, h: c.h } });
    }
    progress("compare", `${vp.name}: ${regions.length} sections`);
    const result = await judge.evaluate(compare, {
      refB64: fs.readFileSync(referenceFile).toString("base64"), cloneB64: fs.readFileSync(shot).toString("base64"), regions, tolerance,
    });
    fs.writeFileSync(path.join(OUT, `diff-${vp.name}.png`), Buffer.from(result.diff, "base64"));
    fs.writeFileSync(path.join(OUT, `original-${vp.name}.jpg`), Buffer.from(result.refPreview, "base64"));
    fs.writeFileSync(path.join(OUT, `clone-${vp.name}.jpg`), Buffer.from(result.clonePreview, "base64"));
    const heightRatio = (x, y) => (x && y ? Math.min(x, y) / Math.max(x, y) : 0);
    const sections = result.parts.map((p) => {
      const region = regions.find((r) => r.id === p.id);
      const meta = (config.sections || []).find((s) => s.id === p.id) || {};
      return {
        id: p.id, name: meta.name || String(p.id), pixels: +p.pixels.toFixed(4), ssim: +p.ssim.toFixed(4), visual: +p.visual.toFixed(4),
        refHeight: region.ref.h, cloneHeight: region.clone.h, layout: +heightRatio(region.ref.h, region.clone.h).toFixed(4),
      };
    });
    report.viewports[vp.name] = {
      width: vp.width, referenceHeight: result.size.ref[1], cloneHeight: result.size.clone[1],
      pixels: +result.whole.pixels.toFixed(4), ssim: +result.whole.ssim.toFixed(4), visual: +result.whole.visual.toFixed(4),
      layout: +heightRatio(result.size.ref[1], result.size.clone[1]).toFixed(4),
      text: vp.name === config.textViewport || config.textViewport === undefined ? (() => { const s = textScore(config.referenceText, shown); return s === null ? null : +s.toFixed(4); })() : null,
      sections, errors, failed, screenshot: path.basename(shot), diff: `diff-${vp.name}.png`, original: `original-${vp.name}.jpg`, preview: `clone-${vp.name}.jpg`,
    };
  }
  const visuals = Object.values(report.viewports).map((v) => v.visual);
  report.accuracy = visuals.length ? +(visuals.reduce((x, y) => x + y, 0) / visuals.length).toFixed(4) : null;
  fs.writeFileSync(path.join(OUT, "report.json"), JSON.stringify(report, null, 1));
  say({ event: "done", result: report });
} catch (error) {
  fail(error);
} finally {
  if (browser) await browser.close().catch(() => {});
}
