// What code can say about a picture, and the pieces of it, before a model looks at it.
//
//   node picture.mjs <config.json>        (env WIR_BROWSER_DIR: where playwright-core is installed)
//
//   {"cmd": "analyze", "image": "ref.png", "out": "dir"}
//       -> dir/analysis.json: size, the colours the picture is made of, and the horizontal bands where it changes
//   {"cmd": "crops", "image": "ref.png", "regions": [{"id": 1, "x": 0, "y": 0, "w": 400, "h": 300}], "out": "dir", "previewSide": 768}
//       -> dir/region-<id>.png (full size) and dir/region-<id>.jpg (longest side at most previewSide), for a model that sees
//
// The picture is decoded in the browser (a canvas), so no image library is needed. stdout is JSON lines like the other scripts.
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const say = (obj) => process.stdout.write(JSON.stringify(obj) + "\n");
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

const MIME = { ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif" };

// In the page. ``bytes`` is the picture as base64.
const analyze = async ({ bytes, mime }) => {
  const bitmap = await createImageBitmap(new Blob([Uint8Array.from(atob(bytes), (c) => c.charCodeAt(0))], { type: mime }));
  const { width, height } = bitmap;
  const canvas = new OffscreenCanvas(width, height);
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(bitmap, 0, 0);
  const data = ctx.getImageData(0, 0, width, height).data;
  // The colours: every third pixel, 4 bits a channel, the most common bins with their real mean.
  const bins = new Map();
  for (let y = 0; y < height; y += 3) {
    for (let x = 0; x < width; x += 3) {
      const i = (y * width + x) * 4;
      const key = ((data[i] >> 4) << 8) | ((data[i + 1] >> 4) << 4) | (data[i + 2] >> 4);
      const bin = bins.get(key) || { n: 0, r: 0, g: 0, b: 0 };
      bin.n++; bin.r += data[i]; bin.g += data[i + 1]; bin.b += data[i + 2];
      bins.set(key, bin);
    }
  }
  const total = [...bins.values()].reduce((s, b) => s + b.n, 0);
  const hex = (b) => "#" + [b.r, b.g, b.b].map((v) => Math.round(v / b.n).toString(16).padStart(2, "0")).join("");
  const palette = [];
  for (const bin of [...bins.values()].sort((a, b) => b.n - a.n)) {
    const mean = [bin.r / bin.n, bin.g / bin.n, bin.b / bin.n];
    // A colour too close to one already listed is the same colour.
    if (palette.some((p) => Math.abs(p.rgb[0] - mean[0]) + Math.abs(p.rgb[1] - mean[1]) + Math.abs(p.rgb[2] - mean[2]) < 60)) continue;
    palette.push({ hex: hex(bin), share: +(bin.n / total).toFixed(3), rgb: mean });
    if (palette.length === 8) break;
  }
  // The bands: where the mean colour of a row differs from the rows above it.
  const rows = [];
  for (let y = 0; y < height; y++) {
    let r = 0, g = 0, b = 0, n = 0;
    for (let x = 0; x < width; x += 4) {
      const i = (y * width + x) * 4;
      r += data[i]; g += data[i + 1]; b += data[i + 2]; n++;
    }
    rows.push([r / n, g / n, b / n]);
  }
  const edges = [0];
  const minGap = Math.max(24, Math.round(height * 0.06));
  for (let y = 8; y < height - 8; y++) {
    const before = [0, 1, 2].map((c) => rows.slice(y - 8, y).reduce((s, row) => s + row[c], 0) / 8);
    const after = [0, 1, 2].map((c) => rows.slice(y, y + 8).reduce((s, row) => s + row[c], 0) / 8);
    const change = Math.abs(before[0] - after[0]) + Math.abs(before[1] - after[1]) + Math.abs(before[2] - after[2]);
    if (change > 40 && y - edges[edges.length - 1] >= minGap) edges.push(y);
  }
  edges.push(height);
  const bands = [];
  for (let i = 0; i + 1 < edges.length; i++) {
    const a = edges[i], b = edges[i + 1];
    const mid = rows[Math.floor((a + b) / 2)];
    bands.push({ y: a, h: b - a, mean: "#" + mid.map((v) => Math.round(v).toString(16).padStart(2, "0")).join("") });
  }
  return { width, height, palette: palette.map(({ hex: h, share }) => ({ hex: h, share })), bands };
};

const crops = async ({ bytes, mime, regions, previewSide }) => {
  const bitmap = await createImageBitmap(new Blob([Uint8Array.from(atob(bytes), (c) => c.charCodeAt(0))], { type: mime }));
  const encode = async (blob) => {
    const buffer = new Uint8Array(await blob.arrayBuffer());
    let binary = "";
    for (let i = 0; i < buffer.length; i += 32768) binary += String.fromCharCode(...buffer.subarray(i, i + 32768));
    return btoa(binary);
  };
  const out = [];
  for (const r of regions) {
    const x = Math.max(0, Math.min(bitmap.width - 1, Math.round(r.x)));
    const y = Math.max(0, Math.min(bitmap.height - 1, Math.round(r.y)));
    const w = Math.max(1, Math.min(bitmap.width - x, Math.round(r.w)));
    const h = Math.max(1, Math.min(bitmap.height - y, Math.round(r.h)));
    const full = new OffscreenCanvas(w, h);
    full.getContext("2d").drawImage(bitmap, x, y, w, h, 0, 0, w, h);
    const scale = Math.min(1, previewSide / Math.max(w, h));
    const small = new OffscreenCanvas(Math.max(1, Math.round(w * scale)), Math.max(1, Math.round(h * scale)));
    small.getContext("2d").drawImage(full, 0, 0, small.width, small.height);
    out.push({
      id: r.id, x, y, w, h, png: await encode(await full.convertToBlob({ type: "image/png" })),
      jpg: await encode(await small.convertToBlob({ type: "image/jpeg", quality: 0.85 })),
    });
  }
  return out;
};

let browser;
try {
  const executable = config.executable;
  browser = await chromium.launch({ executablePath: executable, headless: true });
  const page = await browser.newPage();
  await page.setContent("<html><body></body></html>");
  const file = path.resolve(config.image);
  const mime = MIME[path.extname(file).toLowerCase()];
  if (!mime) fail("The picture must be a PNG, JPEG, WebP or GIF file.");
  const bytes = fs.readFileSync(file).toString("base64");
  if (config.cmd === "analyze") {
    const result = await page.evaluate(analyze, { bytes, mime });
    fs.writeFileSync(path.join(OUT, "analysis.json"), JSON.stringify(result, null, 1));
  } else if (config.cmd === "crops") {
    const pieces = await page.evaluate(crops, { bytes, mime, regions: config.regions || [], previewSide: config.previewSide || 768 });
    const written = [];
    for (const piece of pieces) {
      fs.writeFileSync(path.join(OUT, `region-${piece.id}.png`), Buffer.from(piece.png, "base64"));
      fs.writeFileSync(path.join(OUT, `region-${piece.id}.jpg`), Buffer.from(piece.jpg, "base64"));
      written.push({ id: piece.id, x: piece.x, y: piece.y, w: piece.w, h: piece.h });
    }
    fs.writeFileSync(path.join(OUT, "crops.json"), JSON.stringify(written, null, 1));
  } else {
    fail(`unknown command ${config.cmd}`);
  }
  say({ event: "done" });
} catch (error) {
  fail(error);
} finally {
  if (browser) await browser.close().catch(() => {});
}
