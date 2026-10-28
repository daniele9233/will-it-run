import { Note } from "../../components/ui";
import { libsLabel, limitLines, motionFacts, paletteSwatches, type DnaView } from "../../lib/dna";
import { t } from "../../lib/i18n";

const unknown = () => t("Unknown");

/** What a study measured about a site: colours, fonts, libraries, motion, and what it could not do. Anything absent reads "Unknown". */
export function DnaCard({ dna }: { dna: DnaView }) {
  const swatches = paletteSwatches(dna);
  const motion = motionFacts(dna);
  const limits = limitLines(dna);
  const fonts = dna.tokens.fonts;
  return (
    <section className="clone-card grid content-start gap-3 p-4" aria-label={t("Site DNA")}>
      <div className="clone-label">{t("Site DNA")}</div>
      <div className="grid gap-1">
        <span className="text-2xs text-ink-3">{t("Palette")}</span>
        {swatches.length === 0 ? (
          <span className="text-xs text-ink-2">{unknown()}</span>
        ) : (
          <div className="flex flex-wrap gap-2">
            {swatches.map((s) => (
              <span key={s.hex} className="flex items-center gap-1.5 text-2xs text-ink-2" title={s.role ?? undefined}>
                <span className="h-4 w-4 rounded border border-line" style={{ background: s.hex }} aria-hidden />
                <span className="font-mono">{s.hex}</span>
                {s.role && <span className="text-ink-3">{s.role}</span>}
              </span>
            ))}
          </div>
        )}
      </div>
      <div className="grid gap-1">
        <span className="text-2xs text-ink-3">{t("Fonts")}</span>
        <span className="text-xs text-ink-2">{fonts === null ? unknown() : fonts.length === 0 ? t("None detected") : fonts.map((f) => f.family).join(", ")}</span>
      </div>
      <div className="grid gap-1">
        <span className="text-2xs text-ink-3">{t("Libraries")}</span>
        <span className="text-xs text-ink-2">{libsLabel(dna)}</span>
      </div>
      <div className="grid gap-1">
        <span className="text-2xs text-ink-3">{t("Sections")}</span>
        <span className="text-xs text-ink-2">{dna.sections === null ? unknown() : dna.sections.length === 0 ? t("None found") : String(dna.sections.length)}</span>
      </div>
      <div className="grid gap-1">
        <span className="text-2xs text-ink-3">{t("Motion")}</span>
        {motion.length === 0 ? (
          <span className="text-xs text-ink-2">{unknown()}</span>
        ) : (
          <ul className="grid gap-0.5 text-xs text-ink-2">
            {motion.map((m) => (
              <li key={m}>{m}</li>
            ))}
          </ul>
        )}
      </div>
      {limits.length > 0 && (
        <Note tone="warn">
          <ul className="grid gap-0.5">
            {limits.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        </Note>
      )}
    </section>
  );
}
