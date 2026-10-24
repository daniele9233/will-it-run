import { describe, expect, it as test } from "vitest";

import {
  bottomLine,
  type Cell,
  chosenQuantizations,
  CATEGORY_LABEL,
  type CategoryInfo,
  deltaTint,
  duel,
  fmt,
  groupByCategory,
  inFilter,
  interval,
  KIND_LABEL,
  laneCells,
  type MatrixRow,
  minutes,
  modelEventLine,
  niceTicks,
  type ModelReport,
  perCategory,
  type QuantComparison,
  type QuantMember,
  quantAxisValue,
  questionCount,
  radarPoint,
  remainingSeconds,
  repeatAdvice,
  scoreAxis,
  scoreSoFar,
  seriesVar,
  signed,
  spreadAxis,
  STATUS_LABEL,
  STATUSES,
  strongestArea,
  usedAreas,
} from "./arena";
import { fileName, slug } from "./arenaExport";

const AREAS = ["knowledge", "math", "logic", "coding", "infra", "documents", "agents", "honesty", "vision"];

function cell(status: Cell["status"], score: number | null = status === "passed" ? 1 : status === "failed" ? 0 : null, kind: string | null = null): Cell {
  return { status, score, kind, repeats: 1, statuses: [status], tps: null, tokens: 0, cut: false, manual: null, method: null };
}

function row(id: string, category: string, cells: Record<string, Cell>): MatrixRow {
  return { id, position: 0, category, title: id, tier: 1, difficulty: 1, cells };
}

function model(key: string, position: number, areas: Record<string, number | null>, score: number | null = 50): ModelReport {
  return {
    key, label: key, cloud: false, vision: true, status: "done", error: null, context_asked: 8192, context_granted: 8192, load_seconds: 5, info: {}, position, rank: position + 1, tied: false,
    score, ci: score === null ? null : [score - 3, score + 4], score_all: score, ci_all: null, categories_scored: 3, areas,
    summary: {
      counts: { passed: 0, partial: 0, failed: 0, unscored: 0, skipped: 0, error: 0 }, answers: 0, questions: { total: 0, answered: 0, scored: 0 }, fail_kinds: {},
      speed: { tps_mean: null, tps_median: null, ttft_median_s: null, output_tokens: 0, time_s: 0, timed: 0 }, consistency: null, coverage: 1,
    },
  };
}

const full = (v: number | null): Record<string, number | null> => Object.fromEntries(AREAS.map((a) => [a, v]));

describe("the duel", () => {
  const a = model("a", 0, { ...full(80), math: 92, vision: null }, 83);
  const b = model("b", 1, { ...full(78), math: 78, logic: 80.2, vision: null }, 76.9);
  const matrix = [
    row("m.1", "math", { a: cell("passed"), b: cell("failed", 0, "wrong") }),
    row("m.2", "math", { a: cell("passed"), b: cell("passed") }),
    row("t.1", "tools", { a: cell("failed", 0, "format"), b: cell("passed") }),
    row("w.1", "writing", { a: cell("unscored"), b: cell("unscored") }),
    row("v.1", "vision", { a: cell("skipped"), b: cell("passed") }),
  ];

  test("counts the areas each one wins, and ignores the ones nobody was scored on", () => {
    const d = duel({ models: [a, b], areas: AREAS, matrix }, "a", "b");
    expect(d).not.toBeNull();
    expect(d?.areas.find((r) => r.area === "math")).toMatchObject({ winner: "a", delta: 14 });
    expect(d?.areas.find((r) => r.area === "logic")?.winner).toBe("tie");
    expect(d?.areas.find((r) => r.area === "vision")).toMatchObject({ winner: null, delta: null });
    expect(d?.compared).toBe(8);
    expect(d?.aWins).toBe(7);
    expect(d?.bWins).toBe(0);
    expect(d?.delta).toBeCloseTo(6.1, 5);
  });

  test("lists the questions on which they parted, with who did better, and not the ones nothing could judge", () => {
    const d = duel({ models: [a, b], areas: AREAS, matrix }, "a", "b");
    expect(d?.splits.map((s) => [s.row.id, s.better])).toEqual([["m.1", "a"], ["t.1", "b"]]);
  });

  test("is nothing when a model is not in the run", () => {
    expect(duel({ models: [a], areas: AREAS, matrix }, "a", "zzz")).toBeNull();
  });
});

