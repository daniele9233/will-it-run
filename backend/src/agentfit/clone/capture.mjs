// The capture step of a site clone: what a visitor's Chrome built, frozen into a folder.
//
//   node capture.mjs <config.json>        (env WIR_BROWSER_DIR: where playwright-core is installed)
//
// It opens the page like a visitor (the Chrome or Edge on this PC), lets it settle, scrolls it so lazy content
// loads, removes cookie banners and pop-ups, takes the reference screenshots, and freezes what the browser made:
// the rendered DOM (shadow DOM flattened), every style rule, every image and font the page used. Nothing here
// asks a model anything: the fidelity of a clone comes from copying what is there, not from describing it.
//
// stdout is JSON lines: {"event":"progress","step":"...","detail":"..."}, then {"event":"done","result":{...}}
// or {"event":"fatal","error":"..."}. The folder it writes is read by ``replica.py``.
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
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
const ASSETS = path.join(OUT, "assets");
const MAX_ASSET = (config.maxAssetMB || 20) * 1024 * 1024;
const VIEWPORTS = config.viewports && config.viewports.length ? config.viewports : [{ name: "desktop", width: 1440, height: 900 }];
fs.mkdirSync(ASSETS, { recursive: true });

// -- what the page loads -------------------------------------------------------------------------------------

const EXT = {
  "image/webp": ".webp", "image/avif": ".avif", "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/svg+xml": ".svg",
  "image/x-icon": ".ico", "image/vnd.microsoft.icon": ".ico", "font/woff2": ".woff2", "application/font-woff2": ".woff2", "font/woff": ".woff",
  "application/font-woff": ".woff", "font/ttf": ".ttf", "font/otf": ".otf", "application/x-font-ttf": ".ttf", "application/vnd.ms-fontobject": ".eot",
  "video/mp4": ".mp4", "video/webm": ".webm", "audio/mpeg": ".mp3", "text/css": ".css", "application/json": ".json",
};

function assetName(url, mime) {
  const parsed = new URL(url);
  // Next.js serves pictures as /_next/image?url=<the real path>&w=...: name the file after the real one.
  let base = path.posix.basename(parsed.searchParams.get("url") ? decodeURIComponent(parsed.searchParams.get("url")) : parsed.pathname) || "file";
  base = base.replace(/\.[a-z0-9]{1,5}$/i, "").replace(/[^a-z0-9._-]+/gi, "-").replace(/^-+|-+$/g, "").slice(0, 40) || "file";
  let ext = EXT[(mime || "").toLowerCase()] || path.posix.extname(parsed.pathname).toLowerCase();
  if (!/^\.[a-z0-9]{1,5}$/.test(ext || "")) ext = ".bin";
  const hash = crypto.createHash("sha1").update(url).digest("hex").slice(0, 8);
  return `${hash}-${base}${ext}`;
}

const assets = new Map(); // absolute url -> { file, mime, bytes }
const pending = new Set();

function record(response) {
  const job = (async () => {
    try {
      const req = response.request();
      const type = req.resourceType();
      const status = response.status();
      if (status < 200 || status >= 300 || !["image", "font", "media", "stylesheet", "other"].includes(type)) return;
      const url = response.url();
      if (!/^https?:/.test(url) || assets.has(url)) return;
      const headers = response.headers();
      const mime = (headers["content-type"] || "").split(";")[0].trim();
      const length = Number(headers["content-length"] || 0);
      if (length > MAX_ASSET || type === "stylesheet") return; // styles are read from the page, not copied
      const body = await response.body();
      if (!body || !body.length || body.length > MAX_ASSET) return;
      const file = assetName(url, mime);
      fs.writeFileSync(path.join(ASSETS, file), body);
      assets.set(url, { file, mime, bytes: body.length });
    } catch (_) {
      // evicted, redirected or streamed: fetched again later if the page needs it
    }
  })();
  pending.add(job);
  job.finally(() => pending.delete(job));
}

// -- in the page ---------------------------------------------------------------------------------------------

