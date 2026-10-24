import { ChevronRight, Loader2, Star } from "lucide-react";
import type { ButtonHTMLAttributes, CSSProperties, InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react";
import { useId, useState } from "react";

import { clamp, confidenceLabel, fitLabel, fitTone, type Tone } from "../lib/format";
import type { Confidence, FitCategory } from "../lib/types";
import { t } from "../lib/i18n";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

const TONE_TEXT: Record<Tone, string> = {
  ok: "text-ok",
  accent: "text-accent",
  warn: "text-warn",
  limited: "text-limited",
  bad: "text-bad",
  off: "text-off",
};
const TONE_BG: Record<Tone, string> = {
  ok: "bg-ok",
  accent: "bg-accent",
  warn: "bg-warn",
  limited: "bg-limited",
  bad: "bg-bad",
  off: "bg-off",
};

export function toneVar(tone: Tone): string {
  return `var(--${tone})`;
}

function softStyle(tone: Tone, strength = 15): CSSProperties {
  return { background: `color-mix(in oklch, ${toneVar(tone)} ${strength}%, transparent)` };
}

// -- buttons ------------------------------------------------------------------

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: "sm" | "md";
  loading?: boolean;
  icon?: ReactNode;
}

const BUTTON_VARIANT: Record<ButtonVariant, string> = {
  primary: "bg-accent text-accent-ink font-semibold hover:brightness-110 active:brightness-95",
  secondary: "bg-raised text-ink border border-line hover:border-line-strong active:bg-surface",
  ghost: "text-ink-2 hover:text-ink hover:bg-raised active:bg-surface",
  danger: "text-bad border border-line hover:border-bad active:bg-surface",
};

export function Button({ variant = "secondary", size = "md", loading, icon, children, className, disabled, ...rest }: ButtonProps) {
  return (
    <button
      type="button"
      {...rest}
      disabled={disabled || loading}
      className={cx(
        "inline-flex items-center justify-center gap-1.5 rounded-md whitespace-nowrap transition-[filter,background-color,border-color,color] duration-150",
        "disabled:opacity-45 disabled:hover:brightness-100",
        size === "sm" ? "h-7 px-2.5 text-xs" : "h-8 px-3 text-sm",
        BUTTON_VARIANT[variant],
        className,
      )}
    >
      {loading ? <Loader2 size={14} className="animate-spin" aria-hidden /> : icon}
      {children}
    </button>
  );
}

export function IconButton({ label, children, className, ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      {...rest}
      className={cx(
        "inline-flex h-7 w-7 items-center justify-center rounded-md text-ink-3 transition-colors duration-150 hover:bg-raised hover:text-ink disabled:opacity-40",
        className,
      )}
    >
      {children}
    </button>
  );
}

// -- status -------------------------------------------------------------------

export function Badge({ tone = "off", children, solid = false, title }: { tone?: Tone; children: ReactNode; solid?: boolean; title?: string }) {
  return (
    <span
      title={title}
      style={solid ? undefined : softStyle(tone)}
      className={cx(
        "inline-flex h-5 items-center gap-1 rounded-sm px-1.5 text-2xs font-semibold tracking-wide whitespace-nowrap",
        solid ? cx(TONE_BG[tone], "text-bg") : TONE_TEXT[tone],
      )}
    >
      {children}
    </span>
  );
}

export function FitBadge({ category, solid = false }: { category: FitCategory; solid?: boolean }) {
  return (
    <Badge tone={fitTone(category)} solid={solid}>
      {fitLabel(category)}
    </Badge>
  );
}

export function StatusDot({ tone, pulse = false }: { tone: Tone; pulse?: boolean }) {
  return <span aria-hidden className={cx("inline-block h-2 w-2 shrink-0 rounded-full", TONE_BG[tone], pulse && "pulse-dot")} />;
}

export function ConfidenceTag({ value }: { value: Confidence | string }) {
  const tone: Tone = value === "HIGH" ? "ok" : value === "MEDIUM" ? "accent" : value === "LOW" ? "warn" : "off";
  return (
    <span className={cx("text-2xs font-medium", TONE_TEXT[tone])} title={t("How much Will It Run trusts this figure")}>
      {t("{level} confidence", { level: confidenceLabel(String(value)) })}
    </span>
  );
}

export function AlignmentTag({ alignment }: { alignment: "standard" | "uncensored" }) {
  if (alignment === "standard") return null;
  return (
    <span className="inline-flex h-5 items-center rounded-sm border border-line-strong px-1.5 text-2xs font-medium text-ink-2" title={t("Uncensored / abliterated build")}>
      {t("Uncensored")}
    </span>
  );
}

