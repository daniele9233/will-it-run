import { t } from "./i18n";

/** The Site DNA a study measured (`SiteDNA` in `clone/dna.py`). Any field that is `null` was not measured. */
export interface DnaView {
  schema_version: 1;
  url: string;
  host: string;
  captured_at: string;
  viewport: [number, number];
  framework: string | null;
  libs: { name: string; evidence: string[] }[];
  tokens: {
    palette: { hex: string; share: number; role: "background" | "text" | "muted" | "line" | "accent" | null; also: ("background" | "text" | "muted" | "line" | "accent")[]; contrast: number | null }[] | null;
    fonts: { family: string; share: number; weights: number[] }[] | null;
    type_scale: number[] | null;
    spacing: number[] | null;
    radii: number[] | null;
    shadows: string[] | null;
    container_px: number | null;
    breakpoints: number[] | null;
  };
  sections:
    | {
        index: number;
        tag: string;
        role: "header" | "hero" | "gallery" | "filters" | "content" | "footer" | null;
        height_px: number;
        heading_px: number | null;
        heading_vw: number | null;
        img: number;
        video: number;
        canvas: number;
        svg: number;
        columns: number | null;
        cards: number | null;
        card_height_px: number | null;
        gap_px: number | null;
      }[]
    | null;
  motion: {
    transitions: { duration_ms: number; easing: string; count: number }[] | null;
    keyframes: string[] | null;
    sticky: number | null;
    fixed: number | null;
    scroll_snap: boolean | null;
    scroll_timeline: boolean | null;
    view_transitions: boolean | null;
    smooth_scroll: "lenis" | "native" | "none" | null;
    backdrop: number | null;
    blur: number | null;
    mask: number | null;
    clip_path: number | null;
    blend: number | null;
    videos: { count: number; loop: number; muted: number; autoplay: number; preload_none: number; playing_without_autoplay: number } | null;
    canvases: { context: "webgl" | "webgl2" | "2d" | "unknown"; width: number; height: number; covers_viewport: boolean; in_card: boolean | null }[] | null;
    lottie: number | null;
  };
  patterns: {
    chip_filters: boolean | null;
    dialog: boolean | null;
    carousel: boolean | null;
    masonry: boolean | null;
    sticky_header: boolean | null;
    newsletter_form: boolean | null;
  };
  probes: {
    scroll: { sampled: number; parallax_like: number; pinned: number } | null;
    hover: { sampled: number; changed: number; plays_video: number; props: string[] } | null;
  };
  limits: { code: string; count: number | null }[];
}

/** The measured palette as colour chips; an unmeasured palette gives none. */
export function paletteSwatches(dna: DnaView): { hex: string; role: string | null }[] {
  return (dna.tokens.palette ?? []).map((c) => ({ hex: c.hex, role: c.role }));
}

/** The libraries found, by name. */
export function libsLabel(dna: DnaView): string {
  return dna.libs.length === 0 ? t("None detected") : dna.libs.map((l) => l.name).join(", ");
}

/** What the study could not do, in the person's words (the codes are those of `limit_text` in `clone/dna.py`). */
export function limitLines(dna: DnaView): string[] {
  return dna.limits.map((x) => {
    const n = x.count === null ? t("some") : String(x.count);
    switch (x.code) {
      case "script_unreadable":
        return t("{n} script(s) could not be read, so libraries only they use may be missing", { n });
      case "no_sections":
        return t("The page has no readable sections (it may be a canvas, an iframe or a single block)");
      case "scroll_probe_skipped":
        return t("The scroll behaviour was not tested");
      case "hover_probe_skipped":
        return t("The hover behaviour was not tested");
      case "stylesheet_unreadable":
        return t("{n} stylesheet(s) could not be read, so some styles are missing", { n });
      case "visit_slow":
        return t("The page was slow to settle, so late content may be missing");
      default:
        return t("Limit: {code}", { code: x.code });
    }
  });
}

/** The motion facts that were measured. One that was not measured is absent, never a zero. */
export function motionFacts(dna: DnaView): string[] {
  const m = dna.motion;
  const out: string[] = [];
  if (m.smooth_scroll === "lenis") out.push(t("Smooth scroll with Lenis"));
  else if (m.smooth_scroll === "native") out.push(t("Native smooth scroll"));
  else if (m.smooth_scroll === "none") out.push(t("No smooth scroll"));
  if (m.sticky !== null) out.push(t("{n} sticky element(s)", { n: m.sticky }));
  if (m.fixed !== null) out.push(t("{n} fixed element(s)", { n: m.fixed }));
  if (m.scroll_snap !== null) out.push(m.scroll_snap ? t("Scroll snap") : t("No scroll snap"));
  if (m.scroll_timeline !== null) out.push(m.scroll_timeline ? t("Scroll-driven animations") : t("No scroll-driven animations"));
  if (m.view_transitions !== null) out.push(m.view_transitions ? t("View transitions") : t("No view transitions"));
  if (m.backdrop !== null) out.push(t("{n} backdrop filter(s)", { n: m.backdrop }));
  if (m.blur !== null) out.push(t("{n} blur(s)", { n: m.blur }));
  if (m.mask !== null) out.push(t("{n} mask(s)", { n: m.mask }));
  if (m.clip_path !== null) out.push(t("{n} clip path(s)", { n: m.clip_path }));
  if (m.blend !== null) out.push(t("{n} blend mode(s)", { n: m.blend }));
  if (m.keyframes !== null) out.push(t("{n} keyframe animation(s)", { n: m.keyframes.length }));
  if (m.videos !== null) out.push(t("{n} video(s), {autoplay} autoplaying", { n: m.videos.count, autoplay: m.videos.autoplay }));
  if (m.canvases !== null) out.push(t("{n} canvas(es)", { n: m.canvases.length }));
  if (m.lottie !== null) out.push(t("{n} Lottie animation(s)", { n: m.lottie }));
  const { scroll, hover } = dna.probes;
  if (scroll !== null) out.push(t("Scroll test: {parallax} parallax-like, {pinned} pinned of {n} sampled", { parallax: scroll.parallax_like, pinned: scroll.pinned, n: scroll.sampled }));
  if (hover !== null) out.push(t("Hover test: {changed} of {n} sampled changed", { changed: hover.changed, n: hover.sampled }));
  return out;
}

/** Why a finished study's DNA could not be read: a 404 only means it is not written yet (null, nothing to report); any other failure is shown. */
export function dnaFetchProblem(err: unknown, status?: number): string | null {
  if (status === 404) return null;
  return err instanceof Error ? err.message : typeof err === "string" && err.trim() ? err : "Something went wrong";
}
