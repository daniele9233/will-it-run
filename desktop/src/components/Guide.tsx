// Help for people who are new to local AI: a glossary, small "?" explainers that can sit
// next to any term, and the accuracy meter used wherever a quantization is shown.

import { CircleHelp } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import type { Tone } from "../lib/format";
import { t } from "../lib/i18n";
import { useApp } from "../lib/store";
import type { Accuracy } from "../lib/types";
import { cx, toneBg, toneText } from "./ui";

export type TermId =
  | "model"
  | "params"
  | "quant"
  | "accuracy"
  | "gguf"
  | "vram"
  | "ram"
  | "context"
  | "tps"
  | "fit"
  | "score"
  | "estimate"
  | "safety"
  | "uncensored"
  | "lmstudio"
  | "agents";

export interface Term {
  id: TermId;
  term: string;
  text: string;
}

export const GLOSSARY: Term[] = [
  { id: "model", term: t("Model"), text: t("The AI itself: one large file of numbers that turns text into text. Different models are good at different things.") },
  { id: "params", term: t("Parameters (4B, 14B, 70B)"), text: t("The size of a model, in billions of numbers. Bigger usually means more capable and slower, and it needs more memory.") },
  { id: "quant", term: t("Quantization (Q4, Q5, Q8)"), text: t("A way to shrink a model by storing its numbers with fewer bits. Q8 is almost the original, Q4 is about half the size with a small loss, Q2 is tiny but clearly worse.") },
  { id: "accuracy", term: t("Accuracy"), text: t("How much of the original model's quality a quantization keeps. Will It Run shows the loss measured by the llama.cpp project on a reference model, so treat it as a guide.") },
  { id: "gguf", term: "GGUF", text: t("The file format local models come in. One .gguf file is one model at one quantization.") },
  { id: "vram", term: t("VRAM (graphics memory)"), text: t("Your graphics card's own memory. A model that fits entirely in it runs fast; one that spills into normal memory runs much slower.") },
  { id: "ram", term: t("RAM (system memory)"), text: t("The computer's normal memory. Models can run from it, but several times slower than from graphics memory.") },
  { id: "context", term: t("Context"), text: t("How much text the model can keep in mind at once, counted in tokens. A longer context needs more memory.") },
  { id: "tps", term: "tok/s", text: t("Tokens per second: how fast the model writes. A token is about three quarters of a word. Around 20 tok/s reads comfortably.") },
  { id: "fit", term: t("Fit"), text: t("Will It Run's verdict on how well one model file runs on this PC, from PERFECT down to IMPOSSIBLE.") },
  { id: "score", term: t("Will It Run score"), text: t("A 0 to 10 summary of how good a choice a model file is for this PC: how well it fits, how capable it is, how fast it runs and how trustworthy its information is.") },
  { id: "estimate", term: t("Estimated and measured"), text: t("Estimated speeds are calculated from your hardware. Measured speeds come from a benchmark run on this PC and replace the estimate.") },
  { id: "safety", term: t("Safe to install"), text: t("Will It Run never starts a download that would leave a drive without its reserve of free space.") },
  { id: "uncensored", term: t("Uncensored / abliterated"), text: t("A model edited to refuse less. Will It Run lists and ranks these by the same criteria as any other model.") },
  { id: "lmstudio", term: "LM Studio", text: t("The free program that actually runs the models. Will It Run tells it what to load and unload.") },
  { id: "agents", term: t("Agent, team, workflow"), text: t("An agent is a model given one job. A team is several agents handing work to each other, and the workflow is the order they do it in.") },
];

const BY_ID = new Map(GLOSSARY.map((entry) => [entry.id, entry]));

