# Uncensored and abliterated models

Will It Run is a local model manager. Which legal open model you run on your own machine
is your decision. Models described as *uncensored*, *abliterated*, *derestricted*,
*unfiltered* or similar are discovered, displayed, recommended and installed exactly
like any other model.

## What "uncensored" means here

A label, derived from evidence, nothing more:

- the repository name contains one of: `uncensored`, `abliterated`, `abliteration`,
  `derestricted`, `de-restricted`, `unrestricted`, `unfiltered`, `decensored`,
  `heretic`, `lorablated`;
- or the repository is tagged with one of those terms;
- or its declared base model matches.

The evidence is stored with the model (`alignment_evidence`) and shown in the details
panel, for example *name contains "abliterated"; tagged "abliterated"*. Everything else
is labelled Standard. Will It Run does not test a model's behaviour and makes no claim
about it.

## Guarantees in the code

| Guarantee | Where | Test |
|---|---|---|
| Not hidden because of its name | `catalog/service.py`: no filter on alignment unless you choose one | `test_uncensored_models_are_never_hidden_by_name` |
| Not penalized in ranking | `catalog/scoring.py`: alignment is not a score component | `test_uncensored_models_are_not_penalized` (an uncensored build scores exactly like its standard twin) |
| A preference adds, never subtracts | `PREFERENCE_BONUS` applies to matching models only | `test_preference_adds_a_visible_bonus_and_never_a_penalty` |
| A preference cannot fabricate quality | The bonus requires task quality of at least 50 and a runnable fit | `test_preference_does_not_rescue_a_weak_model` |
| Installable like any model | Same download path, same disk check | `test_selected_uncensored_model_shows_full_trust_information` |

## Finding them

- **Models → Safety alignment**: `Any` / `Standard` / `Uncensored / Abliterated`, or the
  `Uncensored` chip.
- **Search**: `Qwen uncensored 27B`, `coding abliterated`,
  `best uncensored model for 16GB VRAM`. Because publishers use different words for the
  same idea, an uncensored search queries Hugging Face for `abliterated`, `uncensored`
  and `heretic` and merges the results.
- **CLI**: `willitrun recommend --uncensored`, `willitrun model search "qwen abliterated"`.
- **Teams**: choose `Uncensored` under Models, or write it in the description
  ("an uncensored AI team excellent at coding..."). The team then uses only uncensored
  models and says `Model preference: UNCENSORED`.

## Model preference setting

Settings → Models → Model preference:

- **Any model** (default): no effect on ranking at all.
- **Standard alignment preferred** / **Uncensored / abliterated preferred**: matching
  models get +6 points, shown as `preference_bonus` in the score breakdown. Models of the
  other kind stay visible, keep their score and remain installable.

## Trust information

These are community fine-tunes and quantizations, so provenance matters more, not less.
For every model Will It Run shows: publisher, repository, license, model family, fine-tune
lineage (base model), quantizer, architecture, parameters, quantization, download size
and the file's SHA-256. Missing fields are shown as **Unknown** with
`Metadata confidence: LOW`. Nothing is filled in by guesswork.

## Images

Pictures work the same way: any checkpoint or LoRA you put in ComfyUI's folders can be used,
including the ones published as *uncensored*, and the licence of each model is shown next to it.
Will It Run does not scan prompts, filter outputs or keep a record of what you asked for beyond
your own gallery. It does not decide what you may draw; it does ask the engine what it can run
before it runs it. Models, settings and licences: [IMAGE_STUDIO.md](IMAGE_STUDIO.md).

## Your responsibility

Will It Run ranks and runs models; it does not vet their content or their licenses. Read
the model card and license of anything you install, and use what you run lawfully.
High-risk *actions* by agents (deleting files, changing systems, sending data) require
your approval whatever model is behind them. See [SECURITY.md](SECURITY.md).