// -- meters -------------------------------------------------------------------

interface MeterProps {
  /** 0..1 portion that is used / required. */
  value: number;
  /** 0..1 position of the recommended limit, drawn as a tick. */
  limit?: number;
  tone?: Tone;
  height?: number;
  label?: string;
}

export function Meter({ value, limit, tone = "accent", height = 6, label }: MeterProps) {
  const pct = clamp(value, 0, 1) * 100;
  return (
    <div
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(pct)}
      className="relative w-full overflow-hidden rounded-full bg-raised"
      style={{ height }}
    >
      <div className={cx("h-full rounded-full transition-[width] duration-200", TONE_BG[tone])} style={{ width: `${pct}%` }} />
      {limit !== undefined && (
        <div className="absolute top-0 h-full w-px bg-ink-2" style={{ left: `${clamp(limit, 0, 1) * 100}%` }} aria-hidden />
      )}
    </div>
  );
}

export function Stars({ value, label }: { value: number; label: string }) {
  if (value <= 0) return <span className="text-2xs text-ink-3">{t("Unknown")}</span>;
  return (
    <span className="inline-flex items-center gap-px" role="img" aria-label={t("{label}: {value} of 5", { label, value })}>
      {[1, 2, 3, 4, 5].map((n) => (
        <Star key={n} size={11} className={n <= value ? "fill-ink-2 text-ink-2" : "text-line-strong"} aria-hidden />
      ))}
    </span>
  );
}

// -- layout -------------------------------------------------------------------

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="flex shrink-0 items-start justify-between gap-6 border-b border-line px-7 pt-6 pb-4">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 max-w-[70ch] text-sm text-ink-2">{subtitle}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </header>
  );
}

