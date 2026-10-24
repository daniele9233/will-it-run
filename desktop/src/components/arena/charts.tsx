// The Arena's charts, drawn by hand in SVG on the app's tokens. A mark is never alone in saying something: every one carries
// a title (what it is, in words) and sits beside a label or a legend.

import type { ReactNode } from "react";

import { AREA_LABEL, type AreaDuel, fmt, KIND_LABEL, label, niceTicks, radarPoint, scoreAxis, spreadAxis, STATUS_GLYPH, STATUS_LABEL, type Status, statusTone } from "../../lib/arena";
import { t } from "../../lib/i18n";
import { cx, toneVar } from "../ui";

// -- the ring ---------------------------------------------------------------------------------

export function ScoreRing({ value, size = 96, stroke = 7, color, caption }: { value: number | null; size?: number; stroke?: number; color: string; caption?: string }) {
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const share = value === null ? 0 : Math.max(0, Math.min(1, value / 100));
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }} role="img" aria-label={t("Score {value} out of 100", { value: fmt(value) })}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="var(--raised)" strokeWidth={stroke} />
        {value !== null && (
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke={color}
            strokeWidth={stroke}
            strokeLinecap="round"
            strokeDasharray={`${circumference * share} ${circumference}`}
            style={{ transition: "stroke-dasharray 400ms var(--ease-out)" }}
          />
        )}
      </svg>
      <div className="absolute inset-0 grid place-content-center text-center leading-none">
        <span className="tabular font-semibold tracking-tight" style={{ fontSize: size * 0.27 }}>
          {fmt(value)}
        </span>
        <span className="mt-1 text-ink-3" style={{ fontSize: Math.max(9, size * 0.11) }}>
          {caption ?? t("out of 100")}
        </span>
      </div>
    </div>
  );
}

// -- a score with its interval ------------------------------------------------------------------

