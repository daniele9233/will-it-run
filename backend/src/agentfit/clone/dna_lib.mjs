// Pure helpers of the DNA collector (``dna.mjs``): no browser, no file, nothing to launch. Tested on their own.

// Which libraries a script's text shows. A signature matches when every regex of ``all`` matches and, if ``any`` is not
// empty, at least one of ``any`` does. Several signatures may share one id (any of them is enough). Returns the ids once.
export function matchSignatures(text, signatures) {
  const found = [];
  for (const sig of signatures) {
    if (found.includes(sig.id)) continue;
    const any = sig.any || [];
    const all = sig.all || [];
    if (!any.length && !all.length) continue;
    const okAll = all.every((source) => new RegExp(source).test(text));
    const okAny = !any.length || any.some((source) => new RegExp(source).test(text));
    if (okAll && okAny) found.push(sig.id);
  }
  return found;
}

// A colour kept to 5 bits per channel, written as the hex of the bucket's lower edge.
export function quantizeHex(r, g, b) {
  const part = (v) => ((Math.max(0, Math.min(255, Math.round(v))) >> 3) << 3).toString(16).padStart(2, "0");
  return `#${part(r)}${part(g)}${part(b)}`;
}

// "0.2s, 150ms" -> [200, 150]
export function parseDurationsMs(text) {
  const out = [];
  for (const m of String(text || "").matchAll(/(-?\d*\.?\d+)\s*(ms|s)\b/g)) {
    out.push(Math.round(Number(m[1]) * (m[2] === "s" ? 1000 : 1)));
  }
  return out;
}

// A time budget for one stage of the run: ``left()`` is the milliseconds still to spend (never below 0), ``over()`` says it
// is spent. ``clock`` is injectable so a test needs no real waiting.
export function makeBudget(totalMs, clock = Date.now) {
  const start = clock();
  const left = () => Math.max(0, totalMs - (clock() - start));
  return { left, over: () => left() <= 0 };
}

// What the hover probe's outcome means. ``marked`` is how many cards the page-side step marked, or null when that step
// never ran (the probe threw or timed out first); ``measured`` is the probe's result object or null.
// No card on a page is a measurement (sampled 0); a probe that did not finish is "skipped", never a number.
export function settleHover(marked, measured) {
  if (marked === 0) return { value: { sampled: 0, changed: 0, plays_video: 0, props: [] }, skipped: false };
  if (marked === null || !measured) return { value: null, skipped: true };
  return { value: measured, skipped: false };
}
