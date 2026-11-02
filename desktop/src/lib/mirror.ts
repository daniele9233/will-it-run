import { t } from "./i18n";
import { studyFailure } from "./masterPrompts";

/** What the verification measured (`MirrorReport` in `clone/mirror.py`). Any field that is `null` was not measured. */
export interface MirrorReport {
  schema_version: 1;
  live_at: string | null;
  replay_at: string | null;
  dna_match: { overall: number | null; parts: Record<string, number | null>; not_covered?: string[] } | null;
  facts: Record<string, Record<string, { live: number | null; replay: number | null; equal: boolean | null }>> | null;
  motion: { moving_live: number; moving_replay: number; matching: number | null } | null;
  pixels: { at: number; within_16: number | null }[] | null;
  requests: { not_recorded: number; leaked_hosts: string[] } | null;
  loads: { width: number; visible_words: number }[] | null;
  driver: "scroll" | "wheel" | null;
  not_covered: string[] | null;
  seconds: number | null;
}

const notMeasured = (): string => t("Not measured");

/** A share between 0 and 1 as a percentage; a share that was not measured reads "Not measured", never 0%. */
function share(value: number | null | undefined, digits = 0): string {
  return typeof value === "number" ? `${(value * 100).toFixed(digits)}%` : notMeasured();
}

/** How much the mirror's Site DNA resembles the live page's, part by part, and how many document facts are equal at each width. */
export function matchLines(r: MirrorReport): string[] {
  const out: string[] = [];
  const m = r.dna_match;
  if (m === null) {
    out.push(t("Site DNA match: {value}", { value: notMeasured() }));
  } else {
    out.push(t("Overall: {value}", { value: share(m.overall) }));
    const names: [string, string][] = [
      ["palette", t("Palette")],
      ["fonts", t("Fonts")],
      ["libs", t("Libraries")],
      ["motion", t("Motion features")],
      ["sections", t("Sections")],
    ];
    for (const [key, label] of names) out.push(`${label}: ${share(m.parts[key])}`);
  }
  if (r.facts === null) {
    out.push(t("Page facts: {value}", { value: notMeasured() }));
  } else {
    for (const [width, facts] of Object.entries(r.facts)) {
      const known = Object.values(facts).filter((f) => f.equal !== null);
      if (known.length === 0) out.push(t("At {width}px: {value}", { width, value: notMeasured() }));
      else out.push(t("At {width}px: {n} of {total} measured facts are equal", { width, n: known.filter((f) => f.equal === true).length, total: known.length }));
    }
  }
  return out;
}

/** The counts of moving elements in the live page and in the mirror, the share that moved the same way, and which driver moved the page. */
export function motionLines(r: MirrorReport): string[] {
  const m = r.motion;
  if (m === null) return [t("Motion: {value}", { value: notMeasured() })];
  const out = [
    t("Moving elements on the live page: {n}", { n: m.moving_live }),
    t("Moving elements on the mirror: {n}", { n: m.moving_replay }),
    t("Moving the same way: {value}", { value: share(m.matching) }),
  ];
  if (r.driver === "wheel") out.push(t("The page was driven with the mouse wheel (it does not scroll vertically)"));
  else if (r.driver === "scroll") out.push(t("The page was driven by scrolling"));
  return out;
}

/** The share of pixels close to the live page's in the screenshots taken along the page. */
export function pixelLines(r: MirrorReport): string[] {
  if (r.pixels === null) return [t("Pixels: {value}", { value: notMeasured() })];
  return r.pixels.map((p) => (p.within_16 === null ? t("At {at}%: {value}", { at: p.at, value: notMeasured() }) : t("At {at}%: {value} of pixels close", { at: p.at, value: share(p.within_16, 1) })));
}

const coveredText = (): Record<string, string> => ({
  hover: t("Hover"),
  click: t("Click"),
  "other pages": t("Other pages"),
  sound: t("Sound"),
  touch: t("Touch"),
  login: t("Login"),
  "what loads only after a long wait": t("What loads only after a long wait"),
});

/** What the mirror lacks: requests the recording did not hold, hosts the replay still reached, a blank first load, and what the test never covers. */
export function gapLines(r: MirrorReport): string[] {
  const out: string[] = [];
  if (r.requests === null) {
    out.push(t("Requests: {value}", { value: notMeasured() }));
  } else {
    out.push(t("Requests the recording did not hold (404): {n}", { n: r.requests.not_recorded }));
    if (r.requests.leaked_hosts.length > 0) out.push(t("The mirror still reached these hosts: {hosts}", { hosts: r.requests.leaked_hosts.join(", ") }));
  }
  const loads = r.loads ?? [];
  loads.forEach((load, i) => {
    if (loads.slice(0, i).some((x) => x.width === load.width)) out.push(t("The first load at {width}px was blank and was loaded again", { width: load.width }));
  });
  const words = coveredText();
  for (const item of r.not_covered ?? []) out.push(t("Not covered: {what}", { what: words[item] ?? item }));
  return out;
}

/** Why a mirror stopped before its report: what the step that failed said, else the run's own error, else a plain sentence. */
export function mirrorFailure(run: { error: string | null; outputs: Record<string, Record<string, unknown>> } | null): string {
  for (const id of ["record", "verify"]) {
    const out = run?.outputs[id];
    if (out && out.passed === false && typeof out.summary === "string" && out.summary) return out.summary;
  }
  return studyFailure(run);
}