// Pop-ups and banners: they are not part of the site, and they hide it. They are hidden, never removed: a page built with
// React that finds a node of its own gone throws, and its error page replaces the site (seen on brioni.com when the
// newsletter dialog was removed). Returns what was hidden.
const sweepOverlays = (extra) => {
  const removed = [];
  if (!window.__wirHide) {
    const sheet = new CSSStyleSheet();
    sheet.replaceSync("[data-wir-hidden]{display:none!important}");
    document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
    window.__wirHide = sheet;
  }
  const label = (el) => (el.id ? "#" + el.id : el.tagName.toLowerCase() + (typeof el.className === "string" && el.className.trim() ? "." + el.className.trim().split(/\s+/)[0] : ""));
  const textOf = (el) => (el.innerText || "").trim().replace(/\s+/g, " ").slice(0, 80);
  const drop = (el, kind) => {
    if (!el.isConnected || el.hasAttribute("data-wir-hidden")) return;
    removed.push({ kind, el: label(el), text: textOf(el) });
    el.setAttribute("data-wir-hidden", "1");
  };
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const CONSENT = [
    "#onetrust-consent-sdk", "#CybotCookiebotDialog", "#CybotCookiebotDialogBodyUnderlay", "#usercentrics-root", "#usercentrics-cmp-ui", "#iubenda-cs-banner",
    "#didomi-host", ".didomi-popup-container", "#cookie-law-info-bar", "#cmpbox", "#cmpwrapper", ".qc-cmp2-container", "#truste-consent-track",
    "[id^=sp_message_container]", ".cc-window", "#cookie-notice", ".cookie-notice", "#hs-eu-cookie-confirmation", ".grecaptcha-badge", "#CookieBanner",
    ...(extra || []),
  ];
  for (const selector of CONSENT) {
    try {
      for (const el of document.querySelectorAll(selector)) drop(el, "consent");
    } catch (_) { /* a selector this page does not like */ }
  }
  const covers = (r) => Math.min(r.right, vw) - Math.max(r.left, 0) > 0 && Math.min(r.bottom, vh) - Math.max(r.top, 0) > 0;
  // Dialogs that are open now. The root of a dialog is often a box of no size whose children are fixed: look at what it shows.
  for (const el of document.querySelectorAll("[role=dialog], [role=alertdialog], [aria-modal=true], dialog[open]")) {
    if (!el.isConnected) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === "hidden" || cs.display === "none") continue;
    const shown = [el, ...el.querySelectorAll("*")].some((d) => {
      const r = d.getBoundingClientRect();
      return r.width > 40 && r.height > 40 && covers(r) && getComputedStyle(d).visibility !== "hidden";
    });
    if (shown) drop(el, "dialog");
  }
  // Fixed layers over the page that are not its header or navigation: backdrops, banners, pop-ups.
  const WORDS = /cookie|consent|newsletter|iscriviti|subscribe|privacy|gdpr|popup|pop-up|modal|overlay|backdrop|promo-banner/i;
  for (const el of document.body.querySelectorAll("*")) {
    if (!el.isConnected || el.closest("header, nav, footer")) continue;
    const cs = getComputedStyle(el);
    if (cs.position !== "fixed" || cs.visibility === "hidden" || cs.display === "none" || Number(cs.opacity) < 0.05) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 10 || r.height < 10 || !covers(r)) continue;
    const area = (Math.min(r.right, vw) - Math.max(r.left, 0)) * (Math.min(r.bottom, vh) - Math.max(r.top, 0));
    const wordy = WORDS.test(`${el.id} ${typeof el.className === "string" ? el.className : ""}`);
    const backdrop = area >= 0.9 * vw * vh && !el.querySelector("img, video, picture") && (el.innerText || "").trim().length < 20;
    if (wordy || backdrop || (area >= 0.25 * vw * vh && Number(cs.zIndex) >= 100 && !el.querySelector("header, nav"))) drop(el, backdrop ? "backdrop" : "fixed");
  }
  if (removed.length) {
    for (const el of [document.documentElement, document.body]) {
      for (const prop of ["overflow", "padding-right", "margin-right", "position", "top", "height", "touch-action"]) {
        if (prop === "position" && el.style.position !== "fixed") continue;
        el.style.removeProperty(prop);
      }
      el.classList.remove("modal-open", "no-scroll", "noscroll", "overflow-hidden", "scroll-lock", "is-locked", "ReactModal__Body--open");
    }
  }
  for (const el of document.querySelectorAll("[inert]")) el.removeAttribute("inert");
  return removed;
};

// The elements the page is cut into: header, each block of the main content, footer. Stamps them (data-wir-sec).
const stampSections = (limit) => {
  const SKIP = new Set(["SCRIPT", "STYLE", "LINK", "META", "NOSCRIPT", "TEMPLATE", "BASE", "HEAD"]);
  const kids = (el) => [...el.children].filter((c) => !SKIP.has(c.tagName));
  // The height counts what overflows the box too: a header of a fixed height whose children are taller (the in-flow children only).
  const box = (el) => {
    const r = el.getBoundingClientRect();
    let bottom = r.bottom;
    for (const c of el.children) {
      const cs = getComputedStyle(c);
      if (cs.display !== "none" && cs.position !== "fixed" && cs.position !== "absolute") bottom = Math.max(bottom, c.getBoundingClientRect().bottom);
    }
    return { x: Math.round(r.left + scrollX), y: Math.round(r.top + scrollY), w: Math.round(r.width), h: Math.round(Math.max(r.height, bottom - r.top)) };
  };
  const inFlow = (el) => {
    const cs = getComputedStyle(el);
    return cs.display !== "none" && cs.position !== "fixed" && cs.position !== "absolute";
  };
  const big = (el) => {
    const b = box(el);
    return b.w >= 0.3 * window.innerWidth && inFlow(el) && (b.h >= 40 || ["HEADER", "FOOTER"].includes(el.tagName));
  };
  let root = document.body;
  for (let i = 0; i < 6; i++) {
    const flow = kids(root).filter((c) => big(c) || (inFlow(c) && box(c).h > 0));
    if (flow.length === 1 && !["MAIN", "HEADER", "FOOTER"].includes(flow[0].tagName)) root = flow[0];
    else break;
  }
  const picked = [];
  const visit = (parent, depth) => {
    for (const el of kids(parent)) {
      if (!big(el)) continue;
      const inner = kids(el).filter(big);
      const dive = depth < 3 && picked.length < limit && (el.tagName === "MAIN" || el.getAttribute("role") === "main" || (inner.length >= 3 && box(el).h > 1.2 * window.innerHeight && !["HEADER", "FOOTER", "NAV"].includes(el.tagName)));
      if (dive) visit(el, depth + 1);
      else picked.push(el);
    }
  };
  visit(root, 0);
  return picked.map((el, i) => {
    el.setAttribute("data-wir-sec", String(i + 1));
    const heading = el.querySelector("h1, h2, h3, h4, [role=heading]");
    const alt = el.querySelector("img[alt]:not([alt=''])");
    const klass = typeof el.className === "string" ? el.className.trim().split(/\s+/)[0] || "" : "";
    return {
      id: i + 1, tag: el.tagName.toLowerCase(), domId: el.id || "", className: klass,
      heading: ((heading && heading.innerText) || (alt && alt.getAttribute("alt")) || el.getAttribute("aria-label") || "").trim().replace(/\s+/g, " ").slice(0, 80),
      text: (el.innerText || "").trim().replace(/\s+/g, " ").slice(0, 200), images: el.querySelectorAll("img, picture, video, svg").length, ...box(el),
    };
  });
};

