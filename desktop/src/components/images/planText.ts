import { t } from "../../lib/i18n";
import type { ImageWarning } from "../../lib/types";

/** The plan speaks in codes; the interface turns them into a sentence, once, for both pages. */

export function warningText(warning: ImageWarning): string {
  const v = warning.values ?? {};
  switch (warning.code) {
    case "engine_stopped":
      return t("The engine is stopped.");
    case "node_missing":
      return t("A custom node this model needs is not installed.");
    case "size_unknown":
      return t("The size of these files is not known until the download starts.");
    case "does_not_fit":
      return t("It does not fit: it needs {needed} GB of the {usable} GB this PC can give the card.", {
        needed: v.needed ?? 0,
        usable: v.usable ?? 0,
      });
    case "tight":
      return t("At the limit: only {headroom} GB to spare.", { headroom: v.headroom ?? 0 });
    case "free_vram_low":
      return t("{free} GB free right now against {needed} GB needed.", { free: v.free ?? 0, needed: v.needed ?? 0 });
    case "lmstudio_loaded":
      return t("LM Studio has a model loaded, and it competes for the same memory.");
    default:
      return warning.code;
  }
}

export function suggestionText(suggestion: ImageWarning): string {
  const v = suggestion.values ?? {};
  switch (suggestion.code) {
    case "lower_resolution":
      return v.megapixels
        ? t("Generate at about {megapixels} megapixels, then upscale.", { megapixels: v.megapixels })
        : t("Generate at a lower resolution, then upscale.");
    case "lighter_model":
      return t("Use a lighter model from the same family.");
    case "close_lmstudio":
      return t("Close the model loaded in LM Studio before generating.");
    case "enable_offload":
      return t("Let ComfyUI keep part of the system memory as weight cache.");
    case "can_go_large":
      return t("There is room: up to about {megapixels} megapixels.", { megapixels: v.megapixels ?? 0 });
    case "stay_small":
      return t("Stay near {megapixels} megapixels on this card.", { megapixels: v.megapixels ?? 0 });
    default:
      return suggestion.code;
  }
}

/** What to try after a run that died of VRAM. */
export function hintText(hint: ImageWarning): string {
  switch (hint.code) {
    case "lower_resolution":
      return t("Lower the resolution: this run did not fit in the card.");
    case "lighter_model":
      return t("Try a lighter model, or a smaller quantization of this one.");
    case "close_lmstudio":
      return t("Close the model loaded in LM Studio, then try again.");
    case "enable_offload":
      return t("Let ComfyUI keep part of the weights in system memory.");
    default:
      return hint.code;
  }
}