describe("the matrix filters", () => {
  const keys = ["a", "b"];
  test("shows every row, or only the mistakes, or only what was not judged, or only where they differ", () => {
    const both = row("1", "math", { a: cell("passed"), b: cell("passed") });
    const mistake = row("2", "math", { a: cell("passed"), b: cell("failed") });
    const partial = row("3", "math", { a: cell("passed"), b: cell("partial", 0.5) });
    const unjudged = row("4", "writing", { a: cell("unscored"), b: cell("skipped") });
    const same = row("5", "math", { a: cell("failed"), b: cell("failed") });
    const ids = (filter: Parameters<typeof inFilter>[2]) => [both, mistake, partial, unjudged, same].filter((r) => inFilter(r, keys, filter)).map((r) => r.id);
    expect(ids("all")).toEqual(["1", "2", "3", "4", "5"]);
    expect(ids("wrong")).toEqual(["2", "3", "5"]);
    expect(ids("unscored")).toEqual(["4"]);
    expect(ids("split")).toEqual(["2", "3"]);
  });

  test("a difference of less than half a point is not a split", () => {
    const close = row("6", "math", { a: cell("partial", 0.8), b: cell("partial", 0.6) });
    expect(inFilter(close, ["a", "b"], "split")).toBe(false);
  });

  test("groups the rows by category in the order they come", () => {
    const rows = [row("1", "math", {}), row("2", "math", {}), row("3", "logic", {}), row("4", "math", {})];
    expect(groupByCategory(rows).map((g) => [g.category, g.rows.length])).toEqual([["math", 2], ["logic", 1], ["math", 1]]);
  });
});

describe("a lane of the live run", () => {
  const matrix = [row("1", "math", { a: cell("passed") }), row("2", "math", { a: cell("failed") }), row("3", "math", {}), row("4", "writing", { a: cell("unscored") })];
  test("has a square for each question, empty where nothing was asked yet", () => {
    expect(laneCells(matrix, "a")).toEqual(["passed", "failed", null, "unscored"]);
  });
  test("scores what was answered so far, and not what could not be judged", () => {
    expect(scoreSoFar(matrix, "a")).toBe(50);
    expect(scoreSoFar(matrix, "nobody")).toBeNull();
  });
  test("tells how long a run still needs, once it has done enough to say", () => {
    const started = new Date("2026-10-23T10:00:00Z").getTime();
    const run = { started_at: new Date(started).toISOString(), done: 30, total: 120 };
    expect(remainingSeconds(run, started + 60_000)).toBe(180);
    expect(remainingSeconds({ ...run, done: 2 }, started + 60_000)).toBeNull();
    expect(remainingSeconds({ ...run, done: 120 }, started + 60_000)).toBeNull();
    expect(remainingSeconds({ ...run, started_at: null }, started)).toBeNull();
  });
});

describe("what the log says", () => {
  const name = (key: string) => key.toUpperCase();
  test("says a model is loaded, with how long it took", () => {
    expect(modelEventLine({ topic: "model.switch", phase: "ready", to: "a", load_seconds: 9.1, context_length: 8192 }, name)).toBe("A loaded in 9.1 s, context 8,192");
  });
  test("says a model that did not load, and a model unloaded", () => {
    expect(modelEventLine({ topic: "arena.model", phase: "failed", model: "a", message: "out of memory" }, name)).toBe("A could not be loaded: out of memory");
    expect(modelEventLine({ topic: "model.switch", phase: "unloaded", model: "a" }, name)).toBe("A unloaded from memory");
  });
  test("is silent about what is not worth a line", () => {
    expect(modelEventLine({ topic: "model.switch", phase: "retuning", to: "a" }, name)).toBeNull();
    expect(modelEventLine({ topic: "model.switch", phase: "ready", to: "cloud:x/y", cloud: true }, name)).toBeNull();
  });
});

describe("the request", () => {
  const categories: CategoryInfo[] = [
    { id: "general", group: "knowledge", area: "knowledge", scored: true, needs: [], counts: [4, 8, 14] },
    { id: "longctx", group: "agents", area: "knowledge", scored: true, needs: [], counts: [0, 2, 4] },
    { id: "writing", group: "agents", area: "writing", scored: false, needs: [], counts: [2, 3, 4] },
  ];
  test("counts the questions of a pack in each category and in all of them", () => {
    expect(perCategory(categories[1] as CategoryInfo, "quick")).toBe(0);
    expect(perCategory(categories[1] as CategoryInfo, "full")).toBe(4);
    expect(questionCount(categories, "standard", null)).toBe(13);
    expect(questionCount(categories, "standard", ["general"])).toBe(8);
    expect(questionCount(categories, "quick", [])).toBe(0);
  });
});