const sectionBoxes = () => {
  const out = {};
  for (const el of document.querySelectorAll("[data-wir-sec]")) {
    const r = el.getBoundingClientRect();
    let bottom = r.bottom;
    for (const c of el.children) {
      const cs = getComputedStyle(c);
      if (cs.display !== "none" && cs.position !== "fixed" && cs.position !== "absolute") bottom = Math.max(bottom, c.getBoundingClientRect().bottom);
    }
    out[el.getAttribute("data-wir-sec")] = { x: Math.round(r.left + scrollX), y: Math.round(r.top + scrollY), w: Math.round(r.width), h: Math.round(Math.max(r.height, bottom - r.top)) };
  }
  return out;
};

// The page as static markup: every element the visitor could see, with its state, in a form the builder reads without a
// real HTML parser (double-quoted attributes, every element closed, text escaped). Sections are cut out as slots.
const freezePage = (frameStart) => {
  const urls = new Set();
  const customTags = new Set();
  const shadowCss = new Map(); // class -> css text
  const warnings = [];
  const frames = [];
  const VOID = new Set(["area", "base", "br", "col", "embed", "hr", "img", "input", "meta", "param", "source", "track", "wbr"]);
  const DROP = new Set(["script", "noscript", "template", "style", "link", "meta", "base", "title", "head", "object", "embed", "audio", "next-route-announcer", "nextjs-portal"]);
  const absolute = (value, base) => {
    try {
      return new URL(value, base || document.baseURI).href;
    } catch (_) {
      return value;
    }
  };
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;");
  const escAttr = (s) => s.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
  const styleCache = new WeakMap();
  const styleOf = (el) => {
    let cs = styleCache.get(el);
    if (!cs) {
      cs = getComputedStyle(el);
      styleCache.set(el, cs);
    }
    return cs;
  };
  const blockish = (el) => {
    const d = styleOf(el).display;
    return d === "block" || d === "flex" || d === "grid" || d === "table" || d === "list-item" || d === "none" || d === "flow-root" || d.startsWith("table");
  };
  const preserves = (el) => {
    for (let p = el; p && p.nodeType === 1; p = p.parentElement) {
      const t = p.localName;
      if (t === "pre" || t === "textarea" || t === "listing") return true;
      if (/^pre/.test(styleOf(p).whiteSpace)) return true;
    }
    return false;
  };
  // url(...) in a style attribute: absolute, and noted.
  const fixStyle = (value) =>
    value.replace(/url\(\s*(['"]?)([^'")]+)\1\s*\)/g, (whole, _q, u) => {
      if (/^data:|^#/.test(u)) return whole;
      const a = absolute(u);
      urls.add(a);
      return `url("${a}")`;
    });
  const KEEP_DATA = 300;
  const attributes = (el, tag, flat) => {
    const out = [];
    const has = (n) => el.hasAttribute(n);
    for (const attr of el.attributes) {
      let name = attr.name;
      let value = attr.value;
      const lower = name.toLowerCase();
      if (lower.startsWith("on") || lower.startsWith("data-wir-") || lower === "nonce" || lower === "integrity" || lower === "inert" || lower === "autofocus") continue;
      if (["srcset", "sizes", "loading", "decoding", "fetchpriority", "crossorigin", "referrerpolicy", "ping", "is"].includes(lower)) continue;
      if (lower.startsWith("data-") && value.length > KEEP_DATA) continue;
      if (lower === "style") value = fixStyle(value);
      else if (lower === "class" && flat) value = `${value} ${flat}`.trim();
      else if ((lower === "href" || lower === "xlink:href") && tag === "a") {
        value = /^javascript:/i.test(value) ? "#" : value.startsWith("#") ? value : absolute(value);
      } else if ((lower === "href" || lower === "xlink:href") && (tag === "use" || tag === "image")) {
        if (!value.startsWith("#")) {
          value = absolute(value.split("#")[0]) + (value.includes("#") ? "#" + value.split("#")[1] : "");
          urls.add(value.split("#")[0]);
        }
      } else if (lower === "action" || lower === "formaction") value = absolute(value);
      else if (lower === "src" && ["img", "video", "source", "input", "track"].includes(tag)) {
        if (tag === "img") continue; // written below from what the browser chose
        if (!value.startsWith("data:")) {
          value = absolute(value);
          urls.add(value);
        }
      } else if (lower === "poster") {
        value = absolute(value);
        urls.add(value);
      }
      out.push([name, value]);
    }
    if (flat && !has("class")) out.push(["class", flat]);
    return out;
  };

  const sectionSlots = new Map();
  const walkChildren = (nodes, parent, out, state) => {
    const list = [...nodes];
    for (let i = 0; i < list.length; i++) {
      const node = list[i];
      if (node.nodeType === 3) {
        let text = node.nodeValue || "";
        if (!state.pre) {
          text = text.replace(/[ \t\n\r\f]+/g, " ");
          if (text === " ") {
            const before = list.slice(0, i).reverse().find((n) => n.nodeType === 1 && !DROP.has(n.localName));
            const after = list.slice(i + 1).find((n) => n.nodeType === 1 && !DROP.has(n.localName));
            const pcs = parent && parent.nodeType === 1 ? styleOf(parent) : null;
            const flexParent = pcs && /flex|grid/.test(pcs.display);
            const edge = !before || !after;
            const between = (before && blockish(before)) || (after && blockish(after));
            if (flexParent || between || (edge && parent && blockish(parent))) continue;
          }
        }
        if (text) out.push(esc(text));
      } else if (node.nodeType === 1) {
        element(node, out, state);
      }
    }
  };

  const shadowKey = (css) => "wir-sh-" + crypto_hash(css);
  const crypto_hash = (s) => {
    let h = 5381;
    for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) >>> 0;
    return h.toString(36);
  };

  // A shadow root's rules, made to apply to the flattened host: :host -> .cls, ::slotted(x) -> .cls x, the rest under .cls.
  const scopeSelector = (selector, cls) => {
    let s = selector.trim();
    if (!s) return s;
    if (/^:host-context\(/.test(s)) return s.replace(/^:host-context\(([^)]*)\)\s*/, "$1 ." + cls + " ");
    if (/^:host\(/.test(s)) {
      const close = (() => {
        let depth = 0;
        for (let i = 5; i < s.length; i++) {
          if (s[i] === "(") depth++;
          if (s[i] === ")" && --depth === 0) return i;
        }
        return -1;
      })();
      if (close > 0) return "." + cls + s.slice(6, close) + s.slice(close + 1);
    }
    if (/^:host/.test(s)) return "." + cls + s.slice(5);
    s = s.replace(/(^|\s)slot(\[[^\]]*\])?::slotted\(([^)]*)\)/g, "$1$3").replace(/::slotted\(([^)]*)\)/g, " $1");
    return "." + cls + " " + s;
  };
  const scopeRules = (rules, cls) => {
    const out = [];
    for (const rule of rules) {
      if (rule.type === 1) {
        const parts = rule.selectorText.split(/,(?![^(]*\))/).map((p) => scopeSelector(p, cls));
        out.push(`${parts.join(", ")} { ${rule.style.cssText} }`);
      } else if (rule.cssRules && rule.type !== 7) {
        // @media, @supports, @layer, @container: the same header around the scoped rules.
        const text = rule.cssText;
        out.push(`${text.slice(0, text.indexOf("{")).trim()} { ${scopeRules(rule.cssRules, cls)} }`);
      } else out.push(rule.cssText);
    }
    return out.join("\n");
  };
  const shadowStyles = (root) => {
    let text = "";
    for (const sheet of root.adoptedStyleSheets || []) text += [...sheet.cssRules].map((r) => r.cssText).join("\n") + "\n";
    for (const style of root.querySelectorAll("style")) text += style.textContent + "\n";
    return text;
  };

  const element = (el, out, state) => {
    const tag = el.localName;
    if (DROP.has(tag) || el.hasAttribute("data-wir-hidden")) return;
    // A slot of a shadow tree is filled by what the page put in the host (or by its own fallback content).
    if (tag === "slot") {
      const assigned = el.assignedNodes ? el.assignedNodes({ flatten: true }) : [];
      const root = el.getRootNode();
      walkChildren(assigned.length ? assigned : el.childNodes, el.parentElement || (root && root.host) || null, out, state);
      return;
    }
    if (el.hasAttribute("data-wir-sec") && state.sectionOf !== el && state.cutSections) {
      out.push(`<wir-slot n="${el.getAttribute("data-wir-sec")}"/>`);
      return;
    }
    const cs = styleOf(el);
    // Frames cannot be copied. A box of the same size takes their place, with the picture of what they showed.
    if (tag === "iframe") {
      const r = el.getBoundingClientRect();
      if (r.width < 2 || r.height < 2 || cs.position === "fixed" || cs.display === "none") return;
      const index = (frameStart || 0) + frames.length;
      frames.push({ index, w: Math.round(r.width), h: Math.round(r.height), src: el.src || "" });
      el.setAttribute("data-wir-fr", String(index));
      const attrs = attributes(el, "div", "").filter(([n]) => !["src", "width", "height", "allow", "allowfullscreen", "frameborder", "scrolling", "sandbox", "title", "name", "srcdoc"].includes(n.toLowerCase()));
      const style = attrs.find(([n]) => n === "style");
      const rest = attrs.filter(([n]) => n !== "style");
      const own = `${style ? style[1].replace(/;?\s*$/, ";") : ""}position:relative;overflow:hidden;`;
      out.push(`<div${[...rest, ["style", own]].map(([n, v]) => ` ${n}="${escAttr(v)}"`).join("")}><img src="__WIR_FRAME_${index}__" alt="" style="display:block;width:100%;height:100%;object-fit:cover"/></div>`);
      return;
    }
    if (tag === "picture") {
      const img = el.querySelector("img");
      if (!img) return;
      out.push(`<picture${attributes(el, tag, "").map(([n, v]) => ` ${n}="${escAttr(v)}"`).join("")}>`);
      element(img, out, state);
      out.push("</picture>");
      return;
    }
    let attrs;
    let flatClass = "";
    let shadow = null;
    if (el.shadowRoot && el.localName.includes("-")) {
      try {
        const css = shadowStyles(el.shadowRoot);
        flatClass = shadowKey(css);
        if (!shadowCss.has(flatClass)) {
          const sheet = new CSSStyleSheet();
          sheet.replaceSync(css);
          shadowCss.set(flatClass, scopeRules(sheet.cssRules, flatClass));
        }
        shadow = el.shadowRoot;
      } catch (error) {
        warnings.push(`shadow DOM of <${tag}> kept without its styles: ${error && error.message}`);
      }
    }
    attrs = attributes(el, tag, flatClass);
    if (tag.includes("-")) customTags.add(tag);
    if (tag === "img") {
      let src = el.currentSrc || el.getAttribute("src") || "";
      const lazy = el.getAttribute("data-src") || el.getAttribute("data-lazy-src") || el.getAttribute("data-original");
      if ((!src || /^data:image\/(gif|svg\+xml);base64,R0lGOD|^data:image\/gif/.test(src)) && lazy) src = lazy;
      if (src && !src.startsWith("data:")) {
        src = absolute(src);
        urls.add(src);
      }
      if (src) attrs.unshift(["src", src]);
    }
    if (tag === "video") {
      const src = el.currentSrc || el.getAttribute("src");
      attrs = attrs.filter(([n]) => n.toLowerCase() !== "src");
      if (src && !src.startsWith("blob:") && !src.startsWith("data:")) {
        attrs.push(["src", absolute(src)]);
        urls.add(absolute(src));
      }
    }
    if (tag === "canvas") {
      let data = "";
      try {
        data = el.toDataURL("image/png");
      } catch (_) { /* tainted */ }
      const rest = attrs.filter(([n]) => !["width", "height"].includes(n.toLowerCase()));
      const r = el.getBoundingClientRect();
      out.push(`<img${[...rest, ["src", data || "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw=="], ["width", String(Math.round(r.width))], ["height", String(Math.round(r.height))]].map(([n, v]) => ` ${n}="${escAttr(v)}"`).join("")}/>`);
      return;
    }
    if (tag === "input") {
      const type = (el.getAttribute("type") || "text").toLowerCase();
      attrs = attrs.filter(([n]) => !["value", "checked"].includes(n.toLowerCase()));
      if (type === "checkbox" || type === "radio") {
        if (el.checked) attrs.push(["checked", ""]);
      } else if (type !== "password" && type !== "file" && el.value) attrs.push(["value", el.value]);
    }
    if (tag === "option" && el.selected) attrs.push(["selected", ""]);
    const open = `<${tag}${attrs.map(([n, v]) => ` ${n}="${escAttr(v)}"`).join("")}`;
    if (VOID.has(tag)) {
      out.push(open + "/>");
      return;
    }
    out.push(open + ">");
    const inner = { ...state, pre: state.pre || preserves(el) };
    if (tag === "textarea") out.push(esc(el.value || ""));
    else if (shadow) walkChildren(shadow.childNodes, el, out, inner); // its own tree: each <slot> is filled where it stands
    else walkChildren(el.childNodes, el, out, inner);
    out.push(`</${tag}>`);
  };

  // The styles: the rules as the browser holds them (also those a script inserted), in order.
  const css = [];
  const readRules = (sheet) => {
    let text = "";
    for (const rule of sheet.cssRules) {
      if (rule.type === 3 && rule.styleSheet) text += readRules(rule.styleSheet) + "\n";
      else text += rule.cssText + "\n";
    }
    return text;
  };
  for (const sheet of [...document.styleSheets, ...(document.adoptedStyleSheets || [])]) {
    try {
      if (sheet.disabled || sheet === window.__wirHide) continue;
      const media = sheet.media ? sheet.media.mediaText : "";
      if (/^\s*print\s*$/i.test(media)) continue;
      css.push({ base: sheet.href || document.baseURI, media, href: sheet.href || "", text: readRules(sheet) });
    } catch (_) {
      css.push({ base: sheet.href || document.baseURI, media: "", href: sheet.href || "", text: null });
    }
  }

  const sections = [];
  const secEls = [...document.querySelectorAll("[data-wir-sec]")];
  for (const el of secEls) {
    const out = [];
    element(el, out, { pre: false, cutSections: true, sectionOf: el });
    sections.push({ n: Number(el.getAttribute("data-wir-sec")), html: out.join("") });
  }
  const shell = [];
  walkChildren(document.body.childNodes, document.body, shell, { pre: false, cutSections: true });
  const attrsOf = (el) => Object.fromEntries([...el.attributes].filter((a) => !a.name.startsWith("data-wir-") && !a.name.startsWith("on") && a.name.toLowerCase() !== "inert").map((a) => [a.name, a.name === "style" ? fixStyle(a.value) : a.value]));
  const meta = (name) => {
    const m = document.querySelector(`meta[name="${name}"], meta[property="${name}"]`);
    return m ? m.getAttribute("content") || "" : "";
  };
  const icons = [...document.querySelectorAll("link[rel~=icon], link[rel=apple-touch-icon]")].map((l) => ({ rel: l.getAttribute("rel"), href: absolute(l.getAttribute("href") || ""), sizes: l.getAttribute("sizes") || "" })).filter((l) => l.href);
  for (const icon of icons) urls.add(icon.href);
  return {
    shell: shell.join(""), sections, css, shadowCss: Object.fromEntries(shadowCss), urls: [...urls], customTags: [...customTags], frames, warnings,
    // "BackCompat": the page has no doctype and the browser draws it in quirks mode (an old table-based site): see replica.py.
    compat: document.compatMode,
    html: attrsOf(document.documentElement), body: attrsOf(document.body), title: document.title, description: meta("description"), themeColor: meta("theme-color"),
    lang: document.documentElement.lang || "", icons, text: (document.body.innerText || "").replace(/\s+/g, " ").trim(), nodes: document.getElementsByTagName("*").length,
  };
};