/** A small "?" that explains one term in place. */
export function HelpTip({ id, className }: { id: TermId; className?: string }) {
  const entry = BY_ID.get(id);
  const navigate = useApp((s) => s.navigate);
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null);
  const button = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    if (!open || !button.current) return;
    const rect = button.current.getBoundingClientRect();
    const width = 300;
    const height = panel.current?.offsetHeight ?? 140;
    const left = Math.max(12, Math.min(window.innerWidth - width - 12, rect.left + rect.width / 2 - width / 2));
    const below = rect.bottom + 8 + height < window.innerHeight;
    setPosition({ left, top: below ? rect.bottom + 8 : Math.max(12, rect.top - height - 8) });
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const close = (event: Event) => {
      if (event instanceof KeyboardEvent && event.key !== "Escape") return;
      if (event instanceof MouseEvent && (panel.current?.contains(event.target as Node) || button.current?.contains(event.target as Node))) return;
      setOpen(false);
    };
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", close);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("keydown", close);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [open]);

  if (!entry) return null;
  return (
    <>
      <button
        ref={button}
        type="button"
        aria-label={t("What is this? {term}", { term: entry.term })}
        aria-expanded={open}
        onClick={(event) => {
          event.stopPropagation();
          setOpen(!open);
        }}
        className={cx("inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full align-[-2px] text-ink-3 transition-colors duration-150 hover:text-accent", open && "text-accent", className)}
      >
        <CircleHelp size={13} aria-hidden />
      </button>
      {open &&
        createPortal(
          <div
            ref={panel}
            role="dialog"
            aria-label={entry.term}
            className="fade-in fixed z-[20] w-[300px] rounded-lg border border-line-strong bg-panel p-3.5 text-left"
            style={{ left: position?.left ?? 0, top: position?.top ?? 0, visibility: position ? "visible" : "hidden", boxShadow: "var(--shadow)" }}
          >
            <div className="text-sm font-semibold text-ink">{entry.term}</div>
            <p className="mt-1 text-sm font-normal text-ink-2">{entry.text}</p>
            <button
              type="button"
              className="mt-2 text-xs text-accent hover:underline"
              onClick={() => {
                setOpen(false);
                navigate("guide", { term: entry.id });
              }}
            >
              {t("Open the guide")}
            </button>
          </div>,
          document.body,
        )}
    </>
  );
}

const LEVEL_TONE: Tone[] = ["bad", "limited", "warn", "accent", "ok", "ok"];

export function accuracyTone(level: number): Tone {
  return LEVEL_TONE[Math.max(0, Math.min(5, level))] ?? "off";
}

/** Five steps: how much of the original quality a quantization keeps. */
export function AccuracyMeter({ accuracy, showLabel = true }: { accuracy: Accuracy; showLabel?: boolean }) {
  const unknown = accuracy.basis === "unknown";
  const tone = unknown ? "off" : accuracyTone(accuracy.level);
  const exact = accuracy.basis === "measured" || accuracy.basis === "original";
  return (
    <span className="inline-flex items-center gap-2" title={accuracy.note}>
      <span
        role="meter"
        aria-label={t("Accuracy kept by {quant}", { quant: accuracy.quant })}
        aria-valuemin={0}
        aria-valuemax={5}
        aria-valuenow={unknown ? 0 : accuracy.level}
        aria-valuetext={accuracy.label}
        className="inline-flex gap-[3px]"
      >
        {[1, 2, 3, 4, 5].map((step) => (
          <span key={step} className={cx("h-2.5 w-[7px] rounded-[2px]", !unknown && step <= accuracy.level ? toneBg[tone] : "bg-raised")} />
        ))}
      </span>
      {showLabel && (
        <span className={cx("text-xs", toneText[tone])}>
          {exact || unknown ? "" : "~"}
          {accuracy.label}
        </span>
      )}
    </span>
  );
}

/** "+0.0569" for a measured figure, a dash when none is published. */
export function pplLabel(accuracy: Accuracy): string {
  if (accuracy.basis === "original") return "0";
  if (accuracy.ppl_delta == null) return t("not published");
  const text = `+${accuracy.ppl_delta < 0.1 ? accuracy.ppl_delta.toFixed(3) : accuracy.ppl_delta.toFixed(2)}`;
  return accuracy.basis === "measured" ? text : `~${text}`;
}