describe("the names and the numbers", () => {
  test("every status and every kind of failure has a name", () => {
    for (const status of STATUSES) expect(STATUS_LABEL[status]).toBeTruthy();
    for (const kind of ["wrong", "format", "fabricated", "refused", "truncated", "manual", "tool", "tech", "capability"]) expect(KIND_LABEL[kind]).toBeTruthy();
  });
  test("every category of the bank has a name", () => {
    const ids = ["general", "reading", "math", "logic", "coding", "sql", "terraform", "ansible", "documents", "pdf", "json", "vision", "tools", "impossible", "instructions", "injection", "longctx", "writing"];
    for (const id of ids) expect(CATEGORY_LABEL[id]).toBeTruthy();
  });
  test("a model keeps its colour by its place in the run, and the colours wrap after eight", () => {
    expect(seriesVar(0)).toBe("var(--s1)");
    expect(seriesVar(7)).toBe("var(--s8)");
    expect(seriesVar(8)).toBe("var(--s1)");
  });
  test("writes a score with a decimal and no score as a dash", () => {
    expect(fmt(83)).toBe("83.0");
    expect(fmt(null)).toBe("–");
    expect(fmt(76.94, 0)).toBe("77");
    expect(interval([79.6, 86.9], 83)).toBe("±3.9");
    expect(interval(null, 83)).toBeNull();
  });
  test("writes a time as seconds, then as minutes", () => {
    expect(minutes(40)).toBe("40 s");
    expect(minutes(600)).toBe("10 min");
  });
  test("puts the axes of a radar round a circle, the first one straight up, and keeps a score inside it", () => {
    const [x, y] = radarPoint(0, 8, 100, 150, 150, 100);
    expect(x).toBeCloseTo(150, 5);
    expect(y).toBeCloseTo(50, 5);
    const [rx] = radarPoint(2, 8, 50, 150, 150, 100);
    expect(rx).toBeCloseTo(200, 5);
    const [, over] = radarPoint(0, 8, 500, 150, 150, 100);
    expect(over).toBeCloseTo(50, 5);
  });
  test("finds the strongest area of a model", () => {
    expect(strongestArea({ areas: { math: 92, logic: null, coding: 70 } })).toEqual({ area: "math", score: 92 });
    expect(strongestArea({ areas: { math: null } })).toBeNull();
  });
});

describe("the name of a saved report", () => {
  test("is the date and the title of the run, in a form any file system takes", () => {
    expect(slug("Prova dimostrativa (dati inventati)")).toBe("prova-dimostrativa-dati-inventati");
    expect(slug("Città — àèìòù")).toBe("citta-aeiou");
    expect(slug("***")).toBe("run");
    expect(fileName({ title: "Qwen vs Gemma", started_at: "2026-10-23T14:15:00+00:00", created_at: "2026-10-22T10:00:00+00:00" }, "html")).toBe("arena-2026-10-23-qwen-vs-gemma.html");
  });
});

describe("the axes of a radar", () => {
  test("are the areas some model has a score on, and all of them when too few have one", () => {
    const a = model("a", 0, { ...full(null), math: 90, logic: 70, agents: 100 });
    const b = model("b", 1, { ...full(null), math: 50, honesty: 60 });
    expect(usedAreas([a, b], AREAS)).toEqual(["math", "logic", "agents", "honesty"]);
    expect(usedAreas([b], AREAS)).toEqual(AREAS);
    expect(usedAreas([], AREAS)).toEqual(AREAS);
  });
});