export function Section({ title, aside, children, className }: { title?: ReactNode; aside?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={className}>
      {(title || aside) && (
        <div className="mb-2.5 flex items-baseline justify-between gap-4">
          {title && <h2 className="text-sm font-semibold text-ink">{title}</h2>}
          {aside && <div className="text-xs text-ink-3">{aside}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

export function Panel({ children, className, padded = true }: { children: ReactNode; className?: string; padded?: boolean }) {
  return <div className={cx("rounded-lg border border-line bg-surface", padded && "p-4", className)}>{children}</div>;
}

export function KeyValue({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[minmax(110px,auto)_1fr] gap-x-5 gap-y-1.5 text-sm">
      {rows.map(([key, value]) => (
        <div key={key} className="contents">
          <dt className="text-ink-3">{key}</dt>
          <dd className="selectable min-w-0 break-words text-ink">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Collapsible({ title, children, defaultOpen = false }: { title: ReactNode; children: ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();
  return (
    <div className="border-t border-line">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-1.5 py-2.5 text-left text-sm text-ink-2 hover:text-ink"
      >
        <ChevronRight size={14} className={cx("transition-transform duration-150", open && "rotate-90")} aria-hidden />
        {title}
      </button>
      {open && (
        <div id={id} className="fade-in pb-3 pl-5">
          {children}
        </div>
      )}
    </div>
  );
}

// -- forms --------------------------------------------------------------------

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="grid gap-1.5">
      <span className="text-xs font-medium text-ink-2">{label}</span>
      {children}
      {hint && <span className="text-xs text-ink-3">{hint}</span>}
    </label>
  );
}

const INPUT_CLASS =
  "h-8 w-full rounded-md border border-line bg-bg px-2.5 text-sm text-ink placeholder:text-ink-3 transition-colors duration-150 hover:border-line-strong focus:border-accent focus:outline-none disabled:opacity-50";

export function TextInput({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...rest} className={cx(INPUT_CLASS, className)} />;
}

export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select {...rest} className={cx(INPUT_CLASS, "pr-7", className)}>
      {children}
    </select>
  );
}

export function TextArea({ className, ...rest }: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...rest} className={cx(INPUT_CLASS, "h-auto resize-y py-2 leading-relaxed", className)} />;
}

export function Toggle({ checked, onChange, label, disabled }: { checked: boolean; onChange: (value: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cx(
        "relative h-5 w-9 shrink-0 rounded-full transition-colors duration-150 disabled:opacity-40",
        checked ? "bg-accent" : "bg-line-strong",
      )}
    >
      <span
        className={cx(
          "absolute top-0.5 left-0.5 h-4 w-4 rounded-full bg-bg transition-transform duration-150",
          checked && "translate-x-4",
        )}
      />
    </button>
  );
}

export function Segmented<T extends string>({ value, options, onChange, label }: { value: T; options: { value: T; label: string }[]; onChange: (value: T) => void; label: string }) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-md border border-line bg-bg p-0.5">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={option.value === value}
          onClick={() => onChange(option.value)}
          className={cx(
            "h-6 rounded-sm px-2.5 text-xs transition-colors duration-150",
            option.value === value ? "bg-raised font-medium text-ink" : "text-ink-3 hover:text-ink",
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cx(
        "h-7 rounded-md border px-2.5 text-xs transition-colors duration-150",
        active ? "soft-accent border-accent font-medium text-ink" : "border-line text-ink-2 hover:border-line-strong hover:text-ink",
      )}
    >
      {children}
    </button>
  );
}

export function Tabs<T extends string>({ value, tabs, onChange }: { value: T; tabs: { value: T; label: string }[]; onChange: (value: T) => void }) {
  return (
    <div role="tablist" className="flex gap-4 border-b border-line">
      {tabs.map((tab) => (
        <button
          key={tab.value}
          type="button"
          role="tab"
          aria-selected={tab.value === value}
          onClick={() => onChange(tab.value)}
          className={cx(
            "-mb-px shrink-0 border-b-2 pb-2 text-sm whitespace-nowrap transition-colors duration-150",
            tab.value === value ? "border-accent font-medium text-ink" : "border-transparent text-ink-3 hover:text-ink",
          )}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

// -- states -------------------------------------------------------------------

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="mx-auto flex max-w-md flex-col items-start gap-2 py-14">
      <h3 className="text-lg font-semibold">{title}</h3>
      {children && <p className="text-sm text-ink-2">{children}</p>}
      {action && <div className="mt-2 flex gap-2">{action}</div>}
    </div>
  );
}

export function SkeletonRows({ rows = 5, height = 44 }: { rows?: number; height?: number }) {
  return (
    <div className="grid gap-2" aria-busy="true" aria-label={t("Loading")}>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton" style={{ height }} />
      ))}
    </div>
  );
}

export function Note({ tone = "accent", children, action }: { tone?: Tone; children: ReactNode; action?: ReactNode }) {
  return (
    <div role={tone === "bad" ? "alert" : "status"} style={softStyle(tone, 10)} className="flex items-start gap-3 rounded-md border border-line px-3 py-2.5 text-sm">
      <StatusDot tone={tone} />
      <div className="-mt-0.5 min-w-0 flex-1 text-ink-2">{children}</div>
      {action}
    </div>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="rounded-sm border border-line bg-raised px-1.5 py-px font-mono text-2xs text-ink-2">{children}</kbd>;
}

/** "+ / - / ·" reasoning lines behind an automatic choice. */
export function WhyList({ lines }: { lines: string[] }) {
  return (
    <ul className="grid gap-1 text-sm">
      {lines.map((line, i) => {
        const mark = line.slice(0, 1);
        const known = mark === "+" || mark === "-" || mark === "·";
        const text = known ? line.slice(1).trim() : line;
        const tone = mark === "+" ? "text-ok" : mark === "-" ? "text-limited" : "text-ink-3";
        return (
          <li key={i} className="grid grid-cols-[14px_1fr] gap-1.5">
            <span className={cx("font-mono font-semibold", tone)} aria-hidden>
              {mark === "+" ? "+" : mark === "-" ? "−" : "·"}
            </span>
            <span className="selectable text-ink-2">{text}</span>
          </li>
        );
      })}
    </ul>
  );
}

export function ProgressBar({ value, indeterminate = false, tone = "accent" }: { value?: number; indeterminate?: boolean; tone?: Tone }) {
  return (
    <div className="relative h-1.5 w-full overflow-hidden rounded-full bg-raised" role="progressbar" aria-valuenow={indeterminate ? undefined : Math.round((value ?? 0) * 100)} aria-valuemin={0} aria-valuemax={100}>
      {indeterminate ? (
        <div className={cx("indeterminate absolute h-full w-1/4 rounded-full", TONE_BG[tone])} />
      ) : (
        <div className={cx("h-full rounded-full transition-[width] duration-200", TONE_BG[tone])} style={{ width: `${clamp(value ?? 0, 0, 1) * 100}%` }} />
      )}
    </div>
  );
}

export const toneText = TONE_TEXT;
export const toneBg = TONE_BG;