const wakeUp = async (page) => {
  await page.evaluate(async () => {
    const step = Math.max(300, Math.floor(window.innerHeight * 0.8));
    const height = () => Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0);
    for (let y = 0, n = 0; y < Math.min(height(), 60000) && n < 80; y += step, n++) {
      window.scrollTo(0, y);
      await new Promise((r) => setTimeout(r, 140));
    }
    window.scrollTo(0, height());
    await new Promise((r) => setTimeout(r, 500));
    window.scrollTo(0, 0);
  });
  await sleep(700);
};

const settle = async (page, ms = 1500) => {
  try { await page.waitForLoadState("load", { timeout: 30000 }); } catch (_) { /* slow page: go on */ }
  try { await page.waitForLoadState("networkidle", { timeout: 6000 }); } catch (_) { /* never idle: go on */ }
  try { await page.evaluate(() => document.fonts && document.fonts.ready); } catch (_) { /* nothing to wait for */ }
  await sleep(ms);
};

// -- the run -------------------------------------------------------------------------------------------------

const started = Date.now();
let browser;
try {
  progress("browser", "starting Chrome");
  browser = await chromium.launch({
    executablePath: config.executable, headless: config.headless !== false,
    args: ["--no-first-run", "--no-default-browser-check", "--disable-blink-features=AutomationControlled", "--hide-scrollbars"],
  });
  // A headless Chrome introduces itself as "HeadlessChrome", and bot protection answers 403 to that before the site is sent.
  const probe = await browser.newContext();
  const userAgent = (await (await probe.newPage()).evaluate(() => navigator.userAgent)).replace("HeadlessChrome", "Chrome");
  await probe.close();
  const first = VIEWPORTS[0];
  const context = await browser.newContext({
    viewport: { width: first.width, height: first.height }, deviceScaleFactor: 1, locale: config.locale || undefined, userAgent, serviceWorkers: "block",
  });
  context.on("response", record);
  const page = await context.newPage();
  const consoleErrors = [];
  page.on("pageerror", (error) => consoleErrors.length < 20 && consoleErrors.push(String(error.message || error).slice(0, 200)));

  progress("open", config.url);
  const response = await page.goto(config.url, { waitUntil: "domcontentloaded", timeout: (config.timeoutSeconds || 60) * 1000 });
  const status = response ? response.status() : null;
  if (status && status >= 400) fail(`The site answered HTTP ${status}. It may refuse automated visitors, or the address is wrong.`);
  await settle(page, 2500);

  const result = { url: config.url, finalUrl: page.url(), status, viewports: {}, overlays: [], warnings: [], consoleErrors };
  let sections = [];
  let frozen = null;
  const variants = {}; // the sections as a narrower viewport shows them: a site can send other markup and other pictures to a phone
  const shotFrames = async (frames) => {
    // Pictures of the frames the page embeds (a map, a video): what they showed.
    for (const frame of frames) {
      try {
        const file = path.join(ASSETS, `frame-${frame.index}.png`);
        await page.locator(`[data-wir-fr="${frame.index}"]`).first().screenshot({ path: file, timeout: 15000, animations: "disabled" });
        frame.file = path.basename(file);
      } catch (_) {
        frame.file = null;
      }
    }
  };
  for (const [index, vp] of VIEWPORTS.entries()) {
    progress("viewport", `${vp.name} ${vp.width}x${vp.height}`);
    if (index > 0) {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await sleep(1200);
      await settle(page, 1200);
    }
    progress("scroll", "lets lazy content load");
    await wakeUp(page);
    // A pop-up may come on a timer (brioni.com's newsletter appears four seconds after the first scroll): look three times.
    let removed = await page.evaluate(sweepOverlays, config.hideSelectors || []);
    await sleep(3500);
    removed = removed.concat(await page.evaluate(sweepOverlays, config.hideSelectors || []));
    await sleep(1500);
    removed = removed.concat(await page.evaluate(sweepOverlays, config.hideSelectors || []));
    result.overlays.push(...removed.map((r) => ({ ...r, viewport: vp.name })));
    await sleep(300);
    if (index === 0) {
      sections = await page.evaluate(stampSections, config.maxSections || 40);
    }
    const height = await page.evaluate(() => Math.max(document.documentElement.scrollHeight, document.body.scrollHeight));
    progress("screenshot", `${vp.name}: ${vp.width}x${height}`);
    const shot = path.join(OUT, `reference-${vp.name}.png`);
    await page.screenshot({ path: shot, fullPage: true, animations: "disabled", timeout: 90000 });
    if (index === 0) {
      // The first screen, small: what the list of clones shows.
      await page.screenshot({ path: path.join(OUT, "thumb.jpg"), type: "jpeg", quality: 60, fullPage: false, animations: "disabled", timeout: 30000 }).catch(() => {});
    }
    result.viewports[vp.name] = { width: vp.width, height: vp.height, pageHeight: height, screenshot: path.basename(shot), boxes: await page.evaluate(sectionBoxes) };
    if (index === 0) {
      progress("freeze", "the rendered page");
      frozen = await page.evaluate(freezePage, 0);
      await shotFrames(frozen.frames);
    } else {
      progress("freeze", `the sections at ${vp.width} px`);
      const variant = await page.evaluate(freezePage, frozen.frames.length);
      await shotFrames(variant.frames);
      frozen.frames.push(...variant.frames);
      frozen.urls.push(...variant.urls);
      for (const [cls, text] of Object.entries(variant.shadowCss)) if (!(cls in frozen.shadowCss)) frozen.shadowCss[cls] = text;
      variants[vp.name] = Object.fromEntries(variant.sections.map((s) => [s.n, s.html]));
    }
  }
  await page.evaluate(() => document.querySelectorAll("[data-wir-sec],[data-wir-fr],[data-wir-hidden]").forEach((el) => { el.removeAttribute("data-wir-sec"); el.removeAttribute("data-wir-fr"); el.removeAttribute("data-wir-hidden"); }));

  // Everything the page uses that the browser did not hand over while it loaded: fetched once more, the way the page asked.
  const wanted = new Set(frozen.urls);
  const cssUrl = /url\(\s*(['"]?)([^'")]+)\1\s*\)/g;
  const cssBase = (sheet) => sheet.base;
  const resolve = (value, base) => {
    try { return new URL(value, base).href; } catch (_) { return null; }
  };
  const cssSheets = [];
  progress("styles", `${frozen.css.length} style sheets`);
  for (const sheet of frozen.css) {
    let text = sheet.text;
    if (text === null && sheet.href) {
      try {
        const res = await context.request.get(sheet.href, { timeout: 20000 });
        text = res.ok() ? await res.text() : "";
        result.warnings.push(`${sheet.href} was read again, not from the page`);
      } catch (_) {
        text = "";
        result.warnings.push(`${sheet.href} could not be read`);
      }
    }
    cssSheets.push({ ...sheet, text: text || "" });
  }
  // A font the page never fetched is not needed (a map's style sheet lists a hundred scripts' worth of subsets): its rule goes.
  await Promise.allSettled([...pending]);
  let droppedFonts = 0;
  for (const sheet of cssSheets) {
    sheet.text = sheet.text.replace(/@font-face\s*\{[^}]*\}/g, (block) => {
      const refs = [...block.matchAll(/url\(\s*(['"]?)([^'")]+)\1\s*\)/g)].map((m) => resolve(m[2], cssBase(sheet))).filter((u) => u && /^https?:/.test(u));
      if (refs.length && !refs.some((u) => assets.has(u))) {
        droppedFonts++;
        return "";
      }
      return block;
    });
  }
  if (droppedFonts) progress("styles", `${droppedFonts} unused font files left out`);
  for (const sheet of cssSheets) {
    for (const m of sheet.text.matchAll(cssUrl)) {
      const raw = m[2] || m[4];
      if (!raw || /^data:|^#/.test(raw)) continue;
      const a = resolve(raw, cssBase(sheet));
      if (a && /^https?:/.test(a)) wanted.add(a);
    }
  }
  await Promise.allSettled([...pending]);
  const missing = [...wanted].filter((u) => /^https?:/.test(u) && !assets.has(u));
  progress("assets", `${assets.size} saved while loading, ${missing.length} to fetch`);
  let next = 0;
  const worker = async () => {
    while (next < missing.length) {
      const url = missing[next++];
      try {
        const res = await context.request.get(url, { timeout: 25000, headers: { referer: result.finalUrl } });
        if (!res.ok()) continue;
        const body = await res.body();
        if (!body.length || body.length > MAX_ASSET) continue;
        const mime = (res.headers()["content-type"] || "").split(";")[0].trim();
        const file = assetName(url, mime);
        fs.writeFileSync(path.join(ASSETS, file), body);
        assets.set(url, { file, mime, bytes: body.length });
      } catch (_) {
        // stays pointing at the original site
      }
    }
  };
  await Promise.all(Array.from({ length: 6 }, worker));

  const local = (u) => {
    const key = u.split("#")[0];
    const hit = assets.get(key);
    return hit ? `__WIR_ASSET__/${hit.file}${u.includes("#") ? "#" + u.split("#")[1] : ""}` : null;
  };
  const rewriteCss = (text, base) =>
    text
      .replace(/@import[^;]*;/g, "")
      .replace(/::part\(([^)]+)\)/g, ' [part~="$1"]')
      .replace(cssUrl, (whole, _q, raw) => {
        if (!raw || /^data:|^#/.test(raw)) return whole;
        const a = resolve(raw, base);
        const hit = a && local(a);
        return hit ? `url("${hit}")` : a ? `url("${a}")` : whole;
      });
  let cssOut = "";
  for (const sheet of cssSheets) {
    const body = rewriteCss(sheet.text, cssBase(sheet));
    const media = sheet.media && !/^\s*(all)?\s*$/i.test(sheet.media);
    cssOut += `/* ${sheet.href || "inline"} */\n${media ? `@media ${sheet.media} {\n${body}\n}` : body}\n`;
  }
  cssOut += `/* shadow DOM of the page's web components, flattened */\n${rewriteCss(Object.values(frozen.shadowCss).join("\n"), result.finalUrl)}\n`;
  // Where the page's own styles switch from the phone layout to the wide one: the width its media queries use most (640, 768 or 1024).
  const breakpoints = { 640: 0, 768: 0, 1024: 0 };
  for (const m of cssOut.matchAll(/\((?:min|max)-width:\s*(\d+(?:\.\d+)?)px\)/g)) {
    const n = Math.round(Number(m[1]));
    for (const key of Object.keys(breakpoints)) if (n === Number(key) || n === Number(key) - 1) breakpoints[key]++;
  }
  const breakpoint = Number(Object.entries(breakpoints).sort((a, b) => b[1] - a[1] || Math.abs(a[0] - 768) - Math.abs(b[0] - 768))[0][0]);
  fs.writeFileSync(path.join(OUT, "site.css"), cssOut);

  const mapHtml = (html) =>
    html
      .replace(/(\s(?:src|poster|href|xlink:href)=")([^"]*)(")/g, (whole, a, value, c) => {
        const raw = value.replace(/&amp;/g, "&");
        const hit = /^https?:/.test(raw) ? local(raw) : null;
        return hit ? `${a}${hit}${c}` : whole;
      })
      .replace(/url\(&quot;(.*?)&quot;\)/g, (whole, a) => {
        const raw = a.replace(/&amp;/g, "&");
        const hit = /^https?:/.test(raw) ? local(raw) : null;
        return hit ? `url(${hit})` : whole;
      })
      .replace(/__WIR_FRAME_(\d+)__/g, (_w, i) => {
        const frame = frozen.frames[Number(i)];
        return frame && frame.file ? `__WIR_ASSET__/${frame.file}` : "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==";
      });
  const bundle = {
    version: 1, url: config.url, finalUrl: result.finalUrl, host: new URL(result.finalUrl).hostname.replace(/^www\./, ""), title: frozen.title, description: frozen.description,
    themeColor: frozen.themeColor, lang: frozen.lang, compatMode: frozen.compat, htmlAttrs: frozen.html, bodyAttrs: frozen.body, customTags: frozen.customTags,
    shell: mapHtml(frozen.shell), breakpoint,
    sections: frozen.sections.map((s) => {
      const html = mapHtml(s.html);
      const mobile = variants.mobile && variants.mobile[s.n] ? mapHtml(variants.mobile[s.n]) : "";
      return { ...(sections.find((x) => x.id === s.n) || {}), n: s.n, html, htmlMobile: mobile && mobile !== html ? mobile : "" };
    }),
    icons: frozen.icons.map((i) => ({ ...i, file: (assets.get(i.href) || {}).file || null })), text: frozen.text, nodes: frozen.nodes,
    assets: Object.fromEntries([...assets].map(([url, a]) => [url, a])), warnings: [...result.warnings, ...frozen.warnings],
  };
  // Only what the clone uses stays: the pages also loaded trackers, the other viewport's pictures and so on.
  const used = new Set();
  for (const text of [cssOut, bundle.shell, ...bundle.sections.flatMap((s) => [s.html, s.htmlMobile])]) {
    for (const m of text.matchAll(/__WIR_ASSET__\/([^"')#\s]+)/g)) used.add(m[1]);
  }
  for (const icon of bundle.icons) if (icon.file) used.add(icon.file);
  for (const [url, asset] of assets) {
    if (!used.has(asset.file)) {
      assets.delete(url);
      fs.rmSync(path.join(ASSETS, asset.file), { force: true });
    }
  }
  bundle.assets = Object.fromEntries([...assets]);
  fs.writeFileSync(path.join(OUT, "bundle.json"), JSON.stringify(bundle));
  result.sections = sections;
  result.stats = {
    nodes: frozen.nodes, sections: sections.length, styleSheets: cssSheets.length, cssBytes: cssOut.length, assets: assets.size,
    assetBytes: [...assets.values()].reduce((n, a) => n + a.bytes, 0), unsaved: missing.filter((u) => !assets.has(u)).length, frames: frozen.frames.length,
    customTags: frozen.customTags, seconds: Math.round((Date.now() - started) / 1000),
  };
  fs.writeFileSync(path.join(OUT, "capture.json"), JSON.stringify(result, null, 1));
  say({ event: "done", result });
} catch (error) {
  fail(error);
} finally {
  if (browser) await browser.close().catch(() => {});
}