describe("the quantizations of one model", () => {
  const member = (key: string, quant: string, size: number, extra: Partial<QuantMember> = {}): QuantMember => ({
    key, label: "Model", quant, bits: null, size_bytes: size * 1024 ** 3, vram_mib: null, vram_total_mib: null, spilled: false, is_reference: false,
    score: 70, ci: [65, 75], tps_mean: null, ttft_median_s: null, load_seconds: null, context_granted: 8192, ...extra,
  });
  const comparison = (models: QuantMember[]): QuantComparison => ({
    order: models.map((m) => m.key), reference: models[0]?.key ?? "", models: Object.fromEntries(models.map((m) => [m.key, m])), categories: [], questions: 100, dropped: 0,
    settings: { repeats: 1, temperature: 0, seed: 42 }, suite_seed: 42, alpha: 0.05, draws: 1000, speed_comparable: true,
  });
  const q4 = member("q4", "Q4_K_M", 18, { is_reference: true });

  test("write a difference with a real minus sign and no sign for zero", () => {
    expect(signed(3.04)).toBe("+3.0");
    expect(signed(-3.04)).toBe("−3.0");
    expect(signed(0)).toBe("0.0");
    expect(signed(-0.04)).toBe("0.0");
    expect(signed(null)).toBe("–");
  });

  test("draw the accuracy axis tight around the intervals, in steps of five, and never thinner than ten points", () => {
    expect(scoreAxis([61.2, 66.9], [70.4, 74.1])).toEqual([55, 80]);
    expect(scoreAxis([71, 72], [73, 74])).toEqual([65, 80]);
    expect(scoreAxis([0], [100])).toEqual([0, 100]);
    const [low, high] = scoreAxis([97], [99]);
    expect(high).toBe(100);
    expect(high - low).toBeGreaterThanOrEqual(10);
  });

  test("mark an axis at round values, in at most six steps", () => {
    expect(niceTicks(70, 100)).toEqual([70, 75, 80, 85, 90, 95, 100]);
    expect(niceTicks(0, 100)).toEqual([0, 20, 40, 60, 80, 100]);
    expect(niceTicks(55, 80)).toEqual([55, 60, 65, 70, 75, 80]);
    expect(niceTicks(95, 100)).toEqual([95, 96, 97, 98, 99, 100]);
  });

  test("leave room around the weights on the horizontal axis, and not below zero", () => {
    expect(spreadAxis([10, 20])).toEqual([8.5, 21.5]);
    expect(spreadAxis([1, 100])[0]).toBe(0);
    expect(spreadAxis([5, 5])).toEqual([4.5, 5.5]);
  });

  test("plot a quantization against the file, the graphics memory or the speed, and nothing where it was not measured", () => {
    const m = member("a", "Q4_K_M", 18, { vram_mib: 20480, tps_mean: 44.5 });
    expect(quantAxisValue(m, "size")).toBeCloseTo(18);
    expect(quantAxisValue(m, "memory")).toBe(20);
    expect(quantAxisValue(m, "speed")).toBe(44.5);
    expect(quantAxisValue(member("b", "Q3_K_M", 14), "memory")).toBeNull();
  });

  test("say which quantization is the lightest one that does not lose for real", () => {
    const iq3 = member("iq3", "IQ3_XXS", 11, { delta: -9, verdict: "worse" });
    const q3 = member("q3", "Q3_K_M", 14, { delta: -1, verdict: "noise", detectable: 4 });
    const line = bottomLine(comparison([q4, q3, iq3]));
    expect(line.lightest?.key).toBe("q3");
    expect(line.losses.map((m) => m.key)).toEqual(["iq3"]);
    expect(line.saved).toBe(4 * 1024 ** 3);
  });

  test("find nothing light enough when every other quantization loses for real", () => {
    const line = bottomLine(comparison([q4, member("iq3", "IQ3_XXS", 11, { delta: -9, verdict: "worse" }), member("q3", "Q3_K_M", 14, { delta: -4, verdict: "worse" })]));
    expect(line.lightest).toBeNull();
    expect(line.saved).toBeNull();
    expect(line.losses).toHaveLength(2);
  });

  test("do not call a heavier file a saving", () => {
    const bigger = member("q8", "Q8_0", 30, { delta: 1, verdict: "noise" });
    expect(bottomLine(comparison([q4, bigger])).lightest).toBeNull();
  });

  test("tell when repeats cannot help (temperature 0) and when they are too few to trust", () => {
    expect(repeatAdvice({ repeats: 3, temperature: 0, seed: 1 })).toBe("greedy");
    expect(repeatAdvice({ repeats: 1, temperature: 0, seed: 1 })).toBeNull();
    expect(repeatAdvice({ repeats: 1, temperature: 0.6, seed: 1 })).toBe("few");
    expect(repeatAdvice({ repeats: 3, temperature: 0.6, seed: 1 })).toBeNull();
  });

  test("tint a difference red for a loss and green for a gain, and leave a wobble alone", () => {
    expect(deltaTint(null)).toBe("transparent");
    expect(deltaTint(0.3)).toBe("transparent");
    expect(deltaTint(-10)).toContain("--bad");
    expect(deltaTint(10)).toContain("--ok");
    expect(deltaTint(-40)).toBe(deltaTint(-25));
  });

  test("find the chosen models that are one model in different quantizations", () => {
    const local = [
      { key: "a@q4", label: "Qwen", quant: "Q4_K_M", family: "qwen|30b|moe" },
      { key: "a@q3", label: "Qwen", quant: "Q3_K_M", family: "qwen|30b|moe" },
      { key: "a@q3b", label: "Qwen", quant: "Q3_K_M", family: "qwen|30b|moe" },
      { key: "g", label: "Gemma", quant: "Q4_0", family: "gemma|12b|gemma" },
      { key: "n", label: "None", quant: null, family: null },
    ];
    expect(chosenQuantizations(["a@q4", "a@q3", "g"], local)).toEqual({ family: "qwen|30b|moe", label: "Qwen", keys: ["a@q4", "a@q3"] });
    expect(chosenQuantizations(["a@q4", "g", "n"], local)).toBeNull();
    // Two files in the same quantization are not a comparison.
    expect(chosenQuantizations(["a@q3", "a@q3b"], local)).toBeNull();
    expect(chosenQuantizations([], local)).toBeNull();
  });
});
