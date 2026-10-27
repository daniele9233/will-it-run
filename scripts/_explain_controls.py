"""One-off: make the sampling and LoRA controls explain themselves."""

from __future__ import annotations

from pathlib import Path

PAGE = Path(r"D:\SotwareKikko\desktop\src\pages\Images.tsx")
text = PAGE.read_text(encoding="utf-8")

# -- 1. Sampling: the model's own recommendation, plus what each number means -----
start = text.index('              <Collapsible title={t("Sampling")}>')
end = text.index("              </Collapsible>", start) + len("              </Collapsible>")
sampling = '''              <Collapsible title={t("Sampling")}>
                <div className="grid gap-2 pt-1">
                  <p className="text-xs text-ink-3">
                    {model?.sampling?.steps
                      ? t("This model was trained for {steps} steps, CFG {cfg}, {sampler} / {scheduler}. Change them only if the picture says so.", {
                          steps: model.sampling.steps,
                          cfg: model.sampling.cfg ?? "1",
                          sampler: String(model.sampling.sampler ?? "euler"),
                          scheduler: String(model.sampling.scheduler ?? "simple"),
                        })
                      : t("Leave these as they are until the pictures give you a reason.")}
                  </p>
                  <div className="grid grid-cols-2 gap-3">
                    <Field
                      label={t("Steps")}
                      hint={t("How many times the noise is removed. More steps, more detail and more time: distilled models are trained for eight or ten.")}
                    >
                      <TextInput
                        type="number"
                        min={1}
                        max={200}
                        value={steps}
                        onChange={(event) => setSteps(event.target.value === "" ? "" : Number(event.target.value))}
                      />
                    </Field>
                    <Field
                      label={t("CFG")}
                      hint={t("How strictly the words are followed. 1 leaves the model free (turbo and distilled models), 4 to 7 stick to the prompt (SDXL).")}
                    >
                      <TextInput
                        type="number"
                        step="0.1"
                        min={0}
                        value={cfg}
                        onChange={(event) => setCfg(event.target.value === "" ? "" : Number(event.target.value))}
                      />
                    </Field>
                    <Field
                      label={t("Sampler")}
                      hint={t("The rule that removes the noise. euler for distilled models, dpmpp_2m for SDXL.")}
                    >
                      <Select value={sampler} onChange={(event) => setSampler(event.target.value)}>
                        {SAMPLERS.map((name) => (
                          <option key={name} value={name}>
                            {name}
                          </option>
                        ))}
                      </Select>
                    </Field>
                    <Field
                      label={t("Scheduler")}
                      hint={t("How the steps are spaced. simple for Krea and Qwen-Image, karras for SDXL.")}
                    >
                      <Select value={scheduler} onChange={(event) => setScheduler(event.target.value)}>
                        {SCHEDULERS.map((name) => (
                          <option key={name} value={name}>
                            {name}
                          </option>
                        ))}
                      </Select>
                    </Field>
                    {(mode === "edit" || mode === "upscale") && (
                      <Field label={t("How much to repaint")} hint={t("Low keeps the picture, high follows the text")}>
                        <TextInput
                          type="number"
                          step="0.05"
                          min={0}
                          max={1}
                          value={denoise}
                          onChange={(event) => setDenoise(event.target.value === "" ? "" : Number(event.target.value))}
                        />
                      </Field>
                    )}
                    <Field label={t("Pictures in this run")} hint={t("One batch, one wait: the card stays loaded.")}>
                      <Select value={String(batch)} onChange={(event) => setBatch(Number(event.target.value))}>
                        <option value="1">1</option>
                        <option value="2">2</option>
                        <option value="3">3</option>
                        <option value="4">4</option>
                      </Select>
                    </Field>
                  </div>
                </div>
              </Collapsible>'''
text = text[:start] + sampling + text[end:]

# -- 2. LoRA: the preset says it is a preset, and the format is spelled out ------
text = text.replace(
    '                          {t("Apply {name}", { name: preset.name })}',
    '                          {t("Preset: {name}", { name: preset.name })}',
)
text = text.replace(
    '''                              <span className="font-mono text-2xs text-ink-3">{check ?? ""}</span>''',
    '''                              {check && (
                                <span
                                  className="shrink-0 rounded-sm border border-line px-1 text-2xs text-ink-3"
                                  title={option.lora?.note}
                                >
                                  {check === "peft-transformer" ? t("PEFT format") : t("ComfyUI format")}
                                </span>
                              )}''',
)

# -- 3. The free list: video LoRAs are grouped and named as such -----------------
text = text.replace(
    '''                      <option value="">{t("Other LoRAs in ComfyUI")}</option>
                      {(lorasAvailable.data?.loras ?? []).map((name) => (
                        <option key={name} value={name}>
                          {name}
                        </option>
                      ))}''',
    '''                      <option value="">{t("Other LoRAs in ComfyUI")}</option>
                      <optgroup label={t("Images")}>
                        {(lorasAvailable.data?.loras ?? [])
                          .filter((name) => !isVideoLora(name))
                          .map((name) => (
                            <option key={name} value={name}>
                              {name}
                            </option>
                          ))}
                      </optgroup>
                      <optgroup label={t("Video (Wan) — has no effect on an image")}>
                        {(lorasAvailable.data?.loras ?? [])
                          .filter(isVideoLora)
                          .map((name) => (
                            <option key={name} value={name}>
                              {name}
                            </option>
                          ))}
                      </optgroup>''',
)

# a note right under the free selector, before the weight box
text = text.replace(
    '''                  {loras.length === 0 ? (''',
    '''                  <p className="text-2xs text-ink-3">
                    {t("These are the files ComfyUI sees in its loras folder. The Wan ones are for video: on an image model they do nothing, or damage the picture.")}
                  </p>

                  {loras.length === 0 ? (''',
)

# -- 4. The helper that tells a video LoRA from an image one --------------------
text = text.replace(
    "function secondsLabel(",
    '''/** Wan and its accelerators are video LoRAs: they belong to the other studio, not here. */
function isVideoLora(name: string): boolean {
  const low = name.toLowerCase();
  return /wan|lightx2v|i2v|t2v|\\d+steps/.test(low);
}

function secondsLabel(''',
)

PAGE.write_text(text, encoding="utf-8", newline="\n")
print("page patched")
