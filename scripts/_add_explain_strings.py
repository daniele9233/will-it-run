"""One-off: Italian for the explanations of the sampling and LoRA controls."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(r"D:\SotwareKikko")
PAIRS = ROOT / "desktop" / "src" / "locales" / "it.txt"
SEPARATOR = " ||| "

NEW = {
    "This model was trained for {steps} steps, CFG {cfg}, {sampler} / {scheduler}. Change them only if the picture says so.":
        "Questo modello è addestrato per {steps} passi, CFG {cfg}, {sampler} / {scheduler}. Cambiali solo se l'immagine te ne dà motivo.",
    "Leave these as they are until the pictures give you a reason.":
        "Lascia questi valori come sono finché le immagini non ti danno un motivo per cambiarli.",
    "How many times the noise is removed. More steps, more detail and more time: distilled models are trained for eight or ten.":
        "Quante volte viene tolto il rumore. Più passi, più dettaglio e più tempo: i modelli distillati sono addestrati per otto o dieci.",
    "How strictly the words are followed. 1 leaves the model free (turbo and distilled models), 4 to 7 stick to the prompt (SDXL).":
        "Quanto il modello resta attaccato alle parole. 1 lo lascia libero (modelli turbo e distillati), da 4 a 7 si attiene al prompt (SDXL).",
    "The rule that removes the noise. euler for distilled models, dpmpp_2m for SDXL.":
        "La regola con cui toglie il rumore. euler per i modelli distillati, dpmpp_2m per SDXL.",
    "How the steps are spaced. simple for Krea and Qwen-Image, karras for SDXL.":
        "Come distribuisce i passi. simple per Krea e Qwen-Image, karras per SDXL.",
    "Preset: {name}": "Preset: {name}",
    "PEFT format": "formato PEFT",
    "ComfyUI format": "formato ComfyUI",
    "Video (Wan) — has no effect on an image": "Video (Wan) — su un'immagine non ha effetto",
    "These are the files ComfyUI sees in its loras folder. The Wan ones are for video: on an image model they do nothing, or damage the picture.":
        "Sono i file che ComfyUI vede nella sua cartella loras. Quelli Wan sono per il video: su un modello di immagini non fanno niente, o rovinano l'immagine.",
}


def main() -> int:
    text = PAIRS.read_text(encoding="utf-8")
    existing = {line.split(SEPARATOR, 1)[0] for line in text.splitlines() if SEPARATOR in line}
    added = 0
    if not text.endswith("\n"):
        text += "\n"
    for key in sorted(NEW, key=str.lower):
        if key in existing:
            continue
        text += f"{key}{SEPARATOR}{NEW[key]}\n"
        added += 1
    PAIRS.write_text(text, encoding="utf-8", newline="\n")
    print(f"added {added}")
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "build_locale.py")], cwd=ROOT).returncode


if __name__ == "__main__":
    raise SystemExit(main())