export function ScoreBar({ value, ci, color, height = 8 }: { value: number | null; ci: [number, number] | null; color: string; height?: number }) {
  if (value === null) return <div className="h-2 rounded-full bg-raised" aria-hidden />;
  return (
    <div className="relative w-full" style={{ height }} role="img" aria-label={ci ? t("{value} out of 100, between {low} and {high}", { value: fmt(value), low: fmt(ci[0], 0), high: fmt(ci[1], 0) }) : t("Score {value} out of 100", { value: fmt(value) })}>
      <div className="absolute inset-0 rounded-full bg-raised" />
      <div className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${Math.max(0, Math.min(100, value))}%`, background: color, transition: "width 400ms var(--ease-out)" }} />
      {ci && (
        <>
          <div className="absolute top-1/2 h-px -translate-y-1/2 bg-ink" style={{ left: `${ci[0]}%`, width: `${Math.max(0, ci[1] - ci[0])}%` }} />
          <div className="absolute top-[-2px] bottom-[-2px] w-px bg-ink" style={{ left: `${ci[0]}%` }} />
          <div className="absolute top-[-2px] bottom-[-2px] w-px bg-ink" style={{ left: `${ci[1]}%` }} />
        </>
      )}
    </div>
  );
}

// -- the radar ------------------------------------------------------------------------------------

export interface RadarSeries {
  key: string;
  label: string;
  color: string;
  values: Record<string, number | null>;
}

export function Radar({ axes, series, size = 300 }: { axes: string[]; series: RadarSeries[]; size?: number }) {
  const cx = size / 2;
  const cy = size / 2;
  const radius = size / 2 - 64;
  const count = axes.length;
  const ring = (level: number) =>
    axes
      .map((_, i) => radarPoint(i, count, level, cx, cy, radius))
      .map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`)
      .join(" ");
  return (
    <svg viewBox={`0 0 ${size} ${size}`} className="h-auto w-full max-w-[380px] overflow-visible" role="img" aria-label={t("Radar of the areas: {names}", { names: series.map((s) => s.label).join(", ") })}>
      {[25, 50, 75, 100].map((level) => (
        <polygon key={level} points={ring(level)} fill="none" stroke="var(--line)" strokeWidth={1} />
      ))}
      {[50, 100].map((level) => {
        const [x, y] = radarPoint(0, count, level, cx, cy, radius);
        return (
          <text key={level} x={x + 3} y={y + 3} fontSize={9} fill="var(--ink-3)">
            {level}
          </text>
        );
      })}
      {axes.map((axis, i) => {
        const [x, y] = radarPoint(i, count, 100, cx, cy, radius);
        const [lx, ly] = radarPoint(i, count, 100, cx, cy, radius + 14);
        const anchor = Math.abs(lx - cx) < 10 ? "middle" : lx > cx ? "start" : "end";
        return (
          <g key={axis}>
            <line x1={cx} y1={cy} x2={x} y2={y} stroke="var(--line)" />
            <text x={lx} y={ly + 4} textAnchor={anchor} fontSize={11} fill="var(--ink-2)">
              {label(AREA_LABEL, axis)}
            </text>
          </g>
        );
      })}
      {series.map((s) => {
        const points = axes.map((a, i) => radarPoint(i, count, s.values[a] ?? 0, cx, cy, radius));
        return (
          <g key={s.key}>
            <polygon points={points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ")} fill={s.color} fillOpacity={0.12} stroke={s.color} strokeWidth={2} strokeLinejoin="round" />
            {points.map(([x, y], i) => (
              <circle key={axes[i]} cx={x} cy={y} r={3} fill={s.color} stroke="var(--surface)" strokeWidth={1.5}>
                <title>{`${s.label} · ${label(AREA_LABEL, axes[i] ?? "")}: ${s.values[axes[i] ?? ""] === null || s.values[axes[i] ?? ""] === undefined ? t("no score") : fmt(s.values[axes[i] ?? ""], 0)}`}</title>
              </circle>
            ))}
          </g>
        );
      })}
    </svg>
  );
}

// -- grouped bars: one row per area, one bar per model --------------------------------------------------

export function AreaBars({ areas, series }: { areas: string[]; series: RadarSeries[] }) {
  return (
    <div className="grid gap-2.5" role="img" aria-label={t("Score of each model in each area")}>
      {areas.map((area) => (
        <div key={area} className="grid grid-cols-[96px_1fr] items-center gap-3">
          <span className="text-xs text-ink-2">{label(AREA_LABEL, area)}</span>
          <div className="grid gap-[3px]">
            {series.map((s) => {
              const value = s.values[area] ?? null;
              return (
                <div key={s.key} className="flex items-center gap-2" title={`${s.label}: ${value === null ? t("no score") : fmt(value, 0)}`}>
                  <div className="relative h-[6px] flex-1 rounded-r-[4px] bg-transparent">
                    {value !== null && <div className="absolute inset-y-0 left-0 rounded-r-[4px]" style={{ width: `${value}%`, background: s.color }} />}
                  </div>
                  <span className="tabular w-7 text-right text-2xs text-ink-3">{value === null ? "–" : fmt(value, 0)}</span>
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

// -- how the wrong answers went wrong ------------------------------------------------------------------

const KIND_ORDER = ["wrong", "format", "fabricated", "refused", "truncated"];
const KIND_COLOR: Record<string, string> = {
  wrong: "var(--bad)",
  format: "var(--limited)",
  fabricated: "var(--warn)",
  refused: "var(--accent)",
  truncated: "var(--off)",
};

export function kindColor(kind: string): string {
  return KIND_COLOR[kind] ?? "var(--off)";
}

export function ErrorBar({ kinds }: { kinds: Record<string, number> }) {
  const total = Object.values(kinds).reduce((a, b) => a + b, 0);
  if (!total) return <span className="text-xs text-ink-3">–</span>;
  const present = KIND_ORDER.filter((k) => kinds[k]);
  const text = present.map((k) => `${label(KIND_LABEL, k)}: ${kinds[k]}`).join(", ");
  return (
    <div className="flex h-3 w-full gap-[2px]" role="img" aria-label={text} title={text}>
      {present.map((k) => (
        <div key={k} className="min-w-[3px] first:rounded-l-[3px] last:rounded-r-[3px]" style={{ flex: kinds[k], background: kindColor(k) }} />
      ))}
    </div>
  );
}

export function KindLegend() {
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-1 text-2xs text-ink-3">
      {KIND_ORDER.map((k) => (
        <span key={k} className="inline-flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-sm" style={{ background: kindColor(k) }} />
          {label(KIND_LABEL, k)}
        </span>
      ))}
    </div>
  );
}

// -- the duel's butterfly ------------------------------------------------------------------------------

export function Butterfly({ rows, aColor, bColor, aLabel, bLabel }: { rows: AreaDuel[]; aColor: string; bColor: string; aLabel: string; bLabel: string }) {
  return (
    <div className="grid gap-2" role="img" aria-label={t("Area by area: {a} against {b}", { a: aLabel, b: bLabel })}>
      {rows.map((row) => {
        const aWins = row.winner === "a";
        const bWins = row.winner === "b";
        return (
          <div key={row.area} className="grid grid-cols-[1fr_124px_1fr] items-center gap-2 text-xs">
            <div className="flex items-center justify-end gap-2">
              {aWins && row.delta !== null && <span className="tabular font-semibold text-ink">+{fmt(row.delta, 0)}</span>}
              <span className="tabular w-6 text-right text-ink-2">{row.a === null ? "–" : fmt(row.a, 0)}</span>
              <div className="relative h-2 w-[46%] min-w-[60px] flex-1">
                {row.a !== null && <div className="absolute inset-y-0 right-0 rounded-l-[4px]" style={{ width: `${row.a}%`, background: aColor, opacity: bWins ? 0.55 : 1 }} title={`${aLabel}: ${fmt(row.a, 0)}`} />}
              </div>
            </div>
            <span className="text-center text-ink-2">{label(AREA_LABEL, row.area)}</span>
            <div className="flex items-center gap-2">
              <div className="relative h-2 w-[46%] min-w-[60px] flex-1">
                {row.b !== null && <div className="absolute inset-y-0 left-0 rounded-r-[4px]" style={{ width: `${row.b}%`, background: bColor, opacity: aWins ? 0.55 : 1 }} title={`${bLabel}: ${fmt(row.b, 0)}`} />}
              </div>
              <span className="tabular w-6 text-ink-2">{row.b === null ? "–" : fmt(row.b, 0)}</span>
              {bWins && row.delta !== null && <span className="tabular font-semibold text-ink">+{fmt(-row.delta, 0)}</span>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// -- speed against reliability ----------------------------------------------------------------------------

export interface Dot {
  key: string;
  label: string;
  x: number | null;
  y: number | null;
  color: string;
  highlight?: boolean;
}

export function SpeedScatter({ dots, width = 440, height = 220 }: { dots: Dot[]; width?: number; height?: number }) {
  const shown = dots.filter((d): d is Dot & { x: number; y: number } => d.x !== null && d.y !== null);
  if (shown.length === 0) return <p className="text-sm text-ink-3">{t("No speed was measured yet.")}</p>;
  const left = 38;
  const bottom = 26;
  const top = 10;
  const right = 16;
  const maxX = Math.max(10, Math.ceil(Math.max(...shown.map((d) => d.x)) / 20) * 20);
  const sx = (x: number) => left + ((width - left - right) * x) / maxX;
  const sy = (y: number) => top + ((height - top - bottom) * (100 - y)) / 100;
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="h-auto w-full" role="img" aria-label={t("Speed against reliability")}>
      {[0, 25, 50, 75, 100].map((y) => (
        <g key={y}>
          <line x1={left} x2={width - right} y1={sy(y)} y2={sy(y)} stroke="var(--line)" />
          <text x={left - 6} y={sy(y) + 3} textAnchor="end" fontSize={10} fill="var(--ink-3)">
            {y}
          </text>
        </g>
      ))}
      {[0, 0.5, 1].map((share) => (
        <text key={share} x={left + (width - left - right) * share} y={height - 8} textAnchor={share === 0 ? "start" : share === 1 ? "end" : "middle"} fontSize={10} fill="var(--ink-3)">
          {Math.round(maxX * share)}
        </text>
      ))}
      <text x={width - right} y={height - 8} textAnchor="end" fontSize={10} fill="var(--ink-3)" dy={-12}>
        tok/s
      </text>
      {shown.map((d) => (
        <g key={d.key}>
          <circle cx={sx(d.x)} cy={sy(d.y)} r={d.highlight ? 6 : 5} fill={d.color} stroke="var(--surface)" strokeWidth={2}>
            <title>{`${d.label}: ${fmt(d.y)} · ${fmt(d.x, 0)} tok/s`}</title>
          </circle>
          <text x={sx(d.x) + 9} y={sy(d.y) + 3} fontSize={11} fill="var(--ink-2)" paintOrder="stroke" stroke="var(--surface)" strokeWidth={3}>
            {d.label}
          </text>
        </g>
      ))}
    </svg>
  );
}

// -- accuracy against weight ---------------------------------------------------------------------------------------

export interface QuantDot {
  key: string;
  label: string;
  x: number | null;
  y: number;
  low: number | null;
  high: number | null;
  color: string;
  reference: boolean;
}

/** The quantizations of one model: how right each is (with its interval) against what it weighs, joined from the lightest to the heaviest. */
export function QuantScatter({ dots, xLabel, xFormat, width = 600, height = 300 }: { dots: QuantDot[]; xLabel: string; xFormat: (value: number) => string; width?: number; height?: number }) {
  const shown = dots.filter((d): d is QuantDot & { x: number } => d.x !== null);
  if (shown.length < 2) return <p className="text-sm text-ink-3">{t("This was not measured for enough of the quantizations to draw it.")}</p>;
  const left = 46;
  const right = 24;
  const top = 18;
  const bottom = 42;
  const [yMin, yMax] = scoreAxis(shown.map((d) => d.low ?? d.y), shown.map((d) => d.high ?? d.y));
  const [xMin, xMax] = spreadAxis(shown.map((d) => d.x));
  const sx = (x: number) => left + ((width - left - right) * (x - xMin)) / (xMax - xMin);
  const sy = (y: number) => top + ((height - top - bottom) * (yMax - y)) / (yMax - yMin);
  const yTicks = niceTicks(yMin, yMax);
  const xTicks = niceTicks(xMin, xMax);
  const line = [...shown].sort((a, b) => a.x - b.x).map((d) => `${sx(d.x).toFixed(1)},${sy(d.y).toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="h-auto w-full" role="img" aria-label={t("Accuracy against {axis}", { axis: xLabel.toLowerCase() })}>
      {yTicks.map((y) => (
        <g key={y}>
          <line x1={left} x2={width - right} y1={sy(y)} y2={sy(y)} stroke="var(--line)" />
          <text x={left - 7} y={sy(y) + 3} textAnchor="end" fontSize={10} fill="var(--ink-3)">
            {fmt(y, 0)}
          </text>
        </g>
      ))}
      {xTicks.map((x) => (
        <text key={x} x={sx(x)} y={height - bottom + 15} textAnchor="middle" fontSize={10} fill="var(--ink-3)">
          {xFormat(x)}
        </text>
      ))}
      <text x={(left + width - right) / 2} y={height - 6} textAnchor="middle" fontSize={11} fill="var(--ink-2)">
        {xLabel}
      </text>
      <polyline points={line} fill="none" stroke="var(--line-strong)" strokeWidth={1.5} strokeDasharray="4 4" />
      {shown.map((d) => (
        <g key={d.key}>
          {d.low !== null && d.high !== null && (
            <g stroke={d.color} strokeWidth={1.75} opacity={0.75}>
              <line x1={sx(d.x)} x2={sx(d.x)} y1={sy(d.low)} y2={sy(d.high)} />
              <line x1={sx(d.x) - 4} x2={sx(d.x) + 4} y1={sy(d.low)} y2={sy(d.low)} />
              <line x1={sx(d.x) - 4} x2={sx(d.x) + 4} y1={sy(d.high)} y2={sy(d.high)} />
            </g>
          )}
          <circle cx={sx(d.x)} cy={sy(d.y)} r={d.reference ? 7 : 6} fill={d.color} stroke={d.reference ? "var(--ink)" : "var(--surface)"} strokeWidth={d.reference ? 2 : 2}>
            <title>{`${d.label}: ${fmt(d.y)} · ${xFormat(d.x)}${d.low !== null && d.high !== null ? ` · ${fmt(d.low, 0)}–${fmt(d.high, 0)}` : ""}`}</title>
          </circle>
          <text x={sx(d.x)} y={sy(d.high ?? d.y) - 9} textAnchor="middle" fontSize={11} fontWeight={d.reference ? 700 : 500} fill="var(--ink-2)" paintOrder="stroke" stroke="var(--surface)" strokeWidth={3}>
            {d.label}
          </text>
        </g>
      ))}
    </svg>
  );
}

// -- one answer, as a mark ------------------------------------------------------------------------------------

export function StatusMark({ status, size = 22, title, selected = false }: { status: Status | null; size?: number; title?: string; selected?: boolean }) {
  if (status === null) {
    return (
      <span className="inline-grid place-content-center text-ink-3" style={{ width: size, height: size }} title={title ?? t("Not asked")}>
        ·
      </span>
    );
  }
  const tone = toneVar(statusTone(status));
  const hollow = status === "unscored" || status === "skipped";
  return (
    <span
      title={title ?? STATUS_LABEL[status]}
      aria-label={STATUS_LABEL[status]}
      className={cx("inline-grid place-content-center rounded-[5px] text-[11px] font-bold", selected && "ring-2 ring-accent ring-offset-1 ring-offset-bg")}
      style={{
        width: size,
        height: size,
        background: hollow ? "transparent" : `color-mix(in oklch, ${tone} 22%, transparent)`,
        color: tone,
        border: `1.5px ${status === "skipped" ? "dashed" : "solid"} color-mix(in oklch, ${tone} ${hollow ? 70 : 55}%, transparent)`,
      }}
    >
      {STATUS_GLYPH[status]}
    </span>
  );
}

export function Legend({ children }: { children: ReactNode }) {
  return <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-ink-2">{children}</div>;
}

export function Swatch({ color, children }: { color: string; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: color }} aria-hidden />
      {children}
    </span>
  );
}
